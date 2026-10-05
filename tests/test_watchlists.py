import copy
import http.client
import io
import json
import tempfile
import threading
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from sentinel_automation import cli, watchlists
from sentinel_automation.errors import ConcurrentChangeError, ConfigurationError, PlanIntegrityError
from sentinel_automation.gui import GuiConfig, GuiServer, GuiService
from sentinel_automation.plans import apply_plan, load_plan, rollback_run, save_plan
from sentinel_automation.util import atomic_write_json

from .helpers import workspace


class WatchlistClient:
    api_version = "2025-09-01"

    def __init__(self):
        self.rows = {}
        self.writes = []
        self.fail_after_write = False

    def close(self):
        pass

    def get_watchlist(self, target, alias):
        return {"name": alias, "properties": {"itemsSearchKey": "IP", "displayName": alias}}

    def list_watchlists(self, target):
        return [self.get_watchlist(target, "AllowedIPs")]

    def list_watchlist_items(self, target, alias):
        return [
            copy.deepcopy(r)
            for (key, name, _), r in self.rows.items()
            if key == target.key and name == alias and not r["properties"].get("isDeleted")
        ]

    def get_watchlist_item(self, target, alias, row_id):
        return copy.deepcopy(self.rows.get((target.key, alias, row_id)))

    def seed(self, target, ip="10.0.0.1", note="old"):
        row_id = str(uuid.uuid4())
        self.rows[(target.key, "AllowedIPs", row_id)] = {
            "name": row_id,
            "etag": "1",
            "properties": {
                "itemsKeyValue": {"IP": ip, "Note": note},
                "entityMapping": {"IP": "Address"},
            },
        }
        return row_id

    def put_watchlist_item(self, target, alias, row_id, props, etag):
        current = self.rows.get((target.key, alias, row_id))
        if current and current["etag"] != etag:
            raise ConcurrentChangeError("etag mismatch")
        self.writes.append((target.key, row_id, "put"))
        self.rows[(target.key, alias, row_id)] = {
            "name": row_id,
            "etag": str(len(self.writes) + 1),
            "properties": copy.deepcopy(props),
        }
        if self.fail_after_write:
            self.fail_after_write = False
            raise RuntimeError("Connection lost after Azure accepted write")

    def delete_watchlist_item(self, target, alias, row_id, etag):
        current = self.rows[(target.key, alias, row_id)]
        if current["etag"] != etag:
            raise ConcurrentChangeError("etag mismatch")
        self.writes.append((target.key, row_id, "delete"))
        current["properties"]["isDeleted"] = True
        current["etag"] = str(len(self.writes) + 1)


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = WatchlistClient()
        self.target = workspace()
        self.row_id = self.client.seed(self.target)
        self.payload = {
            "alias": "AllowedIPs",
            "action": "update",
            "key_column": "IP",
            "key_value": "10.0.0.1",
            "values": {"Note": "new"},
        }

    def plan(self, payload=None, targets=None):
        plan = watchlists.build_plan(self.client, targets or [self.target], payload or self.payload)
        return save_plan(plan, self.root / "plan.json")

    def test_update_preserves_columns_and_mapping_and_rolls_back(self):
        before = copy.deepcopy(self.client.rows)
        plan = self.plan()
        self.assertEqual(self.client.rows, before)
        self.assertEqual(load_plan(self.root / "plan.json"), plan)
        run_id, report = apply_plan(self.client, plan, self.root)
        self.assertTrue(report["successful"])
        resource = self.client.get_watchlist_item(self.target, "AllowedIPs", self.row_id)
        self.assertEqual(resource["properties"]["itemsKeyValue"], {"IP": "10.0.0.1", "Note": "new"})
        self.assertEqual(resource["properties"]["entityMapping"], {"IP": "Address"})
        self.assertTrue(rollback_run(self.client, self.root, run_id)["successful"])
        restored = self.client.get_watchlist_item(self.target, "AllowedIPs", self.row_id)
        self.assertEqual(
            restored["properties"],
            before[(self.target.key, "AllowedIPs", self.row_id)]["properties"],
        )
        self.assertEqual(
            rollback_run(self.client, self.root, run_id)["results"][0]["status"], "already_restored"
        )

    def test_add_and_delete_recovery_including_soft_deleted_rows(self):
        for action in ("add", "delete"):
            with self.subTest(action=action):
                payload = {**self.payload, "action": action, "values": {"IP": "10.0.0.2"}}
                plan = self.plan(payload)
                run_id, report = apply_plan(self.client, plan, self.root)
                self.assertTrue(report["successful"])
                self.assertTrue(rollback_run(self.client, self.root, run_id)["successful"])
                self.assertEqual(
                    len(self.client.list_watchlist_items(self.target, "AllowedIPs")), 1
                )

    def test_stale_snapshot_prevents_writes(self):
        plan = self.plan()
        self.client.seed(self.target, "10.0.0.2")
        _, report = apply_plan(self.client, plan, self.root)
        self.assertFalse(report["successful"])
        self.assertEqual(self.client.writes, [])

    def test_failed_write_is_recoverable(self):
        plan = self.plan()
        self.client.fail_after_write = True
        run_id, report = apply_plan(self.client, plan, self.root)
        self.assertFalse(report["successful"])
        self.assertTrue(report["results"][0]["write_attempted"])
        self.assertTrue(rollback_run(self.client, self.root, run_id)["successful"])

    def test_rollback_rejects_later_edits(self):
        run_id, _ = apply_plan(self.client, self.plan(), self.root)
        self.client.rows[(self.target.key, "AllowedIPs", self.row_id)]["properties"][
            "itemsKeyValue"
        ]["Note"] = "someone else"
        self.assertFalse(rollback_run(self.client, self.root, run_id)["successful"])
        self.assertTrue(rollback_run(self.client, self.root, run_id, force=True)["successful"])

    def test_csv_replace_merge_and_roundtrip(self):
        self.client.seed(self.target, "10.0.0.9")
        payload = {
            "alias": "AllowedIPs",
            "action": "import",
            "key_column": "IP",
            "csv": 'IP,Note\r\n10.0.0.1,"hello, world"\r\n10.0.0.2,"two\nlines"\r\n',
        }
        merge = self.plan(payload)
        self.assertEqual([t["status"] for t in merge["targets"]], ["modify", "create"])
        replace = self.plan({**payload, "mode": "replace"})
        self.assertEqual([t["status"] for t in replace["targets"]], ["modify", "create", "delete"])
        run_id, result = apply_plan(self.client, replace, self.root)
        self.assertTrue(result["successful"])
        exported = watchlists.export_csv(
            self.client.list_watchlist_items(self.target, "AllowedIPs"), "IP"
        )
        self.assertEqual(watchlists.parse_csv(exported), watchlists.parse_csv(payload["csv"]))
        self.assertTrue(rollback_run(self.client, self.root, run_id)["successful"])

    def test_invalid_csv_rejected(self):
        for text in ("", "IP,IP\na,b", "IP,Note\na", "IP\na,b", "IP\n", 'IP\n"unclosed'):
            with self.subTest(text=text), self.assertRaises(ConfigurationError):
                watchlists.parse_csv(text)

    def test_duplicates_missing_rows_and_search_key_rejected(self):
        for payload in (
            {**self.payload, "key_value": "missing"},
            {**self.payload, "values": {"IP": ""}},
            {**self.payload, "action": "import", "csv": "IP\na\na"},
        ):
            with self.assertRaises(ConfigurationError):
                self.plan(payload)
        self.client.seed(self.target)
        with self.assertRaises(ConfigurationError):
            self.plan()
        plan = self.plan({**self.payload, "item_id": self.row_id})
        self.assertEqual(len(plan["targets"]), 1)

    def test_multi_workspace_uses_each_item_id_and_continues_failures(self):
        other = workspace("customer-b")
        second_id = self.client.seed(other)
        plan = self.plan(targets=[self.target, other])
        self.assertEqual([t["item_id"] for t in plan["targets"]], [self.row_id, second_id])
        self.client.seed(self.target, "10.0.0.3")
        _, report = apply_plan(self.client, plan, self.root)
        self.assertEqual([r["status"] for r in report["results"]], ["failed", "updated"])

    def test_tampering_and_api_mismatch_rejected(self):
        plan = self.plan()
        plan["targets"][0]["after"]["itemsKeyValue"]["Note"] = "tampered"
        with self.assertRaises(PlanIntegrityError):
            apply_plan(self.client, plan, self.root)
        plan = self.plan()
        self.client.api_version = "other"
        with self.assertRaises(ConfigurationError):
            apply_plan(self.client, plan, self.root)

    def setup_gui(self):
        inventory = self.root / "inventory.json"
        (self.root / "catalog").mkdir(exist_ok=True)
        atomic_write_json(inventory, {"workspaces": [self.target.to_dict()]})
        service = GuiService(
            GuiConfig(
                inventory, self.root, self.root / "catalog", "interactive", self.client.api_version
            ),
            self.client,
        )
        return inventory, service

    def test_gui_and_cli_share_saved_plans_apply_and_rollback(self):
        inventory, service = self.setup_gui()
        args = cli.build_parser().parse_args(
            [
                "--inventory",
                str(inventory),
                "--state-dir",
                str(self.root),
                "watchlists",
                "plan",
                "update",
                "--targets",
                self.target.key,
                "--alias",
                "AllowedIPs",
                "--key-column",
                "IP",
                "--key-value",
                "10.0.0.1",
                "--set",
                "Note=from CLI",
            ]
        )
        with patch.object(cli, "_client", return_value=self.client), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.run(args), 0)
        filename = service.bootstrap()["plans"][0]["file"]
        self.assertEqual(
            service.open_plan({"plan_file": filename})["plan"]["resource_type"], "watchlist-items"
        )
        with self.assertRaises(ConfigurationError):
            service.apply({"plan_file": filename})
        result = service.apply({"plan_file": filename, "confirmation": "APPLY"})
        self.assertTrue(result["report"]["successful"])
        args = cli.build_parser().parse_args(
            [
                "--inventory",
                str(inventory),
                "--state-dir",
                str(self.root),
                "rollback",
                "--run",
                result["run_id"],
                "--yes",
            ]
        )
        with patch.object(cli, "_client", return_value=self.client), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.run(args), 0)
        planned = service.watchlists({**self.payload, "request": "plan", "targets": "all"})
        args = cli.build_parser().parse_args(
            [
                "--inventory",
                str(inventory),
                "--state-dir",
                str(self.root),
                "apply",
                "--plan",
                planned["plan_file"],
                "--yes",
            ]
        )
        with patch.object(cli, "_client", return_value=self.client), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.run(args), 0)

    def test_gui_list_export_and_scope_checks(self):
        _, service = self.setup_gui()
        self.assertEqual(
            len(service.watchlists({"request": "list", "targets": "all"})["watchlists"]), 1
        )
        exported = service.watchlists(
            {"request": "export", "targets": "all", "alias": "AllowedIPs"}
        )
        self.assertEqual(watchlists.parse_csv(exported["csv"])[0]["IP"], "10.0.0.1")
        planned = service.watchlists({**self.payload, "request": "plan", "targets": "all"})
        result = service.apply({"plan_file": planned["plan_file"], "confirmation": "APPLY"})
        service.inventory = type(service.inventory)(None, (workspace("other"),))
        with self.assertRaises(ConfigurationError):
            service.rollback({"run_id": result["run_id"], "confirmation": "ROLLBACK"})

    def test_http_watchlist_routes_require_token_and_return_preview(self):
        _, service = self.setup_gui()
        server = GuiServer(("127.0.0.1", 0), service, "test-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(*server.server_address)
        try:
            connection.request(
                "POST",
                "/api/watchlists",
                json.dumps({"targets": "all"}),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            connection.close()
            connection = http.client.HTTPConnection(*server.server_address)
            connection.request(
                "POST",
                "/api/watchlists",
                json.dumps({**self.payload, "request": "plan", "targets": "all"}),
                {"Content-Type": "application/json", "X-CSRF-Token": "test-token"},
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            result = json.loads(response.read())
            self.assertEqual(result["plan"]["targets"][0]["after"]["itemsKeyValue"]["Note"], "new")
            self.assertEqual(self.client.writes, [])
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cli_csv_import_export_and_no_change(self):
        inventory, _ = self.setup_gui()
        output = self.root / "rows.csv"
        prefix = ["--inventory", str(inventory), "--state-dir", str(self.root), "watchlists"]
        suffix = ["--targets", self.target.key, "--alias", "AllowedIPs"]
        for arguments in (
            ["export", *suffix, "--output", str(output)],
            ["import", *suffix, "--file", str(output), "--key-column", "IP"],
        ):
            args = cli.build_parser().parse_args([*prefix, *arguments])
            with (
                patch.object(cli, "_client", return_value=self.client),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.run(args), 0)
        saved = next((self.root / "plans").glob("*.json"))
        plan = load_plan(saved)
        self.assertEqual(plan["targets"][0]["status"], "no_change")
        _, result = apply_plan(self.client, plan, self.root)
        self.assertTrue(result["successful"])
        self.assertEqual(self.client.writes, [])
