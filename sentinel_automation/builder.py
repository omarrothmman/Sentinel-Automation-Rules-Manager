"""Validation for the guided rule builder's supported Azure rule fields."""

from __future__ import annotations

import re
import uuid
from typing import Any

from .errors import ConfigurationError
from .rules import resource_properties

PROPERTIES = {
    "IncidentTitle",
    "IncidentDescription",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentRelatedAnalyticRuleIds",
    "IncidentProviderName",
    "IncidentTactics",
    "AlertAnalyticRuleIds",
}
OPERATORS = {
    "Equals",
    "NotEquals",
    "Contains",
    "NotContains",
    "StartsWith",
    "NotStartsWith",
    "EndsWith",
    "NotEndsWith",
}


def validate_builder_rule(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError("Rule definition must be an object")
    properties = resource_properties({"properties": value})
    if len(properties["displayName"]) > 500:
        raise ConfigurationError("Rule name must be at most 500 characters")
    logic = properties["triggeringLogic"]
    trigger = (logic.get("triggersOn"), logic.get("triggersWhen"))
    if trigger not in {("Incidents", "Created"), ("Incidents", "Updated"), ("Alerts", "Created")}:
        raise ConfigurationError("Choose a supported incident or alert trigger")
    if not isinstance(logic.get("isEnabled"), bool):
        raise ConfigurationError("Enabled must be true or false")
    conditions = logic.get("conditions")
    if not isinstance(conditions, list) or len(conditions) > 50:
        raise ConfigurationError("Use up to 50 conditions")
    for condition in conditions:
        if not isinstance(condition, dict) or condition.get("conditionType") != "Property":
            raise ConfigurationError("The builder supports property conditions")
        config = condition.get("conditionProperties", {})
        if not isinstance(config, dict) or config.get("propertyName") not in PROPERTIES:
            raise ConfigurationError("Choose a supported condition property")
        if trigger[0] == "Alerts" and config["propertyName"] != "AlertAnalyticRuleIds":
            raise ConfigurationError("Alert triggers support analytics-rule conditions only")
        if trigger[0] == "Incidents" and config["propertyName"] == "AlertAnalyticRuleIds":
            raise ConfigurationError("Use incident analytics-rule IDs for an incident trigger")
        if config.get("operator") not in OPERATORS:
            raise ConfigurationError("Choose a supported condition operator")
        values = config.get("propertyValues")
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(v, str) or not v.strip() for v in values)
        ):
            raise ConfigurationError("Each condition needs non-empty values")
    actions = properties["actions"]
    if not actions or len(actions) > 20:
        raise ConfigurationError("Add between 1 and 20 actions")
    for index, action in enumerate(actions, 1):
        if not isinstance(action, dict) or action.get("order") != index:
            raise ConfigurationError("Actions must have consecutive execution order")
        config = action.get("actionConfiguration")
        if not isinstance(config, dict):
            raise ConfigurationError("Each action needs configuration")
        kind = action.get("actionType")
        if kind == "RunPlaybook":
            if not re.fullmatch(
                r"/subscriptions/[0-9a-fA-F-]{36}/resourceGroups/[^/]+/providers/Microsoft\.Logic/workflows/[^/]+",
                str(config.get("logicAppResourceId", "")),
                re.IGNORECASE,
            ):
                raise ConfigurationError("Enter a complete Logic App workflow resource ID")
            try:
                uuid.UUID(str(config.get("tenantId", "")))
            except ValueError as exc:
                raise ConfigurationError("Enter the playbook tenant's UUID") from exc
        elif trigger[0] == "Alerts":
            raise ConfigurationError("Alert rules support Run playbook actions only")
        elif kind == "AddIncidentTask":
            if not isinstance(config.get("title"), str) or not config["title"].strip():
                raise ConfigurationError("Enter a task title")
        elif kind == "ModifyProperties":
            if not config or set(config) - {"severity", "status", "classification", "labels"}:
                raise ConfigurationError("Unsupported incident action settings")
            if "severity" in config and config["severity"] not in {
                "High",
                "Medium",
                "Low",
                "Informational",
            }:
                raise ConfigurationError("Choose a valid severity")
            if "status" in config and config["status"] not in {"New", "Active", "Closed"}:
                raise ConfigurationError("Choose a valid status")
            if config.get("status") == "Closed" and config.get("classification") not in {
                "Undetermined",
                "TruePositive",
                "BenignPositive",
                "FalsePositive",
            }:
                raise ConfigurationError("Choose a closure classification")
            if "labels" in config and (
                not isinstance(config["labels"], list)
                or not config["labels"]
                or any(
                    not isinstance(label, dict)
                    or not isinstance(label.get("labelName"), str)
                    or not label["labelName"].strip()
                    for label in config["labels"]
                )
            ):
                raise ConfigurationError("Enter at least one tag")
        else:
            raise ConfigurationError("Choose a supported action")
    return properties
