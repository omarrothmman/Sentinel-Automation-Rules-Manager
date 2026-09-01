import copy
import json
import tempfile
import unittest
from pathlib import Path

from sentinel_automation.errors import ConfigurationError
from sentinel_automation.rules import (
    add_condition,
    add_property_value,
    load_deployment_file,
    remove_condition,
    remove_property_value,
    resource_properties,
    set_enabled,
)

from .helpers import sample_properties


class RuleMutationTests(unittest.TestCase):
    def test_add_title_preserves_source_and_is_idempotent(self) -> None:
        source = sample_properties()
        changed, _ = add_property_value(source, "IncidentTitle", "New title")
        self.assertEqual(
            source["triggeringLogic"]["conditions"][0]["conditionProperties"]["propertyValues"],
            ["Existing title"],
        )
        values = changed["triggeringLogic"]["conditions"][0]["conditionProperties"][
            "propertyValues"
        ]
        self.assertEqual(values, ["Existing title", "New title"])
        unchanged, _ = add_property_value(changed, "IncidentTitle", "new TITLE")
        self.assertEqual(unchanged, changed)

    def test_nested_title_condition_is_found(self) -> None:
        source = sample_properties()
        title_condition = source["triggeringLogic"]["conditions"][0]
        source["triggeringLogic"]["conditions"] = [
            {
                "conditionType": "Boolean",
                "conditionProperties": {"operator": "And", "innerConditions": [title_condition]},
            }
        ]
        changed, _ = add_property_value(source, "IncidentTitle", "Nested")
        values = changed["triggeringLogic"]["conditions"][0]["conditionProperties"][
            "innerConditions"
        ][0]["conditionProperties"]["propertyValues"]
        self.assertIn("Nested", values)

    def test_ambiguous_title_conditions_require_index(self) -> None:
        source = sample_properties()
        source["triggeringLogic"]["conditions"].append(
            copy.deepcopy(source["triggeringLogic"]["conditions"][0])
        )
        with self.assertRaises(ConfigurationError):
            add_property_value(source, "IncidentTitle", "New")
        changed, _ = add_property_value(source, "IncidentTitle", "New", condition_index=2)
        first = changed["triggeringLogic"]["conditions"][0]["conditionProperties"]["propertyValues"]
        second = changed["triggeringLogic"]["conditions"][1]["conditionProperties"][
            "propertyValues"
        ]
        self.assertNotIn("New", first)
        self.assertIn("New", second)

    def test_remove_last_property_value_is_blocked(self) -> None:
        with self.assertRaises(ConfigurationError):
            remove_property_value(sample_properties(), "IncidentTitle", "Existing title")

    def test_add_and_remove_condition(self) -> None:
        source = sample_properties()
        changed, _ = add_condition(source, "IncidentSeverity", "Equals", ["Informational"])
        self.assertEqual(len(changed["triggeringLogic"]["conditions"]), 2)
        restored, _ = remove_condition(changed, "IncidentSeverity", "Equals", None)
        self.assertEqual(restored, source)

    def test_set_enabled(self) -> None:
        changed, _ = set_enabled(sample_properties(), False)
        self.assertFalse(changed["triggeringLogic"]["isEnabled"])

    def test_server_fields_are_removed(self) -> None:
        properties = sample_properties()
        properties["lastModifiedTimeUtc"] = "tomorrow"
        properties["actions"][0]["actionConfiguration"]["owner"] = None
        cleaned = resource_properties({"properties": properties})
        self.assertNotIn("lastModifiedTimeUtc", cleaned)
        self.assertNotIn("owner", cleaned["actions"][0]["actionConfiguration"])

    def test_exported_arm_template_is_accepted(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "rule.json"
        rule_id = "5c1a45fc-08c1-42f1-84fd-c5f86596e382"
        path.write_text(
            json.dumps(
                {
                    "resources": [
                        {
                            "id": f"/providers/Microsoft.SecurityInsights/AutomationRules/{rule_id}",
                            "type": "Microsoft.SecurityInsights/automationRules",
                            "properties": sample_properties(),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        discovered, properties = load_deployment_file(path)
        self.assertEqual(discovered, rule_id)
        self.assertEqual(properties["displayName"], "Close Known Benign Incidents")


if __name__ == "__main__":
    unittest.main()
