from __future__ import annotations

import copy
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .azure import ArmClient
from .catalog import CatalogRule
from .errors import (
    ConcurrentChangeError,
    ConfigurationError,
    PlanIntegrityError,
    RuleNotFoundError,
)
from .inventory import Workspace
from .rules import Mutation, RuleSelector, discover_rule, resource_properties
from .util import atomic_write_json, content_hash, read_json, safe_key, timestamp_id, utc_now

PLAN_VERSION = 1


def _pointer(path: tuple[str | int, ...]) -> str:
    if not path:
        return "/"
    return "/" + "/".join(str(item).replace("~", "~0").replace("/", "~1") for item in path)


def json_diff(before: Any, after: Any, path: tuple[str | int, ...] = ()) -> list[dict[str, Any]]:
    """Return deterministic, exact before/after changes using JSON Pointer paths."""
    if type(before) is not type(after):
        return [{"path": _pointer(path), "before": before, "after": after}]
    if isinstance(before, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(before.keys() | after.keys()):
            child_path = path + (key,)
            if key not in before:
                changes.append({"path": _pointer(child_path), "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": _pointer(child_path), "before": before[key], "after": None})
            else:
                changes.extend(json_diff(before[key], after[key], child_path))
        return changes
    # Arrays are deliberately reported as one atomic field. This makes title-list
    # additions readable and matches how Azure receives the updated array.
    if isinstance(before, list):
        return (
            [] if before == after else [{"path": _pointer(path), "before": before, "after": after}]
        )
    return [] if before == after else [{"path": _pointer(path), "before": before, "after": after}]


def _seal(plan: dict[str, Any]) -> dict[str, Any]:
    sealed = copy.deepcopy(plan)
    sealed.pop("integrity", None)
    sealed["integrity"] = content_hash(sealed)
    return sealed


def validate_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise PlanIntegrityError("Plan root must be an object")
    if plan.get("plan_version") != PLAN_VERSION:
        raise PlanIntegrityError(f"Unsupported plan version: {plan.get('plan_version')!r}")
    expected = plan.get("integrity")
    unsigned = copy.deepcopy(plan)
    unsigned.pop("integrity", None)
    if not isinstance(expected, str) or content_hash(unsigned) != expected:
        raise PlanIntegrityError("Plan integrity check failed; the plan may have been edited")
    if not isinstance(plan.get("targets"), list) or not plan["targets"]:
        raise PlanIntegrityError("Plan contains no targets")
    if not isinstance(plan.get("api_version"), str) or not plan["api_version"]:
        raise PlanIntegrityError("Plan has no valid API version")
    allowed_statuses = {"modify", "create", "no_change", "skipped"}
    for index, target in enumerate(plan["targets"], start=1):
        if not isinstance(target, dict):
            raise PlanIntegrityError(f"Plan target {index} must be an object")
        if target.get("status") not in allowed_statuses:
            raise PlanIntegrityError(f"Plan target {index} has an invalid status")
        if target["status"] == "skipped" and target.get("skip_reason") == "rule_not_found":
            if target.get("rule_id") is not None:
                raise PlanIntegrityError(f"Missing-rule target {index} unexpectedly has a rule ID")
            if target.get("before_hash") is not None or target.get("after_hash") is not None:
                raise PlanIntegrityError(
                    f"Missing-rule target {index} unexpectedly has rule hashes"
                )
            if target.get("after_body") is not None or target.get("changes") != []:
                raise PlanIntegrityError(f"Missing-rule target {index} contains a request body")
            continue
        if not isinstance(target.get("rule_id"), str) or not target["rule_id"]:
            raise PlanIntegrityError(f"Plan target {index} has no rule ID")
        if not isinstance(target.get("after_body"), dict) or not isinstance(
            target["after_body"].get("properties"), dict
        ):
            raise PlanIntegrityError(f"Plan target {index} has no valid request body")
        if not isinstance(target.get("after_hash"), str):
            raise PlanIntegrityError(f"Plan target {index} has no after hash")
        if not isinstance(target.get("changes"), list):
            raise PlanIntegrityError(f"Plan target {index} has no change details")
        if content_hash(target["after_body"]["properties"]) != target["after_hash"]:
            raise PlanIntegrityError(f"Plan target {index} request body does not match its hash")
        if target["status"] == "create" and target.get("before_hash") is not None:
            raise PlanIntegrityError(f"Create target {index} unexpectedly has a before hash")
        if target["status"] != "create" and not isinstance(target.get("before_hash"), str):
            raise PlanIntegrityError(f"Existing target {index} has no before hash")
        if target["status"] == "no_change" and target["before_hash"] != target["after_hash"]:
            raise PlanIntegrityError(f"No-change target {index} contains different hashes")
    return plan


def load_plan(path: Path) -> dict[str, Any]:
    return validate_plan(read_json(path))


def save_plan(plan: dict[str, Any], path: Path) -> dict[str, Any]:
    sealed = _seal(plan)
    atomic_write_json(path, sealed)
    return sealed


def build_mutation_plan(
    client: ArmClient,
    workspaces: Iterable[Workspace],
    selector: RuleSelector,
    operation: str,
    parameters: dict[str, Any],
    mutation: Mutation,
    skip_missing: bool = False,
) -> dict[str, Any]:
    targets = []
    for workspace in workspaces:
        try:
            resource = discover_rule(client, workspace, selector)
        except RuleNotFoundError:
            if not skip_missing:
                raise
            targets.append(
                {
                    "workspace": workspace.to_dict(),
                    "rule_id": None,
                    "display_name": selector.display_name or selector.rule_id,
                    "status": "skipped",
                    "skip_reason": "rule_not_found",
                    "summary": "rule not found; no change planned",
                    "before_etag": None,
                    "before_hash": None,
                    "after_hash": None,
                    "after_body": None,
                    "changes": [],
                }
            )
            continue
        before = resource_properties(resource)
        after, summary = mutation(before)
        rule_id = resource.get("name")
        if not isinstance(rule_id, str) or not rule_id:
            raise ConfigurationError(f"Azure rule in '{workspace.key}' has no resource name")
        before_hash = content_hash(before)
        after_hash = content_hash(after)
        targets.append(
            {
                "workspace": workspace.to_dict(),
                "rule_id": rule_id,
                "display_name": before.get("displayName"),
                "status": "no_change" if before_hash == after_hash else "modify",
                "summary": summary,
                "before_etag": resource.get("etag"),
                "before_hash": before_hash,
                "after_hash": after_hash,
                "after_body": {"properties": after},
                "changes": json_diff(before, after),
            }
        )
    return {
        "plan_version": PLAN_VERSION,
        "created_at": utc_now(),
        "api_version": client.api_version,
        "operation": operation,
        "parameters": parameters,
        "targets": targets,
    }


def build_deployment_plan(
    client: ArmClient,
    workspaces: Iterable[Workspace],
    rule_id: str,
    properties: dict[str, Any],
    source_file: str,
    if_exists: str,
) -> dict[str, Any]:
    targets = []
    for workspace in workspaces:
        existing = client.get_rule(workspace, rule_id)
        existing_rules = client.list_rules(workspace) if existing is None else [existing]
        targets.append(
            _deployment_target(workspace, rule_id, properties, if_exists, existing, existing_rules)
        )
    return {
        "plan_version": PLAN_VERSION,
        "created_at": utc_now(),
        "api_version": client.api_version,
        "operation": "deploy",
        "parameters": {"source_file": source_file, "if_exists": if_exists},
        "targets": targets,
    }


def build_catalog_deployment_plan(
    client: ArmClient,
    workspaces: Iterable[Workspace],
    rules: list[CatalogRule],
    if_exists: str,
) -> dict[str, Any]:
    targets = []
    workspace_list = list(workspaces)
    for workspace in workspace_list:
        existing_rules = client.list_rules(workspace)
        by_id = {
            str(rule.get("name", "")).casefold(): rule
            for rule in existing_rules
            if rule.get("name")
        }
        for rule in rules:
            target = _deployment_target(
                workspace,
                rule.rule_id,
                rule.properties,
                if_exists,
                by_id.get(rule.rule_id.casefold()),
                existing_rules,
            )
            target["catalog_rule"] = rule.logical_name
            targets.append(target)
    return {
        "plan_version": PLAN_VERSION,
        "created_at": utc_now(),
        "api_version": client.api_version,
        "operation": "deploy-catalog",
        "parameters": {
            "rules": [rule.logical_name for rule in rules],
            "if_exists": if_exists,
        },
        "targets": targets,
    }


def _deployment_target(
    workspace: Workspace,
    rule_id: str,
    properties: dict[str, Any],
    if_exists: str,
    existing: dict[str, Any] | None,
    existing_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    desired_hash = content_hash(properties)
    desired_display_name = str(properties.get("displayName"))
    if existing is None:
        duplicates = [
            rule
            for rule in existing_rules
            if str(rule.get("properties", {}).get("displayName", "")).casefold()
            == desired_display_name.casefold()
        ]
        if duplicates:
            duplicate_ids = ", ".join(str(rule.get("name")) for rule in duplicates)
            raise ConfigurationError(
                f"Workspace '{workspace.key}' already has rule(s) named '{desired_display_name}' "
                f"under different IDs: {duplicate_ids}"
            )
        status = "create"
        before_hash = None
        before_etag = None
    else:
        before = resource_properties(existing)
        before_hash = content_hash(before)
        before_etag = existing.get("etag")
        if before_hash == desired_hash:
            status = "no_change"
        elif if_exists == "fail":
            raise ConfigurationError(
                f"Rule ID '{rule_id}' already exists with different content in '{workspace.key}'; "
                "use --if-exists skip or update"
            )
        elif if_exists == "skip":
            status = "skipped"
        else:
            status = "modify"
    return {
        "workspace": workspace.to_dict(),
        "rule_id": rule_id,
        "display_name": desired_display_name,
        "status": status,
        "summary": f"deploy '{desired_display_name}'",
        "before_etag": before_etag,
        "before_hash": before_hash,
        "after_hash": desired_hash,
        "after_body": {"properties": copy.deepcopy(properties)},
        "changes": (
            [{"path": "/properties", "before": None, "after": copy.deepcopy(properties)}]
            if existing is None
            else json_diff(resource_properties(existing), properties)
        ),
    }


def _workspace_from_plan(value: Any) -> Workspace:
    if not isinstance(value, dict):
        raise PlanIntegrityError("Plan target workspace must be an object")
    try:
        return Workspace(
            key=value["key"],
            display_name=value["display_name"],
            subscription_id=value["subscription_id"],
            resource_group=value["resource_group"],
            workspace_name=value["workspace_name"],
            enabled=True,
            tags=(),
        )
    except (KeyError, TypeError) as exc:
        raise PlanIntegrityError("Plan target contains an invalid workspace") from exc


def plan_summary(plan: dict[str, Any]) -> str:
    counts: dict[str, int] = {}
    for target in plan["targets"]:
        status = str(target["status"])
        counts[status] = counts.get(status, 0) + 1

    lines = ["AUTOMATION RULE CHANGE PLAN", ""]
    operation = str(plan["operation"])
    lines.append(f"Operation:  {operation}")
    display_names = {
        str(target.get("display_name")) for target in plan["targets"] if target.get("display_name")
    }
    if len(display_names) == 1:
        lines.append(f"Rule:       {next(iter(display_names))}")
    title = plan.get("parameters", {}).get("title")
    if isinstance(title, str):
        lines.append(f"Title:      {title}")
    lines.append(f"Created:    {plan['created_at']}")

    totals = "  ".join(
        f"{_display_status(status, operation)}: {count}"
        for status, count in _ordered_counts(counts)
    )
    count_label = "Rule targets" if operation == "deploy-catalog" else "Targets"
    lines.extend((f"{count_label + ':':<14}{len(plan['targets'])}  |  {totals}", ""))

    terminal_width = shutil.get_terminal_size((140, 24)).columns
    if terminal_width < 85:
        for index, target in enumerate(plan["targets"], start=1):
            status = _display_status(str(target["status"]), operation)
            lines.extend(
                (
                    f"[{index:02}] {_target_label(target, operation)}",
                    f"     Status: {status}",
                    f"     Result: {target['summary']}",
                    "",
                )
            )
    else:
        capped_width = min(terminal_width, 180)
        target_width = min(36, max(22, int(capped_width * 0.27)))
        status_width = 18
        result_width = max(30, capped_width - target_width - status_width - 10)
        widths = (4, target_width, status_width, result_width)
        lines.append(_summary_row(("#", "TARGET", "STATUS", "RESULT"), widths))
        lines.append("-" * min(sum(widths) + 6, terminal_width))
        for index, target in enumerate(plan["targets"], start=1):
            lines.append(
                _summary_row(
                    (
                        str(index),
                        _target_label(target, operation),
                        _display_status(str(target["status"]), operation),
                        str(target["summary"]),
                    ),
                    widths,
                )
            )
    lines.extend(("", "Full before/after details are stored in the plan JSON."))
    return "\n".join(lines)


def _ordered_counts(counts: dict[str, int]) -> list[tuple[str, int]]:
    order = ("modify", "create", "no_change", "skipped")
    return [(status, counts[status]) for status in order if counts.get(status)]


def _target_label(target: dict[str, Any], operation: str) -> str:
    workspace = str(target["workspace"]["key"])
    catalog_rule = target.get("catalog_rule")
    if operation == "deploy-catalog" and isinstance(catalog_rule, str):
        return f"{workspace} / {catalog_rule}"
    return workspace


def _display_status(status: str, operation: str) -> str:
    if status == "modify":
        return "WILL CHANGE"
    if status == "create":
        return "WILL CREATE"
    if status == "no_change":
        return "ALREADY PRESENT" if operation == "add-title" else "NO CHANGE"
    if status == "skipped":
        return "NOT FOUND" if operation not in ("deploy", "deploy-catalog") else "SKIPPED"
    return status.upper().replace("_", " ")


def _summary_row(values: tuple[str, ...], widths: tuple[int, ...]) -> str:
    cells = [_summary_truncate(value, width).ljust(width) for value, width in zip(values, widths)]
    return "  ".join(cells).rstrip()


def _summary_truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(1, width - 3)] + "..."


def apply_plan(
    client: ArmClient, plan: dict[str, Any], state_dir: Path
) -> tuple[str, dict[str, Any]]:
    validate_plan(plan)
    if client.api_version != plan["api_version"]:
        raise PlanIntegrityError(
            f"Plan API version is {plan['api_version']}, but client uses {client.api_version}"
        )
    run_id = f"{timestamp_id()}-{uuid.uuid4().hex[:8]}"
    run_dir = state_dir / "backups" / run_id
    manifest_path = run_dir / "manifest.json"
    results: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {
        "run_version": 1,
        "run_id": run_id,
        "started_at": utc_now(),
        "operation": plan["operation"],
        "api_version": plan["api_version"],
        "plan_integrity": plan["integrity"],
        "results": results,
    }
    atomic_write_json(manifest_path, manifest)

    for target in plan["targets"]:
        workspace = _workspace_from_plan(target["workspace"])
        result: dict[str, Any] = {
            "workspace": workspace.to_dict(),
            "rule_id": target["rule_id"],
            "display_name": target["display_name"],
            "planned_status": target["status"],
            "status": "pending",
            "before_hash": target["before_hash"],
            "after_hash": target["after_hash"],
        }
        results.append(result)
        try:
            if target["status"] in ("no_change", "skipped"):
                result["status"] = target["status"]
                continue
            live = client.get_rule(workspace, target["rule_id"])
            if target["status"] == "create":
                if live is not None:
                    raise ConcurrentChangeError(
                        f"Rule '{target['rule_id']}' was created in '{workspace.key}' after planning"
                    )
            else:
                if live is None:
                    raise ConcurrentChangeError(
                        f"Rule '{target['rule_id']}' was deleted from '{workspace.key}' after planning"
                    )
                live_hash = content_hash(resource_properties(live))
                if live_hash != target["before_hash"]:
                    raise ConcurrentChangeError(
                        f"Rule in '{workspace.key}' changed after planning; generate a new plan"
                    )

            backup_path = run_dir / f"{safe_key(workspace.key)}--{target['rule_id']}.json"
            atomic_write_json(
                backup_path,
                {
                    "workspace": workspace.to_dict(),
                    "rule_id": target["rule_id"],
                    "captured_at": utc_now(),
                    "resource": live,
                },
            )
            result["backup_file"] = backup_path.name
            etag = live.get("etag") if live else None
            result["write_attempted"] = True
            atomic_write_json(manifest_path, manifest)
            client.put_rule(workspace, target["rule_id"], target["after_body"], etag)
            result["write_completed"] = True
            verified = client.get_rule(workspace, target["rule_id"])
            if (
                verified is None
                or content_hash(resource_properties(verified)) != target["after_hash"]
            ):
                raise ConcurrentChangeError(
                    f"Post-deployment verification failed in workspace '{workspace.key}'"
                )
            result["status"] = "created" if target["status"] == "create" else "updated"
            result["completed_at"] = utc_now()
        except Exception as exc:
            result["status"] = "failed"
            result["error"] = str(exc)
        finally:
            atomic_write_json(manifest_path, manifest)

    manifest["completed_at"] = utc_now()
    manifest["successful"] = not any(result["status"] == "failed" for result in results)
    atomic_write_json(manifest_path, manifest)
    return run_id, manifest


def rollback_run(
    client: ArmClient, state_dir: Path, run_id: str, force: bool = False
) -> dict[str, Any]:
    run_dir = state_dir / "backups" / safe_key(run_id)
    manifest = read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("results"), list):
        raise ConfigurationError(f"Invalid run manifest for {run_id}")
    if manifest.get("api_version") != client.api_version:
        raise ConfigurationError(
            f"Run uses API {manifest.get('api_version')}, but client uses {client.api_version}"
        )
    rollback_results: list[dict[str, Any]] = []
    for applied in reversed(manifest["results"]):
        if applied.get("status") not in ("updated", "created") and not applied.get(
            "write_attempted"
        ):
            continue
        workspace = _workspace_from_plan(applied["workspace"])
        result: dict[str, Any] = {
            "workspace": workspace.to_dict(),
            "rule_id": applied["rule_id"],
            "display_name": applied.get("display_name"),
            "status": "pending",
        }
        rollback_results.append(result)
        try:
            backup_name = applied.get("backup_file")
            if not isinstance(backup_name, str):
                raise ConfigurationError(f"Missing backup filename for workspace '{workspace.key}'")
            if Path(backup_name).name != backup_name:
                raise ConfigurationError(f"Invalid backup filename for workspace '{workspace.key}'")
            backup = read_json(run_dir / backup_name)
            live = client.get_rule(workspace, applied["rule_id"])
            if live is None:
                if backup.get("resource") is None:
                    result["status"] = "already_absent"
                    continue
                raise ConcurrentChangeError(f"Cannot rollback missing rule in '{workspace.key}'")
            live_hash = content_hash(resource_properties(live))
            if live_hash == applied.get("before_hash"):
                result["status"] = "already_restored"
                continue
            if not force and live_hash != applied["after_hash"]:
                raise ConcurrentChangeError(
                    f"Rule in '{workspace.key}' changed after deployment; use --force only after review"
                )
            previous = backup.get("resource")
            if previous is None:
                client.delete_rule(workspace, applied["rule_id"], live.get("etag"))
                if client.get_rule(workspace, applied["rule_id"]) is not None:
                    raise ConcurrentChangeError(
                        f"Deletion verification failed in '{workspace.key}'"
                    )
                result["status"] = "deleted"
            else:
                previous_properties = resource_properties(previous)
                client.put_rule(
                    workspace,
                    applied["rule_id"],
                    {"properties": previous_properties},
                    live.get("etag"),
                )
                verified = client.get_rule(workspace, applied["rule_id"])
                if (
                    verified is None
                    or content_hash(resource_properties(verified)) != applied["before_hash"]
                ):
                    raise ConcurrentChangeError(
                        f"Rollback verification failed in '{workspace.key}'"
                    )
                result["status"] = "restored"
        except Exception as exc:
            result["status"] = "failed"
            result["error"] = str(exc)

    report = {
        "rollback_version": 1,
        "source_run_id": run_id,
        "completed_at": utc_now(),
        "successful": not any(item["status"] == "failed" for item in rollback_results),
        "results": rollback_results,
    }
    atomic_write_json(run_dir / f"rollback-{timestamp_id()}.json", report)
    return report
