from __future__ import annotations

import copy
from typing import Any

from sentinel_automation.errors import ConcurrentChangeError
from sentinel_automation.inventory import Workspace


def sample_properties() -> dict[str, Any]:
    return {
        "displayName": "Close Known Benign Incidents",
        "order": 2,
        "triggeringLogic": {
            "isEnabled": True,
            "triggersOn": "Incidents",
            "triggersWhen": "Created",
            "conditions": [
                {
                    "conditionType": "Property",
                    "conditionProperties": {
                        "propertyName": "IncidentTitle",
                        "operator": "Contains",
                        "propertyValues": ["Existing title"],
                    },
                }
            ],
        },
        "actions": [
            {
                "order": 1,
                "actionType": "ModifyProperties",
                "actionConfiguration": {"status": "Closed"},
            }
        ],
    }


def workspace(key: str = "customer-a") -> Workspace:
    return Workspace(
        key=key,
        display_name=key.title(),
        subscription_id="11111111-1111-1111-1111-111111111111",
        resource_group="sentinel-rg",
        workspace_name=f"{key}-sentinel",
        enabled=True,
        tags=(),
    )


class FakeArmClient:
    api_version = "2025-09-01"

    def __init__(self, resources: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.resources = copy.deepcopy(resources or {})
        self.etag_counter = 1

    def list_rules(self, target: Workspace) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(resource)
            for (key, _), resource in self.resources.items()
            if key == target.key
        ]

    def get_rule(self, target: Workspace, rule_id: str) -> dict[str, Any] | None:
        resource = self.resources.get((target.key, rule_id))
        return copy.deepcopy(resource) if resource is not None else None

    def put_rule(
        self,
        target: Workspace,
        rule_id: str,
        body: dict[str, Any],
        etag: str | None,
    ) -> dict[str, Any]:
        current = self.resources.get((target.key, rule_id))
        if current is not None and etag != current.get("etag"):
            raise ConcurrentChangeError("etag mismatch")
        self.etag_counter += 1
        resource = {
            "name": rule_id,
            "etag": f'"etag-{self.etag_counter}"',
            "properties": copy.deepcopy(body["properties"]),
        }
        self.resources[(target.key, rule_id)] = resource
        return copy.deepcopy(resource)

    def delete_rule(self, target: Workspace, rule_id: str, etag: str | None = None) -> None:
        current = self.resources.get((target.key, rule_id))
        if current is not None and etag != current.get("etag"):
            raise ConcurrentChangeError("etag mismatch")
        self.resources.pop((target.key, rule_id), None)


def resource(rule_id: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": rule_id,
        "etag": '"etag-1"',
        "properties": copy.deepcopy(properties or sample_properties()),
    }
