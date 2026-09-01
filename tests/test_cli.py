import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from sentinel_automation.cli import _resolve_plan_path, _validate_plan_scope, build_parser, run
from sentinel_automation.discovery import DiscoveryResult
from sentinel_automation.errors import ConfigurationError
from sentinel_automation.inventory import Inventory
from sentinel_automation.util import atomic_write_json

from .helpers import sample_properties, workspace


class CliTests(unittest.TestCase):
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
