"""Generate or install local desktop MCP entries, preserving existing client settings."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .errors import ConfigurationError
from .util import timestamp_id

SERVER_NAME = "sentinel-manager"


def desktop_entry(root: Path) -> dict[str, Any]:
    root = root.resolve()
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise ConfigurationError(
            f"Project Python is missing: {python}. Create .venv and install .[mcp]."
        )
    return {
        "command": str(python),
        "args": ["-m", "sentinel_automation.mcp_server", "--root", str(root)],
    }


def codex_block(entry: dict[str, Any]) -> str:
    return (
        f"[mcp_servers.{SERVER_NAME}]\ncommand = {json.dumps(entry['command'])}\n"
        f"args = {json.dumps(entry['args'])}\nstartup_timeout_sec = 30\ntool_timeout_sec = 600\n"
    )


def prepare_codex(path: Path, entry: dict[str, Any]) -> str | None:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    parsed = tomllib.loads(text)
    existing = parsed.get("mcp_servers", {}).get(SERVER_NAME)
    if existing is not None:
        if (
            existing.get("command") == entry["command"]
            and existing.get("args", []) == entry["args"]
        ):
            return None
        raise ConfigurationError(f"{SERVER_NAME} is already configured differently in {path}")
    result = text.rstrip() + "\n\n" + codex_block(entry)
    tomllib.loads(result)
    return result


def prepare_claude(path: Path, entry: dict[str, Any]) -> str | None:
    data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise ConfigurationError("Claude configuration must contain an object mcpServers")
    servers = data.setdefault("mcpServers", {})
    if SERVER_NAME in servers:
        if servers[SERVER_NAME] == entry:
            return None
        raise ConfigurationError(f"{SERVER_NAME} is already configured differently in {path}")
    servers[SERVER_NAME] = entry
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_config(path: Path, text: str) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup = path.with_name(
            f"{path.name}.sentinel-backup-{timestamp_id()}-{uuid.uuid4().hex[:6]}"
        )
        backup.write_bytes(path.read_bytes())
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return backup


def claude_config_path() -> Path:
    if os.name == "nt":
        return (
            Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
            / "Claude/claude_desktop_config.json"
        )
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview/install Sentinel MCP desktop configuration"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--client", choices=("codex", "claude", "both"), default="both")
    parser.add_argument(
        "--install",
        action="store_true",
        help="Back up and update configs; default only prints new snippets",
    )
    parser.add_argument("--codex-config", type=Path, default=Path.home() / ".codex/config.toml")
    parser.add_argument("--claude-config", type=Path, default=claude_config_path())
    args = parser.parse_args(argv)
    try:
        entry = desktop_entry(args.root)
        if not args.install:
            if args.client in ("codex", "both"):
                print(codex_block(entry))
            if args.client in ("claude", "both"):
                print(json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2))
            return 0
        pending = []
        if args.client in ("codex", "both"):
            pending.append((args.codex_config, prepare_codex(args.codex_config, entry)))
        if args.client in ("claude", "both"):
            pending.append((args.claude_config, prepare_claude(args.claude_config, entry)))
        for path, text in pending:
            if text is None:
                print(f"Already configured: {path}")
                continue
            backup = write_config(path, text)
            print(f"Configured: {path}")
            if backup:
                print(f"Backup: {backup}")
        print("Restart the selected desktop clients to load sentinel-manager.")
        return 0
    except (ConfigurationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
