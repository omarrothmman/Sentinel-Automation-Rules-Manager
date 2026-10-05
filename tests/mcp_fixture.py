"""Subprocess fixture for real stdio MCP protocol tests; never uses Azure credentials."""

import sys
from pathlib import Path

from sentinel_automation.gui import GuiConfig, GuiService
from sentinel_automation.mcp_server import create_server

from .helpers import workspace
from .test_watchlists import WatchlistClient


def main():
    root = Path(sys.argv[1])
    client = WatchlistClient()
    client.seed(workspace())
    service = GuiService(
        GuiConfig(
            root / "inventory.json", root / "state", root / "rules", "cli", client.api_version
        ),
        client,
    )
    create_server(service, "--read-only" in sys.argv).run(transport="stdio")


if __name__ == "__main__":
    main()
