# Adversarial Security Review — signoz-cli v0.5.39

Reviewer stance: hostile-but-benevolent engineer trying to leak credentials,
corrupt data, hang a pipeline, or make the tool silently do the wrong thing.
Reviewed against live SigNoz v0.138.0 (`:8080`) and v0.136.1 (`:8081`).
Re-verified claim-by-claim against v0.5.39; every finding below was
reproduced on the current tree before being listed as holding.

Scope: `src/signoz_cli/` — client, config, output, query, timeutil,
lint, and `commands/*` (including the MCP server).
Method: source audit plus live/fake-client reproduction of every claim;
findings are only listed after a repro, and fixes ship with regression tests.

Baseline at review time: 575 unit tests + 24 live tests, `ruff` and `mypy` clean.

---

## 1. Executive summary

No credential-leak, injection, or destructive-action blocker remains. The
tool keeps secrets out of argv, history, and error text; sanitizes
server-controlled output before it reaches a terminal; refuses destructive
operations without explicit confirmation; and bounds untrusted input.

Two findings were found and fixed in this round, both local-hardening gaps
around **file writes** (CWE-59 symlink following, CWE-276 wrong directory
permissions). Both are fixed and regression-tested.

---

## 2. Findings this round

### S1 — Symlink-following writes on secret-bearing files (Medium-Low) — FIXED

**What.** `_write_private()` opened the destination with
`O_WRONLY | O_CREAT | O_TRUNC` (no `O_NOFOLLOW`), and the update-check cache
used `Path.write_text`. A symlink planted at `~/.signoz/history.json`,
`~/.signoz/queries.json`, or `~/.signoz/.update-check.json` caused the CLI
to follow it and overwrite whatever file it pointed at.

**Reproduced.**

```
$ python - <<'PY'
os.symlink(outside_file, CONFIG_DIR / "history.json")
_record_history(["logs", "search", "--limit", "1"])
PY
history symlink followed: [   {"ts": "...            <- outside_file overwritten
```

The same write-through was demonstrated for `queries.json` and the update
cache. (`config.json` itself was already safe: it is written to a fresh temp
path and installed with `os.replace`, which swaps the symlink out.)

**Impact.** Requires the attacker to be able to create a symlink inside
`~/.signoz` (i.e. already owns the user's config dir), so it is not a remote
path. It is a defense-in-depth gap: a planted link must never redirect a
secret-file write onto another file the user can write.

**Fix.** `_write_private` now rejects symlinks explicitly and passes
`O_NOFOLLOW`; the update cache routes through the same helper. Regression
tests cover history and saved-query stores.

### S2 — Config directory created world/group-accessible (Low) — FIXED

**What.** `~/.signoz` was created with default permissions under the umask
(observed `0775`). Files inside are `0600`, so secrets were not directly
readable, but the directory was group- and world-traversable/listable.

**Reproduced.** `stat` of a freshly created config dir: `0o775`.

**Impact.** Leaks the presence/names of config, history, and saved-query
files to other local users and widens the tampering surface for S1.

**Fix.** New `_ensure_config_dir()` creates the directory `0700` and
tightens an existing loose one; every writer calls it.

### Process incident (disclosed)

While probing S1, a test harness patched `config.CONFIG_DIR` but not
`config.CONFIG_FILE` (bound at import time) and wrote to the developer's
real `~/.signoz/config.json`, replacing it. The file was reconstructed
(profiles `range-test` → `:8080`, `v136` → `:8081`), the directory tightened
to `0700`, and `signoz doctor` re-verified all-pass. The test suite's global
`_isolated_config` fixture already patches both names, so this cannot happen
in tests; the lesson is recorded here for anyone scripting against the
package directly.

---

## 3. Findings from earlier rounds (all fixed)

| # | Finding | Class | Fix / PR |
|---|---------|-------|----------|
| 1 | Terminal injection via telemetry (OSC title, OSC-8 links, Rich markup) in tables, tail/watch | CWE-150 | `_safe_text` / `_safe_plain` (#23, #25) |
| 2 | `--token=SECRET` bypassed the history never-record guard | CWE-312 | equals-form guard (#23) |
| 3 | `history.json` / `config.json` world-readable on creation | CWE-276 | 0600 from birth (#23, #28) |
| 4 | Endpoint userinfo (`https://user:pass@host`) leaked into history, displays, configs | CWE-522 | fail-fast + redaction (#24) |
| 5 | Registry credentials echoed in `update` argv/terminal | CWE-522 | `PIP_INDEX_URL` env + redaction (#24, #27) |
| 6 | Server-controlled error/warning/diff text emitted raw | CWE-150 | choke-point sanitization (#25) |
| 7 | Unbounded JSON intake (files + stdin) | CWE-400 | 32 MiB cap (#26) |
| 8 | Bundle import followed symlinks out of the bundle dir | CWE-59 | realpath containment (#26) |
| 9 | `export` silently overwrote colliding sanitized filenames | CWE-22 | suffix disambiguation (#26) |
| 10 | `profile insecure: "false"` (string) disabled TLS verification | CWE-295 | strict truthiness (#27) |
| 11 | Malformed configs crashed with raw tracebacks | CWE-20 | fail-closed usage errors (#27) |
| 12 | pip-git upgrade instructions echoed embedded credentials | CWE-522 | redacted descriptor (#27) |
| 13 | `watch` echoed `--token` in its repeating header | CWE-522 | redaction (#32) |
| 14 | MCP auth/config failures crashed with tracebacks | CWE-209 | `ToolError` mapping (#36, #44) |
| 15 | Chart crash on non-finite values | CWE-20 | skip non-finite (#36) |
| 16 | `watch` looped credential/state/migration commands | CWE-693 | refused roots (#36, #44) |
| 17 | MCP non-numeric timestamp bounds raised `TypeError` | CWE-20 | `_window` validation (#37, #44) |
| 18 | `apply` non-object docs, id-less prune DELETEs, empty-bundle prune | CWE-20/693 | guards + refusal (#44) |
| 19 | Uncapped text-file reads in `saved save --file` | CWE-400 | 32 MiB cap (#44) |

---

## 4. Cleared with evidence

- **Redirects** are never followed (`302` yields a clean failure; the
  `SIGNOZ-API-KEY` header never reaches a `Location` host).
- **Collection-path DELETE** (`/api/v2/dashboards/`) returns the SPA
  fallback, not a bulk delete; the id-less guard is belt-and-braces.
- **Shell injection**: no `shell=True`, `eval`, `exec`, `pickle`, or
  `yaml.load` anywhere; subprocess calls are list-form (pager, `git
  ls-remote`, pip/pipx).
- **Filter escaping**: values use SQL-style doubled quotes (`_q`), verified
  live; whole `--query` expressions are intentionally passed through.
- **Retry/duplication**: only idempotent calls retry transport/5xx; `429`
  may retry non-idempotent calls; `Retry-After` is honoured but capped
  (≤60 s, ≤3 attempts).
- **Terminal output**: JSON/YAML/CSV stay byte-faithful for pipelines;
  table/plain paths strip C0 controls; labels in charts, waterfalls and
  diffs are sanitized.
- **MCP**: stdio stdout carries protocol frames only (warnings go to
  stderr); no tool accepts a `token` argument; 34 tools, mutation tools
  refuse without `--allow-mutations`.
- **Hostile inputs**: null/array bundle docs, unreadable configs, deeply
  nested JSON, non-finite numbers, and malformed selectors all fail cleanly
  (no traceback, no hang) — fuzzed across renderers and parsers.
- **Destructive ops**: all deletes and overwrites gate on `--dry-run` /
  `--yes` / `_confirm`; `--no-input` fails fast instead of prompting.

## 5. Residual risk / accepted limitations

- Writes to **user-chosen output paths** (`-o FILE`, `export DIR`) follow
  symlinks, matching standard Unix redirect semantics; not treated as a
  finding.
- `_q` cannot reliably escape **backslashes** inside filter values
  (server-grammar limitation); documented, use `--query` for those.
- A local attacker who already owns `~/.signoz` retains significant power
  (the files are the trust anchor). `0700` + `O_NOFOLLOW` reduce the blast
  radius but cannot eliminate it.
- The MCP server trusts its parent process's stdio; transport-level
  authentication is the host's responsibility (stdio only by design).

## 6. Recommended next actions

1. Keep the per-change review loop: every feature PR gets an adversarial
   pass before merge (this round reviewed doctor, cardinality, apply,
   channels-test, saved, MCP).
2. When the remaining feature backlog lands (encrypted bundles, Grafana
   import, cross-instance diff), review each for the same classes:
   path traversal, secret echoing, unbounded input, and destructive defaults.
3. Consider `O_NOFOLLOW` for `export` output files if bundles are ever
   treated as a trusted backup medium.

## 7. Verification status

- Unit: 575 passed. Live: 24 passed (both instances).
- `ruff check .` clean; `mypy src/signoz_cli/` clean.
- Final `main` verified after merge; no open PRs, clean worktree.
