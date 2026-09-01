from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .azure import ArmClient
from .errors import AzureRequestError, ConfigurationError
from .inventory import Inventory, Workspace
from .util import safe_key

SUBSCRIPTIONS_QUERY = """
ResourceContainers
| where type =~ 'microsoft.resources/subscriptions'
| project subscriptionId, subscriptionName=name, tenantId
| order by subscriptionName asc
""".strip()

WORKSPACES_QUERY = """
Resources
| where type =~ 'microsoft.operationalinsights/workspaces'
| project id, name, resourceGroup, subscriptionId, tenantId, location
| order by subscriptionId asc, resourceGroup asc, name asc
""".strip()


@dataclass(frozen=True)
class DiscoveryIssue:
    subscription_id: str
    resource_group: str
    workspace_name: str
    error: str


@dataclass(frozen=True)
class DiscoveryResult:
    workspaces: tuple[Workspace, ...]
    issues: tuple[DiscoveryIssue, ...]
    log_analytics_count: int
    subscription_count: int
    subscription_names: dict[str, str] = field(default_factory=dict)


def _required_string(row: dict[str, Any], field: str, context: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"Azure Resource Graph {context} row has no valid '{field}'")
    return value.strip()


def _unique_key(base: str, subscription_id: str, resource_group: str, used: set[str]) -> str:
    candidate = safe_key(base).casefold()
    if candidate not in used:
        used.add(candidate)
        return candidate
    candidate = f"{candidate}-{subscription_id[:8].casefold()}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    candidate = f"{candidate}-{safe_key(resource_group).casefold()}"
    suffix = 2
    unique = candidate
    while unique in used:
        unique = f"{candidate}-{suffix}"
        suffix += 1
    used.add(unique)
    return unique


def discover_sentinel_workspaces(
    client: ArmClient,
    managing_tenant_id: str | None = None,
    subscriptions: list[str] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> DiscoveryResult:
    subscription_rows = client.resource_graph_query(SUBSCRIPTIONS_QUERY, subscriptions)
    subscription_names: dict[str, str] = {}
    subscription_tenants: dict[str, str] = {}
    for row in subscription_rows:
        subscription_id = _required_string(row, "subscriptionId", "subscription")
        name = row.get("subscriptionName")
        tenant_id = row.get("tenantId")
        subscription_names[subscription_id.casefold()] = (
            name.strip() if isinstance(name, str) and name.strip() else subscription_id
        )
        if isinstance(tenant_id, str) and tenant_id.strip():
            subscription_tenants[subscription_id.casefold()] = tenant_id.strip()

    workspace_rows = client.resource_graph_query(WORKSPACES_QUERY, subscriptions)
    candidates: list[tuple[Workspace, str | None]] = []
    used_keys: set[str] = set()
    for row in workspace_rows:
        subscription_id = _required_string(row, "subscriptionId", "workspace")
        resource_group = _required_string(row, "resourceGroup", "workspace")
        workspace_name = _required_string(row, "name", "workspace")
        subscription_name = subscription_names.get(subscription_id.casefold(), subscription_id)
        tenant_id_value = row.get("tenantId") or subscription_tenants.get(
            subscription_id.casefold()
        )
        tenant_id = tenant_id_value.strip() if isinstance(tenant_id_value, str) else None
        key = _unique_key(workspace_name, subscription_id, resource_group, used_keys)
        tags = ["discovered"]
        if (
            managing_tenant_id
            and tenant_id
            and tenant_id.casefold() != managing_tenant_id.casefold()
        ):
            tags.append("lighthouse")
        candidates.append(
            (
                Workspace(
                    key=key,
                    display_name=f"{subscription_name} / {workspace_name}",
                    subscription_id=subscription_id,
                    resource_group=resource_group,
                    workspace_name=workspace_name,
                    enabled=True,
                    tags=tuple(tags),
                ),
                tenant_id,
            )
        )

    discovered: list[Workspace] = []
    issues: list[DiscoveryIssue] = []
    if progress:
        progress(0, len(candidates), "")
    for index, (workspace, _tenant_id) in enumerate(candidates, start=1):
        try:
            if client.is_sentinel_workspace(workspace):
                discovered.append(workspace)
        except AzureRequestError as exc:
            issues.append(
                DiscoveryIssue(
                    subscription_id=workspace.subscription_id,
                    resource_group=workspace.resource_group,
                    workspace_name=workspace.workspace_name,
                    error=str(exc),
                )
            )
        finally:
            if progress:
                progress(index, len(candidates), workspace.workspace_name)

    return DiscoveryResult(
        workspaces=tuple(discovered),
        issues=tuple(issues),
        log_analytics_count=len(candidates),
        subscription_count=len(subscription_names),
        subscription_names=dict(subscription_names),
    )


def inventory_document(
    result: DiscoveryResult,
    managing_tenant_id: str | None,
    existing: Inventory | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    discovered_by_identity = {_identity(item): item for item in result.workspaces}
    output: list[Workspace] = []

    if existing and not replace:
        for current in existing.workspaces:
            identity = _identity(current)
            discovered = discovered_by_identity.pop(identity, None)
            if discovered is None:
                output.append(current)
                continue
            output.append(
                Workspace(
                    key=current.key,
                    display_name=current.display_name,
                    subscription_id=discovered.subscription_id,
                    resource_group=discovered.resource_group,
                    workspace_name=discovered.workspace_name,
                    enabled=current.enabled,
                    tags=tuple(dict.fromkeys((*current.tags, *discovered.tags))),
                )
            )
    elif existing and replace:
        existing_by_identity = {_identity(item): item for item in existing.workspaces}
        for identity, discovered in list(discovered_by_identity.items()):
            current = existing_by_identity.get(identity)
            if current:
                discovered_by_identity[identity] = Workspace(
                    key=current.key,
                    display_name=current.display_name,
                    subscription_id=discovered.subscription_id,
                    resource_group=discovered.resource_group,
                    workspace_name=discovered.workspace_name,
                    enabled=current.enabled,
                    tags=tuple(dict.fromkeys((*current.tags, *discovered.tags))),
                )

    used_output_keys = {item.key.casefold() for item in output}
    for discovered in discovered_by_identity.values():
        if discovered.key.casefold() in used_output_keys:
            key = _unique_key(
                discovered.key,
                discovered.subscription_id,
                discovered.resource_group,
                used_output_keys,
            )
            discovered = Workspace(
                key=key,
                display_name=discovered.display_name,
                subscription_id=discovered.subscription_id,
                resource_group=discovered.resource_group,
                workspace_name=discovered.workspace_name,
                enabled=discovered.enabled,
                tags=discovered.tags,
            )
        else:
            used_output_keys.add(discovered.key.casefold())
        output.append(discovered)
    output.sort(key=lambda item: item.key.casefold())
    return {
        "managing_tenant_id": managing_tenant_id
        or (existing.managing_tenant_id if existing else None),
        "workspaces": [_workspace_document(item) for item in output],
    }


def _identity(workspace: Workspace) -> tuple[str, str, str]:
    return (
        workspace.subscription_id.casefold(),
        workspace.resource_group.casefold(),
        workspace.workspace_name.casefold(),
    )


def _workspace_document(workspace: Workspace) -> dict[str, Any]:
    return {
        "key": workspace.key,
        "display_name": workspace.display_name,
        "subscription_id": workspace.subscription_id,
        "resource_group": workspace.resource_group,
        "workspace_name": workspace.workspace_name,
        "enabled": workspace.enabled,
        "tags": list(workspace.tags),
    }


def discovery_table(result: DiscoveryResult) -> str:
    lines = [
        "MICROSOFT SENTINEL WORKSPACE DISCOVERY",
        (
            f"Found {len(result.workspaces)} Sentinel workspaces across "
            f"{result.subscription_count} accessible subscriptions "
            f"({result.log_analytics_count} Log Analytics workspaces checked)."
        ),
        "",
    ]
    terminal_width = shutil.get_terminal_size((140, 24)).columns
    if terminal_width < 110:
        for index, workspace in enumerate(result.workspaces, start=1):
            subscription = result.subscription_names.get(
                workspace.subscription_id.casefold(),
                workspace.display_name.rsplit(" / ", 1)[0],
            )
            access = "Lighthouse" if "lighthouse" in workspace.tags else "Direct"
            lines.extend(
                (
                    f"[{index:02}] {workspace.workspace_name}",
                    f"     Key:            {workspace.key}",
                    f"     Subscription:   {subscription}",
                    f"     Resource group: {workspace.resource_group}",
                    f"     Access:         {access}",
                    "",
                )
            )
        return "\n".join(lines).rstrip()

    available = min(terminal_width, 180) - 23
    widths = (
        3,
        max(18, int(available * 0.22)),
        max(24, int(available * 0.30)),
        max(20, int(available * 0.25)),
        max(18, int(available * 0.23)),
        10,
    )
    headers = ("#", "KEY", "SUBSCRIPTION", "WORKSPACE", "RESOURCE GROUP", "ACCESS")
    lines.append(_table_row(headers, widths))
    lines.append("-" * min(sum(widths) + 10, terminal_width))
    for index, workspace in enumerate(result.workspaces, start=1):
        subscription = result.subscription_names.get(
            workspace.subscription_id.casefold(),
            workspace.display_name.rsplit(" / ", 1)[0],
        )
        access = "Lighthouse" if "lighthouse" in workspace.tags else "Direct"
        row = (
            str(index),
            workspace.key,
            subscription,
            workspace.workspace_name,
            workspace.resource_group,
            access,
        )
        lines.append(_table_row(row, widths))
    return "\n".join(lines)


def _table_row(values: tuple[str, ...], widths: tuple[int, ...]) -> str:
    cells = [_truncate(value, width).ljust(width) for value, width in zip(values, widths)]
    return "  ".join(cells).rstrip()


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(1, width - 3)] + "..."
