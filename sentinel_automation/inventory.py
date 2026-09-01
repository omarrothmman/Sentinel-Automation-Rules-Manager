from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .presentation import Column, render_table
from .util import read_json


@dataclass(frozen=True)
class Workspace:
    key: str
    display_name: str
    subscription_id: str
    resource_group: str
    workspace_name: str
    enabled: bool
    tags: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "subscription_id": self.subscription_id,
            "resource_group": self.resource_group,
            "workspace_name": self.workspace_name,
        }


@dataclass(frozen=True)
class Inventory:
    managing_tenant_id: str | None
    workspaces: tuple[Workspace, ...]

    @classmethod
    def load(cls, path: Path) -> Inventory:
        data = read_json(path)
        if not isinstance(data, dict):
            raise ConfigurationError("Inventory root must be a JSON object")

        # Backward compatibility with the original numeric-key DataBase.json.
        if "workspaces" not in data and data and all(isinstance(v, dict) for v in data.values()):
            rows = []
            for key, value in data.items():
                rows.append(
                    {
                        "key": str(key),
                        "display_name": value.get("tenant_name", str(key)),
                        "subscription_id": value.get("subscription_id"),
                        "resource_group": value.get("resource_group_name"),
                        "workspace_name": value.get("workspace_name"),
                        "enabled": True,
                        "tags": [],
                    }
                )
            managing_tenant_id = None
        else:
            rows = data.get("workspaces")
            managing_tenant_id = data.get("managing_tenant_id")

        if not isinstance(rows, list) or not rows:
            raise ConfigurationError("Inventory must contain a non-empty 'workspaces' array")
        if managing_tenant_id is not None and not isinstance(managing_tenant_id, str):
            raise ConfigurationError("'managing_tenant_id' must be a string")

        workspaces: list[Workspace] = []
        seen: set[str] = set()
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ConfigurationError(f"Workspace entry {index} must be an object")
            required = ("key", "subscription_id", "resource_group", "workspace_name")
            missing = [
                field
                for field in required
                if not isinstance(row.get(field), str) or not row[field].strip()
            ]
            if missing:
                raise ConfigurationError(
                    f"Workspace entry {index} has missing/invalid fields: {', '.join(missing)}"
                )
            key = row["key"].strip()
            if key.casefold() in seen:
                raise ConfigurationError(f"Duplicate workspace key: {key}")
            seen.add(key.casefold())
            tags = row.get("tags", [])
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                raise ConfigurationError(f"Workspace '{key}' tags must be an array of strings")
            workspaces.append(
                Workspace(
                    key=key,
                    display_name=str(row.get("display_name") or key),
                    subscription_id=row["subscription_id"].strip(),
                    resource_group=row["resource_group"].strip(),
                    workspace_name=row["workspace_name"].strip(),
                    enabled=bool(row.get("enabled", True)),
                    tags=tuple(tags),
                )
            )
        return cls(managing_tenant_id=managing_tenant_id, workspaces=tuple(workspaces))

    def select(self, expression: str) -> list[Workspace]:
        enabled = [workspace for workspace in self.workspaces if workspace.enabled]
        if expression.strip().casefold() == "all":
            return enabled

        selectors = [item.strip() for item in expression.split(",") if item.strip()]
        if not selectors:
            raise ConfigurationError("Target selection cannot be empty")
        selected: list[Workspace] = []
        selected_keys: set[str] = set()
        for selector in selectors:
            matches = [
                workspace
                for workspace in enabled
                if workspace.key.casefold() == selector.casefold()
                or selector.casefold() in (tag.casefold() for tag in workspace.tags)
            ]
            if not matches:
                raise ConfigurationError(f"Unknown enabled workspace key or tag: {selector}")
            for workspace in matches:
                if workspace.key.casefold() not in selected_keys:
                    selected.append(workspace)
                    selected_keys.add(workspace.key.casefold())
        return selected


def display_inventory(workspaces: Iterable[Workspace]) -> str:
    items = list(workspaces)
    enabled = sum(workspace.enabled for workspace in items)
    lighthouse = sum("lighthouse" in workspace.tags for workspace in items)
    rows = [
        (
            workspace.key,
            workspace.display_name,
            workspace.workspace_name,
            workspace.resource_group,
            "Enabled" if workspace.enabled else "Disabled",
        )
        for workspace in items
    ]
    return render_table(
        "MICROSOFT SENTINEL WORKSPACE INVENTORY",
        f"{len(items)} workspaces | Enabled: {enabled} | Disabled: {len(items) - enabled} | Lighthouse: {lighthouse}",
        (
            Column("KEY", 18, 34),
            Column("DISPLAY NAME", 22, 46),
            Column("WORKSPACE", 20, 36),
            Column("RESOURCE GROUP", 18, 34),
            Column("STATUS", 8, 8),
        ),
        rows,
    )
