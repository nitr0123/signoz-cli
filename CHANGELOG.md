# Changelog

## Unreleased

### Fixed

- `auth verify` now honours the `--profile` flag (it was silently dropped, so
  `signoz auth verify -p prod` verified the default profile instead)
- `traces search --all` now paginates correctly. The previous cursor walk never
  sent the cursor back to the server, so it re-fetched the first page and
  returned duplicated rows without advancing; it now walks the offset like
  `logs search --all`
- `dashboards patch` / `alerts patch`: JSON-Patch paths that traverse *into* a
  list element by index (e.g. `/widgets/0/title`) no longer crash with
  "list indices must be integers"
- Masked-token display: a real but short (<=12 char) token no longer renders as
  `<not set>` in `auth whoami` / `profile list` / `profile show`
- `logs tail --severity` help text corrected (exact match, not "minimum")
- `signoz promql --step` is now applied. The client's inline PromQL payload
  accepted the step interval but never sent it; it now uses the shared builder
- `logs tail` no longer reprints the same lines on every poll. Dedup keyed on an
  `id` field that the log query never selects, so nothing was ever deduplicated;
  it now keys on the server id when present and otherwise on
  timestamp+service+body, with insertion-ordered eviction
- Filter values are escaped before interpolation. Service names, severities,
  search text, operations and trace ids containing `'` or `\` (e.g.
  `--search-text "it's broken"`) no longer produce malformed queries

### Changed

- Requests now retry on transient network errors (connection reset, DNS,
  read timeout), not just on HTTP 429/5xx; `Retry-After` HTTP-date values are
  honoured in addition to delta-seconds
- Request timeouts now report a clear, actionable message instead of a raw
  `httpx` exception
- The HTTP client's connection pool is closed on exit (context-manager support
  plus an atexit hook)
- Stricter linting: an explicit ruff rule set (import-sorting, bugbear,
  pyupgrade, blind-except) replaces the previously inert `BLE001` ignore
- The package now ships a `py.typed` marker so downstream consumers get types
- End-to-end CLI tests added for the query commands (logs/traces/metrics,
  JSON/table/CSV, `-o`, error paths, `logs tail` dedup)

- PromQL/SQL request payloads are built by the shared `query.py` builders
  instead of duplicated inline in the client, removing a second source of truth
- `resolve_timestamps` now honours a lone `--start` (window runs to now) or
  lone `--end` (runs back one range) instead of silently ignoring it
- A present-but-unreadable `~/.signoz/config.json` now prints a warning to
  stderr instead of silently resolving to "no config"
- `logs search --all` / `traces search --all` warn on stderr when results are
  truncated at the internal page cap, instead of stopping silently

## v0.3.0 (2026-08-26)

### Added

- `metrics query` - query metric values over time (v5 time series) with
  time/space aggregation, temporality control and group-by; server warnings
  surfaced on stderr
- `signoz promql` and `signoz sql` raw query passthroughs
- `traces detail` - full waterfall spans with attributes/events
- `logs tail` - poll-based follow mode
- Alert rule management: create/update/patch/delete/test (patch ops merged
  client-side)
- Notification channel management (admin), saved-view commands,
  service-account admin commands (`service-accounts list/create/delete/roles`)
- Auto-pagination: `--all` on logs search, traces search, dashboards list,
  alerts history
- Resilience: automatic retry/backoff on 429/5xx honoring Retry-After,
  `--timeout`, `--insecure` (+ SIGNOZ_TIMEOUT / SIGNOZ_INSECURE env vars)
- CSV output format and `-o/--output FILE`
- Release automation: tag pushes build a wheel and attach it to the Forgejo
  release
- mypy type checking in CI; marker-gated live integration test suite

## v0.2.1 (2026-08-25)

### Added

- `auth verify` now reports the server's SigNoz version and whether it is
  within the tested compatibility range (`>=0.136`)
- Supported-range metadata: verified live against SigNoz v0.136.1 and v0.138.0
- Pinned install instructions; release wheel published to Forgejo releases

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
