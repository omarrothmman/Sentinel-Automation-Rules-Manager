from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from . import watchlists
from .errors import ConfigurationError, SentinelAutomationError
from .plans import plan_summary, save_plan
from .presentation import Column, render_table
from .util import timestamp_id


def add_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("watchlists", help="Browse and safely edit existing watchlists")
    actions = parser.add_subparsers(dest="watchlist_command", required=True)
    for name in ("list", "items", "export", "import", "plan"):
        command = actions.add_parser(name)
        if name == "plan":
            edits = command.add_subparsers(dest="watchlist_action", required=True)
            for action in ("add", "update", "delete"):
                edit = edits.add_parser(action)
                _target_args(edit)
                edit.add_argument("--key-column", help="Unique column used to match rows")
                edit.add_argument("--key-value", help="Exact, case-sensitive value to match")
                edit.add_argument("--item-id", help="Select a row by UUID in one workspace")
                if action != "delete":
                    edit.add_argument(
                        "--set",
                        action="append",
                        required=True,
                        dest="fields",
                        metavar="COLUMN=VALUE",
                        help="Repeat for multiple columns",
                    )
                edit.add_argument("--out", type=Path)
            continue
        command.add_argument("--targets", required=True)
        if name != "list":
            command.add_argument("--alias", required=True)
        if name == "export":
            command.add_argument(
                "--output", type=Path, required=True, help="CSV file; select exactly one workspace"
            )
        if name == "import":
            command.add_argument("--file", type=Path, required=True)
            command.add_argument("--key-column", required=True)
            command.add_argument(
                "--mode",
                choices=("merge", "replace"),
                default="merge",
                help="Replace also plans deletion of rows absent from CSV",
            )
            command.add_argument("--out", type=Path)


def _target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--targets", required=True)
    parser.add_argument("--alias", required=True)


def run(args, inventory, client) -> int:
    workspaces = inventory.select(args.targets)
    command = args.watchlist_command
    if command in ("plan", "import"):
        payload = {"alias": args.alias, "key_column": args.key_column}
        if command == "import":
            payload.update(
                action="import", mode=args.mode, csv=args.file.read_text(encoding="utf-8-sig")
            )
        else:
            values = {}
            for field in getattr(args, "fields", []) or []:
                key, sep, value = field.partition("=")
                if not sep or key in values:
                    raise ConfigurationError("Use --set COLUMN=VALUE with unique column names")
                values[key] = value
            payload.update(
                action=args.watchlist_action,
                values=values,
                key_value=args.key_value,
                item_id=args.item_id,
            )
        plan = watchlists.build_plan(client, workspaces, payload)
        path = args.out or args.state_dir / "plans" / (
            f"{timestamp_id()}-{plan['operation']}-{uuid.uuid4().hex[:6]}.json"
        )
        sealed = save_plan(plan, path)
        print(plan_summary(sealed))
        print(f'\nPlan saved to: {path.resolve()}\nApply with: sentinel-auto apply --plan "{path}"')
        return 0
    if command == "export":
        if len(workspaces) != 1:
            raise ConfigurationError("CSV export requires exactly one workspace")
        workspace = workspaces[0]
        definition = client.get_watchlist(workspace, args.alias)
        if not definition:
            raise ConfigurationError("Watchlist does not exist")
        rows = client.list_watchlist_items(workspace, args.alias)
        data = watchlists.export_csv(rows, definition["properties"].get("itemsSearchKey", ""))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding="utf-8-sig")
        print(f"Exported {len(rows)} rows to {args.output.resolve()}")
        return 0
    rows, failures = [], []
    for workspace in workspaces:
        try:
            resources = (
                client.list_watchlists(workspace)
                if command == "list"
                else client.list_watchlist_items(workspace, args.alias)
            )
            for resource in resources:
                props = resource.get("properties", {})
                if command == "list":
                    rows.append(
                        (
                            workspace.key,
                            props.get("watchlistAlias") or resource.get("name"),
                            props.get("displayName", ""),
                            props.get("itemsSearchKey", ""),
                        )
                    )
                else:
                    rows.append(
                        {
                            "workspace": workspace.key,
                            "item_id": watchlists.item_id(resource),
                            "values": watchlists.item_properties(resource)["itemsKeyValue"],
                        }
                    )
        except SentinelAutomationError as exc:
            failures.append(f"{workspace.key}: {exc}")
    if command == "list":
        print(
            render_table(
                "SENTINEL WATCHLISTS",
                f"{len(rows)} watchlists",
                (
                    Column("WORKSPACE", 18, 30),
                    Column("ALIAS", 20, 50),
                    Column("NAME", 24, 60),
                    Column("SEARCH KEY", 16, 40),
                ),
                rows,
            )
        )
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    for failure in failures:
        print(f"ERROR: {failure}")
    return 2 if failures else 0
