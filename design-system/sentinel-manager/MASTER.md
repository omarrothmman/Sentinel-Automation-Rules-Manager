# Sentinel Toolkit UI

Audience: security operators managing rules and watchlist rows across Azure workspaces.
Implementation: existing HTML, CSS, and vanilla JavaScript; no framework migration.

UI UX Pro Max search: `security operations dashboard enterprise`, then
`internal operations dashboard dense`, variance 2, density 8.
Both searches selected Minimalism & Swiss Style. Marketing page patterns were
unsuitable for this application and excluded. The following is the adapted app direction.

- Dark navy navigation, light neutral canvas, white working surfaces.
- Blue for primary actions and selection; green for success, red for failures.
- Segoe UI/system fonts, no remote font or asset dependencies.
- Compact tables with readable 13px data and 12px secondary metadata.
- 8px spacing rhythm, 6px corners, thin borders, minimal shadows.
- Persistent descriptive navigation; explicit current-page state and visible keyboard focus.
- Configure, review, apply remain separate steps. Keep confirmation and rollback controls.
- Overview summarizes actual inventory and history; no illustrative operational metrics.
- Mobile navigation wraps labels; tables scroll within their containers.
- Subtle control-state transitions only; respect reduced-motion preferences.
- Automation rules and watchlists use native details/summary sections per workspace,
  collapsed initially with item counts. Search temporarily opens matching groups;
  clearing it restores manual expansion. Show empty and failed workspace loads separately.

Core tokens live in `sentinel_automation/web/styles.css`.
