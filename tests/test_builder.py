import copy
import unittest

from sentinel_automation.builder import validate_builder_rule
from sentinel_automation.errors import ConfigurationError


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.rule = {
            "displayName": "New rule",
            "order": 1,
            "triggeringLogic": {
                "triggersOn": "Incidents",
                "triggersWhen": "Created",
                "isEnabled": False,
                "conditions": [],
            },
            "actions": [
                {
                    "order": 1,
                    "actionType": "RunPlaybook",
                    "actionConfiguration": {
                        "logicAppResourceId": "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/test/providers/Microsoft.Logic/workflows/playbook",
                        "tenantId": "00000000-0000-0000-0000-000000000002",
                    },
                }
            ],
        }

    def test_playbook_and_alert_trigger(self):
        self.assertEqual(validate_builder_rule(self.rule), self.rule)
        self.rule["triggeringLogic"]["triggersOn"] = "Alerts"
        self.assertEqual(validate_builder_rule(self.rule), self.rule)

    def test_invalid_configurations_are_rejected(self):
        for change in (
            "empty-actions",
            "bad-tenant",
            "bad-resource",
            "action-order",
            "alert-update",
            "bad-condition",
            "alert-incident-action",
        ):
            with self.subTest(change=change):
                rule = copy.deepcopy(self.rule)
                if change == "empty-actions":
                    rule["actions"] = []
                elif change == "bad-tenant":
                    rule["actions"][0]["actionConfiguration"]["tenantId"] = "invalid"
                elif change == "bad-resource":
                    rule["actions"][0]["actionConfiguration"]["logicAppResourceId"] = "name-only"
                elif change == "action-order":
                    rule["actions"][0]["order"] = 2
                elif change == "alert-update":
                    rule["triggeringLogic"].update(triggersOn="Alerts", triggersWhen="Updated")
                elif change == "bad-condition":
                    rule["triggeringLogic"]["conditions"] = [{"conditionType": "Invalid"}]
                else:
                    rule["triggeringLogic"]["triggersOn"] = "Alerts"
                    rule["actions"] = [
                        {
                            "order": 1,
                            "actionType": "ModifyProperties",
                            "actionConfiguration": {"severity": "High"},
                        }
                    ]
                with self.assertRaises(ConfigurationError):
                    validate_builder_rule(rule)
