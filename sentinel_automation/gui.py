from __future__ import annotations

import argparse
import functools
import json
import secrets
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .auth import create_credential
from .azure import DEFAULT_API_VERSION, ArmClient
from .catalog import load_catalog, select_catalog_rules
from .cli import _validate_plan_scope
from .discovery import discover_sentinel_workspaces, inventory_document
from .errors import ConfigurationError, SentinelAutomationError
from .inventory import Inventory
from .plans import (
    apply_plan,
    build_catalog_deployment_plan,
    build_mutation_plan,
    load_plan,
    rollback_run,
    save_plan,
)
from .rules import (
    RuleSelector,
    add_condition,
    add_property_value,
    remove_condition,
    remove_property_value,
    set_enabled,
)
from .util import atomic_write_json, read_json, safe_key, timestamp_id

DEFAULT_INVENTORY = Path("config/workspaces.json")
DEFAULT_STATE_DIR = Path(".sentinel-automation")
DEFAULT_CATALOG_DIR = Path("rules")
WEB_DIR = Path(__file__).with_name("web")


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} cannot be empty")
    return value.strip()


def _optional_index(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ConfigurationError("Condition index must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("Condition index must be a positive integer") from exc
    if result < 1:
        raise ConfigurationError("Condition index must be a positive integer")
    return result


def _selector(payload: dict[str, Any]) -> RuleSelector:
    display_name = payload.get("display_name")
    rule_id = payload.get("rule_id")
    if bool(display_name) == bool(rule_id):
        raise ConfigurationError("Select a rule by either display name or rule ID")
    return RuleSelector(
        display_name=_required_string(display_name, "Display name") if display_name else None,
        rule_id=_required_string(rule_id, "Rule ID") if rule_id else None,
    )


@dataclass(frozen=True)
class GuiConfig:
    inventory: Path
    state_dir: Path
    catalog_dir: Path
    auth: str
    api_version: str
    tenant_id: str | None = None


class GuiService:
    """Small adapter that exposes the existing safe plan/apply workflow to the GUI."""

    def __init__(self, config: GuiConfig, client: ArmClient | None = None) -> None:
        self.config = config
        self.client = client
        self.inventory = Inventory.load(config.inventory) if config.inventory.is_file() else None
        self.auth_mode = config.auth
        self.tenant_id = config.tenant_id or (
            self.inventory.managing_tenant_id if self.inventory else None
        )
        self._lock = threading.Lock()
        self._setup_lock = threading.Lock()

    def bootstrap(self) -> dict[str, Any]:
        catalog = load_catalog(self.config.catalog_dir)
        inventory = self.inventory
        client = self.client
        return {
            "authenticated": client is not None,
            "needs_setup": inventory is None,
            "auth_mode": self.auth_mode,
            "managing_tenant_id": self.tenant_id,
            "api_version": client.api_version if client else self.config.api_version,
            "inventory_path": str(self.config.inventory.resolve()),
            "workspaces": [
                {
                    **workspace.to_dict(),
                    "enabled": workspace.enabled,
                    "tags": list(workspace.tags),
                }
                for workspace in inventory.workspaces
            ] if inventory else [],
            "catalog": [
                {
                    "logical_name": rule.logical_name,
                    "rule_id": rule.rule_id,
                    "display_name": rule.display_name,
                }
                for rule in catalog
            ],
            "plans": self._plans(),
            "runs": self._runs(),
        }

    def setup(self, payload: dict[str, Any]) -> dict[str, Any]:
        auth_mode = str(payload.get("auth", self.config.auth))
        if auth_mode not in ("interactive", "cli", "default"):
            raise ConfigurationError("Invalid authentication method")
        tenant_value = payload.get("tenant_id")
        tenant_id = (
            _required_string(tenant_value, "Tenant ID")
            if tenant_value not in (None, "")
            else self.tenant_id
        )

        with self._setup_lock:
            credential = create_credential(auth_mode, tenant_id)
            new_client = ArmClient(credential, api_version=self.config.api_version)
            try:
                new_client.authenticate()
                inventory = self.inventory
                discovery_issues: list[dict[str, str]] = []
                if inventory is None:
                    result = discover_sentinel_workspaces(new_client, tenant_id)
                    if not result.workspaces:
                        raise ConfigurationError(
                            "No Microsoft Sentinel workspaces were found for this account"
                        )
                    document = inventory_document(result, managing_tenant_id=tenant_id)
                    atomic_write_json(self.config.inventory, document)
                    inventory = Inventory.load(self.config.inventory)
                    discovery_issues = [
                        {
                            "subscription_id": issue.subscription_id,
                            "resource_group": issue.resource_group,
                            "workspace_name": issue.workspace_name,
                            "error": issue.error,
                        }
                        for issue in result.issues
                    ]
            except Exception:
                new_client.close()
                raise

            with self._lock:
                old_client = self.client
                self.client = new_client
                self.inventory = inventory
                self.auth_mode = auth_mode
                self.tenant_id = tenant_id
            if old_client is not None:
                old_client.close()

        response = self.bootstrap()
        response["discovery_issues"] = discovery_issues
        return response

    def _require_client(self) -> ArmClient:
        if self.client is None:
            raise ConfigurationError("Connect to Azure first")
        return self.client

    def _require_inventory(self) -> Inventory:
        if self.inventory is None:
            raise ConfigurationError("Discover Sentinel workspaces first")
        return self.inventory

    def list_rules(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._require_client()
        inventory = self._require_inventory()
        targets = _required_string(payload.get("targets", "all"), "Targets")
        workspaces = inventory.select(targets)
        rows: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        with self._lock:
            for workspace in workspaces:
                try:
                    resources = client.list_rules(workspace)
                except Exception as exc:
                    failures.append({"workspace": workspace.key, "error": str(exc)})
                    continue
                for resource in resources:
                    properties = resource.get("properties", {})
                    logic = properties.get("triggeringLogic", {})
                    rows.append(
                        {
                            "workspace": workspace.key,
                            "workspace_name": workspace.display_name,
                            "rule_id": resource.get("name", ""),
                            "display_name": properties.get("displayName", ""),
                            "enabled": logic.get("isEnabled"),
                            "order": properties.get("order"),
                            "triggers_on": logic.get("triggersOn"),
                            "triggers_when": logic.get("triggersWhen"),
                        }
                    )
        rows.sort(
            key=lambda item: (
                str(item["workspace"]).casefold(),
                str(item["display_name"]).casefold(),
            )
        )
        return {"rules": rows, "failures": failures, "workspace_count": len(workspaces)}

    def create_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._require_client()
        inventory = self._require_inventory()
        operation = _required_string(payload.get("operation"), "Operation")
        targets = _required_string(payload.get("targets", "all"), "Targets")
        workspaces = inventory.select(targets)
        skip_missing = bool(payload.get("skip_missing", True))

        if operation == "deploy-catalog":
            expression = _required_string(payload.get("catalog_rules"), "Catalog rules")
            rules = select_catalog_rules(load_catalog(self.config.catalog_dir), expression)
            if_exists = payload.get("if_exists", "fail")
            if if_exists not in ("fail", "skip", "update"):
                raise ConfigurationError("Invalid existing-rule behavior")
            with self._lock:
                plan = build_catalog_deployment_plan(client, workspaces, rules, str(if_exists))
        else:
            selector = _selector(payload)
            condition_index = _optional_index(payload.get("condition_index"))
            parameters: dict[str, Any]
            mutation: Callable[[dict[str, Any]], tuple[dict[str, Any], str]]
            if operation in ("add-title", "remove-title"):
                title = _required_string(payload.get("title"), "Title")
                function = add_property_value if operation == "add-title" else remove_property_value
                mutation = functools.partial(
                    function,
                    property_name="IncidentTitle",
                    value=title,
                    condition_index=condition_index,
                )
                parameters = {"title": title, "condition_index": condition_index}
            elif operation == "set-enabled":
                enabled = payload.get("enabled")
                if not isinstance(enabled, bool):
                    raise ConfigurationError("Enabled must be true or false")
                mutation = functools.partial(set_enabled, enabled=enabled)
                parameters = {"enabled": enabled}
            elif operation == "add-condition":
                property_name = _required_string(payload.get("property"), "Property")
                operator = _required_string(payload.get("operator"), "Operator")
                raw_values = payload.get("values")
                if not isinstance(raw_values, list) or not raw_values:
                    raise ConfigurationError("Provide at least one condition value")
                values = [_required_string(value, "Condition value") for value in raw_values]
                mutation = functools.partial(
                    add_condition,
                    property_name=property_name,
                    operator=operator,
                    values=values,
                )
                parameters = {"property": property_name, "operator": operator, "values": values}
            elif operation == "remove-condition":
                property_name = _required_string(payload.get("property"), "Property")
                operator_value = payload.get("operator")
                operator = _required_string(operator_value, "Operator") if operator_value else None
                mutation = functools.partial(
                    remove_condition,
                    property_name=property_name,
                    operator=operator,
                    condition_index=condition_index,
                )
                parameters = {
                    "property": property_name,
                    "operator": operator,
                    "condition_index": condition_index,
                }
            else:
                raise ConfigurationError(f"Unsupported GUI plan operation: {operation}")
            parameters["skip_missing"] = skip_missing
            with self._lock:
                plan = build_mutation_plan(
                    client,
                    workspaces,
                    selector,
                    operation,
                    parameters,
                    mutation,
                    skip_missing=skip_missing,
                )

        filename = f"{timestamp_id()}-{operation}-{secrets.token_hex(3)}.json"
        path = self.config.state_dir / "plans" / filename
        sealed = save_plan(plan, path)
        return {"plan": sealed, "plan_file": filename}

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._require_client()
        inventory = self._require_inventory()
        if payload.get("confirmation") != "APPLY":
            raise ConfigurationError("Type APPLY to confirm this Azure change")
        plan_path = self._plan_path(payload.get("plan_file"))
        plan = load_plan(plan_path)
        _validate_plan_scope(plan, inventory)
        with self._lock:
            run_id, report = apply_plan(client, plan, self.config.state_dir)
        return {"run_id": run_id, "report": report}

    def rollback(self, payload: dict[str, Any]) -> dict[str, Any]:
        client = self._require_client()
        if payload.get("confirmation") != "ROLLBACK":
            raise ConfigurationError("Type ROLLBACK to confirm this Azure change")
        run_id = _required_string(payload.get("run_id"), "Run ID")
        if safe_key(run_id) != run_id:
            raise ConfigurationError("Invalid run ID")
        manifest = read_json(self.config.state_dir / "backups" / run_id / "manifest.json")
        api_version = manifest.get("api_version") if isinstance(manifest, dict) else None
        if api_version != client.api_version:
            raise ConfigurationError(
                f"Run uses API {api_version}, but this session uses {client.api_version}"
            )
        with self._lock:
            report = rollback_run(
                client, self.config.state_dir, run_id, bool(payload.get("force", False))
            )
        return {"report": report}

    def _plan_path(self, value: Any) -> Path:
        filename = _required_string(value, "Plan file")
        if Path(filename).name != filename or not filename.endswith(".json"):
            raise ConfigurationError("Invalid plan filename")
        path = self.config.state_dir / "plans" / filename
        if not path.is_file():
            raise ConfigurationError(f"Plan does not exist: {filename}")
        return path

    def _plans(self) -> list[dict[str, Any]]:
        directory = self.config.state_dir / "plans"
        plans: list[dict[str, Any]] = []
        if not directory.is_dir():
            return plans
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                plan = load_plan(path)
                counts: dict[str, int] = {}
                for target in plan["targets"]:
                    status = str(target.get("status", "unknown"))
                    counts[status] = counts.get(status, 0) + 1
                plans.append(
                    {
                        "file": path.name,
                        "operation": plan.get("operation"),
                        "created_at": plan.get("created_at"),
                        "targets": len(plan["targets"]),
                        "counts": counts,
                        "integrity": plan.get("integrity"),
                    }
                )
            except SentinelAutomationError:
                continue
        return plans[:50]

    def _runs(self) -> list[dict[str, Any]]:
        directory = self.config.state_dir / "backups"
        runs: list[dict[str, Any]] = []
        if not directory.is_dir():
            return runs
        for path in sorted(directory.glob("*/manifest.json"), reverse=True):
            try:
                manifest = read_json(path)
            except SentinelAutomationError:
                continue
            if not isinstance(manifest, dict):
                continue
            results = manifest.get("results", [])
            runs.append(
                {
                    "run_id": manifest.get("run_id", path.parent.name),
                    "operation": manifest.get("operation"),
                    "started_at": manifest.get("started_at"),
                    "completed_at": manifest.get("completed_at"),
                    "successful": manifest.get("successful"),
                    "results": len(results) if isinstance(results, list) else 0,
                }
            )
        return runs[:50]

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


class GuiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: GuiService, csrf_token: str) -> None:
        self.service = service
        self.csrf_token = csrf_token
        super().__init__(address, GuiRequestHandler)


class GuiRequestHandler(BaseHTTPRequestHandler):
    server: GuiServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if not self._valid_host():
            return
        path = urlsplit(self.path).path
        if path == "/api/bootstrap":
            self._api(self.server.service.bootstrap)
            return
        static = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
        filename = static.get(path)
        if filename is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        content = (WEB_DIR / filename).read_bytes()
        if filename == "index.html":
            content = content.replace(b"__CSRF_TOKEN__", self.server.csrf_token.encode("ascii"))
        content_type = (
            "text/html; charset=utf-8"
            if filename.endswith(".html")
            else "text/css; charset=utf-8"
            if filename.endswith(".css")
            else "text/javascript; charset=utf-8"
        )
        self._send(HTTPStatus.OK, content, content_type)

    def do_POST(self) -> None:
        if not self._valid_host():
            return
        if self.headers.get("X-CSRF-Token") != self.server.csrf_token:
            self.close_connection = True
            self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local session token"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 1_048_576:
            self.close_connection = True
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Request is too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be JSON"})
            return
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Request body must be an object"})
            return
        routes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "/api/setup": self.server.service.setup,
            "/api/rules": self.server.service.list_rules,
            "/api/plans": self.server.service.create_plan,
            "/api/apply": self.server.service.apply,
            "/api/rollback": self.server.service.rollback,
        }
        action = routes.get(urlsplit(self.path).path)
        if action is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return
        self._api(lambda: action(payload))

    def _api(self, action: Callable[[], dict[str, Any]]) -> None:
        try:
            self._json(HTTPStatus.OK, action())
        except SentinelAutomationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "Unexpected local server failure; check the terminal for details"},
            )
            self.log_error("Unexpected GUI failure: %s", exc)

    def _json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        self._send(
            status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json"
        )

    def _send(self, status: HTTPStatus, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(content)

    def _valid_host(self) -> bool:
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "").casefold() not in allowed:
            self.close_connection = True
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid local host"})
            return False
        return True

    def log_message(self, format: str, *args: Any) -> None:
        print(f"GUI {self.address_string()} - {format % args}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel-auto-gui",
        description="Authenticate and launch the Sentinel Automation Rules Manager locally.",
    )
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG_DIR)
    parser.add_argument("--auth", choices=("interactive", "cli", "default"), default="interactive")
    parser.add_argument(
        "--tenant-id",
        help="Optional managing tenant ID to prefill on the first-run setup screen",
    )
    parser.add_argument("--api-version", default=DEFAULT_API_VERSION)
    parser.add_argument("--port", type=int, default=0, help="Local port; 0 chooses a free port")
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open the browser automatically"
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if not 0 <= args.port <= 65535:
        raise ConfigurationError("Port must be between 0 and 65535")
    service = GuiService(
        GuiConfig(
            args.inventory,
            args.state_dir,
            args.catalog_dir,
            args.auth,
            args.api_version,
            args.tenant_id,
        ),
    )
    try:
        server = GuiServer(("127.0.0.1", args.port), service, secrets.token_urlsafe(32))
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Sentinel Automation Rules Manager: {url}")
        print("Complete Azure sign-in in the local GUI.")
        print("Press Ctrl+C to stop the local GUI.")
        if not args.no_browser:
            threading.Timer(0.2, webbrowser.open, args=(url,)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping local GUI.")
        finally:
            server.server_close()
    finally:
        service.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except SentinelAutomationError as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
