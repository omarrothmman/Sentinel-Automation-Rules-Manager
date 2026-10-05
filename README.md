# Microsoft Sentinel Automation Rules Manager

[![CI](https://github.com/omarrothmman/Sentinel-Automation-Rules-Manager/actions/workflows/ci.yml/badge.svg)](https://github.com/omarrothmman/Sentinel-Automation-Rules-Manager/actions/workflows/ci.yml)

Manage Microsoft Sentinel **automation rules and watchlist rows** across one or many workspaces, including workspaces delegated through Azure Lighthouse. Use the command line or the optional local browser interface.

The tool separates changes into two steps: `plan` previews and saves a change without writing to Azure; `apply` checks the live rule again, backs it up, writes the change, and verifies it. Applied runs can be rolled back.

## Quick start

Requires Python 3.10+, PowerShell, and an Azure account with access to the workspaces you manage. Interactive sign-in uses your Microsoft account; a service principal is not required.

```powershell
git clone https://github.com/omarrothmman/Sentinel-Automation-Rules-Manager.git
cd Sentinel-Automation-Rules-Manager
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .

sentinel-auto-gui
```

The browser opens a first-run setup screen. Choose **Browser sign-in**, leave the tenant ID blank unless you need to force a specific managing tenant, and select **Sign in and discover workspaces**. The GUI signs in to Azure, discovers accessible Microsoft Sentinel workspaces, and saves the local inventory automatically.

For the CLI workflow instead:

```powershell
sentinel-auto discover --save
sentinel-auto list-targets
sentinel-auto list-rules --targets all
```

On later visits, activate `.venv` again from the repository folder. `discover --save` writes your workspace inventory to `config/workspaces.json`; that file is ignored by Git.

To use an existing Azure CLI sign-in, put `--auth cli` **before** the command: `sentinel-auto --auth cli list-rules --targets all`. The default `--auth interactive` opens Microsoft sign-in. `--auth default` uses the Azure Identity default credential chain.

## Browser interface

```powershell
sentinel-auto-gui
```

The GUI starts only on `127.0.0.1` and opens your browser. On first use, its setup screen handles Azure sign-in and workspace discovery; on later uses, it reuses the local inventory and asks you to sign in. Stop it with `Ctrl+C` in the terminal. It uses the same plan, apply, and rollback engine as the CLI.

| GUI option | Purpose |
| --- | --- |
| `--inventory PATH` | Inventory file; default `config/workspaces.json` |
| `--state-dir PATH` | Plans and backups; default `.sentinel-automation` |
| `--catalog-dir PATH` | Rule catalog; default `rules` |
| `--auth` | `interactive`, `cli`, or `default`; default `interactive` |
| `--tenant-id ID` | Optional managing tenant ID shown on the setup screen |
| `--api-version VERSION` | Azure API version |
| `--port NUMBER` | Local port; `0` (the default) chooses a free port |
| `--no-browser` | Print the local address without opening a browser |
| `--help` | Show GUI launcher help |

## Common workflow

First, create a plan. This example finds a rule by its exact display name and adds an incident title where needed:

```powershell
sentinel-auto plan add-title --targets all --display-name "Close Known Benign Incidents" --title "Known Benign Security Test" --skip-missing
```

The output identifies rules that will change, already have the title, or are missing. It also prints the saved plan path. Review that plan, then apply it:

```powershell
sentinel-auto apply --plan PLAN_FILE.json
```

`apply` asks for confirmation and prints a run ID. To restore successful changes from that run:

```powershell
sentinel-auto rollback --run RUN_ID
```

Planning does not change Azure. `apply` and `rollback` do. `catalog add`, `catalog import-bundle`, and `discover --save` write local files.

## CLI commands

| Command | Purpose |
| --- | --- |
| `sentinel-auto login` | Sign in and verify an Azure token |
| `sentinel-auto discover` | Find accessible Sentinel workspaces |
| `sentinel-auto list-targets` | Show saved workspace keys and tags |
| `sentinel-auto list-rules --targets TARGETS` | List rules in selected workspaces |
| `sentinel-auto export ...` | Export existing rules to JSON files |
| `sentinel-auto catalog list` | List local base rules |
| `sentinel-auto catalog validate` | Validate the local catalog |
| `sentinel-auto catalog add ...` | Add one exported rule to the catalog |
| `sentinel-auto catalog import-bundle ...` | Import rules from an ARM template |
| `sentinel-auto plan add-title ...` | Plan adding an IncidentTitle value |
| `sentinel-auto plan remove-title ...` | Plan removing an IncidentTitle value |
| `sentinel-auto plan add-condition ...` | Plan adding a property condition |
| `sentinel-auto plan remove-condition ...` | Plan removing a property condition |
| `sentinel-auto plan set-enabled ...` | Plan enabling or disabling a rule |
| `sentinel-auto plan deploy ...` | Plan deploying a complete rule from a file |
| `sentinel-auto plan deploy-catalog ...` | Plan deploying catalog rules |
| `sentinel-auto apply --plan PATH` | Apply and verify a saved plan |
| `sentinel-auto rollback --run RUN_ID` | Restore an applied run |

Run `sentinel-auto --help` or add `--help` after any command for its exact syntax, for example `sentinel-auto plan add-title --help`.

## CLI options

**Global options** go between `sentinel-auto` and the command: `sentinel-auto --auth cli discover --save`.

| Option | Purpose |
| --- | --- |
| `--inventory PATH` | Inventory file; default `config/workspaces.json` |
| `--state-dir PATH` | Plans and backups; default `.sentinel-automation` |
| `--catalog-dir PATH` | Rule catalog; default `rules` |
| `--auth` | `interactive`, `cli`, or `default`; default `interactive` |
| `--api-version VERSION` | Azure API version |
| `--debug` | Show a traceback for unexpected errors |
| `--version` | Show the installed version |
| `--help` | Show help |

**Command options** follow the command. Required options are marked **required**.

| Command | Options |
| --- | --- |
| `discover` | `--tenant-id ID`; repeat `--subscription ID` to limit the search; `--save` to merge results into the inventory; `--replace` to remove entries not rediscovered when saving; `--yes` to skip save confirmation |
| `list-rules` | **`--targets TARGETS`**; `--name-contains TEXT` for a literal, case-insensitive name search; `--missing` to show workspaces with no match (requires `--name-contains`) |
| `export` | **`--targets TARGETS`**, **rule selector**, **`--output PATH`** |
| `catalog add` | **`--name NAME`**, **`--file PATH`**; `--rule-id UUID` to override the source ID; `--force` to replace an existing catalog rule |
| `catalog import-bundle` | **`--file PATH`**; `--force` to replace matching catalog rules |
| `plan add-title`, `plan remove-title` | **`--targets TARGETS`**, **rule selector**, **`--title TEXT`**; `--condition-index NUMBER`, `--skip-missing`, `--out PATH` |
| `plan add-condition` | **`--targets TARGETS`**, **rule selector**, **`--property NAME`**, **`--operator NAME`**, **`--value TEXT`** (repeat for multiple values); `--skip-missing`, `--out PATH` |
| `plan remove-condition` | **`--targets TARGETS`**, **rule selector**, **`--property NAME`**; `--operator NAME`, `--condition-index NUMBER`, `--skip-missing`, `--out PATH` |
| `plan set-enabled` | **`--targets TARGETS`**, **rule selector**, **`--enabled` or `--disabled`**; `--skip-missing`, `--out PATH` |
| `plan deploy` | **`--targets TARGETS`**, **`--file PATH`**; `--rule-id UUID`, `--if-exists` (`fail`, `skip`, or `update`), `--out PATH` |
| `plan deploy-catalog` | **`--targets TARGETS`**, **`--rules NAMES`**; `--if-exists` (`fail`, `skip`, or `update`), `--out PATH` |
| `apply` | **`--plan PATH`**; `--yes` to skip confirmation |
| `rollback` | **`--run RUN_ID`**; `--yes` to skip confirmation; `--force` to overwrite changes made after the original run |

`login`, `list-targets`, `catalog list`, and `catalog validate` have no command-specific options.

**Targets:** `--targets customer-a` selects one saved workspace key; `--targets "customer-a,customer-b"` selects several; `--targets production` selects a saved tag; `--targets all` selects every enabled workspace.

**Rule selector:** For existing-rule operations, provide either `--display-name "Exact rule name"` or `--rule-id UUID`. A display name must match exactly (case-insensitively); ambiguous names cause an error. Use a display name when the same logical rule has different IDs across workspaces.

**Planning details:** `--skip-missing` records absent rules as skipped; it does not ignore permission or Azure errors. `--condition-index` is 1-based when several conditions match. `--out PATH` chooses the plan file; otherwise plans go under `.sentinel-automation/plans/`. For catalog deployments, `--rules` accepts comma-separated logical names or `all`. For deployments, `--if-exists` defaults to `fail`; `skip` leaves an existing rule alone, and `update` plans to replace its complete definition.

## Watchlists (GUI and CLI)

Open **Watchlists** in the browser interface, load a workspace's watchlists, and choose **Open**.
Search the row table, select **Edit** or **Delete**, or choose **Add row**. **Import CSV** is available
in the operation selector, and **Export CSV** downloads the current watchlist. Use **Refresh rows**
to see changes applied through another session or the CLI.

In **Target workspaces**, open the picker and check the workspaces to change. Search by name or key,
or select **All workspaces** to select every enabled workspace, including ones hidden by the search.
The selection count includes all selected workspaces. Opening a watchlist starts with just its own
workspace selected; refreshing preserves your selection. Every selected workspace must contain the
same watchlist alias.

Choose **Preview changes** to save a plan and open the separate **Review changes** page. Review each row's **Before / after** table,
then type `APPLY` and apply it. Saved plans from either interface can be reopened with **Review** in
**Plans & runs**. The same history provides rollback for applied watchlist changes.

The top **Refresh** button reloads the current page: live rules, the watchlist list, or the open
watchlist's rows. Refreshing rows preserves your draft edits and search. On **Review changes**, it
reloads the saved preview; Azure is checked again when applying. **Plan a change** is for starting
a new draft, and never displays an unrelated saved preview beside the editor.

CLI examples (global options such as `--auth cli` go before `watchlists`):

```powershell
sentinel-auto watchlists list --targets all
sentinel-auto watchlists items --targets customer-a --alias AllowedIPs
sentinel-auto watchlists export --targets customer-a --alias AllowedIPs --output allowed-ips.csv

sentinel-auto watchlists plan add --targets customer-a --alias AllowedIPs --key-column IP --set IP=10.0.0.1 --set "Description=Office network"
sentinel-auto watchlists plan update --targets "customer-a,customer-b" --alias AllowedIPs --key-column IP --key-value 10.0.0.1 --set "Description=Updated description"
sentinel-auto watchlists plan delete --targets customer-a --alias AllowedIPs --key-column IP --key-value 10.0.0.1

sentinel-auto watchlists import --targets customer-a --alias AllowedIPs --key-column IP --file allowed-ips.csv
sentinel-auto apply --plan PLAN_FILE.json
sentinel-auto rollback --run RUN_ID
```

- Watchlists must already exist. This feature edits their rows; it does not create watchlists or change their alias, search key, or metadata.
- Matching uses an explicit column and exact, case-sensitive values. Blank or duplicate match values are rejected. Matching rows can have different item IDs in different workspaces. For a single workspace, CLI update/delete also accepts `--item-id UUID` instead of a match column/value; the GUI uses the selected row's ID when editing in its original workspace.
- Row updates merge the supplied fields into the existing row. CSV imports replace all values of matching rows, so include every column you want to retain. The watchlist's search key must have a non-empty value.
- CSV import defaults to `--mode merge`: add new rows, update matching rows, keep all other rows. `--mode replace` also plans deletion of rows missing from the CSV. Empty imports are rejected. Import only saves a local plan until you apply it.
- CSV export selects one workspace at a time. GUI imports accept files up to 750 KB; larger imports use the CLI. CSV supports UTF-8, quoted commas, and multiline values.
- Plans recheck the watchlist contents before the first write in each workspace and recheck each affected row before writing. Writes use available ETags and are verified. These are individual Azure requests, not an atomic transaction across rows or workspaces; another writer can still race between requests, subject to Azure's conditional request support.
- Backups and attempted writes are saved in the run manifest before each write. Rollback restores original row values (including deleted rows) and removes newly added rows. It does not restore Azure-generated audit timestamps. Later edits block rollback unless explicitly forced after review. Per-row failures are retained in the manifest; successful changes in other workspaces are not automatically undone.

API reference: [Microsoft Sentinel watchlist items](https://learn.microsoft.com/en-us/rest/api/securityinsights/watchlist-items/create-or-update?view=rest-securityinsights-2025-09-01).

## License

This project is licensed under the [MIT License](LICENSE).

The **Plan a change** page has Automation rule and Watchlist tabs. The Watchlist tab includes source workspace/watchlist selection and a searchable target-workspace checklist. Errors in dialogs and saved runs show a readable message, code, HTTP status, request ID, and additional details when supplied by Azure.
