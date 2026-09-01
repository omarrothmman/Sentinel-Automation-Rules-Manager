# Security policy

## Reporting a vulnerability

Do not open a public issue for a suspected security vulnerability or accidentally exposed customer
data. Use the repository's **Security** tab to submit a private vulnerability report. If private
reporting is unavailable, contact the repository owner privately before sharing technical details.

Include the affected version, impact, reproduction steps, and any suggested mitigation. Do not
include Azure access tokens, credentials, production plans, backups, inventories, or unredacted
automation-rule exports.

## Operational security

- Review every generated plan before applying it.
- Keep `.sentinel-automation/`, `config/workspaces.json`, and `rules/*.json` out of public Git.
- Treat exported rules as potentially sensitive because actions may contain resource IDs, object
  IDs, and email addresses.
- Use least-privilege Azure RBAC and review Azure Lighthouse delegations regularly.
- Prefer interactive Microsoft Entra authentication for operator-driven use; Conditional Access and
  MFA remain controlled by the tenant.

Security fixes are supported on the latest release line.
