"""Shared watchlist row planning, CSV handling, apply and recovery."""

from __future__ import annotations

import copy
import csv
import io
import json
import uuid
from pathlib import Path
from typing import Any

from .errors import ConcurrentChangeError, ConfigurationError, PlanIntegrityError
from .inventory import Workspace
from .util import atomic_write_json, content_hash, read_json, safe_key, timestamp_id, utc_now


def _workspace(value: dict) -> Workspace:
    from .plans import _workspace_from_plan

    return _workspace_from_plan(value)


def required(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{label} must be a non-empty string")
    return value


def row_values(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ConfigurationError("Row values must be a non-empty object of column/value strings")
    for key, cell in value.items():
        required(key, "Column name")
        if not isinstance(cell, str):
            raise ConfigurationError(f"Value for '{key}' must be a string")
    return copy.deepcopy(value)


def item_properties(resource: dict | None) -> dict | None:
    if resource is None or resource.get("properties", {}).get("isDeleted"):
        return None
    props = resource.get("properties", {})
    result = {"itemsKeyValue": row_values(props.get("itemsKeyValue"))}
    if "entityMapping" in props:
        result["entityMapping"] = copy.deepcopy(props["entityMapping"])
    return result


def item_id(resource: dict) -> str:
    value = resource.get("properties", {}).get("watchlistItemId") or resource.get("name")
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigurationError("Watchlist row has no valid item UUID") from exc


def parse_csv(text: str) -> list[dict[str, str]]:
    if not isinstance(text, str):
        raise ConfigurationError("CSV content must be text")
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
        columns = reader.fieldnames
        if not columns or any(not c.strip() for c in columns) or len(set(columns)) != len(columns):
            raise ConfigurationError("CSV needs unique, non-empty column headers")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ConfigurationError(f"CSV row {reader.line_num} has the wrong column count")
            rows.append(row_values(row))
        if not rows:
            raise ConfigurationError("CSV must contain at least one data row")
        return rows
    except csv.Error as exc:
        raise ConfigurationError(f"Invalid CSV: {exc}") from exc


def export_csv(resources: list[dict], search_key: str = "") -> str:
    rows = [item_properties(resource)["itemsKeyValue"] for resource in resources]
    columns = list(
        dict.fromkeys(([search_key] if search_key else []) + [key for row in rows for key in row])
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def snapshot(resources: list[dict]) -> str:
    return content_hash({item_id(r): item_properties(r) for r in resources})


def _index(resources: list[dict], column: str) -> dict[str, dict]:
    indexed = {}
    for resource in resources:
        value = item_properties(resource)["itemsKeyValue"].get(column)
        required(value, f"Match column '{column}' in every row")
        if value in indexed:
            raise ConfigurationError(f"Duplicate match value '{value}' in column '{column}'")
        indexed[value] = resource
    return indexed


def build_plan(client: Any, workspaces: list[Workspace], payload: dict) -> dict:
    from .plans import json_diff

    alias = required(payload.get("alias"), "Watchlist alias")
    action = payload.get("action")
    if action not in ("add", "update", "delete", "import"):
        raise ConfigurationError("Choose add, update, delete or import")
    column = payload.get("key_column")
    selected_id = payload.get("item_id")
    if selected_id:
        try:
            selected_id = str(uuid.UUID(selected_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ConfigurationError("Item ID must be a UUID") from exc
        if len(workspaces) != 1 or action not in ("update", "delete"):
            raise ConfigurationError("Item ID selection requires one workspace and update/delete")
    else:
        column = required(column, "Match column")
    values = row_values(payload.get("values")) if action in ("add", "update") else None
    rows = parse_csv(payload.get("csv")) if action == "import" else []
    if action == "import":
        incoming = {}
        for row in rows:
            key = required(row.get(column), "CSV match value")
            if key in incoming:
                raise ConfigurationError(f"Duplicate CSV match value '{key}'")
            incoming[key] = row
    mode = payload.get("mode", "merge")
    if mode not in ("merge", "replace"):
        raise ConfigurationError("Import mode must be merge or replace")
    targets = []
    for workspace in workspaces:
        watchlist = client.get_watchlist(workspace, alias)
        if not watchlist:
            raise ConfigurationError(f"Watchlist '{alias}' does not exist in '{workspace.key}'")
        search_key = required(watchlist.get("properties", {}).get("itemsSearchKey"), "Search key")
        resources = client.list_watchlist_items(workspace, alias)
        fingerprint = snapshot(resources)
        indexed = _index(resources, column) if not selected_id else {}
        changes = []
        if action == "import":
            for key, row in incoming.items():
                before = indexed.get(key)
                props = item_properties(before) or {}
                changes.append((before, {**props, "itemsKeyValue": row}))
            if mode == "replace":
                changes.extend((r, None) for key, r in indexed.items() if key not in incoming)
        elif action == "add":
            key = required(values.get(column), "New row match value")
            if key in indexed:
                raise ConfigurationError(f"'{key}' already exists in '{workspace.key}'")
            changes.append((None, {"itemsKeyValue": values}))
        else:
            if selected_id:
                matches = [r for r in resources if item_id(r) == selected_id]
                before = matches[0] if matches else None
            else:
                key = required(payload.get("key_value"), "Match value")
                before = indexed.get(key)
            if before is None:
                raise ConfigurationError(f"Matching row was not found in '{workspace.key}'")
            props = item_properties(before)
            after = (
                None
                if action == "delete"
                else {**props, "itemsKeyValue": {**props["itemsKeyValue"], **values}}
            )
            if after and not selected_id:
                new_key = required(after["itemsKeyValue"].get(column), "Match value")
                if new_key in indexed and item_id(indexed[new_key]) != item_id(before):
                    raise ConfigurationError(f"Match value '{new_key}' already exists")
            changes.append((before, after))
        for before, after in changes:
            if after:
                required(after["itemsKeyValue"].get(search_key), f"Search key '{search_key}'")
            previous = item_properties(before)
            status = (
                "no_change"
                if previous == after
                else "delete"
                if after is None
                else "create"
                if previous is None
                else "modify"
            )
            target_id = item_id(before) if before else str(uuid.uuid4())
            targets.append(
                {
                    "workspace": workspace.to_dict(),
                    "alias": alias,
                    "item_id": target_id,
                    "display_name": f"{alias} / {target_id}",
                    "status": status,
                    "before": previous,
                    "after": after,
                    "snapshot": fingerprint,
                    "search_key": search_key,
                    "summary": f"{status} watchlist row",
                    "changes": json_diff(previous, after),
                }
            )
    if not targets:
        raise ConfigurationError("No watchlist rows selected")
    return {
        "plan_version": 1,
        "resource_type": "watchlist-items",
        "operation": f"watchlists-{action}",
        "api_version": client.api_version,
        "created_at": utc_now(),
        "targets": targets,
    }


def validate_plan(plan: dict) -> dict:
    unsigned = copy.deepcopy(plan)
    integrity = unsigned.pop("integrity", None)
    if plan.get("plan_version") != 1 or integrity != content_hash(unsigned):
        raise PlanIntegrityError("Watchlist plan integrity check failed")
    required(plan.get("api_version"), "API version")
    if not isinstance(plan.get("targets"), list) or not plan["targets"]:
        raise PlanIntegrityError("Watchlist plan contains no targets")
    seen = set()
    for target in plan["targets"]:
        _workspace(target["workspace"])
        required(target.get("alias"), "Watchlist alias")
        uuid.UUID(target["item_id"])
        required(target.get("snapshot"), "Watchlist snapshot")
        required(target.get("search_key"), "Search key")
        identity = (target["workspace"]["key"], target["alias"], target["item_id"])
        if identity in seen:
            raise PlanIntegrityError("Duplicate watchlist plan target")
        seen.add(identity)
        before, after = target["before"], target["after"]
        for props in (before, after):
            if props is not None:
                row_values(props.get("itemsKeyValue"))
        expected = (
            "no_change"
            if before == after
            else "delete"
            if after is None
            else "create"
            if before is None
            else "modify"
        )
        if target["status"] != expected or (before is None and after is None):
            raise PlanIntegrityError("Invalid watchlist change status")
    return plan


def summary(plan: dict) -> str:
    lines = [f"WATCHLIST PLAN: {plan['operation']}"]
    for target in plan["targets"]:
        lines.append(
            f"{target['workspace']['key']} | {target['alias']} | "
            f"{target['item_id']} | {target['status']}"
        )
        lines.append(
            json.dumps(
                {"before": target["before"], "after": target["after"]}, ensure_ascii=False, indent=2
            )
        )
    return "\n".join(lines)


def _write(
    client: Any, workspace: Workspace, target: dict, desired: dict | None, live: dict | None
) -> None:
    alias, row_id = target["alias"], target["item_id"]
    etag = live.get("etag") if live else None
    if desired is None:
        client.delete_watchlist_item(workspace, alias, row_id, etag)
    else:
        client.put_watchlist_item(workspace, alias, row_id, desired, etag)
    verified = client.get_watchlist_item(workspace, alias, row_id)
    if item_properties(verified) != desired:
        raise ConcurrentChangeError("Watchlist row verification failed; inspect run and rollback")


def apply_plan(client: Any, plan: dict, state_dir: Path) -> tuple[str, dict]:
    validate_plan(plan)
    if client.api_version != plan["api_version"]:
        raise ConfigurationError("Plan API version differs from the active client")
    run_id = f"{timestamp_id()}-{uuid.uuid4().hex[:8]}"
    path = state_dir / "backups" / run_id / "manifest.json"
    report = {
        "resource_type": "watchlist-items",
        "run_id": run_id,
        "api_version": client.api_version,
        "operation": plan["operation"],
        "started_at": utc_now(),
        "results": [],
    }
    atomic_write_json(path, report)
    checked = {}
    for target in plan["targets"]:
        result = copy.deepcopy(target)
        result["status"] = "pending"
        report["results"].append(result)
        try:
            workspace = _workspace(target["workspace"])
            group = (workspace.key, target["alias"])
            if group not in checked:
                try:
                    watchlist = client.get_watchlist(workspace, target["alias"])
                    if (
                        not watchlist
                        or watchlist["properties"].get("itemsSearchKey") != target["search_key"]
                    ):
                        raise ConcurrentChangeError(
                            "Watchlist definition changed; generate a new plan"
                        )
                    live_rows = client.list_watchlist_items(workspace, target["alias"])
                    if snapshot(live_rows) != target["snapshot"]:
                        raise ConcurrentChangeError(
                            "Watchlist changed after planning; generate a new plan"
                        )
                    checked[group] = None
                except Exception as exc:
                    checked[group] = str(exc)
            if checked[group]:
                raise ConcurrentChangeError(checked[group])
            live = client.get_watchlist_item(workspace, target["alias"], target["item_id"])
            if item_properties(live) != target["before"]:
                raise ConcurrentChangeError("Row changed after planning; generate a new plan")
            if target["status"] == "no_change":
                result["status"] = "no_change"
                continue
            result["backup_resource"] = live
            result["write_attempted"] = True
            atomic_write_json(path, report)
            _write(client, workspace, target, target["after"], live)
            result["status"] = {"create": "created", "modify": "updated", "delete": "deleted"}[
                target["status"]
            ]
        except Exception as exc:
            result["status"], result["error"] = "failed", str(exc)
        finally:
            atomic_write_json(path, report)
    report["completed_at"] = utc_now()
    report["successful"] = not any(r["status"] == "failed" for r in report["results"])
    atomic_write_json(path, report)
    return run_id, report


def rollback_run(client: Any, state_dir: Path, run_id: str, force: bool = False) -> dict:
    directory = state_dir / "backups" / safe_key(run_id)
    manifest = read_json(directory / "manifest.json")
    if manifest["api_version"] != client.api_version:
        raise ConfigurationError("Run API version differs from the active client")
    report = {"resource_type": "watchlist-items", "source_run_id": run_id, "results": []}
    for applied in reversed(manifest["results"]):
        if not applied.get("write_attempted"):
            continue
        result = {
            "workspace": applied["workspace"],
            "display_name": applied["display_name"],
            "item_id": applied["item_id"],
            "status": "pending",
        }
        report["results"].append(result)
        try:
            workspace = _workspace(applied["workspace"])
            previous = item_properties(applied["backup_resource"])
            if previous != applied["before"]:
                raise ConfigurationError("Backup does not match planned original row")
            live = client.get_watchlist_item(workspace, applied["alias"], applied["item_id"])
            current = item_properties(live)
            if current == previous:
                result["status"] = "already_restored"
                continue
            if not force and current != applied["after"]:
                raise ConcurrentChangeError(
                    "Row changed after apply; review before forcing rollback"
                )
            definition = client.get_watchlist(workspace, applied["alias"])
            if (
                not definition
                or definition["properties"].get("itemsSearchKey") != applied["search_key"]
            ):
                raise ConcurrentChangeError("Watchlist definition changed since apply")
            _write(client, workspace, applied, previous, live)
            result["status"] = "restored"
        except Exception as exc:
            result["status"], result["error"] = "failed", str(exc)
    report["completed_at"] = utc_now()
    report["successful"] = not any(r["status"] == "failed" for r in report["results"])
    atomic_write_json(directory / f"rollback-{timestamp_id()}-{uuid.uuid4().hex[:6]}.json", report)
    return report
