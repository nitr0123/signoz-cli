# Changelog

## v0.2.0 (2026-08-25)

Targets SigNoz **v0.138+** APIs.

### Added

- Named connection profiles (`signoz configure -p`, `profile list/use/show/remove`,
  `SIGNOZ_PROFILE`) for working with multiple SigNoz instances
- `-p/--profile` accepted at root level and per subcommand
- `--json` shorthand on all query commands
- `auth verify` command validating service-account keys
- Dashboard v2 commands: create/get/update/patch/delete + search DSL
- Alert history v2 commands: timeline (cursor-paginated), history-stats,
  overall-status, top-contributors
- GitHub Actions CI: ruff lint, pytest matrix (3.10-3.12), package build check

### Changed

- Dashboards moved to `/api/v2` Perses schema (v1 endpoints retired in SigNoz 0.135)
- Alert history moved to `/api/v2/rules/{id}/history/*` GET endpoints
  (v1 deprecated in 0.136)
- `metrics list-keys` now backed by v3 autocomplete (v1 route removed by 0.138)
- Query Builder v5 responses flattened into plain row dicts for table/JSON output

### Fixed

- Structured error surfacing (SigNoz error codes surfaced verbatim)
