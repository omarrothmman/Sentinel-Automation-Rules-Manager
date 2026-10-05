import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from sentinel_automation.errors import ConfigurationError
from sentinel_automation.mcp_config import prepare_claude, prepare_codex, write_config


@unittest.skipUnless(
    importlib.util.find_spec("tomllib") or importlib.util.find_spec("tomli"),
    "Install the MCP extra for TOML configuration tests",
)
class McpConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.entry = {
            "command": "C:\\tools\\python.exe",
            "args": [
                "-m",
                "sentinel_automation.mcp_server",
                "--root",
                "C:\\tools\\Sentinel Manager",
            ],
        }

    def test_claude_preserves_existing_servers_and_creates_exact_backup(self):
        path = self.root / "claude.json"
        original = (
            '{"preferences":{"theme":"dark"},"mcpServers":{"logicapp-mcp":{"command":"logic.cmd"}}}'
        )
        path.write_text(original)
        update = prepare_claude(path, self.entry)
        backup = write_config(path, update)
        self.assertEqual(backup.read_text(), original)
        current = json.loads(path.read_text())
        self.assertEqual(current["preferences"], {"theme": "dark"})
        self.assertEqual(current["mcpServers"]["logicapp-mcp"], {"command": "logic.cmd"})
        self.assertEqual(current["mcpServers"]["sentinel-manager"], self.entry)
        self.assertIsNone(prepare_claude(path, self.entry))

    def test_codex_preserves_content_and_is_idempotent(self):
        path = self.root / "config.toml"
        original = '# personal settings\nmodel = "existing-model"\n[mcp_servers.logicapp-mcp]\ncommand = "logic.cmd"\n'
        path.write_text(original)
        updated = prepare_codex(path, self.entry)
        self.assertTrue(updated.startswith(original.rstrip()))
        write_config(path, updated)
        self.assertIsNone(prepare_codex(path, self.entry))

    def test_existing_different_entry_is_not_overwritten(self):
        path = self.root / "claude.json"
        path.write_text('{"mcpServers":{"sentinel-manager":{"command":"different"}}}')
        before = path.read_bytes()
        with self.assertRaises(ConfigurationError):
            prepare_claude(path, self.entry)
        self.assertEqual(path.read_bytes(), before)
