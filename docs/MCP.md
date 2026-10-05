# Sentinel MCP for Codex and Claude Desktop

The `sentinel-manager` MCP server uses the official Python MCP SDK over stdio. It runs locally,
uses the same Azure identity and inventory as the GUI/CLI, and shares their saved plans and backups.
It does not require an OpenAI or Anthropic API key, expose an HTTP port, or start Azure sign-in at launch.

## Install and connect

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[mcp]"
# Preview client entries without changing any client configuration:
.\.venv\Scripts\python.exe -m sentinel_automation.mcp_config
# Back up existing files and add Sentinel to both clients:
.\.venv\Scripts\python.exe -m sentinel_automation.mcp_config --install --client both
```

Use `--client codex` or `--client claude` for one client. Existing servers and other settings are
preserved. Backups are written beside each config; an existing conflicting Sentinel entry is
rejected instead of overwritten. The installer uses absolute Python and root paths, so it works
regardless of the desktop client's current directory. Restart the client after installation.

The local Windows launcher is `start-mcp.cmd`; it also accepts options such as `--auth cli` or
`--read-only`. Direct launch is available on any supported platform:

```text
sentinel-auto-mcp --root /absolute/path/to/repository --auth interactive
```

Default files under `--root`: `config/workspaces.json`, `rules/`, and `.sentinel-automation/`.
Override them with `--inventory`, `--catalog-dir`, and `--state-dir`. Relative overrides resolve
against `--root`, not the client working directory. Keep the root on a local filesystem you trust.

## Start a conversation

Ask Codex or Claude:

> Use sentinel-manager to check its status, connect to Azure, and list my workspaces.

Then, for example:

> Preview adding IP 192.0.2.10 with Description "Shared test entry" to the AllowedIPs watchlist
> in customer-a and customer-b. Match rows by IP. Show me the affected workspaces before applying.

> Preview disabling the automation rule "Test closure" in customer-a.

> Review the last Sentinel run and show which workspaces failed, including the error codes.

The server starts disconnected. `connect_azure` defaults to interactive browser sign-in with the
same encrypted token cache as the GUI; `cli` reuses Azure CLI login and `default` uses the Azure
Identity credential chain. If there is no inventory, first connection discovers and saves it.
For later discovery use `discover_workspaces`, optionally with `save=true` to merge local inventory.

## Tools and write workflow

| Tools | Purpose |
| --- | --- |
| `sentinel_status`, `connect_azure` | Check configuration and connect |
| `list_workspaces`, `discover_workspaces` | Find and select Sentinel workspaces |
| `list_rules`, `get_rule`, `list_catalog` | Inspect rules and deployment catalog |
| `list_watchlists`, `get_watchlist`, `export_watchlist` | Inspect rows and return CSV |
| `plan_rule_change`, `plan_deploy_rule`, `plan_watchlist_change` | Save previews without writing Azure |
| `list_history`, `review_plan`, `review_run` | Inspect shared plans, changes, results and backups |
| `apply_changes`, `rollback_changes` | Write Azure after review and explicit approval |

Scope is comma-separated workspace keys/tags or explicit `all`, just like the CLI. There is no
implicit all-workspace default. Watchlists must already exist under the same alias. Row updates
merge supplied fields; CSV import replaces matching row values, and `replace` mode additionally
plans removal of rows absent from the CSV. `get_watchlist` pages returned rows with `offset` and
`limit` (maximum 500); it currently reads the full watchlist from Azure for each call.

Plans return a readable before/after summary and structured data. Review the scope and changes,
then approve. `apply_changes` requires `APPLY <full plan integrity hash>` from that exact preview;
an arbitrary `APPLY` string is rejected. This binds the call to a reviewed artifact, but cannot
prove a human approved it: clients must retain their tool approval controls and follow the server
instructions. Read-only/destructive tool annotations describe behavior; they are not authorization.

Rollback requires `ROLLBACK <run ID>`. Overwriting later edits additionally requires `force=true`
and the phrase `ROLLBACK <run ID> FORCE`. The server validates inventory scope, backs up attempted
writes, rechecks live state and verifies results through the existing engine. Per-workspace failures
are returned with `isError=true` while retaining all successful results and the run ID. There is
no transaction across workspaces; rollback must be explicitly requested after partial failures.

`--read-only` omits apply and rollback tools. Local previews and inventory-saving discovery remain
available; no tool in that mode writes Azure resources. No arbitrary shell, URL, or file-write tool
is exposed. CSV export returns text to the client rather than choosing a local destination.

## Verification and troubleshooting

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_mcp tests.test_mcp_config -v
```

Tests launch an actual stdio MCP client/server pair with fake Azure data, discover tool schemas,
exercise plan/apply/rollback, verify approval rejection and read-only tools, and start the installed
server from an unrelated directory. They never require real Azure credentials.

If a server is missing, restart the desktop client and verify the generated absolute Python path.
If a tool reports "Connect to Azure first", call `connect_azure`. Inventory and watchlist errors
contain a readable message and structured error fields. MCP logs go to stderr; stdout is reserved
for the protocol. Codex entries use a 600-second tool timeout for multi-workspace operations.
Live Azure behavior and a desktop application's connection indicator must be checked after sign-in.

References: [Codex MCP configuration](https://developers.openai.com/codex/mcp),
[Claude Desktop local MCP setup](https://modelcontextprotocol.io/docs/develop/connect-local-servers),
[official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x).
