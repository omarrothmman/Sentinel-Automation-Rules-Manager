from __future__ import annotations

import argparse
import functools
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .auth import create_credential
from .azure import DEFAULT_API_VERSION, ArmClient
from .catalog import (
    add_catalog_rule,
    catalog_table,
    import_catalog_bundle,
    load_catalog,
    select_catalog_rules,
)
from .discovery import discover_sentinel_workspaces, discovery_table, inventory_document
from .errors import ConfigurationError, SentinelAutomationError
from .inventory import Inventory, display_inventory
from .plans import (
    apply_plan,
    build_catalog_deployment_plan,
    build_deployment_plan,
    build_mutation_plan,
    load_plan,
    plan_summary,
    rollback_run,
    save_plan,
)
from .presentation import Column, render_table, status_text
from .rules import (
    RuleSelector,
    add_condition,
    add_property_value,
    discover_rule,
    load_deployment_file,
    remove_condition,
    remove_property_value,
    resource_properties,
    set_enabled,
)
from .util import atomic_write_json, read_json, safe_key, timestamp_id

DEFAULT_INVENTORY = Path("config/workspaces.json")
DEFAULT_STATE_DIR = Path(".sentinel-automation")
DEFAULT_CATALOG_DIR = Path("rules")


def _add_targets(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--targets", required=True, help="Comma-separated workspace keys/tags, or 'all'"
    )


def _add_selector(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--rule-id", help="Automation rule UUID (when identical across targets)")
    group.add_argument("--display-name", help="Exact automation rule display name")


def _add_condition_index(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--condition-index",
        type=int,
        help="1-based match index when more than one condition uses the property",
    )


def _add_skip_missing(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Record workspaces where the rule is absent as skipped instead of stopping the plan",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel-auto",
        description="Safely manage Microsoft Sentinel automation rules across workspaces.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG_DIR)
    parser.add_argument("--auth", choices=("interactive", "cli", "default"), default="interactive")
    parser.add_argument("--api-version", default=DEFAULT_API_VERSION)
    parser.add_argument(
        "--debug", action="store_true", help="Show a traceback for unexpected errors"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("login", help="Authenticate interactively and verify an ARM token")
    discover_parser = subparsers.add_parser(
        "discover", help="Discover Sentinel workspaces, including Azure Lighthouse scopes"
    )
    discover_parser.add_argument(
        "--tenant-id", help="Managing tenant ID (uses the existing inventory value when omitted)"
    )
    discover_parser.add_argument(
        "--subscription",
        action="append",
        help="Limit discovery to a subscription UUID; repeat for multiple subscriptions",
    )
    discover_parser.add_argument(
        "--save", action="store_true", help="Merge results into the inventory file"
    )
    discover_parser.add_argument(
        "--replace",
        action="store_true",
        help="When saving, remove inventory entries that were not rediscovered",
    )
    discover_parser.add_argument("--yes", action="store_true", help="Skip save confirmation")
    subparsers.add_parser("list-targets", help="Validate and display the workspace inventory")

    list_rules = subparsers.add_parser(
        "list-rules", help="List automation rules in target workspaces"
    )
    _add_targets(list_rules)
    list_rules.add_argument(
        "--name-contains",
        help="Filter automation rule names by literal, case-insensitive text",
    )
    list_rules.add_argument(
        "--missing",
        action="store_true",
        help="Show workspaces with no matching rule (requires --name-contains)",
    )

    export = subparsers.add_parser(
        "export", help="Export existing rules into Git-friendly JSON files"
    )
    _add_targets(export)
    _add_selector(export)
    export.add_argument("--output", type=Path, required=True)

    catalog = subparsers.add_parser("catalog", help="Manage the Git-backed base-rule catalog")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_commands.add_parser("list", help="List base rules stored in the catalog")
    catalog_commands.add_parser("validate", help="Validate every base rule and catalog uniqueness")
    catalog_add = catalog_commands.add_parser(
        "add", help="Normalize and add an exported automation rule to the catalog"
    )
    catalog_add.add_argument("--name", required=True, help="Stable logical name used for selection")
    catalog_add.add_argument("--file", type=Path, required=True)
    catalog_add.add_argument("--rule-id", help="Override the UUID from the source file")
    catalog_add.add_argument(
        "--force", action="store_true", help="Replace an existing catalog rule with the same name"
    )
    catalog_bundle = catalog_commands.add_parser(
        "import-bundle", help="Atomically import every automation rule from an ARM template"
    )
    catalog_bundle.add_argument("--file", type=Path, required=True)
    catalog_bundle.add_argument(
        "--force", action="store_true", help="Replace matching catalog rules intentionally"
    )

    plan = subparsers.add_parser(
        "plan", help="Create a non-modifying, integrity-protected change plan"
    )
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)

    add_title_parser = plan_commands.add_parser("add-title", help="Append an IncidentTitle value")
    _add_targets(add_title_parser)
    _add_selector(add_title_parser)
    _add_condition_index(add_title_parser)
    _add_skip_missing(add_title_parser)
    add_title_parser.add_argument("--title", required=True)
    add_title_parser.add_argument("--out", type=Path)

    remove_title_parser = plan_commands.add_parser(
        "remove-title", help="Remove an IncidentTitle value"
    )
    _add_targets(remove_title_parser)
    _add_selector(remove_title_parser)
    _add_condition_index(remove_title_parser)
    _add_skip_missing(remove_title_parser)
    remove_title_parser.add_argument("--title", required=True)
    remove_title_parser.add_argument("--out", type=Path)

    add_condition_parser = plan_commands.add_parser(
        "add-condition", help="Append a property condition"
    )
    _add_targets(add_condition_parser)
    _add_selector(add_condition_parser)
    _add_skip_missing(add_condition_parser)
    add_condition_parser.add_argument("--property", required=True)
    add_condition_parser.add_argument("--operator", required=True)
    add_condition_parser.add_argument("--value", action="append", required=True)
    add_condition_parser.add_argument("--out", type=Path)

    remove_condition_parser = plan_commands.add_parser(
        "remove-condition", help="Remove a matching property condition"
    )
    _add_targets(remove_condition_parser)
    _add_selector(remove_condition_parser)
    _add_condition_index(remove_condition_parser)
    _add_skip_missing(remove_condition_parser)
    remove_condition_parser.add_argument("--property", required=True)
    remove_condition_parser.add_argument("--operator")
    remove_condition_parser.add_argument("--out", type=Path)

    enabled_parser = plan_commands.add_parser("set-enabled", help="Enable or disable a rule")
    _add_targets(enabled_parser)
    _add_selector(enabled_parser)
    _add_skip_missing(enabled_parser)
    enabled_group = enabled_parser.add_mutually_exclusive_group(required=True)
    enabled_group.add_argument("--enabled", dest="enabled", action="store_true")
    enabled_group.add_argument("--disabled", dest="enabled", action="store_false")
    enabled_parser.add_argument("--out", type=Path)

    deploy_parser = plan_commands.add_parser(
        "deploy", help="Plan deployment of a new complete rule"
    )
    _add_targets(deploy_parser)
    deploy_parser.add_argument("--file", type=Path, required=True)
    deploy_parser.add_argument("--rule-id", help="Override the UUID from the deployment file")
    deploy_parser.add_argument("--if-exists", choices=("fail", "skip", "update"), default="fail")
    deploy_parser.add_argument("--out", type=Path)

    deploy_catalog_parser = plan_commands.add_parser(
        "deploy-catalog", help="Plan deployment of one, selected, or all catalog rules"
    )
    _add_targets(deploy_catalog_parser)
    deploy_catalog_parser.add_argument(
        "--rules", required=True, help="Comma-separated logical names, or 'all'"
    )
    deploy_catalog_parser.add_argument(
        "--if-exists", choices=("fail", "skip", "update"), default="fail"
    )
    deploy_catalog_parser.add_argument("--out", type=Path)

    apply_parser = subparsers.add_parser(
        "apply", help="Apply and verify a previously generated plan"
    )
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation")

    rollback_parser = subparsers.add_parser(
        "rollback", help="Restore all successful changes from a run"
    )
    rollback_parser.add_argument("--run", required=True)
    rollback_parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation")
    rollback_parser.add_argument(
        "--force", action="store_true", help="Overwrite changes made after the original deployment"
    )
    return parser


def _inventory(args: argparse.Namespace) -> Inventory:
    return Inventory.load(args.inventory)


def _client(
    args: argparse.Namespace, inventory: Inventory, api_version: str | None = None
) -> ArmClient:
    credential = create_credential(args.auth, inventory.managing_tenant_id)
    return ArmClient(credential, api_version=api_version or args.api_version)


def _selector(args: argparse.Namespace) -> RuleSelector:
    return RuleSelector(
        rule_id=getattr(args, "rule_id", None), display_name=getattr(args, "display_name", None)
    )


def _confirm(prompt: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise ConfigurationError(
            "Confirmation is required; rerun with --yes in non-interactive environments"
        )
    answer = input(f"{prompt} Type 'yes' to continue: ").strip().casefold()
    if answer != "yes":
        raise ConfigurationError("Operation cancelled")


def _validate_plan_scope(plan: dict[str, Any], inventory: Inventory) -> None:
    allowed = {
        workspace.key.casefold(): workspace.to_dict()
        for workspace in inventory.workspaces
        if workspace.enabled
    }
    for target in plan["targets"]:
        planned = target.get("workspace")
        key = planned.get("key") if isinstance(planned, dict) else None
        if not isinstance(key, str) or key.casefold() not in allowed:
            raise ConfigurationError(f"Plan targets workspace not present in inventory: {key!r}")
        if planned != allowed[key.casefold()]:
            raise ConfigurationError(
                f"Plan workspace details for '{key}' no longer match the current inventory; generate a new plan"
            )


def _default_plan_path(args: argparse.Namespace) -> Path:
    return args.state_dir / "plans" / f"{timestamp_id()}-{args.plan_command}.json"


def _resolve_plan_path(path: Path, state_dir: Path) -> Path:
    """Resolve a plan path, allowing a generated filename without its state directory."""
    if path.exists() or path.is_absolute() or path.parent != Path("."):
        return path
    generated_path = state_dir / "plans" / path.name
    return generated_path if generated_path.exists() else path


def _nonempty(value: str, name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ConfigurationError(f"{name} cannot be empty")
    return stripped


def _handle_plan(args: argparse.Namespace, inventory: Inventory, client: ArmClient) -> int:
    workspaces = inventory.select(args.targets)
    if args.plan_command == "deploy-catalog":
        catalog_rules = select_catalog_rules(load_catalog(args.catalog_dir), args.rules)
        plan = build_catalog_deployment_plan(
            client,
            workspaces,
            catalog_rules,
            args.if_exists,
        )
    elif args.plan_command == "deploy":
        rule_id, properties = load_deployment_file(args.file, args.rule_id)
        plan = build_deployment_plan(
            client,
            workspaces,
            rule_id,
            properties,
            str(args.file),
            args.if_exists,
        )
    else:
        selector = _selector(args)
        if args.plan_command == "add-title":
            title = _nonempty(args.title, "title")
            mutation = functools.partial(
                add_property_value,
                property_name="IncidentTitle",
                value=title,
                condition_index=args.condition_index,
            )
            parameters = {"title": title, "condition_index": args.condition_index}
        elif args.plan_command == "remove-title":
            title = _nonempty(args.title, "title")
            mutation = functools.partial(
                remove_property_value,
                property_name="IncidentTitle",
                value=title,
                condition_index=args.condition_index,
            )
            parameters = {"title": title, "condition_index": args.condition_index}
        elif args.plan_command == "add-condition":
            property_name = _nonempty(args.property, "property")
            operator = _nonempty(args.operator, "operator")
            values = [_nonempty(value, "value") for value in args.value]
            mutation = functools.partial(
                add_condition, property_name=property_name, operator=operator, values=values
            )
            parameters = {"property": property_name, "operator": operator, "values": values}
        elif args.plan_command == "remove-condition":
            property_name = _nonempty(args.property, "property")
            operator = _nonempty(args.operator, "operator") if args.operator else None
            mutation = functools.partial(
                remove_condition,
                property_name=property_name,
                operator=operator,
                condition_index=args.condition_index,
            )
            parameters = {
                "property": property_name,
                "operator": operator,
                "condition_index": args.condition_index,
            }
        elif args.plan_command == "set-enabled":
            mutation = functools.partial(set_enabled, enabled=args.enabled)
            parameters = {"enabled": args.enabled}
        else:
            raise ConfigurationError(f"Unknown plan command: {args.plan_command}")
        parameters["skip_missing"] = args.skip_missing
        plan = build_mutation_plan(
            client,
            workspaces,
            selector,
            args.plan_command,
            parameters,
            mutation,
            skip_missing=args.skip_missing,
        )
    output = args.out or _default_plan_path(args)
    sealed = save_plan(plan, output)
    print(plan_summary(sealed))
    print(f"\nPlan saved to: {output.resolve()}")
    return 0


def _result_detail(result: dict[str, Any]) -> str:
    if result.get("error"):
        return str(result["error"])
    details = {
        "updated": "Rule updated and verified",
        "created": "Rule created and verified",
        "no_change": "No Azure change required",
        "skipped": "Skipped by the plan",
        "restored": "Previous rule restored and verified",
        "deleted": "Newly created rule removed",
        "already_absent": "Rule was already absent",
        "already_restored": "Rule was already restored",
    }
    return details.get(str(result.get("status")), "Completed")


def _print_results(report: dict[str, Any], title: str) -> None:
    results = report["results"]
    successful = sum(result.get("status") != "failed" for result in results)
    rows = [
        (
            result["workspace"]["key"],
            result.get("display_name") or result.get("rule_id"),
            status_text(result.get("status", "unknown")),
            _result_detail(result),
        )
        for result in results
    ]
    print(
        render_table(
            title,
            f"{len(results)} targets | Successful: {successful} | Failed: {len(results) - successful}",
            (
                Column("TARGET", 20, 36),
                Column("RULE", 24, 54),
                Column("STATUS", 18, 20),
                Column("RESULT", 34, 100),
            ),
            rows,
        )
    )


def _discovery_progress(current: int, total: int, workspace_name: str) -> None:
    if not sys.stdout.isatty():
        return
    name = workspace_name[:35]
    print(
        f"\rVerifying Sentinel onboarding: {current:>3}/{total:<3} {name:<35}",
        end="\n" if current == total else "",
        flush=True,
    )


def run(args: argparse.Namespace) -> int:
    if (
        args.command == "list-rules"
        and args.missing
        and not (args.name_contains and args.name_contains.strip())
    ):
        raise ConfigurationError("--missing requires a non-empty --name-contains filter")
    if args.command == "discover":
        if args.replace and not args.save:
            raise ConfigurationError("--replace requires --save")
        existing = Inventory.load(args.inventory) if args.inventory.exists() else None
        tenant_id = args.tenant_id or (existing.managing_tenant_id if existing else None)
        credential = create_credential(args.auth, tenant_id)
        client = ArmClient(credential, api_version=args.api_version)
        print("Discovering accessible Azure and Lighthouse workspaces...", flush=True)
        try:
            result = discover_sentinel_workspaces(
                client, tenant_id, args.subscription, _discovery_progress
            )
        finally:
            client.close()

        print(discovery_table(result))
        if result.issues:
            print(f"\nCOULD NOT VERIFY ({len(result.issues)})", file=sys.stderr)
            for index, issue in enumerate(result.issues, start=1):
                print(
                    f"  {index}. {issue.workspace_name} "
                    f"({issue.subscription_id}/{issue.resource_group})\n"
                    f"     "
                    f"{issue.error}",
                    file=sys.stderr,
                )
        if not result.workspaces:
            raise ConfigurationError("No verified Microsoft Sentinel workspaces were discovered")
        if args.save:
            action = "replace" if args.replace else "merge into"
            _confirm(f"Save and {action} inventory at {args.inventory}?", args.yes)
            document = inventory_document(result, tenant_id, existing, args.replace)
            if args.inventory.exists():
                backup_path = (
                    args.state_dir
                    / "inventory-backups"
                    / f"{safe_key(args.inventory.stem)}-{timestamp_id()}.json"
                )
                atomic_write_json(backup_path, read_json(args.inventory))
                print(f"Previous inventory backed up to: {backup_path.resolve()}")
            atomic_write_json(args.inventory, document)
            Inventory.load(args.inventory)
            print(f"Inventory saved to: {args.inventory.resolve()}")
        else:
            print("\nNEXT STEP")
            print("  Preview only; no file was changed.")
            print("  Save these workspaces with: sentinel-auto discover --save")
        return 2 if result.issues else 0

    if args.command == "catalog":
        if args.catalog_command == "add":
            added = add_catalog_rule(
                args.catalog_dir,
                args.file,
                args.name,
                args.rule_id,
                args.force,
            )
            print(catalog_table([added], args.catalog_dir, validated=True))
            print(f"\nCatalog file: {added.path}")
            return 0
        if args.catalog_command == "import-bundle":
            imported = import_catalog_bundle(args.catalog_dir, args.file, args.force)
            print(catalog_table(imported, args.catalog_dir, validated=True))
            print(f"\nImported {len(imported)} rules from: {args.file.resolve()}")
            return 0
        rules = load_catalog(args.catalog_dir)
        print(catalog_table(rules, args.catalog_dir, validated=args.catalog_command == "validate"))
        return 0

    inventory = _inventory(args)
    if args.command == "list-targets":
        print(display_inventory(inventory.workspaces))
        return 0

    if args.command == "apply":
        plan_path = _resolve_plan_path(args.plan, args.state_dir)
        plan = load_plan(plan_path)
        _validate_plan_scope(plan, inventory)
        print(plan_summary(plan))
        changes = sum(target["status"] in ("modify", "create") for target in plan["targets"])
        _confirm(f"Apply {changes} Azure change(s)?", args.yes)
        client = _client(args, inventory, api_version=plan["api_version"])
        try:
            run_id, report = apply_plan(client, plan, args.state_dir)
        finally:
            client.close()
        _print_results(report, "AUTOMATION RULE APPLY RESULTS")
        print(f"\nRun ID:   {run_id}")
        print(f"Backups:  {(args.state_dir / 'backups' / run_id).resolve()}")
        return 0 if report["successful"] else 2

    if args.command == "rollback":
        if safe_key(args.run) != args.run:
            raise ConfigurationError("Invalid run ID")
        manifest_path = args.state_dir / "backups" / safe_key(args.run) / "manifest.json"
        manifest = read_json(manifest_path)
        api_version = manifest.get("api_version") if isinstance(manifest, dict) else None
        if not isinstance(api_version, str):
            raise ConfigurationError(f"Run '{args.run}' has no valid API version")
        _confirm(f"Rollback changes from run {args.run}?", args.yes)
        client = _client(args, inventory, api_version=api_version)
        try:
            report = rollback_run(client, args.state_dir, args.run, args.force)
        finally:
            client.close()
        _print_results(report, "AUTOMATION RULE ROLLBACK RESULTS")
        return 0 if report["successful"] else 2

    client = _client(args, inventory)
    try:
        if args.command == "login":
            client.authenticate()
            print("AZURE AUTHENTICATION\nStatus:  SUCCESS\nMode:    " + args.auth)
            return 0
        if args.command == "list-rules":
            workspaces = inventory.select(args.targets)
            rows = []
            failures = 0
            missing_rows = []
            matched_workspaces = 0
            search = args.name_contains.casefold() if args.name_contains is not None else None
            for workspace in workspaces:
                try:
                    rules = client.list_rules(workspace)
                except SentinelAutomationError as exc:
                    failures += 1
                    print(f"ERROR: {workspace.key}: {exc}", file=sys.stderr)
                    continue
                previous_count = len(rows)
                for rule in rules:
                    properties = rule.get("properties", {})
                    if (
                        search is not None
                        and search not in properties.get("displayName", "").casefold()
                    ):
                        continue
                    enabled = properties.get("triggeringLogic", {}).get("isEnabled", "unknown")
                    rows.append(
                        (
                            workspace.key,
                            "Enabled"
                            if enabled is True
                            else "Disabled"
                            if enabled is False
                            else "Unknown",
                            rule.get("name", ""),
                            properties.get("displayName", ""),
                        )
                    )
                matched_workspaces += len(rows) > previous_count
                if len(rows) == previous_count:
                    missing_rows.append(
                        (
                            workspace.key,
                            workspace.display_name,
                            workspace.workspace_name,
                            "NO MATCH",
                        )
                    )
            if args.missing:
                print(
                    render_table(
                        "SENTINEL WORKSPACES WITHOUT A MATCHING AUTOMATION RULE",
                        f"{len(missing_rows)} workspaces with no matching rule; "
                        f"{len(workspaces) - failures}/{len(workspaces)} workspaces checked; "
                        f"{failures} failed (not classified as missing)",
                        (
                            Column("TARGET", 18, 30),
                            Column("DISPLAY NAME", 24, 70),
                            Column("WORKSPACE", 24, 70),
                            Column("RESULT", 8, 8),
                        ),
                        missing_rows,
                    )
                )
                return 2 if failures else 0
            summary = (
                f"{len(rows)} {'matching rules' if search is not None else 'rules'} across "
                f"{matched_workspaces} workspaces; "
                f"{len(workspaces) - failures}/{len(workspaces)} workspaces checked; "
                f"{failures} failed"
            )
            print(
                render_table(
                    "MICROSOFT SENTINEL AUTOMATION RULES",
                    summary,
                    (
                        Column("TARGET", 18, 30),
                        Column("STATUS", 8, 8),
                        Column("RULE ID", 36, 36),
                        Column("DISPLAY NAME", 24, 70),
                    ),
                    rows,
                )
            )
            return 2 if failures else 0
        if args.command == "export":
            selector = _selector(args)
            args.output.mkdir(parents=True, exist_ok=True)
            workspaces = inventory.select(args.targets)
            rows = []
            for workspace in workspaces:
                rule = discover_rule(client, workspace, selector)
                properties = resource_properties(rule)
                path = args.output / f"{safe_key(workspace.key)}.json"
                atomic_write_json(
                    path,
                    {
                        "schema_version": 1,
                        "logical_name": safe_key(str(properties["displayName"]).casefold()),
                        "rule_id": rule["name"],
                        "source_workspace": workspace.key,
                        "properties": properties,
                    },
                )
                rows.append((workspace.key, "Exported", path.resolve()))
            print(
                render_table(
                    "AUTOMATION RULE EXPORT RESULTS",
                    f"{len(rows)} rules exported to {args.output.resolve()}",
                    (
                        Column("TARGET", 20, 36),
                        Column("STATUS", 10, 10),
                        Column("FILE", 34, 110),
                    ),
                    rows,
                )
            )
            return 0
        if args.command == "plan":
            return _handle_plan(args, inventory, client)
        raise ConfigurationError(f"Unknown command: {args.command}")
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except SentinelAutomationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        if args.debug:
            raise
        print(f"ERROR: Unexpected failure: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
