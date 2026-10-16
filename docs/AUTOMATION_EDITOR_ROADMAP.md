# Automation rule editor

Build incrementally on the shared plan/review/apply/rollback engine. Each phase must
preserve unrelated rule fields, show exact before/after changes, and receive validation
before Azure writes. Existing GUI and MCP work remains compatible.

1. Operation discovery: explicit operation selection, grouped choices and contextual
   help. Existing support covers enabled state, property conditions, incident-title
   values, and catalog deployment. This is the first UI increment.
2. Condition editor: read the selected rule's current definition; show existing
   conditions with targeted edit/remove controls. Add guided property/operator choices,
   analytics-rule scoping, and nested-condition support with validation.
3. Action editor: list current ordered actions; add/configure/remove/reorder Run playbook
   actions. Discover compatible Logic Apps, select a playbook, validate its trigger and
   required parameters, and surface permission errors without granting permissions.
4. Rule settings: name, execution order, enabled state, trigger, expiration, and the
   supported incident actions (status, severity, owner, tags, tasks as applicable).
5. New-rule wizard: basics, trigger, conditions, ordered actions, target scope, review.
   Validate each target and avoid overwriting existing definitions implicitly.
6. Coverage audit: compare against supported Azure API capabilities and trigger limits;
   test unsupported/unknown fields, concurrency, partial failure, backups and rollback.

Analytics-rule selection in an automation condition is separate from creating or
editing an analytics detection rule. Treat the latter as a separate future module.

Reference: https://learn.microsoft.com/en-us/azure/sentinel/create-manage-use-automation-rules
