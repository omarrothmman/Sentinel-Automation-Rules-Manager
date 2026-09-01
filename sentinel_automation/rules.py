from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .azure import ArmClient
from .errors import AmbiguousRuleError, ConfigurationError, RuleNotFoundError
from .inventory import Workspace
from .util import normalize_properties, read_json


@dataclass(frozen=True)
class RuleSelector:
    rule_id: str | None = None
    display_name: str | None = None

    def validate(self) -> None:
        if bool(self.rule_id) == bool(self.display_name):
            raise ConfigurationError("Specify exactly one of --rule-id or --display-name")


def discover_rule(
    client: ArmClient, workspace: Workspace, selector: RuleSelector
) -> dict[str, Any]:
    selector.validate()
    if selector.rule_id:
        rule = client.get_rule(workspace, selector.rule_id)
        if rule is None:
            raise RuleNotFoundError(
                f"Rule ID '{selector.rule_id}' was not found in workspace '{workspace.key}'"
            )
        return rule

    expected = selector.display_name.casefold() if selector.display_name else ""
    matches = [
        rule
        for rule in client.list_rules(workspace)
        if str(rule.get("properties", {}).get("displayName", "")).casefold() == expected
    ]
    if not matches:
        raise RuleNotFoundError(
            f"Rule display name '{selector.display_name}' was not found in workspace '{workspace.key}'"
        )
    if len(matches) > 1:
        ids = ", ".join(str(rule.get("name", "unknown")) for rule in matches)
        raise AmbiguousRuleError(
            f"Multiple rules named '{selector.display_name}' exist in workspace '{workspace.key}': {ids}"
        )
    return matches[0]


def _walk_conditions(
    value: Any, path: tuple[int | str, ...] = ()
) -> Iterator[tuple[tuple[int | str, ...], dict[str, Any]]]:
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_conditions(item, path + (index,))
        return
    if not isinstance(value, dict):
        return
    if isinstance(value.get("conditionType"), str) and isinstance(
        value.get("conditionProperties"), dict
    ):
        yield path, value
    for key, child in value.items():
        if key in ("innerConditions", "itemConditions", "conditions"):
            yield from _walk_conditions(child, path + (key,))
        elif key == "conditionProperties" and isinstance(child, dict):
            for nested_key in ("innerConditions", "itemConditions", "conditions"):
                if nested_key in child:
                    yield from _walk_conditions(child[nested_key], path + (key, nested_key))


def condition_path_text(path: tuple[int | str, ...]) -> str:
    text = ""
    for item in path:
        text += f"[{item}]" if isinstance(item, int) else ("." if text else "") + item
    return text or "<root>"


def _property_conditions(
    properties: dict[str, Any], property_name: str, operator: str | None = None
) -> list[tuple[tuple[int | str, ...], dict[str, Any]]]:
    triggering = properties.get("triggeringLogic")
    if not isinstance(triggering, dict):
        raise ConfigurationError("Rule is missing triggeringLogic")
    conditions = triggering.get("conditions")
    if not isinstance(conditions, list):
        raise ConfigurationError("Rule triggeringLogic.conditions must be an array")
    matches = []
    for path, condition in _walk_conditions(conditions, ("triggeringLogic", "conditions")):
        details = condition["conditionProperties"]
        if str(details.get("propertyName", "")).casefold() != property_name.casefold():
            continue
        if operator and str(details.get("operator", "")).casefold() != operator.casefold():
            continue
        matches.append((path, condition))
    return matches


def _select_condition(
    matches: list[tuple[tuple[int | str, ...], dict[str, Any]]],
    property_name: str,
    condition_index: int | None,
) -> dict[str, Any]:
    if not matches:
        raise ConfigurationError(f"No condition for property '{property_name}' was found")
    if condition_index is not None:
        if condition_index < 1 or condition_index > len(matches):
            raise ConfigurationError(
                f"--condition-index must be between 1 and {len(matches)} for property '{property_name}'"
            )
        return matches[condition_index - 1][1]
    if len(matches) > 1:
        paths = ", ".join(condition_path_text(path) for path, _ in matches)
        raise ConfigurationError(
            f"Found {len(matches)} conditions for '{property_name}' ({paths}); specify --condition-index"
        )
    return matches[0][1]


def add_property_value(
    source: dict[str, Any],
    property_name: str,
    value: str,
    condition_index: int | None = None,
) -> tuple[dict[str, Any], str]:
    properties = copy.deepcopy(source)
    condition = _select_condition(
        _property_conditions(properties, property_name), property_name, condition_index
    )
    details = condition["conditionProperties"]
    values = details.get("propertyValues")
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ConfigurationError(
            f"Condition '{property_name}' does not have a string propertyValues array"
        )
    if any(item.casefold() == value.casefold() for item in values):
        return properties, f"'{value}' already exists in {property_name}"
    values.append(value)
    return properties, f"add '{value}' to {property_name}"


def remove_property_value(
    source: dict[str, Any],
    property_name: str,
    value: str,
    condition_index: int | None = None,
) -> tuple[dict[str, Any], str]:
    properties = copy.deepcopy(source)
    condition = _select_condition(
        _property_conditions(properties, property_name), property_name, condition_index
    )
    details = condition["conditionProperties"]
    values = details.get("propertyValues")
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ConfigurationError(
            f"Condition '{property_name}' does not have a string propertyValues array"
        )
    matching = [index for index, item in enumerate(values) if item.casefold() == value.casefold()]
    if not matching:
        return properties, f"'{value}' is not present in {property_name}"
    values.pop(matching[0])
    if not values:
        raise ConfigurationError(
            f"Removing '{value}' would leave the {property_name} condition empty; remove the condition instead"
        )
    return properties, f"remove '{value}' from {property_name}"


def add_condition(
    source: dict[str, Any], property_name: str, operator: str, values: list[str]
) -> tuple[dict[str, Any], str]:
    properties = copy.deepcopy(source)
    triggering = properties.get("triggeringLogic")
    if not isinstance(triggering, dict) or not isinstance(triggering.get("conditions"), list):
        raise ConfigurationError("Rule triggeringLogic.conditions must be an array")
    if sum(1 for _ in _walk_conditions(triggering["conditions"])) >= 50:
        raise ConfigurationError("Automation rule already has the maximum of 50 conditions")
    candidate = {
        "conditionType": "Property",
        "conditionProperties": {
            "propertyName": property_name,
            "operator": operator,
            "propertyValues": list(values),
        },
    }
    for _, existing in _walk_conditions(triggering["conditions"]):
        if existing == candidate:
            return properties, f"condition {property_name} {operator} already exists"
    triggering["conditions"].append(candidate)
    return properties, f"add condition {property_name} {operator} {values!r}"


def remove_condition(
    source: dict[str, Any], property_name: str, operator: str | None, condition_index: int | None
) -> tuple[dict[str, Any], str]:
    properties = copy.deepcopy(source)
    matches = _property_conditions(properties, property_name, operator)
    selected = _select_condition(matches, property_name, condition_index)

    def remove_from_lists(value: Any) -> bool:
        if isinstance(value, list):
            for index, item in enumerate(value):
                if item is selected:
                    value.pop(index)
                    return True
                if remove_from_lists(item):
                    return True
        elif isinstance(value, dict):
            for child in value.values():
                if remove_from_lists(child):
                    return True
        return False

    if not remove_from_lists(properties["triggeringLogic"]["conditions"]):
        raise ConfigurationError("Internal error: matched condition could not be removed")
    return properties, f"remove condition for {property_name}"


def set_enabled(source: dict[str, Any], enabled: bool) -> tuple[dict[str, Any], str]:
    properties = copy.deepcopy(source)
    triggering = properties.get("triggeringLogic")
    if not isinstance(triggering, dict):
        raise ConfigurationError("Rule is missing triggeringLogic")
    triggering["isEnabled"] = enabled
    return properties, f"set rule {'enabled' if enabled else 'disabled'}"


def validate_properties(properties: dict[str, Any]) -> None:
    for field in ("displayName", "order", "triggeringLogic", "actions"):
        if field not in properties:
            raise ConfigurationError(
                f"Automation rule properties are missing required field '{field}'"
            )
    if not isinstance(properties["displayName"], str) or not properties["displayName"].strip():
        raise ConfigurationError("Automation rule displayName must be a non-empty string")
    if not isinstance(properties["order"], int) or not 1 <= properties["order"] <= 1000:
        raise ConfigurationError("Automation rule order must be an integer from 1 to 1000")
    if not isinstance(properties["triggeringLogic"], dict):
        raise ConfigurationError("Automation rule triggeringLogic must be an object")
    if not isinstance(properties["actions"], list):
        raise ConfigurationError("Automation rule actions must be an array")


GUID_PATTERN = re.compile(r"automationRules[/\\]([0-9a-fA-F-]{36})", re.IGNORECASE)


def load_deployment_file(
    path: Path, rule_id_override: str | None = None
) -> tuple[str, dict[str, Any]]:
    data = read_json(path)
    return load_deployment_document(data, rule_id_override)


def load_deployment_document(
    data: Any, rule_id_override: str | None = None
) -> tuple[str, dict[str, Any]]:
    if not isinstance(data, dict):
        raise ConfigurationError("Deployment file root must be an object")

    properties: dict[str, Any] | None = None
    discovered_rule_id: str | None = None
    if isinstance(data.get("properties"), dict):
        properties = data["properties"]
        discovered_rule_id = data.get("rule_id") or data.get("name")
    elif isinstance(data.get("resources"), list):
        resources = [
            resource
            for resource in data["resources"]
            if isinstance(resource, dict)
            and "automationrules" in str(resource.get("type", "")).casefold()
        ]
        if len(resources) != 1:
            raise ConfigurationError(
                f"ARM template must contain exactly one automation rule resource; found {len(resources)}"
            )
        resource = resources[0]
        if isinstance(resource.get("properties"), dict):
            properties = resource["properties"]
        match = GUID_PATTERN.search(str(resource.get("id", "")))
        if match:
            discovered_rule_id = match.group(1)
        if not discovered_rule_id:
            guids = re.findall(r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}", str(resource.get("name", "")))
            if guids:
                discovered_rule_id = guids[-1]
    else:
        raise ConfigurationError(
            "Deployment file must be a catalog object with 'properties' or an exported ARM template"
        )

    if properties is None:
        raise ConfigurationError("Deployment file does not contain automation rule properties")
    properties = resource_properties({"properties": properties})
    rule_id = rule_id_override or discovered_rule_id
    if not rule_id:
        raise ConfigurationError("Could not determine rule ID; provide --rule-id")
    try:
        rule_id = str(uuid.UUID(str(rule_id)))
    except ValueError as exc:
        raise ConfigurationError(f"Automation rule ID is not a valid UUID: {rule_id}") from exc
    return rule_id, copy.deepcopy(properties)


Mutation = Callable[[dict[str, Any]], tuple[dict[str, Any], str]]


def resource_properties(resource: dict[str, Any]) -> dict[str, Any]:
    properties = copy.deepcopy(normalize_properties(resource))
    # These fields are populated by Azure and either cannot be sent back or change
    # after every PUT, which would otherwise make verification report false drift.
    read_only = {
        "createdby",
        "createdtimeutc",
        "lastmodifiedby",
        "lastmodifiedtimeutc",
    }
    for key in list(properties):
        if key.casefold() in read_only:
            properties.pop(key)
    properties = _without_null_object_fields(properties)
    validate_properties(properties)
    return properties


def _without_null_object_fields(value: Any) -> Any:
    """Normalize optional ARM fields that Azure may omit when their value is null."""
    if isinstance(value, dict):
        return {
            key: _without_null_object_fields(child)
            for key, child in value.items()
            if child is not None
        }
    if isinstance(value, list):
        return [_without_null_object_fields(child) for child in value]
    return value
