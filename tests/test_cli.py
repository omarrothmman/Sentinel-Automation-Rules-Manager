import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from sentinel_automation.cli import _resolve_plan_path, _validate_plan_scope, build_parser, run
from sentinel_automation.discovery import DiscoveryResult
from sentinel_automation.errors import AzureRequestError, ConfigurationError
from sentinel_automation.inventory import Inventory
from sentinel_automation.util import atomic_write_json

from .helpers import sample_properties, workspace


class CliTests(unittest.TestCase):
    def test_missing_rules_excludes_matches_and_failed_workspaces(self) -> None:
        args = build_parser().parse_args(
            ["list-rules", "--targets", "all", "--name-contains", "[DEF] Failsafe", "--missing"]
        )
        targets = tuple(
            replace(workspace(), key=key) for key in ("failed", "match", "empty", "other")
        )
        client = Mock()
        client.list_rules.side_effect = [
            AzureRequestError("SubscriptionNotFound"),
            [
                {
                    "properties": {
                        "displayName": "[def] FAILSAFE - Copy 1",
                        "triggeringLogic": {"isEnabled": False},
                    }
                }
            ],
            [],
            [{"properties": {"displayName": "D Failsafe"}}],
        ]
        with (
            patch("sentinel_automation.cli.Inventory.load", return_value=Inventory(None, targets)),
            patch("sentinel_automation.cli._client", return_value=client),
            patch("sentinel_automation.cli.render_table", return_value="result") as render,
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()) as errors,
        ):
            self.assertEqual(run(args), 2)
        self.assertEqual([row[0] for row in render.call_args.args[3]], ["empty", "other"])
        self.assertIn("3/4 workspaces checked; 1 failed", render.call_args.args[1])
        self.assertIn("failed: SubscriptionNotFound", errors.getvalue())
        client.close.assert_called_once()

    def test_missing_requires_nonempty_filter_before_azure_access(self) -> None:
        for options in ([], ["--name-contains", ""], ["--name-contains", "   "]):
            with self.subTest(options=options):
                args = build_parser().parse_args(
                    ["list-rules", "--targets", "all", "--missing", *options]
                )
                with patch("sentinel_automation.cli._client") as client:
                    with self.assertRaisesRegex(ConfigurationError, "requires a non-empty"):
                        run(args)
                    client.assert_not_called()

    def test_list_rules_name_search_and_partial_failure(self) -> None:
        args = build_parser().parse_args(
            ["list-rules", "--targets", "all", "--name-contains", "[DEF] Failsafe"]
        )
        inventory = Inventory(None, tuple(replace(workspace(), key=key) for key in ("a", "b", "c")))
        client = Mock()
        client.list_rules.side_effect = [
            AzureRequestError("SubscriptionNotFound"),
            [
                {"name": "match", "properties": {"displayName": "[def] FAILSAFE - Copy 1"}},
                {"name": "decoy", "properties": {"displayName": "D Failsafe"}},
            ],
            [],
        ]
        output, errors = StringIO(), StringIO()
        with (
            patch("sentinel_automation.cli.Inventory.load", return_value=inventory),
            patch("sentinel_automation.cli._client", return_value=client),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            self.assertEqual(run(args), 2)
        self.assertIn("[def] FAILSAFE - Copy 1", output.getvalue())
        self.assertNotIn("decoy", output.getvalue())
        self.assertIn("1 matching rules across 1 workspaces", output.getvalue())
        self.assertIn("2/3 workspaces checked; 1 failed", output.getvalue())
        self.assertIn("a: SubscriptionNotFound", errors.getvalue())
        self.assertEqual(client.list_rules.call_count, 3)
        client.close.assert_called_once()

    def test_list_rules_unfiltered_and_no_matches(self) -> None:
        for options, expected_count in (([], 1), (["--name-contains", "absent"], 0)):
            with self.subTest(options=options):
                args = build_parser().parse_args(["list-rules", "--targets", "all", *options])
                client = Mock()
                client.list_rules.return_value = [
                    {"name": "rule-id", "properties": {"displayName": "Failsafe"}}
                ]
                with (
                    patch(
                        "sentinel_automation.cli.Inventory.load",
                        return_value=Inventory(None, (workspace(),)),
                    ),
                    patch("sentinel_automation.cli._client", return_value=client),
                    patch("sentinel_automation.cli.render_table", return_value="result") as render,
                    redirect_stdout(StringIO()),
                ):
                    self.assertEqual(run(args), 0)
                self.assertEqual(len(render.call_args.args[3]), expected_count)
                client.close.assert_called_once()

    def test_version_argument(self) -> None:
        output = StringIO()
        with self.assertRaises(SystemExit) as result, redirect_stdout(output):
            build_parser().parse_args(["--version"])
        self.assertEqual(result.exception.code, 0)
        self.assertRegex(output.getvalue(), r"^sentinel-auto \d+\.\d+\.\d+\s*$")

    def test_add_title_arguments(self) -> None:
        args = build_parser().parse_args(
            [
                "plan",
                "add-title",
                "--targets",
                "all",
                "--display-name",
                "Rule",
                "--title",
                "Title",
                "--skip-missing",
            ]
        )
        self.assertEqual(args.plan_command, "add-title")
        self.assertEqual(args.title, "Title")
        self.assertTrue(args.skip_missing)

    def test_discover_save_arguments(self) -> None:
        args = build_parser().parse_args(["discover", "--tenant-id", "tenant", "--save", "--yes"])
        self.assertEqual(args.command, "discover")
        self.assertEqual(args.tenant_id, "tenant")
        self.assertTrue(args.save)

    def test_catalog_and_catalog_deployment_arguments(self) -> None:
        catalog = build_parser().parse_args(
            ["catalog", "add", "--name", "closure", "--file", "rule.json"]
        )
        self.assertEqual(catalog.catalog_command, "add")
        self.assertEqual(catalog.name, "closure")
        bundle = build_parser().parse_args(["catalog", "import-bundle", "--file", "rules.json"])
        self.assertEqual(bundle.catalog_command, "import-bundle")
        deployment = build_parser().parse_args(
            [
                "plan",
                "deploy-catalog",
                "--rules",
                "closure,assignment",
                "--targets",
                "all",
                "--if-exists",
                "skip",
            ]
        )
        self.assertEqual(deployment.plan_command, "deploy-catalog")
        self.assertEqual(deployment.rules, "closure,assignment")

    def test_catalog_commands_do_not_require_workspace_inventory(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        source = root / "source.json"
        catalog_dir = root / "rules"
        atomic_write_json(
            source,
            {
                "rule_id": "5c1a45fc-08c1-42f1-84fd-c5f86596e382",
                "properties": sample_properties(),
            },
        )
        add = build_parser().parse_args(
            [
                "--catalog-dir",
                str(catalog_dir),
                "catalog",
                "add",
                "--name",
                "closure",
                "--file",
                str(source),
            ]
        )
        self.assertEqual(run(add), 0)
        listing = build_parser().parse_args(
            ["--catalog-dir", str(catalog_dir), "catalog", "validate"]
        )
        self.assertEqual(run(listing), 0)

    def test_plan_scope_must_match_inventory(self) -> None:
        target = workspace()
        inventory = Inventory(None, (target,))
        plan = {"targets": [{"workspace": target.to_dict()}]}
        _validate_plan_scope(plan, inventory)
        changed = target.to_dict()
        changed["subscription_id"] = "different"
        with self.assertRaises(ConfigurationError):
            _validate_plan_scope({"targets": [{"workspace": changed}]}, inventory)

    def test_bare_plan_filename_resolves_in_default_plan_directory(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        state_dir = Path(directory.name)
        plan_path = state_dir / "plans" / "change-plan.json"
        plan_path.parent.mkdir()
        plan_path.touch()

        self.assertEqual(_resolve_plan_path(Path("change-plan.json"), state_dir), plan_path)
        explicit = Path("another-directory") / "change-plan.json"
        self.assertEqual(_resolve_plan_path(explicit, state_dir), explicit)

    def test_discover_save_creates_valid_inventory(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        inventory_path = Path(directory.name) / "workspaces.json"
        args = build_parser().parse_args(
            ["--inventory", str(inventory_path), "discover", "--save", "--yes"]
        )

        class Client:
            def close(self) -> None:
                return None

        result = DiscoveryResult((workspace(),), (), 1, 1)
        with (
            patch("sentinel_automation.cli.create_credential", return_value=object()),
            patch("sentinel_automation.cli.ArmClient", return_value=Client()),
            patch("sentinel_automation.cli.discover_sentinel_workspaces", return_value=result),
        ):
            self.assertEqual(run(args), 0)

        saved = Inventory.load(inventory_path)
        self.assertEqual(saved.workspaces[0].key, "customer-a")


if __name__ == "__main__":
    unittest.main()
