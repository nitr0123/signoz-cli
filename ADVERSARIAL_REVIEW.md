# Adversarial Review — signoz-cli v0.5.8

Reviewer perspective: a hostile-but-benevolent engineer trying to break the tool,
leak credentials, corrupt data, hang in production, or silently do the wrong thing.
Reviewed against the live v0.138.0 (`:8080`) and v0.136.1 (`:8081`) instances.

Scope: `src/signoz_cli/` (client, output, query, config, timeutil, commands/*).
309 unit tests + 20 live tests green at time of review.

---

## Table of contents

1. Executive summary
2. Critical findings (would ship blockers)
3. High findings (should fix next release)
4. Medium findings (worth triaging)
5. Low / informational findings
6. Explicitly cleared (reviewed and OK)
7. Cumulative remediation history
8. Recommended next actions

---

## 1. Executive summary

The CLI is in good shape: error paths funnel through `_handle_error` (exit 1 for
API/transport errors, exit 2 for usage), secrets are masked everywhere we probed,
config is chmod 0600, destructuring deletes honor `--dry-run` and `--yes`,
the pager honours `--no-pager`/`NO_PAGER`, and retry/backoff is now scoped by
idempotency. No **critical** (ship-blocking) issues remain.

What **is** worth fixing: several **fail-soft** behaviours that degrade silently —
empty result sets that misrender under some formats, an unvalidated `--default-time-range`
that pre-dates the save-time guard, the `PAGER` shell-split fragility, and a
documented-but-divergent default temporality for counters.

Ratings use: **Critical** (data loss / credential leak / hang), **High**
(silent wrong result or crash on realistic input), **Medium** (robustness /
maintainability), **Low** (style / info).

---

## 2. Critical findings

None reproduced. Evidence of the highest-risk surfaces:

- **Credential leak** — token appears only in the `SIGNOZ-API-KEY` header. Probed
  `-o FILE`, transport errors, and `status`/`profile show`/`auth whoami`:
  the token never appears in output or errors. `_mask_token` correctly renders
  `None`/`""` as `<not set>`, short tokens as `***`, and truncates the middle.
- **Config permissions** — `~/.signoz/config.json` is written `chmod 0o600`
  (`config.py:56`); confirmed `stat` = `600`.
- **Destructive paths** — all five deletes (`dashboards`, `alerts`, `channels`,
  `views`, `service-accounts`) guard on `--dry-run` then `--yes`, and `--no-input`
  fails with exit 2 rather than prompting. Verified via a fake-client suite.
- **Retry/duplication** — mutating calls (`create_*`, `patch_*`) retry 429 only;
  transport errors and 5xx on non-idempotent calls return immediately to avoid
  committing twice after a timeout. Attempt-count matrix tests pin this.
- **Timeout/input hangs** — `--step <= 0`, `--interval <= 0`, `--timeout <= 0`,
  `start >= end`, malformed `--field-selector`, unknown profile, and schemeless
  endpoints all fail fast (exit 2), and `logs tail` no longer busy-loops.

---

## 3. High findings

### H1. Empty-result rendering is inconsistent across formats
`alerts history`/`metrics query` now print `(no results)` for table mode and a
correct empty shape for JSON, but the empty hint is emitted as *data*, so:

- `metrics stats` empty → `emit("(no results)", fmt)`. For `--json` this yields a
  JSON **string** `"(no results)"`, whereas tabular empties elsewhere yield `[]`.
  **Reproduced**: `_emit_scalar_stats` with `data: []` emits `"(no results)"`.
- `metrics query` empty → `emit(rows, ...)` with `rows == []`, so JSON yields `[]`
  but table yields `(no results)`. Different commands, different conventions.

Recommendation: standardise on an empty-result contract — `[]`/`{"data": []}` for
JSON and `(no results)` on stderr for tables — and route the hint through stderr
(see H2). `metrics.py` `_emit_scalar_stats` currently builds the `value` column
fallback path that can emit a header-only table.

### H2. Empty/warning hints interleave with stdout JSON
`metrics query`/`stats` print `Warning: no points ...` to **stderr** (good), but
`_emit_scalar_stats` for the empty case emits `(no results)` to stdout where a
JSON consumer expects a clean payload. Any consumer that does `signoz metrics
stats x --json | jq` gets a non-parseable first token when empty. Same class as H1.

### H3. `--default-time-range` guard is post-hoc
`configure --default-time-range` is now validated at save time, but a profile
persisted by an *older* release with a bad value (e.g. `24hours`) will make every
query raise `Invalid time range` at runtime rather than at read. Consider validating
profile defaults when loading a profile, or decoding with a friendlier error that
names the offending key. Reproduced the runtime failure path via `parse_time_range`.

### H4. `PAGER` is `shlex`-naive
`output.py:141` does `pager_cmd.split()`. A `PAGER` value with quoted single
argument (e.g. `PAGER="less -R -X"` is fine, but `PAGER='my wrapper -p "file" '`
or a path with a space) breaks. No `shell=True` is used (good — no injection),
so the impact is limited to odd `$PAGER` values, but the naive split yields
confusing `FileNotFoundError: '"/usr/bin/less'`-style messages that fall to the
`sys.stdout.write(text)` fallback.

---

## 4. Medium findings

### M1. `metrics query` default temporality returns empty for counters
For a Sum counter `e2e.requests.total`:
- `signoz metrics query e2e.requests.total` (default `--time-aggregation avg`,
  temporality `Unspecified`) → `[]` (verified).
- `--time-aggregation sum` auto-selects Cumululative → rows (verified).

The help text documents this, but the *default* (avg) quietly returns nothing on
counters. This is the single most likely "why is my query empty" user confusion.
Recommendation: when the metric's data type is Sum and temporality is unset,
default to `sum`/Cumulative rather than `avg`/Unspecified, or auto-detect via the
metric metadata (the CLI already calls `list_metric_keys`/autocomplete internally).

### M2. `--generate-cli-skeleton` exists only on `query`
Wired at `main.py:83` for `query` only. `sql` and `promql` reject the flag
(`No such option '--generate-cli-skeleton'`). The original feature spec named
both `query` and `sql`. Low impact (it's a convenience command) but inconsistent
with the documented feature set.

### M3. Raw field dumps are unflattened
`metrics fields` / `traces fields` / `logs fields` (and `field-values`) emit the
raw envelope, producing a one-row table whose cell is a sliced Python `dict`
repr (verified live: `status` / `data` columns with `{'attributeKeys': [...]...`).
Truthful but poor UX; `list-keys` is flattened via `_extract_attribute_keys`
while `fields` is not. Consider unifying the field-listing path.

### M4. Bare `except Exception` swallowing version errors
`auth.py:216` and `meta.py:46` wrap `get_server_version()` in `except Exception`
and default to `""`. This hides transport/permission errors for the *version*
probe while `verify()` failures still surface. Intentional (version is best-effort)
but the comment isn't in code — a future reader may not realize it's deliberate.

---

## 5. Low / informational findings

- **Dead/unreachable**: earlier `_watch_loop` removed; current tree clean.
- **`SUPPORTED_SIGNOZ_MIN = (0, 136)`** — declared floor is 0.136; both instances
  in range. `_is_supported_server` handles `()`. No action.
- **`_parse_version` on unknown format returns `()`** — safe.
- **Help/exit codes** — `--help` exits 0; unknown command yields suggestion via
  `SuggestGroup`; `metricss` (typo) did not coerce to a suggestion at root because
  root has no sibling match — acceptable verbosity.
- **Message wording** — `Error: Request URL is missing an 'http://' or 'https://'
  protocol.` (pre-fix) is now pre-empted by the http:// prepend.

---

## 6. Explicitly cleared (reviewed, no action)

- **Token in URLs/headers/errors** — header-only, masked, not logged. Cleared.
- **Config perms** — 0600. Cleared.
- **`-o FILE`** — standard Unix clobber semantics; single write at end. Cleared.
- **SIGPIPE** — table/JSON piped through `head` exits cleanly (0). Cleared.
- **`--limit`/`--offset`/`--step` negatives** — server rejects with clear 400s,
  CLI maps to exit 1; positive-vs-negative handled. Cleared.
- **Pagination `--all`** — 13 spans across pages, zero duplicates (offset walked
  correctly). Cleared.
- **completion** scripts — `bash -n` clean for bash,zsh,fish. `powershell` unsupported
  (choice enum) — expected. Cleared.
- **history endpoints on 0.136** — `history`/`history-stats`/`overall-status`/
  `top-contributors` return clean empty/error shapes. Cleared.
- **`services` / `top-operations`** — ns vs ms units documented per command;
  list_services takes ns requests. Cleared.
- **`explain` unknown topic** — UsageError with available topics. Cleared.
- **`_read_json('-')` stdin** — works; non-object rule files now rejected. Cleared.
- **JSON-Patch** — dashboards uses atomic server PATCH; alerts now direct PATCH
  (was GET+PUT race). Cleared.
- **`--insecure`/`--timeout` env+profile precedence** — flag > env > profile,
  validated non-positive reject. Cleared.

---

## 7. Cumulative remediation history

Since these reviews began, merged via PRs (all CI-green, auto-released):

- **PR #4** — live-telemetry seeder + expanded live suite (20 tests, both versions), contract fixtures (PR #7).
- **PR #6** — split `main.py` (3309 → 86 lines) into `commands/*`.
- **PR #7** — recorded fixtures (v0.138+v0.136), 18 contract tests, 10 hypothesis
  property tests for `_q()`/filter builders, 14 negative-path tests; fixed
  `_append_filter` trailing-`AND` bug.
- **PR #8** — jsonpath `[*]`/`[N]` remainder bug, `table_keys` invisible tables,
  empty-envelope handling, non-JSON-200 loud failure, metrics null-`columns` crash.
- **PR #9** — idempotent-only retry for mutations, `start>=end` and `--interval<=0`
  validation, `logs tail` fatal-4xx fast-fail, removed dead `_watch_loop`.
- **PR #10** — `alerts patch` atomic server-side PATCH, `--step<=0` ×3, timeout
  value validation, `configure --default-time-range` save-time validation,
  12 orphan banners removed, conftest resets `_ROOT_PROFILE`.
- **PR #11** — `--field-selector` malformed-part rejection, unknown-profile
  fail-fast (flag+env), schemeless endpoint `http://` prepend, `alerts test`
  non-object rejection.
- **PR #23** — terminal injection in table rendering (`_safe_text`),
  `--token=` history guard bypass, `history.json` 0600.
- **PR #24 (security round 1)** — endpoint URLs embedding userinfo
  (`https://user:pass@host`) rejected fail-fast at resolve/configure
  time (they flowed into history, displays, stored configs); history
  skips userinfo invocations and redacts legacy secrets on display;
  `rerun` refuses poisoned legacy entries; registry index credentials
  moved from ps-visible argv to `PIP_INDEX_URL` env; upgrade
  descriptions redacted.
- **PR #25 (security round 2)** — server-controlled text in plain-output
  paths sanitized (`_safe_plain`: API errors, v5 warnings, export-skip
  notices, diffs, tail/watch loops; brackets preserved where Rich
  markup is inert).
- **PR #26 (security round 3)** — 32MiB JSON intake cap (files+stdin),
  bundle-import symlink/escape guard (realpath containment), export
  filename-collision suffixes.
- **PR #27 (security rounds 4-6 + final pass)** — pip-git upgrade
  instructions redacted; export/import confirms honor `--no-input`;
  views overwrite restores the old doc on replacement failure; profile
  `insecure` strict truthiness (`"false"` no longer disables TLS);
  malformed configs (non-object profiles, non-string endpoint/token/
  default/time-range) fail closed with exit-2 guidance.
  Cleared with evidence: hostile tag refs vs `_parse_version` gate,
  redirects never followed, Retry-After capped, `install.sh` quoted,
  `history --clear` promptless by design.

---

## 8. Recommended next actions (priority order)

1. **Standardise empty-result contract** (H1/H2): `[]`/`{"data": []}` for JSON,
   `(no results)` on **stderr** for tables; stop emitting hint text to stdout.
2. **Auto-temporality for counters** (M1): default to `sum`/Cumulative when the
   metric is a Sum and temporality is unset, or hint on `avg`-with-zero-rows.
3. **Validate profile time-range at load** (H3) so pre-existing bad profile
   defaults fail with a named-key message instead of a generic range error.
4. **`shlex.split` for `PAGER`** (H4).
5. **Wire `--generate-cli-skeleton` to `sql`/`promql`** or drop it from the
   documented feature set (M2).
6. **Flatten `fields`/`field-values`** like `list-keys` (M3).
7. **Comment the deliberate bare-except** for version probe (M4).

No critical fixes remain; these are robustness and UX items, not blockers.
