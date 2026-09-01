import copy
import tempfile
import unittest
import uuid
from pathlib import Path

from sentinel_automation.catalog import (
    add_catalog_rule,
    catalog_table,
    import_catalog_bundle,
    load_catalog,
    select_catalog_rules,
)
from sentinel_automation.errors import ConfigurationError
from sentinel_automation.util import atomic_write_json

from .helpers import sample_properties


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.catalog = self.root / "rules"

    def _source(self, name: str, rule_id: str, display_name: str) -> Path:
        properties = sample_properties()
        properties["displayName"] = display_name
        path = self.root / name
        atomic_write_json(path, {"rule_id": rule_id, "properties": properties})
        return path

    def _bundle(self, rules: list[tuple[str, str]]) -> Path:
        resources = []
        for rule_id, display_name in rules:
            properties = sample_properties()
            properties["displayName"] = display_name
            resources.append(
                {
                    "name": (
                        f"[concat(parameters('workspace'),'/Microsoft.SecurityInsights/{rule_id}')]"
                    ),
                    "type": "Microsoft.OperationalInsights/workspaces/providers/AutomationRules",
                    "properties": properties,
                }
            )
        path = self.root / "bundle.json"
        atomic_write_json(path, {"resources": resources})
        return path

    def test_add_load_select_and_render_catalog_rule(self) -> None:
        rule_id = str(uuid.uuid4())
        source = self._source("source.json", rule_id, "Close Known Benign Incidents")
        added = add_catalog_rule(self.catalog, source, "Known Benign Closure")
        self.assertEqual(added.logical_name, "known-benign-closure")
        self.assertEqual(added.path.name, "known-benign-closure.json")

        rules = load_catalog(self.catalog)
        selected = select_catalog_rules(rules, "known-benign-closure")
        self.assertEqual(selected[0].rule_id, rule_id)
        output = catalog_table(rules, self.catalog, validated=True)
        self.assertIn("AUTOMATION RULE CATALOG", output)
        self.assertIn("Validation: SUCCESS", output)
        self.assertIn("DEPENDENCIES", output)

    def test_empty_catalog_lists_cleanly_but_cannot_be_deployed(self) -> None:
        self.catalog.mkdir()
        rules = load_catalog(self.catalog)
        self.assertEqual(rules, [])
        self.assertIn("No results.", catalog_table(rules, self.catalog))
        with self.assertRaises(ConfigurationError):
            select_catalog_rules(rules, "all")

    def test_duplicate_display_name_is_rejected_and_new_file_is_removed(self) -> None:
        first = self._source("one.json", str(uuid.uuid4()), "Shared Display Name")
        second = self._source("two.json", str(uuid.uuid4()), "Shared Display Name")
        add_catalog_rule(self.catalog, first, "first")
        with self.assertRaises(ConfigurationError):
            add_catalog_rule(self.catalog, second, "second")
        self.assertFalse((self.catalog / "second.json").exists())
        self.assertEqual(len(load_catalog(self.catalog)), 1)

    def test_force_failure_restores_existing_catalog_file(self) -> None:
        original = self._source("original.json", str(uuid.uuid4()), "Original")
        conflict = self._source("conflict.json", str(uuid.uuid4()), "Conflict")
        replacement = self._source("replacement.json", str(uuid.uuid4()), "Conflict")
        add_catalog_rule(self.catalog, original, "base")
        add_catalog_rule(self.catalog, conflict, "other")
        before = copy.deepcopy(load_catalog(self.catalog)[0].document())
        with self.assertRaises(ConfigurationError):
            add_catalog_rule(self.catalog, replacement, "base", force=True)
        restored = {rule.logical_name: rule for rule in load_catalog(self.catalog)}["base"]
        self.assertEqual(restored.document(), before)

    def test_arm_bundle_imports_all_rules_atomically(self) -> None:
        first_id = str(uuid.uuid4())
        second_id = str(uuid.uuid4())
        bundle = self._bundle(
            [(first_id, "Close Known Benign Incidents"), (second_id, "Assign High Severity")]
        )
        imported = import_catalog_bundle(self.catalog, bundle)
        self.assertEqual(len(imported), 2)
        rules = load_catalog(self.catalog)
        self.assertEqual(
            {rule.logical_name for rule in rules},
            {"close-known-benign-incidents", "assign-high-severity"},
        )

    def test_bundle_conflict_leaves_catalog_unchanged(self) -> None:
        existing = self._source("existing.json", str(uuid.uuid4()), "Conflicting Name")
        add_catalog_rule(self.catalog, existing, "existing-base")
        before = load_catalog(self.catalog)[0].document()
        bundle = self._bundle([(str(uuid.uuid4()), "Conflicting Name")])
        with self.assertRaises(ConfigurationError):
            import_catalog_bundle(self.catalog, bundle)
        rules = load_catalog(self.catalog)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].document(), before)


if __name__ == "__main__":
    unittest.main()
