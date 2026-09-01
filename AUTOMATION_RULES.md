# Sentinel Automation Rules Manager

This repository contains a production-oriented CLI for modifying and deploying Microsoft
Sentinel automation rules across subscriptions and tenants delegated through Azure Lighthouse.
It is separate from the original analytics-rules script.

The default authentication mode opens a Microsoft sign-in page and uses your own Azure account.
A service principal is not required. Your account must have permission to read and update
automation rules in every selected workspace. For cross-tenant workspaces, those permissions are
normally delegated through Azure Lighthouse.

## Safety model

Azure exposes automation-rule updates as complete-resource `PUT` operations. The CLI therefore
never sends a small unverified edit directly. It uses two explicit stages:

1. `plan` retrieves each live rule, applies the requested change in memory, and writes a
   checksum-protected plan. Azure is not modified.
2. `apply` retrieves each rule again, rejects concurrent changes, creates a local backup, performs
   the update, retrieves the rule again, and verifies the result.

Changes are idempotent. Adding an existing title or condition produces `no_change`, not a
duplicate. One customer failing does not prevent the remaining selected customers from being
processed, and the final run report identifies every success and failure.

Plans and backups are stored under `.sentinel-automation/`, which is excluded from Git. A plan
checksum detects accidental edits; it is not a digital signature. Only apply plans from trusted
sources.

## Installation

Python 3.10 or newer is required.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

After installation, either command form works:

```powershell
sentinel-auto --help
python -m sentinel_automation --help
```

## Automatic workspace discovery

Sign in and preview every accessible Microsoft Sentinel workspace, including workspaces in
subscriptions delegated through Azure Lighthouse:

```powershell
sentinel-auto discover
```

Discovery uses tenant-scope Azure Resource Graph to find Log Analytics workspaces, then checks the
Sentinel onboarding state of every candidate. Ordinary Log Analytics workspaces are not added.
Discovery is read-only and never modifies an automation rule.

Save the results:

```powershell
sentinel-auto discover --save
```

The command displays the results before asking for confirmation. Existing inventory entries are
merged safely:

- Existing keys, display names, enabled/disabled settings, and tags are preserved.
- Newly discovered workspaces are added.
- Existing entries that are temporarily inaccessible or no longer returned are preserved.
- Generated key collisions are resolved automatically.
- Workspaces in a tenant different from the configured managing tenant receive a `lighthouse` tag.

Before overwriting an existing inventory, the previous JSON is backed up under
`.sentinel-automation/inventory-backups/`.

To save without an interactive confirmation in an approved workflow:

```powershell
sentinel-auto discover --save --yes
```

To deliberately rebuild the inventory and remove entries that were not rediscovered:

```powershell
sentinel-auto discover --save --replace
```

`--replace` is intentionally explicit. Review the preview first, especially if some customer
delegations or permissions may be temporarily unavailable.

Discovery can be limited to selected subscriptions:

```powershell
sentinel-auto discover `
  --subscription 11111111-1111-1111-1111-111111111111 `
  --subscription 22222222-2222-2222-2222-222222222222
```

The managing tenant ID is optional. Supplying it makes the login tenant explicit and enables
accurate `lighthouse` tagging:

```powershell
sentinel-auto discover `
  --tenant-id 00000000-0000-0000-0000-000000000000 `
  --save
```

If `config/workspaces.json` already contains `managing_tenant_id`, discovery uses that value
automatically.

If a workspace is visible through Resource Graph but its Sentinel state cannot be verified,
discovery reports it separately, saves the successfully verified workspaces when requested, and
returns a nonzero exit code so incomplete discovery is visible to scripts.

## Manual workspace inventory

Automatic discovery is recommended. For manual control, copy the example and enter the workspace
details:

```powershell
Copy-Item .\config\workspaces.example.json .\config\workspaces.json
```

Example:

```json
{
  "managing_tenant_id": "00000000-0000-0000-0000-000000000000",
  "workspaces": [
    {
      "key": "customer-a",
      "display_name": "Customer A",
      "subscription_id": "11111111-1111-1111-1111-111111111111",
      "resource_group": "sentinel-rg",
      "workspace_name": "customer-a-sentinel",
      "enabled": true,
      "tags": ["production", "managed"]
    }
  ]
}
```

`key` is the stable CLI name. Tags allow group selection. Disabled workspaces are excluded from
all targeting, including `--targets all`.

The original numeric-key `DataBase.json` format is also accepted with
`--inventory .\DataBase.json`, but the new format is recommended because it records the managing
tenant ID and supports tags.

Validate the inventory without signing in:

```powershell
sentinel-auto list-targets
```

## Authentication

Verify interactive authentication:

```powershell
sentinel-auto login
```

The browser token cache is protected using the operating system's secure persistence mechanism.
The following optional modes are also available:

```powershell
sentinel-auto --auth cli login
sentinel-auto --auth default login
```

`cli` uses an existing Azure CLI login. `default` uses Azure Identity's default credential chain
and is useful if automation with workload identity is added later. Interactive authentication is
the default and requires no service principal.

## Inspect existing automation rules

```powershell
sentinel-auto list-rules --targets all
sentinel-auto list-rules --targets customer-a,customer-b
sentinel-auto list-rules --targets production
```

## Add an incident title to existing rules

Rule IDs may differ between customer workspaces. Exact display-name discovery handles that case:

```powershell
sentinel-auto plan add-title `
  --display-name "Close Known Benign Incidents" `
  --title "New Incident Title" `
  --targets all `
  --skip-missing
```

`--skip-missing` is optional. When present, a workspace that does not contain the selected rule is
recorded as `skipped` in the plan instead of stopping the entire command. It does not hide permission
errors, ambiguous duplicate names, invalid rule content, or Azure request failures.

Review the table printed by the command and then apply the generated path:

```powershell
sentinel-auto apply --plan .\.sentinel-automation\plans\PLAN_FILE.json
```

Apply requires typing `yes`. In an approved non-interactive workflow, use `--yes`.

If a rule contains multiple `IncidentTitle` conditions, planning stops instead of guessing. Review
the condition ordering and explicitly select the 1-based match:

```powershell
sentinel-auto plan add-title `
  --display-name "Close Known Benign Incidents" `
  --title "New Incident Title" `
  --condition-index 2 `
  --targets customer-a
```

Remove a title in the same controlled manner:

```powershell
sentinel-auto plan remove-title `
  --display-name "Close Known Benign Incidents" `
  --title "Old Incident Title" `
  --targets all
```

The CLI refuses to remove the final value from a property condition. Use `remove-condition` when
the whole condition should be removed.

## Add or remove a condition

Add a top-level property condition:

```powershell
sentinel-auto plan add-condition `
  --display-name "Close Known Benign Incidents" `
  --property IncidentSeverity `
  --operator Equals `
  --value Informational `
  --targets customer-a,customer-b
```

Repeat `--value` for a condition containing multiple values.

Remove a matching condition:

```powershell
sentinel-auto plan remove-condition `
  --display-name "Close Known Benign Incidents" `
  --property IncidentSeverity `
  --operator Equals `
  --targets customer-a,customer-b
```

Ambiguous matches require `--condition-index`. Sentinel currently permits at most 50 automation
rule conditions; the CLI enforces that limit before deployment.

## Enable or disable an existing rule

```powershell
sentinel-auto plan set-enabled `
  --display-name "Close Known Benign Incidents" `
  --disabled `
  --targets customer-a
```

Use `--enabled` to enable it.

## Export existing rules into Git

Exporting each customer separately is useful during the initial migration because it preserves
the customer's current values and rule ID:

```powershell
sentinel-auto export `
  --display-name "Close Known Benign Incidents" `
  --targets all `
  --output .\catalog\automated-rule-closure\existing
```

Review these files before committing them. Rule definitions can contain email addresses, object
IDs, Logic App resource IDs, or other customer-specific information.

## Deploy a new automation rule

For Git-managed base rules, store normalized JSON definitions under `rules/`:

```powershell
sentinel-auto catalog add `
  --name "known-benign-closure" `
  --file "C:\Exports\known-benign-closure.json"

sentinel-auto catalog validate
```

For an ARM template containing multiple automation rules:

```powershell
sentinel-auto catalog import-bundle `
  --file "C:\Exports\automation-rules.json"
```

The bundle is split into normalized per-rule catalog files atomically.

Create one plan for every catalog rule across the selected workspaces:

```powershell
sentinel-auto plan deploy-catalog `
  --rules all `
  --targets all `
  --if-exists skip
```

Use a comma-separated `--rules` value to deploy only selected logical names. Catalog deployments
use the same plan integrity, concurrency checks, backups, verification, reports, and rollback as
single-rule deployments.

The CLI accepts either a Sentinel-exported ARM JSON template or a catalog JSON object containing
`rule_id` and `properties`. Your exported file can be used directly:

```powershell
sentinel-auto plan deploy `
  --file "C:\Exports\close-known-benign-incidents.json" `
  --targets all
```

For new deployments, the same UUID is intentionally used in every workspace. If a UUID cannot be
read from the file, supply one with `--rule-id`.

The default `--if-exists fail` prevents accidental replacement. Alternatives are:

- `--if-exists skip`: leave existing rules untouched.
- `--if-exists update`: deliberately replace the complete existing rule with the file's version.

Use targeted commands such as `add-title` for routine modifications; they preserve unrelated live
content. Use `--if-exists update` only when full replacement is intended.

## Rollback

Every apply prints a run ID. Restore updated rules and delete rules created by that run with:

```powershell
sentinel-auto rollback --run 20260827T120000Z-ab12cd34
```

Rollback refuses to overwrite changes made after the original deployment. `--force` exists for a
reviewed emergency restoration and should be used carefully.

## Exit codes and operational behavior

- `0`: command completed successfully.
- `2`: configuration, validation, Azure, partial deployment, or verification failure.
- `130`: cancelled with Ctrl+C.

Azure throttling, request timeouts, and temporary server failures are retried with bounded
exponential backoff. HTTP failures include the Azure request ID when available. A deployment run
continues after an individual workspace failure and stores its durable report in the backup
manifest.

## Local verification

The test suite does not require Azure credentials:

```powershell
python -m unittest discover -v
```

Before the first broad deployment, use one non-production Sentinel workspace to verify RBAC,
Lighthouse delegation, the selected API version, and the exact condition structure used by your
environment. Then generate a fresh plan for the production workspaces.
