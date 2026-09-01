# Contributing

Contributions should preserve the tool's plan-before-apply safety model and automation-rules-only
scope.

## Local setup

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip ".[dev]"
```

## Before opening a pull request

```powershell
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -v
python -m build --wheel --no-isolation
```

Add or update tests for behavior changes. Azure-facing tests must use fakes or mocks and must not
require credentials, customer access, or a live subscription.

Do not commit generated plans, backups, inventories, tokens, customer identifiers, or operational
rule catalogs. Sanitized examples belong under `examples/` and should use fictional UUIDs and
names. Changes that perform Azure writes must retain explicit planning, confirmation, concurrency
checks, durable backups, verification, and rollback support.
