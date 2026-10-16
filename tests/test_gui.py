import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sentinel_automation.discovery import DiscoveryResult
from sentinel_automation.errors import ConfigurationError
from sentinel_automation.gui import GuiConfig, GuiServer, GuiService, build_parser
from sentinel_automation.util import atomic_write_json

from .helpers import FakeArmClient, resource, sample_properties, workspace


class GuiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.inventory_path = self.root / "config" / "workspaces.json"
        self.state_dir = self.root / "state"
        self.catalog_dir = self.root / "rules"
        self.catalog_dir.mkdir(parents=True)
        self.target = workspace()
        atomic_write_json(
            self.inventory_path,
            {
                "managing_tenant_id": "tenant-id",
                "workspaces": [
                    {
                        **self.target.to_dict(),
                        "enabled": True,
                        "tags": ["production"],
                    }
                ],
            },
        )
        self.rule_id = "00000000-0000-0000-0000-000000000001"
        self.client = FakeArmClient(
            {(self.target.key, self.rule_id): resource(self.rule_id, sample_properties())}
        )
        self.service = GuiService(
            GuiConfig(
                self.inventory_path,
                self.state_dir,
                self.catalog_dir,
                "interactive",
                self.client.api_version,
            ),
            self.client,  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_add_live_rule_to_catalog_rejects_duplicates_without_changing_azure(self):
        from sentinel_automation.catalog import load_catalog

        before = self.client.get_rule(self.target, self.rule_id)
        payload = {"workspace": self.target.key, "rule_id": self.rule_id, "name": "My rule"}
        result = self.service.add_rule_to_catalog(payload)
        self.assertEqual(result["name"], "my-rule")
        self.assertEqual(load_catalog(self.catalog_dir)[0].rule_id, self.rule_id)
        with self.assertRaises(ConfigurationError):
            self.service.add_rule_to_catalog(payload)
        with self.assertRaises(ConfigurationError):
            self.service.add_rule_to_catalog({**payload, "name": "another-name"})
        self.assertEqual(len(load_catalog(self.catalog_dir)), 1)
        self.assertEqual(self.client.get_rule(self.target, self.rule_id), before)

    def test_builder_save_plan_apply_and_rollback(self):
        from sentinel_automation.catalog import load_catalog
        from sentinel_automation.plans import apply_plan, rollback_run

        properties = sample_properties()
        properties["displayName"] = "New reusable rule"
        properties["actions"] = [
            {
                "order": 1,
                "actionType": "ModifyProperties",
                "actionConfiguration": {"severity": "High"},
            }
        ]
        saved = self.service.create_catalog_rule({"name": "new-rule", "properties": properties})
        self.assertIsNone(self.client.get_rule(self.target, saved["rule_id"]))
        self.assertEqual(load_catalog(self.catalog_dir)[0].rule_id, saved["rule_id"])
        with self.assertRaises(ConfigurationError):
            self.service.create_catalog_rule({"name": "new-rule", "properties": properties})
        with self.assertRaises(ConfigurationError):
            self.service.plan_catalog_assignment({"name": "new-rule", "workspaces": []})
        with self.assertRaises(ConfigurationError):
            self.service.plan_catalog_assignment({"name": "new-rule", "workspaces": ["all"]})
        planned = self.service.plan_catalog_assignment(
            {"name": "new-rule", "workspaces": [self.target.key]}
        )
        self.assertEqual(planned["plan"]["targets"][0]["status"], "create")
        self.assertIsNone(self.client.get_rule(self.target, saved["rule_id"]))
        run_id, report = apply_plan(self.client, planned["plan"], self.state_dir)
        self.assertTrue(report["successful"])
        self.assertIsNotNone(self.client.get_rule(self.target, saved["rule_id"]))
        self.assertTrue(rollback_run(self.client, self.state_dir, run_id)["successful"])
        self.assertIsNone(self.client.get_rule(self.target, saved["rule_id"]))

    def test_gui_parser_does_not_change_cli_parser(self) -> None:
        args = build_parser().parse_args(
            [
                "--auth",
                "cli",
                "--tenant-id",
                "tenant-override",
                "--port",
                "8123",
                "--no-browser",
            ]
        )
        self.assertEqual(args.auth, "cli")
        self.assertEqual(args.tenant_id, "tenant-override")
        self.assertEqual(args.port, 8123)
        self.assertTrue(args.no_browser)

    def test_first_run_setup_authenticates_discovers_and_saves_inventory(self) -> None:
        inventory_path = self.root / "fresh" / "workspaces.json"
        service = GuiService(
            GuiConfig(
                inventory_path,
                self.state_dir,
                self.catalog_dir,
                "interactive",
                self.client.api_version,
            )
        )
        initial = service.bootstrap()
        self.assertFalse(initial["authenticated"])
        self.assertTrue(initial["needs_setup"])
        self.assertEqual(initial["workspaces"], [])

        azure_client = Mock()
        azure_client.api_version = self.client.api_version
        discovery = DiscoveryResult(
            workspaces=(self.target,),
            issues=(),
            log_analytics_count=1,
            subscription_count=1,
            subscription_names={self.target.subscription_id: "Customer subscription"},
        )
        with (
            patch("sentinel_automation.gui.create_credential", return_value=object()),
            patch("sentinel_automation.gui.ArmClient", return_value=azure_client),
            patch(
                "sentinel_automation.gui.discover_sentinel_workspaces",
                return_value=discovery,
            ) as discover,
        ):
            result = service.setup({"auth": "interactive", "tenant_id": "tenant-first-run"})

        azure_client.authenticate.assert_called_once_with()
        discover.assert_called_once_with(azure_client, "tenant-first-run")
        self.assertTrue(inventory_path.is_file())
        self.assertTrue(result["authenticated"])
        self.assertFalse(result["needs_setup"])
        self.assertEqual(result["managing_tenant_id"], "tenant-first-run")
        self.assertEqual(len(result["workspaces"]), 1)
        service.close()
        azure_client.close.assert_called_once_with()

    def test_lists_live_rules_as_structured_data(self) -> None:
        result = self.service.list_rules({"targets": "all"})
        self.assertEqual(result["workspace_count"], 1)
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["rules"][0]["rule_id"], self.rule_id)
        self.assertTrue(result["rules"][0]["enabled"])

    def test_plan_and_apply_reuse_safe_core_workflow(self) -> None:
        created = self.service.create_plan(
            {
                "operation": "add-title",
                "targets": "all",
                "display_name": "Close Known Benign Incidents",
                "title": "GUI managed title",
                "skip_missing": True,
            }
        )
        self.assertEqual(created["plan"]["targets"][0]["status"], "modify")
        self.assertTrue((self.state_dir / "plans" / created["plan_file"]).is_file())

        with self.assertRaisesRegex(ConfigurationError, "Type APPLY"):
            self.service.apply({"plan_file": created["plan_file"], "confirmation": "yes"})

        applied = self.service.apply({"plan_file": created["plan_file"], "confirmation": "APPLY"})
        self.assertTrue(applied["report"]["successful"])
        updated = self.client.get_rule(self.target, self.rule_id)
        values = updated["properties"]["triggeringLogic"]["conditions"][0]["conditionProperties"][
            "propertyValues"
        ]
        self.assertIn("GUI managed title", values)

    def test_plan_path_cannot_escape_state_directory(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "Invalid plan filename"):
            self.service.apply({"plan_file": "../outside.json", "confirmation": "APPLY"})

    def test_local_server_serves_assets_and_rejects_invalid_csrf(self) -> None:
        bootstrap = {
            "authenticated": True,
            "workspaces": [],
            "catalog": [],
            "plans": [],
            "runs": [],
        }
        web_service = SimpleNamespace(bootstrap=lambda: bootstrap)
        server = GuiServer(("127.0.0.1", 0), web_service, "test-session-token")  # type: ignore[arg-type]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            index = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn('content="test-session-token"', index)
            self.assertIn("<title>Sentinel Toolkit</title>", index)
            self.assertEqual(index.count("data-theme-toggle"), 2)

            connection.request("GET", "/app.js")
            response = connection.getresponse()
            script = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("sentinel-manager-theme", script)
            self.assertIn("prefers-color-scheme: dark", script)

            connection.request(
                "POST",
                "/api/rules",
                body=json.dumps({"targets": "all"}),
                headers={"Content-Type": "application/json", "X-CSRF-Token": "wrong"},
            )
            response = connection.getresponse()
            body = json.loads(response.read())
            self.assertEqual(response.status, 403)
            self.assertIn("session token", body["error"])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
