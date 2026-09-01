import tempfile
import unittest
from pathlib import Path

from sentinel_automation.catalog import CatalogRule
from sentinel_automation.errors import PlanIntegrityError, RuleNotFoundError
from sentinel_automation.plans import (
    apply_plan,
    build_catalog_deployment_plan,
    build_deployment_plan,
    build_mutation_plan,
    json_diff,
    plan_summary,
    rollback_run,
    save_plan,
    validate_plan,
)
from sentinel_automation.rules import RuleSelector, add_property_value
from sentinel_automation.util import read_json

from .helpers import FakeArmClient, resource, sample_properties, workspace

RULE_ID = "5c1a45fc-08c1-42f1-84fd-c5f86596e382"


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = workspace()
        self.client = FakeArmClient({(self.target.key, RULE_ID): resource(RULE_ID)})
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)

    def _plan(self):
        return build_mutation_plan(
            self.client,
            [self.target],
            RuleSelector(display_name="Close Known Benign Incidents"),
            "add-title",
            {"title": "New title"},
            lambda source: add_property_value(source, "IncidentTitle", "New title"),
        )

    def test_integrity_detects_edits(self) -> None:
        path = self.state / "plan.json"
        plan = save_plan(self._plan(), path)
        validate_plan(plan)
        plan["targets"][0]["after_body"]["properties"]["displayName"] = "tampered"
        with self.assertRaises(PlanIntegrityError):
            validate_plan(plan)

    def test_diff_reports_title_array_path_and_values(self) -> None:
        changes = json_diff(
            {"triggeringLogic": {"titles": ["old"]}},
            {"triggeringLogic": {"titles": ["old", "new"]}},
        )
        self.assertEqual(changes[0]["path"], "/triggeringLogic/titles")
        self.assertEqual(changes[0]["before"], ["old"])
        self.assertEqual(changes[0]["after"], ["old", "new"])

    def test_apply_verifies_and_rollback_restores(self) -> None:
        plan = save_plan(self._plan(), self.state / "plan.json")
        run_id, report = apply_plan(self.client, plan, self.state)
        self.assertTrue(report["successful"])
        changed = self.client.get_rule(self.target, RULE_ID)
        values = changed["properties"]["triggeringLogic"]["conditions"][0]["conditionProperties"][
            "propertyValues"
        ]
        self.assertIn("New title", values)

        rollback = rollback_run(self.client, self.state, run_id)
        self.assertTrue(rollback["successful"])
        restored = self.client.get_rule(self.target, RULE_ID)
        values = restored["properties"]["triggeringLogic"]["conditions"][0]["conditionProperties"][
            "propertyValues"
        ]
        self.assertEqual(values, ["Existing title"])

    def test_apply_rejects_state_changed_after_plan(self) -> None:
        plan = save_plan(self._plan(), self.state / "plan.json")
        changed = sample_properties()
        changed["order"] = 99
        self.client.resources[(self.target.key, RULE_ID)]["properties"] = changed
        _, report = apply_plan(self.client, plan, self.state)
        self.assertFalse(report["successful"])
        self.assertIn("changed after planning", report["results"][0]["error"])

    def test_new_rule_deployment_and_rollback_deletes_it(self) -> None:
        client = FakeArmClient()
        plan = build_deployment_plan(
            client, [self.target], RULE_ID, sample_properties(), "rule.json", "fail"
        )
        sealed = save_plan(plan, self.state / "deploy.json")
        run_id, report = apply_plan(client, sealed, self.state)
        self.assertTrue(report["successful"])
        self.assertIsNotNone(client.get_rule(self.target, RULE_ID))
        rollback = rollback_run(client, self.state, run_id)
        self.assertTrue(rollback["successful"])
        self.assertIsNone(client.get_rule(self.target, RULE_ID))

    def test_catalog_deployment_plans_multiple_rules_and_workspaces(self) -> None:
        second_id = "6c1a45fc-08c1-42f1-84fd-c5f86596e383"
        second_properties = sample_properties()
        second_properties["displayName"] = "Assign High Severity Incidents"
        rules = [
            CatalogRule("closure", RULE_ID, sample_properties(), Path("closure.json")),
            CatalogRule("assignment", second_id, second_properties, Path("assignment.json")),
        ]
        second_workspace = workspace("customer-b")
        client = FakeArmClient()
        plan = build_catalog_deployment_plan(client, [self.target, second_workspace], rules, "fail")
        self.assertEqual(len(plan["targets"]), 4)
        self.assertTrue(all(target["status"] == "create" for target in plan["targets"]))

        sealed = save_plan(plan, self.state / "catalog.json")
        run_id, report = apply_plan(client, sealed, self.state)
        self.assertTrue(report["successful"])
        self.assertIsNotNone(client.get_rule(second_workspace, second_id))
        rollback = rollback_run(client, self.state, run_id)
        self.assertTrue(rollback["successful"])
        self.assertIsNone(client.get_rule(self.target, RULE_ID))

    def test_manifest_is_written(self) -> None:
        plan = save_plan(self._plan(), self.state / "plan.json")
        run_id, _ = apply_plan(self.client, plan, self.state)
        manifest = read_json(self.state / "backups" / run_id / "manifest.json")
        self.assertEqual(manifest["run_id"], run_id)

    def test_skip_missing_records_workspace_without_writing(self) -> None:
        missing = workspace("customer-b")
        plan = build_mutation_plan(
            self.client,
            [self.target, missing],
            RuleSelector(display_name="Close Known Benign Incidents"),
            "add-title",
            {"title": "New title", "skip_missing": True},
            lambda source: add_property_value(source, "IncidentTitle", "New title"),
            skip_missing=True,
        )
        self.assertEqual([target["status"] for target in plan["targets"]], ["modify", "skipped"])
        skipped = plan["targets"][1]
        self.assertEqual(skipped["skip_reason"], "rule_not_found")
        self.assertIsNone(skipped["after_body"])

        sealed = save_plan(plan, self.state / "skip-missing.json")
        validate_plan(sealed)
        _, report = apply_plan(self.client, sealed, self.state)
        self.assertTrue(report["successful"])
        self.assertEqual(report["results"][1]["status"], "skipped")

    def test_missing_rule_still_fails_without_skip_missing(self) -> None:
        with self.assertRaises(RuleNotFoundError):
            build_mutation_plan(
                self.client,
                [workspace("customer-b")],
                RuleSelector(display_name="Close Known Benign Incidents"),
                "add-title",
                {"title": "New title"},
                lambda source: add_property_value(source, "IncidentTitle", "New title"),
            )

    def test_plan_summary_is_compact_and_does_not_dump_rule_arrays(self) -> None:
        missing = workspace("customer-b")
        plan = build_mutation_plan(
            self.client,
            [self.target, missing],
            RuleSelector(display_name="Close Known Benign Incidents"),
            "add-title",
            {"title": "New title", "skip_missing": True},
            lambda source: add_property_value(source, "IncidentTitle", "New title"),
            skip_missing=True,
        )
        output = plan_summary(plan)
        self.assertIn("AUTOMATION RULE CHANGE PLAN", output)
        self.assertIn("WILL CHANGE", output)
        self.assertIn("NOT FOUND", output)
        self.assertIn("New title", output)
        self.assertNotIn("propertyValues", output)
        self.assertNotIn("Existing title", output)


if __name__ == "__main__":
    unittest.main()
