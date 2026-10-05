import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from sentinel_automation.gui import GuiConfig, GuiService
from sentinel_automation.mcp_server import SentinelMcp, create_server
from sentinel_automation.util import atomic_write_json

from .helpers import FakeArmClient, resource, workspace
from .test_watchlists import WatchlistClient

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


@unittest.skipUnless(MCP_AVAILABLE, 'Install the "mcp" extra to run MCP tests')
class McpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "rules").mkdir()
        self.target = workspace()
        atomic_write_json(self.root / "inventory.json", {"workspaces": [self.target.to_dict()]})
        self.client = WatchlistClient()
        self.client.seed(self.target)
        self.service = GuiService(
            GuiConfig(
                self.root / "inventory.json",
                self.root / "state",
                self.root / "rules",
                "cli",
                self.client.api_version,
            ),
            self.client,
        )
        self.adapter = SentinelMcp(self.service)

    async def test_protocol_plan_apply_rollback_and_discovery(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tests.mcp_fixture", str(self.root)],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            self.assertEqual(initialized.serverInfo.name, "sentinel-manager")
            listed = await session.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            self.assertEqual(len(tools), 18)
            self.assertTrue(tools["get_watchlist"].annotations.readOnlyHint)
            self.assertTrue(tools["apply_changes"].annotations.destructiveHint)
            self.assertIn("approval_phrase", tools["apply_changes"].inputSchema["required"])
            status = await session.call_tool("sentinel_status", {})
            self.assertFalse(status.isError)
            self.assertEqual(status.structuredContent["transport"], "stdio")
            plan = await session.call_tool(
                "plan_watchlist_change",
                {
                    "targets": self.target.key,
                    "alias": "AllowedIPs",
                    "action": "update",
                    "key_column": "IP",
                    "key_value": "10.0.0.1",
                    "values": {"Note": "MCP edit"},
                },
            )
            self.assertFalse(plan.isError, plan.content)
            self.assertIn("Before:", plan.content[0].text)
            details = plan.structuredContent
            rejected = await session.call_tool(
                "apply_changes", {"plan_file": details["plan_file"], "approval_phrase": "APPLY"}
            )
            self.assertTrue(rejected.isError)
            current = await session.call_tool(
                "get_watchlist", {"workspace": self.target.key, "alias": "AllowedIPs"}
            )
            self.assertEqual(current.structuredContent["items"][0]["values"]["Note"], "old")
            applied = await session.call_tool(
                "apply_changes",
                {
                    "plan_file": details["plan_file"],
                    "approval_phrase": details["approval_phrase"],
                },
            )
            self.assertFalse(applied.isError, applied.content)
            run_id = applied.structuredContent["run_id"]
            self.assertTrue((self.root / "state" / "backups" / run_id / "manifest.json").is_file())
            run = await session.call_tool("review_run", {"run_id": run_id})
            restored = await session.call_tool(
                "rollback_changes",
                {"run_id": run_id, "approval_phrase": run.structuredContent["approval_phrase"]},
            )
            self.assertFalse(restored.isError, restored.content)
            current = await session.call_tool(
                "get_watchlist", {"workspace": self.target.key, "alias": "AllowedIPs"}
            )
            self.assertEqual(current.structuredContent["items"][0]["values"]["Note"], "old")
            invalid = await session.call_tool("review_plan", {"plan_file": "../inventory.json"})
            self.assertTrue(invalid.isError)
            self.assertIn("error_info", invalid.structuredContent)
            malformed = await session.call_tool("plan_watchlist_change", {"action": "update"})
            self.assertTrue(malformed.isError)

    async def test_read_only_omits_writes_and_local_guard_rejects(self):
        server = create_server(self.service, read_only=True)
        names = {tool.name for tool in await server.list_tools()}
        self.assertNotIn("apply_changes", names)
        self.assertNotIn("rollback_changes", names)
        self.assertIn("plan_rule_change", names)
        readonly = SentinelMcp(self.service, read_only=True)
        self.assertTrue(readonly.call(lambda: readonly.apply("test.json", "APPLY")).isError)

    async def test_tool_schemas_and_rule_flow(self):
        rule_id = "00000000-0000-0000-0000-000000000001"
        self.service.client = FakeArmClient({(self.target.key, rule_id): resource(rule_id)})
        server = create_server(self.service)

        # Invoke registered functions directly here; the subprocess test covers MCP serialization.
        def invoke(name, **kwargs):
            return server._tool_manager.get_tool(name).fn(**kwargs)

        planned = invoke(
            "plan_rule_change",
            targets=self.target.key,
            operation="set-enabled",
            rule_id=rule_id,
            enabled=False,
        )
        self.assertFalse(planned.isError, planned.content)
        details = planned.structuredContent
        self.assertTrue(
            self.service.client.get_rule(self.target, rule_id)["properties"]["triggeringLogic"][
                "isEnabled"
            ]
        )
        applied = invoke(
            "apply_changes",
            plan_file=details["plan_file"],
            approval_phrase=details["approval_phrase"],
        )
        self.assertFalse(applied.isError, applied.content)
        self.assertFalse(
            self.service.client.get_rule(self.target, rule_id)["properties"]["triggeringLogic"][
                "isEnabled"
            ]
        )
        run_id = applied.structuredContent["run_id"]
        denied = invoke(
            "rollback_changes", run_id=run_id, approval_phrase=f"ROLLBACK {run_id}", force=True
        )
        self.assertTrue(denied.isError)
        restored = invoke("rollback_changes", run_id=run_id, approval_phrase=f"ROLLBACK {run_id}")
        self.assertFalse(restored.isError, restored.content)

    async def test_offline_startup_from_unrelated_working_directory(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        # The installed entry point must not depend on the desktop client's working directory.
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "sentinel_automation.mcp_server",
                "--root",
                str(self.root),
                "--inventory",
                "inventory.json",
                "--read-only",
            ],
            cwd=str(self.root),
        )
        import anyio

        with anyio.fail_after(20):
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("sentinel_status", {})
                self.assertFalse(result.structuredContent["connected"])
                self.assertTrue(result.structuredContent["read_only"])
                inventory = await session.call_tool("list_workspaces", {})
                self.assertFalse(inventory.isError)
                self.assertEqual(
                    inventory.structuredContent["workspaces"][0]["key"], self.target.key
                )
