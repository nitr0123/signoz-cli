# Changelog

## Unreleased

### Added

- Encrypted bundles: `export --encrypt` packs the bundle into a single
  `<dir>.enc` file (Fernet + PBKDF2, passphrase via prompt or
  `SIGNOZ_BUNDLE_PASSPHRASE`), readable with `import --decrypt` —
  safe to commit to git.
- Grafana import: `dashboards convert grafana.json --out signoz.json`
  (PromQL/SQL targets, text/interval variables; unsupported panels and
  targets reported, never silently dropped; output passes `lint`).

### Changed

- The project now lives on GitHub: https://github.com/nitr0123/signoz-cli.
  `signoz update`, `install.sh` and the README install from GitHub over
  HTTPS (no SSH or registry token needed); releases are GitHub Releases.
  `SIGNOZ_PYPI_INDEX` still works for installs from a custom package index.
- CI also tests Python 3.13 and 3.14; Actions are pinned to commit SHAs
  and kept current by Dependabot.
- Requires click 8.3 or newer: the previous `>=8.1` floor was never
  actually compatible. CI now tests every dependency at its declared floor.
- A version hand-bumped in a PR (minor/major release) is tagged as-is
  instead of being overwritten by the automatic patch bump.
- Releases can also be published to PyPI (trusted publishing, opt-in).

## v0.5.43 (2026-10-08)

### Fixed

- v5 time-series results whose label keys arrive as objects (rather than
  plain strings) are read correctly instead of producing empty labels.

## v0.5.42 (2026-09-29)

### Added

- SigNoz 0.140+ AI observability: `ai fields`, `ai field-values`,
  `ai pricing-rules list/get/create/delete`, `ai unmapped-models`.
- Notification channels **v2** (SigNoz 0.141+): `channels
  list/get/create/update/delete --api v2` (paginated, searchable,
  kind-filtered) plus `channels repair ID [--apply]` (dry-run by
  default). v1 remains the default and works on all servers.
- `dashboards diff-remote` / `alerts diff-remote` compare resources across
  two SigNoz instances.

### Changed

- Dashboard `lint` no longer flags 0.142+ text/static panels for missing
  `queries`.
- Internal refactors: `inventory.py` and `history.py` split out of the large
  command modules; one shared time-series output pipeline.
- Dependency floors refreshed (PyYAML).

## v0.5.28 – v0.5.41 (2026-09-21 – 2026-09-23)

### Added

- MCP server (`signoz mcp`) built on the official SDK: stdio transport,
  read-only by default.
- ASCII charts (`--chart`) and a `watch` command.
- Trace waterfall view and week-over-week `compare`.
- Offline `dashboards lint` / `alerts lint`, suitable for CI.
- `doctor` diagnostics, `channels test` notifications, a metrics
  cardinality explorer, and declarative bundle `apply` (plan preview,
  prune, skip-if-identical).
- Saved queries: a local named store for PromQL/SQL/builder queries.
- Z-score anomaly detection on time series.

### Security

- Secret files are never written through symlinks; the config directory is
  tightened to 0700.
- CSV output neutralises spreadsheet formula injection.
- Environment-controlled git repo URLs are passed after `--` (end of
  options), and failing upgrade tools report a clean error.
- Security review of the chart, watch and MCP error paths; MCP timestamp
  arguments are type-validated.

## v0.5.18 – v0.5.27 (2026-09-19 – 2026-09-20)

### Added

- Downtime schedules.
- YAML output through PyYAML (replacing the hand-rolled emitter).

### Security

- Terminal-injection, history secret-bypass and file-permission fixes;
  private files are created with restrictive permissions from birth.
- Secret-leak sweep: credentials embedded in endpoint URLs and pip/git
  install URLs are redacted from display; index credentials travel via the
  environment, never argv.
- Hostile-file hardening for bundle import (size cap, symlink guard, export
  de-duplication) and safe rendering of untrusted values in plain output.
- Transport strictness (explicit insecure flag, redirect evidence) and
  confirm-before-destroy semantics, including views restore.
- Malformed configs fail closed.

### Fixed

- YAML round-trip fidelity (exponent floats, resolver lookalikes, flow-style
  commas) and JSON fidelity in output.

## v0.5.11 – v0.5.17 (2026-09-10 – 2026-09-18)

### Added

- Complete views and channels CRUD; views migrated to the explorer API.
- Bundle `export` / `import` with manifest, dry-run, and
  skip/overwrite/fail conflict modes, plus `dashboards diff` and
  `alerts diff` against the server version.
- `traces tail` / `alerts watch` follow new spans and firing alerts with
  dedupe caps, interval validation, and fatal-error fast exit.
- `metrics top` ranks series by latest value.
- `history` / `rerun`: local query history (`--token` invocations are never
  recorded).
- `signoz update` checks for newer releases and upgrades (`--apply`
  supports pipx, pip-from-git and index installs; source checkouts get
  `git pull` instructions). A daily cached check prints a stderr notice in
  TTY sessions (opt out with `SIGNOZ_NO_UPDATE_CHECK=1`).
- `install.sh`: one-step installer resolving the newest release tag (pipx
  preferred, pip fallback, `signoz --version` verification); installs from
  a package index when `SIGNOZ_PYPI_INDEX` is set.
- Releases were also published to a self-hosted PyPI registry.

## v0.5.0 – v0.5.10 (2026-09-03 – 2026-09-08)

### Added

- kubectl-style output options and an `explain` command.
- `metrics stats`: summary statistics per group.
- Pager support and per-profile defaults for time range and output format.
- `status` command and shell completion; command-line conventions aligned
  with clig.dev.
- Automatic releases: merging to `main` bumps the patch version, tags, and
  publishes the build.

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

### Changed

- `main.py` split into a `commands/` package.
- Contract fixtures, filter property tests and negative-path tests; live
  integration suite seeds its own telemetry.
- Several adversarial review rounds: idempotent retries, input validation,
  fail-fast inputs and profile safety.

## v0.4.0 (2026-08-26)

### Fixed

- `logs tail` no longer reprints overlapping rows on every poll (dedup key
  never existed in the query output); eviction now keeps insertion order
- Filter values containing apostrophes no longer produce HTTP 400 -
  escaped with SQL-style quote doubling, live-verified against v0.138
- Network errors (connection reset, DNS, read timeout) are retried like
  transient HTTP failures instead of failing immediately

### Changed

- Stricter ruff rule set (import sorting, bugbear, pyupgrade, blind-except)
- Package ships a `py.typed` marker
- Timeouts report an actionable message suggesting --timeout / narrower range
- Client connection pool closed on process exit

### Added

- Command-level e2e tests for logs/traces/metrics queries (JSON/table/CSV,
  -o, error paths), live integration suite remains opt-in via pytest marker

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
- Release automation: tag pushes build a wheel and attach it to the
  release
- mypy type checking in CI; marker-gated live integration test suite

## v0.2.1 (2026-08-25)

### Added

- `auth verify` now reports the server's SigNoz version and whether it is
  within the tested compatibility range (`>=0.136`)
- Supported-range metadata: verified live against SigNoz v0.136.1 and v0.138.0
- Pinned install instructions; release wheel published to the release page

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
