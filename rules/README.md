# Automation rule catalog

This directory stores reviewed Microsoft Sentinel automation-rule definitions as JSON files.

Add a Sentinel-exported rule through the CLI so it is normalized and validated:

```powershell
sentinel-auto catalog add `
  --name "known-benign-closure" `
  --file "C:\Exports\known-benign-closure.json"
```

Each rule receives a stable lowercase logical name and is stored as `<logical-name>.json`.

Import an ARM template containing several automation rules in one operation:

```powershell
sentinel-auto catalog import-bundle `
  --file "C:\Exports\automation-rules.json"
```

Bundle imports are atomic: either every rule passes catalog validation or the catalog is restored
to its previous state.

This public repository ignores `rules/*.json` by default. Real Sentinel exports can contain tenant
IDs, subscription IDs, Logic App resource IDs, Entra object IDs, and email addresses. Review and
sanitize every definition before versioning it. For a private rules repository, remove the ignore
entry only after deciding which environment-specific values belong in the shared base rule.

The disabled file under `../examples/` is safe to use as a catalog-format reference.
