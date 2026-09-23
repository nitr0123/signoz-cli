# signoz-cli feature backlog

Tracked on disk per maintainer request. Checked items ship via PR with
tests + docs; security-sensitive items get an adversarial pass first.

## Planned (ordered)

- [ ] **Encrypted bundles** — `export --encrypt` / `import --decrypt` so
  bundles can live in git safely. Needs a crypto dependency decision
  (`cryptography` vs `age`); passphrase via prompt/env, never argv.
- [ ] **Grafana dashboard import conversion** — translate Grafana JSON to
  the Perses-compatible v2 schema on import. Needs research into Grafana
  panel/query schema coverage.

## Shipped (v0.5.x series)

- [x] MCP server (34 tools, stdio, read-only default)
- [x] ASCII charts (`--chart`), `watch` command
- [x] Trace waterfall, week-over-week `--compare`
- [x] Declarative `apply` (prune, skip-if-identical)
- [x] `doctor`, cardinality explorer, z-score anomalies
- [x] `channels test`, saved queries, dashboards/alerts `lint`
- [x] YAML via PyYAML, offline `history`/`rerun`
- [x] Cross-instance `diff-remote` (dashboards + alerts)

## Security posture

Adversarial reviews live in `ADVERSARIAL_REVIEW.md` (21 findings to date,
all fixed). Standing rule: every feature PR gets a security pass before
merge; open deeper lanes are concurrency/TOCTOU races, pager edge cases,
and MCP prompt-injection via telemetry content.
