# Microsoft Sentinel Automation Rules Manager

[![CI](https://github.com/omarrothmman/Sentinel-Automation-Rules-Manager/actions/workflows/ci.yml/badge.svg)](https://github.com/omarrothmman/Sentinel-Automation-Rules-Manager/actions/workflows/ci.yml)

A production-focused command-line tool for managing Microsoft Sentinel automation rules across
one or many workspaces, including customer environments delegated through Azure Lighthouse.

The tool modifies rules that already exist, deploys new rules, and keeps every Azure change behind
a reviewable plan. It is designed for repeated operational changes such as adding the same incident
title to a closure rule across dozens of Sentinel workspaces.

## Key capabilities

- Discover accessible Microsoft Sentinel workspaces automatically.
- Work across direct subscriptions and Azure Lighthouse delegations.
- Add or remove incident-title values without replacing existing customer values.
- Add or remove automation-rule conditions.
- Enable or disable existing automation rules.
- Deploy new rules from JSON or a Sentinel-exported ARM template.
- Target one workspace, several workspaces, a tagged group, or every enabled workspace.
- Preview every change before writing to Azure.
- Detect rules that are already correct or missing from selected workspaces.
- Protect changes with integrity checks, backups, post-write verification, and rollback support.

## How changes are handled

The normal workflow has two separate stages:

1. `plan` reads the current rules and creates an integrity-protected JSON plan. It does not modify
   Azure.
2. `apply` reviews the live rule again, creates a backup, performs the planned write, and verifies
   the result.

This makes a plan safe to use as an audit. For example, `plan add-title` reports where a title is
already present, where it is missing, and where the selected rule does not exist. Nothing changes
until the generated plan is explicitly applied.

## Requirements

- Windows PowerShell or PowerShell 7
- Python 3.10 or newer
- An Azure account with permission to read or manage the selected Sentinel automation rules
- Azure Lighthouse delegation for customer subscriptions managed from another tenant

A service principal is not required for interactive use.

## Installation

Open PowerShell in the repository folder:

```powershell
git clone https://github.com/omarrothmman/Sentinel-Automation-Rules-Manager.git
cd Sentinel-Analytic-Rules-Mgm
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Confirm that the CLI is installed:

```powershell
sentinel-auto --help
sentinel-auto --version
```

When returning to the project later, activate the existing environment:

```powershell
cd C:\Users\YourName\Downloads\Sentinel-Analytic-Rules-Mgm
.\.venv\Scripts\Activate.ps1
```

## Authentication

Interactive browser authentication is the default:

```powershell
sentinel-auto login
```

The tool opens Microsoft's sign-in page and receives the Azure token through a temporary localhost
callback. Microsoft Entra ID remains responsible for passwords, MFA, Conditional Access, and
session validation. The tool never receives or stores your password or MFA secret.

Other supported authentication modes are:

```powershell
sentinel-auto --auth cli login
sentinel-auto --auth default login
```

`cli` uses an existing Azure CLI session. `default` uses the Azure Identity default credential
chain. Global options such as `--auth` must appear before the command name.

## Discover Sentinel workspaces

Preview all accessible Sentinel workspaces:

```powershell
sentinel-auto discover
```

Save the discovered workspaces to `config/workspaces.json`:

```powershell
sentinel-auto discover --save
```

Useful discovery options:

```powershell
# Save without an interactive confirmation
sentinel-auto discover --save --yes

# Search only one subscription
sentinel-auto discover --subscription "00000000-0000-0000-0000-000000000000"

# Search several subscriptions
sentinel-auto discover `
  --subscription "00000000-0000-0000-0000-000000000000" `
  --subscription "11111111-1111-1111-1111-111111111111"

# Explicitly select the managing tenant
sentinel-auto discover `
  --tenant-id "22222222-2222-2222-2222-222222222222" `
  --save
```

Discovery merges new results into the existing inventory by default. To replace the inventory and
remove entries that are no longer discovered, use:

```powershell
sentinel-auto discover --save --replace
```

Review `--replace` carefully because it intentionally removes stale inventory entries. The previous
inventory is backed up before a saved update.

## Target workspaces

List the saved workspace keys:

```powershell
sentinel-auto list-targets
```

Commands that accept `--targets` can select:

```powershell
# One workspace key
--targets customer-a

# Several workspace keys
--targets "customer-a,customer-b,customer-c"

# A tag defined in the inventory
--targets production

# Every enabled workspace
--targets all
```

## List automation rules

```powershell
# All enabled workspaces
sentinel-auto list-rules --targets all

# One workspace
sentinel-auto list-rules --targets customer-a

# Selected workspaces
sentinel-auto list-rules --targets "customer-a,customer-b"
```

## Check or add an incident title

The following command checks every selected Sentinel for the title and creates a plan to add it
where it is missing:

```powershell
sentinel-auto plan add-title `
  --display-name "Close Known Benign Incidents" `
  --title "Known Benign Security Test" `
  --targets all `
  --skip-missing
```

The plan uses these statuses:

- `ALREADY PRESENT`: the title is already configured; no change is needed.
- `WILL CHANGE`: the rule exists and the title would be added.
- `NOT FOUND`: the selected automation rule does not exist in that workspace.

If the goal is only to check availability, stop after planning. Do not run `apply`.

To target selected workspaces:

```powershell
sentinel-auto plan add-title `
  --display-name "Close Known Benign Incidents" `
  --title "Known Benign Security Test" `
  --targets "customer-a,customer-b" `
  --skip-missing
```

If a rule contains multiple matching `IncidentTitle` conditions, select the intended condition by
its one-based position:

```powershell
sentinel-auto plan add-title `
  --display-name "Close Known Benign Incidents" `
  --title "Known Benign Security Test" `
  --condition-index 2 `
  --targets customer-a
```

## Remove an incident title

```powershell
sentinel-auto plan remove-title `
  --display-name "Close Known Benign Incidents" `
  --title "Retired Security Test" `
  --targets all `
  --skip-missing
```

The operation refuses to remove the final value from an `IncidentTitle` condition. Remove the
condition itself when that is the intended result.

## Add a condition

Repeat `--value` to place several values in the same property condition:

```powershell
sentinel-auto plan add-condition `
  --display-name "Close Known Benign Incidents" `
  --property "IncidentTitle" `
  --operator "Contains" `
  --value "Known Benign Security Test" `
  --value "Approved Vulnerability Scan" `
  --targets all `
  --skip-missing
```

## Remove a condition

```powershell
sentinel-auto plan remove-condition `
  --display-name "Close Known Benign Incidents" `
  --property "IncidentTitle" `
  --operator "Contains" `
  --targets all `
  --skip-missing
```

Use `--condition-index 2` when more than one condition matches the supplied property and operator.

## Enable or disable a rule

Enable the selected rule:

```powershell
sentinel-auto plan set-enabled `
  --display-name "Close Known Benign Incidents" `
  --enabled `
  --targets all `
  --skip-missing
```

Disable the selected rule:

```powershell
sentinel-auto plan set-enabled `
  --display-name "Close Known Benign Incidents" `
  --disabled `
  --targets all `
  --skip-missing
```

## Select a rule by ID

Existing-rule operations accept either `--display-name` or `--rule-id`, but not both:

```powershell
sentinel-auto plan add-title `
  --rule-id "33333333-3333-3333-3333-333333333333" `
  --title "Known Benign Security Test" `
  --targets customer-a
```

Use `--display-name` when logically equivalent rules have different IDs across customers. Matching
is exact and case-insensitive. The command stops if several rules have the same display name rather
than guessing which one to modify.

## Export existing rules

Export a rule from selected workspaces into Git-friendly JSON files:

```powershell
sentinel-auto export `
  --display-name "Close Known Benign Incidents" `
  --targets "customer-a,customer-b" `
  --output .\exports\close-known-benign-incidents
```

Export by UUID when the same ID is used across all selected workspaces:

```powershell
sentinel-auto export `
  --rule-id "33333333-3333-3333-3333-333333333333" `
  --targets customer-a `
  --output .\exports\close-known-benign-incidents
```

## Deploy a new automation rule

### Git-managed base-rule catalog

Store reusable base automation rules in the local `rules/` directory. Importing through the CLI
normalizes a Sentinel export, assigns it a stable logical name, and validates the entire catalog:

```powershell
sentinel-auto catalog add `
  --name "known-benign-closure" `
  --file "C:\Exports\known-benign-closure.json"
```

If one ARM template contains several automation rules, import the whole bundle atomically:

```powershell
sentinel-auto catalog import-bundle `
  --file "C:\Exports\automation-rules.json"
```

Every rule is written to its own Git-friendly JSON file. If any resource is invalid or conflicts
with the existing catalog, none of the bundle changes are kept. Use `--force` only when intentionally
replacing matching catalog definitions.

Override the exported UUID when required:

```powershell
sentinel-auto catalog add `
  --name "known-benign-closure" `
  --file "C:\Exports\known-benign-closure.json" `
  --rule-id "33333333-3333-3333-3333-333333333333"
```

List or validate the stored base rules without connecting to Azure:

```powershell
sentinel-auto catalog list
sentinel-auto catalog validate
```

Deploy every catalog rule through one reviewable plan:

```powershell
sentinel-auto plan deploy-catalog `
  --rules all `
  --targets all `
  --if-exists skip
```

Deploy selected base rules to selected Sentinels:

```powershell
sentinel-auto plan deploy-catalog `
  --rules "known-benign-closure,high-severity-assignment" `
  --targets "customer-a,customer-b" `
  --if-exists skip
```

The catalog rejects invalid JSON, duplicate logical names, duplicate rule UUIDs, duplicate display
names, and unintentional overwrites. Use `catalog add --force` only when intentionally replacing an
existing base definition. The replacement is transactional: if full-catalog validation fails, the
previous file is restored.

Catalog deployments use the same integrity checks, backups, verification, reports, and rollback as
single-rule deployments. Because this repository is public, `rules/*.json` is ignored by default to
prevent accidental publication of tenant IDs, resource IDs, object IDs, or email addresses. A safe
example is available at `examples/known-benign-closure.json`.

For a private catalog repository, review each normalized rule, remove customer-specific values that
should be overlays, then remove the `rules/*.json` entry from `.gitignore`. Commit and review those
definitions through pull requests. Never force-add an unreviewed Sentinel export to a public repo.

### Deploy directly from one file

Create a deployment plan from a catalog JSON file or a Sentinel-exported ARM template:

```powershell
sentinel-auto plan deploy `
  --file .\rules\close-known-benign-incidents.json `
  --targets all
```

Override the rule UUID from the file when necessary:

```powershell
sentinel-auto plan deploy `
  --file .\rules\close-known-benign-incidents.json `
  --rule-id "33333333-3333-3333-3333-333333333333" `
  --targets all
```

Control how an existing rule with the same UUID is handled:

- `--if-exists fail`: stop if different content already exists. This is the default.
- `--if-exists skip`: leave the existing rule unchanged.
- `--if-exists update`: plan a complete replacement with the supplied file.

Example:

```powershell
sentinel-auto plan deploy `
  --file .\rules\close-known-benign-incidents.json `
  --targets all `
  --if-exists skip
```

Targeted operations such as `add-title` are preferable for routine updates because they preserve
unrelated live configuration. Use deployment updates when the file is intentionally the complete
desired rule definition.

## Save a plan to a chosen path

Without `--out`, plans are written automatically under `.sentinel-automation/plans/`.

```powershell
sentinel-auto plan add-title `
  --display-name "Close Known Benign Incidents" `
  --title "Known Benign Security Test" `
  --targets all `
  --skip-missing `
  --out .\plans\add-known-benign-title.json
```

The console shows a compact summary. The plan JSON retains the complete before-and-after details
and an integrity hash.

All list and result commands use numbered, terminal-width-aware tables. On a narrow terminal, the
same information automatically switches to a card layout so values are not lost to wrapping.

## Apply a plan

After reviewing the summary and generated JSON file:

```powershell
sentinel-auto apply `
  --plan .\.sentinel-automation\plans\PLAN_FILE.json
```

For a plan created in the default state directory, the filename alone also works:

```powershell
sentinel-auto apply --plan PLAN_FILE.json
```

The command asks for confirmation before changing Azure. For an approved non-interactive workflow:

```powershell
sentinel-auto apply `
  --plan .\.sentinel-automation\plans\PLAN_FILE.json `
  --yes
```

Before each write, the tool confirms that the live rule still matches the planned version. It then
backs up the rule, performs the update with Azure concurrency protection, and verifies the final
content. A run ID is printed when processing finishes.

## Roll back an applied run

Use the run ID printed by `apply`:

```powershell
sentinel-auto rollback --run "RUN_ID"
```

For an approved non-interactive rollback:

```powershell
sentinel-auto rollback --run "RUN_ID" --yes
```

If a rule was changed after the original run, rollback stops to protect the newer work. Override
that protection only after reviewing the later changes:

```powershell
sentinel-auto rollback `
  --run "RUN_ID" `
  --force
```

## Command reference

```text
sentinel-auto login
sentinel-auto --version
sentinel-auto discover
sentinel-auto list-targets
sentinel-auto list-rules
sentinel-auto export
sentinel-auto catalog list
sentinel-auto catalog validate
sentinel-auto catalog add
sentinel-auto catalog import-bundle
sentinel-auto plan add-title
sentinel-auto plan remove-title
sentinel-auto plan add-condition
sentinel-auto plan remove-condition
sentinel-auto plan set-enabled
sentinel-auto plan deploy
sentinel-auto plan deploy-catalog
sentinel-auto apply
sentinel-auto rollback
```

Display help for the complete CLI or a specific operation:

```powershell
sentinel-auto --help
sentinel-auto discover --help
sentinel-auto plan --help
sentinel-auto plan add-title --help
```

## Global options

Global options appear between `sentinel-auto` and the command:

```text
--inventory PATH
--state-dir PATH
--catalog-dir PATH
--auth interactive|cli|default
--api-version VERSION
--debug
--version
```

Examples:

```powershell
sentinel-auto `
  --inventory C:\SentinelAutomation\workspaces.json `
  list-targets

sentinel-auto `
  --state-dir C:\SentinelAutomation\state `
  list-rules --targets all

sentinel-auto --debug list-rules --targets customer-a
```

Most users should keep the default API version and enable `--debug` only while troubleshooting.

## Repository data and safety

- `config/workspaces.json` contains the local workspace inventory and is excluded from Git.
- `config/workspaces.example.json` is a safe inventory template for the repository.
- `rules/*.json` contains the local operational catalog and is excluded from this public repository.
- `examples/known-benign-closure.json` is a sanitized, disabled sample definition.
- `.sentinel-automation/plans/` contains generated plans.
- `.sentinel-automation/backups/` contains pre-change backups and run manifests.
- `.sentinel-automation/inventory-backups/` contains previous inventory versions.
- A private repository can opt in to Git-managed `rules/*.json` after reviewing every external
  reference.

Do not commit access tokens, customer identifiers, generated state, or production inventory files.

## Failure behavior

- `--skip-missing` skips only workspaces where the selected rule is absent.
- Permission failures, Azure request failures, ambiguous display names, and invalid rules remain
  errors.
- An already-present title produces `ALREADY PRESENT` and no write.
- A rule changed after planning is rejected and must be replanned.
- Successful writes in a partially failed run remain recorded and can be rolled back using the run
  ID.

## Development verification

```powershell
python -m unittest discover -v
python -m ruff check .
python -m ruff format --check .
python -m pip check
python -m build --wheel --no-isolation
```

Additional implementation details are available in [AUTOMATION_RULES.md](AUTOMATION_RULES.md).
Contribution and vulnerability-reporting guidance is available in
[CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
