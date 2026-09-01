from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .presentation import Column, render_table
from .rules import load_deployment_document, load_deployment_file
from .util import atomic_write_json, read_json, safe_key


@dataclass(frozen=True)
class CatalogRule:
    logical_name: str
    rule_id: str
    properties: dict[str, Any]
    path: Path

    @property
    def display_name(self) -> str:
        return str(self.properties["displayName"])

    def document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "logical_name": self.logical_name,
            "rule_id": self.rule_id,
            "properties": copy.deepcopy(self.properties),
        }


def _logical_name(value: str) -> str:
    return re.sub(r"-{2,}", "-", safe_key(value)).casefold()


def load_catalog(directory: Path) -> list[CatalogRule]:
    if not directory.exists():
        raise ConfigurationError(
            f"Rule catalog does not exist: {directory}. Add a rule with 'sentinel-auto catalog add'."
        )
    if not directory.is_dir():
        raise ConfigurationError(f"Rule catalog path is not a directory: {directory}")

    paths = sorted(directory.glob("*.json"), key=lambda path: path.name.casefold())
    rules: list[CatalogRule] = []
    logical_names: set[str] = set()
    rule_ids: dict[str, str] = {}
    display_names: dict[str, str] = {}
    for path in paths:
        document = read_json(path)
        if not isinstance(document, dict):
            raise ConfigurationError(f"Catalog rule root must be an object: {path}")
        logical_value = document.get("logical_name", path.stem)
        if not isinstance(logical_value, str) or not logical_value.strip():
            raise ConfigurationError(f"Catalog rule has no valid logical_name: {path}")
        logical_name = _logical_name(logical_value.strip())
        if _logical_name(path.stem) != logical_name:
            raise ConfigurationError(
                f"Catalog filename '{path.name}' must match logical_name '{logical_name}.json'"
            )
        if logical_name in logical_names:
            raise ConfigurationError(f"Duplicate catalog logical name: {logical_name}")

        rule_id, properties = load_deployment_file(path)
        display_name = str(properties["displayName"])
        duplicate_id = rule_ids.get(rule_id.casefold())
        if duplicate_id:
            raise ConfigurationError(
                f"Catalog rules '{duplicate_id}' and '{logical_name}' use the same rule ID {rule_id}"
            )
        duplicate_name = display_names.get(display_name.casefold())
        if duplicate_name:
            raise ConfigurationError(
                f"Catalog rules '{duplicate_name}' and '{logical_name}' use the same display name "
                f"'{display_name}'"
            )

        logical_names.add(logical_name)
        rule_ids[rule_id.casefold()] = logical_name
        display_names[display_name.casefold()] = logical_name
        rules.append(CatalogRule(logical_name, rule_id, properties, path.resolve()))
    return rules


def select_catalog_rules(rules: list[CatalogRule], expression: str) -> list[CatalogRule]:
    if not rules:
        raise ConfigurationError(
            "Rule catalog is empty. Add a rule with 'sentinel-auto catalog add' first."
        )
    if expression.strip().casefold() == "all":
        return list(rules)
    requested = [_logical_name(value.strip()) for value in expression.split(",") if value.strip()]
    if not requested:
        raise ConfigurationError("Catalog rule selection cannot be empty")
    by_name = {rule.logical_name.casefold(): rule for rule in rules}
    unknown = [name for name in requested if name not in by_name]
    if unknown:
        raise ConfigurationError(f"Unknown catalog rule(s): {', '.join(unknown)}")
    selected: list[CatalogRule] = []
    seen: set[str] = set()
    for name in requested:
        if name not in seen:
            selected.append(by_name[name])
            seen.add(name)
    return selected


def add_catalog_rule(
    directory: Path,
    source: Path,
    name: str,
    rule_id_override: str | None = None,
    force: bool = False,
) -> CatalogRule:
    logical_name = _logical_name(name.strip())
    rule_id, properties = load_deployment_file(source, rule_id_override)
    path = directory / f"{logical_name}.json"
    if path.exists() and not force:
        raise ConfigurationError(
            f"Catalog rule already exists: {path}. Use --force only to replace it intentionally."
        )
    previous = read_json(path) if path.exists() else None
    rule = CatalogRule(logical_name, rule_id, properties, path.resolve())
    atomic_write_json(path, rule.document())
    try:
        # Validate the whole catalog and restore the prior state if the import conflicts.
        load_catalog(directory)
    except Exception:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_json(path, previous)
        raise
    return rule


def import_catalog_bundle(directory: Path, source: Path, force: bool = False) -> list[CatalogRule]:
    document = read_json(source)
    if not isinstance(document, dict) or not isinstance(document.get("resources"), list):
        raise ConfigurationError("Automation-rule bundle must be an ARM template with resources")
    resources = [
        resource
        for resource in document["resources"]
        if isinstance(resource, dict)
        and "automationrules" in str(resource.get("type", "")).casefold()
    ]
    if not resources:
        raise ConfigurationError("ARM template contains no automation rule resources")

    imported: list[CatalogRule] = []
    names: set[str] = set()
    ids: set[str] = set()
    display_names: set[str] = set()
    for resource in resources:
        rule_id, properties = load_deployment_document({"resources": [resource]})
        display_name = str(properties["displayName"])
        logical_name = _logical_name(display_name)
        if logical_name in names:
            raise ConfigurationError(f"Bundle produces duplicate logical name: {logical_name}")
        if rule_id.casefold() in ids:
            raise ConfigurationError(f"Bundle contains duplicate rule ID: {rule_id}")
        if display_name.casefold() in display_names:
            raise ConfigurationError(f"Bundle contains duplicate display name: {display_name}")
        names.add(logical_name)
        ids.add(rule_id.casefold())
        display_names.add(display_name.casefold())
        path = directory / f"{logical_name}.json"
        if path.exists() and not force:
            raise ConfigurationError(
                f"Catalog rule already exists: {path}. Use --force only to replace the bundle intentionally."
            )
        imported.append(CatalogRule(logical_name, rule_id, properties, path.resolve()))

    previous: dict[Path, Any | None] = {
        rule.path: read_json(rule.path) if rule.path.exists() else None for rule in imported
    }
    try:
        for rule in imported:
            atomic_write_json(rule.path, rule.document())
        load_catalog(directory)
    except Exception:
        for path, prior_document in previous.items():
            if prior_document is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_json(path, prior_document)
        raise
    return imported


def catalog_table(rules: list[CatalogRule], directory: Path, validated: bool = False) -> str:
    rows = []
    dependency_rules = 0
    for rule in rules:
        enabled = rule.properties["triggeringLogic"].get("isEnabled")
        status = "Enabled" if enabled is True else "Disabled" if enabled is False else "Unknown"
        dependencies = _dependency_text(rule)
        dependency_rules += bool(dependencies)
        rows.append(
            (rule.logical_name, rule.display_name, rule.rule_id, status, dependencies or "None")
        )
    state = " | Validation: SUCCESS" if validated else ""
    output = render_table(
        "AUTOMATION RULE CATALOG",
        f"{len(rules)} base rules in {directory.resolve()}{state} | External references: {dependency_rules}",
        (
            Column("LOGICAL NAME", 20, 34),
            Column("DISPLAY NAME", 24, 54),
            Column("RULE ID", 36, 36),
            Column("STATUS", 8, 8),
            Column("DEPENDENCIES", 16, 24),
        ),
        rows,
    )
    if dependency_rules:
        output += (
            "\n\nReview preserved playbook resource IDs and owner references before deployment "
            "to another environment."
        )
    return output


def _dependency_text(rule: CatalogRule) -> str:
    playbooks = 0
    owners = 0
    for action in rule.properties.get("actions", []):
        if not isinstance(action, dict):
            continue
        configuration = action.get("actionConfiguration")
        if not isinstance(configuration, dict):
            continue
        if str(action.get("actionType", "")).casefold() == "runplaybook" and configuration.get(
            "logicAppResourceId"
        ):
            playbooks += 1
        if configuration.get("owner"):
            owners += 1
    parts = []
    if playbooks:
        parts.append(f"{playbooks} playbook" + ("s" if playbooks != 1 else ""))
    if owners:
        parts.append("owner")
    return ", ".join(parts)
