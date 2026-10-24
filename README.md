# signoz-cli

[![CI](https://github.com/nitr0123/signoz-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/nitr0123/signoz-cli/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/nitr0123/signoz-cli)](https://github.com/nitr0123/signoz-cli/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.14-blue)](pyproject.toml)
[![License](https://img.shields.io/github/license/nitr0123/signoz-cli)](LICENSE)

A command-line tool for querying traces, logs, metrics, alerts, dashboards, and services from [SigNoz](https://signoz.io/).

> [!NOTE]
> **This project is entirely AI-generated.** All of the code, tests and
> documentation were written by an AI coding assistant (Claude). Review
> it as you would any third-party tool before using it against
> production systems.

**Supported SigNoz versions:** `>= 0.136` — verified live against **v0.136.1**, **v0.138.0** and **v0.147.0** (latest).
`signoz auth verify` reports your server's version and whether it is in the tested range.

Built for the current SigNoz API surface:

- Query Builder **v5** (`/api/v5/query_range`) for logs / traces / metrics
- Dashboards **v2** (`/api/v2/dashboards`, Perses-compatible schema — the v1 dashboard APIs were retired in 0.135)
- Alert history **v2** (`/api/v2/rules/{id}/history/*` — the v1 history endpoints were deprecated in 0.136)
- Service-account API keys (`SIGNOZ-API-KEY` header)
- Notification channels **v2** (`/api/v2/notification_channels`, opt-in
  via `channels --api v2` — needs SigNoz 0.141+)
- AI observability (`ai` commands: LLM span fields, model pricing rules —
  needs SigNoz 0.140+)

## Installation

Requires Python 3.10+.

### From GitHub (recommended)

Install a pinned release tag with pipx:

```shell
pipx install "git+https://github.com/nitr0123/signoz-cli.git@v0.5.46"
# or plain pip:
pip install "git+https://github.com/nitr0123/signoz-cli.git@v0.5.46"
```

### Helper script

`install.sh` resolves the newest tag (via `git ls-remote`), prefers pipx with
a pip fallback, and verifies with `signoz --version`:

```shell
curl -fsSL https://raw.githubusercontent.com/nitr0123/signoz-cli/main/install.sh | sh
sh install.sh v0.5.46      # pinned, from a clone
```

Set `SIGNOZ_PYPI_INDEX` to install from a PyPI-compatible package index
instead of git.

### From a release wheel

```shell
pip install "https://github.com/nitr0123/signoz-cli/releases/download/v0.5.46/signoz_cli-0.5.46-py3-none-any.whl"
```

### From source

```shell
git clone https://github.com/nitr0123/signoz-cli.git && cd signoz-cli
pip install .

# Editable with dev tools
pip install -e '.[dev]'
```

Tags are immutable — `@v0.5.46` always gives you exactly the v0.5.46 build.
See [releases](https://github.com/nitr0123/signoz-cli/releases) for available versions.

## Updating

The CLI checks for newer releases at most once a day (cached in
`~/.signoz/.update-check.json`, TTY sessions only) and prints a notice to
stderr. Disable with `SIGNOZ_NO_UPDATE_CHECK=1`.

```shell
signoz update            # show installed vs latest + upgrade command
signoz update --apply    # perform the upgrade for your install method
```

`--apply` supports pipx and pip-from-git installs out of the box; source
checkouts get `git pull` instructions instead.

## Configuration

Credentials are resolved in this order:

1. Command-line flags (`--endpoint`, `--token`)
2. Environment variables (`SIGNOZ_ENDPOINT`, `SIGNOZ_ACCESS_TOKEN`)
3. The selected **profile** in `~/.signoz/config.json` (see below)
4. Legacy flat config keys (older configs without profiles)

On self-hosted SigNoz, create a key under **Settings → Service Accounts → Keys**
(the account needs at least the Viewer role; Admin creates service accounts).
Verify credentials any time with `signoz auth verify`. For full
config/auth/TLS/version diagnostics (support-bundle style, never prints
secrets), run `signoz doctor`.

### Multiple SigNoz instances (profiles)

Modelled on the aws-cli profile pattern. Store one profile per instance:

```shell
signoz configure -p local --endpoint http://localhost:8080 --token KEY1
signoz configure -p prod  --endpoint https://signoz.example.com --token KEY2
```

Then use them in any of these equivalent ways:

```shell
signoz -p prod logs search                 # root flag (before subcommand)
signoz logs search -p prod --limit 10      # leaf flag
export SIGNOZ_PROFILE=prod                 # environment variable
signoz profile use prod                    # set the default for future calls
```

Manage profiles with:

```shell
signoz profile list          # shows endpoints + masked tokens, marks default
signoz profile show prod     # single profile details
signoz profile remove prod   # delete a profile
```

Config file lives at `~/.signoz/config.json` (override the directory with
`SIGNOZ_CONFIG_DIR`) and is written with `0600` permissions.

## Output Formats

Every query command supports `-f/--format table|json`, plus a `--json`
shorthand for machine-readable output (per clig.dev):

```shell
signoz alerts list --json | jq '.[0]'
signoz logs search -f json
```

Table output is rendered with rich; pipe-safe JSON goes to stdout and errors to
stderr, so both play well in scripts and CI.

## Usage

### Auth

```shell
signoz auth whoami        # show configured endpoint + masked token
signoz auth verify        # validate the key against the live instance
```

### Logs

```shell
signoz logs search
signoz logs search --service payment-svc --severity ERROR
signoz logs search --query "http.status_code >= 400" --time-range 1h
signoz logs errors --service payment-svc
signoz logs by-service consumer-svc --search-text "timeout"
signoz logs aggregate --aggregation count --group-by "service.name"
signoz logs fields
signoz logs field-values service.name
```

### Traces

```shell
signoz traces search --error true --time-range 2h
signoz traces by-service payment-svc --operation "/api/pay"
signoz traces get <TRACE_ID>
signoz traces errors --service checkout-svc
signoz traces aggregate --aggregation p99 --aggregate-on durationNano --group-by "service.name"
signoz traces fields
```

### Metrics

```shell
signoz metrics list-keys
signoz metrics search cpu
signoz metrics fields --search memory
signoz metrics field-values system_memory_usage
```

### Alerts

```shell
signoz alerts list
signoz alerts rules --query "team = infra" --state firing --sort name  # v3 API (0.144+)
signoz alerts get RULE_ID
# v2 history API: cursor-paginated timeline
signoz alerts history RULE_ID --time-range 24h --order desc
signoz alerts history RULE_ID --cursor <nextCursor>
signoz alerts history-stats RULE_ID
signoz alerts overall-status RULE_ID
signoz alerts top-contributors RULE_ID
```

Note: `alerts history*` commands require the rule **UUID** (visible in the rule's
URL), not the numeric id.

### Dashboards (v2 API)

```shell
signoz dashboards list
signoz dashboards list --query "name contains prod"
signoz dashboards get UUID
signoz dashboards create dashboard.json       # v2 spec schema
signoz dashboards update UUID dashboard.json  # full replace
signoz dashboards patch UUID patch.json       # JSON-Patch array
signoz dashboards delete UUID
signoz dashboards lint ./dashboards/*.json    # offline validation (CI-ready)
signoz dashboards diff-remote --name D --from-profile prod --to-profile staging
signoz dashboards convert grafana.json --out signoz.json  # Grafana import (heatmaps: 0.146+)
signoz alerts lint ./rules/*.json
```

Create payloads must use the v2 schema and include `"schemaVersion": "v6"` and a
unique lowercase `name`; the human title lives at `spec.display.name`:

```json
{
  "schemaVersion": "v6",
  "name": "my-dashboard",
  "tags": [{"key": "team", "value": "cli"}],
  "spec": {
    "display": {"name": "My Dashboard", "description": "..."},
    "layouts": [],
    "panels": {},
    "variables": []
  }
}
```

Patch example (`patch.json`, JSON-Patch array):

```json
[
  { "op": "add", "path": "/tags/-", "value": { "key": "env", "value": "prod" } }
]
```

### Services

```shell
signoz services list --time-range 24h
signoz services top-operations payment-svc --time-range 2h
```

### Metrics (values)

```shell
signoz metrics query app.payments.total --time-aggregation sum --time-range 1h
signoz metrics query app.payments.total --time-aggregation sum --chart  # sparklines
signoz metrics cardinality app.payments.total --by service.name,operation  # cost drivers
signoz metrics query app.payments.total --time-aggregation sum --anomalies  # z-score outliers
signoz promql 'sum({__name__="app.payments.total"})' --time-range 1h
```

OTel metrics keep dotted names, so PromQL queries need brace selectors.

### More

```shell
signoz traces detail TRACE_ID        # spans with attributes/events
signoz traces waterfall TRACE_ID     # ASCII timeline (raw: detail -f json)
signoz traces flamegraph TRACE_ID    # flamegraph view (SigNoz 0.144+)
signoz metrics query e2e.requests.total --time-aggregation sum --compare 7d
signoz logs tail --service payment-svc
signoz alerts create rule.json       # v2 rule schema; also update/patch/delete/test
signoz channels list                 # admin only
signoz channels list --api v2 --kind slack  # v2 API (SigNoz 0.141+)
signoz channels repair CH_ID          # diagnose a v2-unreadable channel
signoz channels test ops             # fire a test notification by channel name
signoz service-accounts list         # admin only
signoz views list                    # saved views
signoz watch -n 5 -- alerts list     # re-run any command (secrets redacted)
signoz export ./backup -p prod       # dashboards/rules/channels/views bundle
signoz export ./backup -p prod --encrypt  # single .enc file, safe for git
signoz import ./backup.enc -p staging --decrypt --yes
signoz apply ./backup -p staging --dry-run   # declarative sync preview
signoz apply ./backup -p staging --prune --yes  # sync + delete unmanaged
signoz logs search --all             # auto-paginate everything
signoz logs search -f csv -o out.csv # CSV to file
signoz ai fields --search gen_ai      # LLM span attributes
signoz ai pricing-rules list          # per-model LLM pricing
signoz ai unmapped-models             # models without pricing
signoz --timeout 30 --insecure auth verify   # TLS/proxy options; retries on 429/5xx are automatic
eval "$(_SIGNOZ_COMPLETE=bash_source signoz)"  # bash completion (zsh/fish variants too)
```

### Raw Query Builder v5

```shell
signoz query my-query.json
signoz saved save errors-1h --kind promql --query 'sum({__name__="app.errors.total"})'
signoz saved run errors-1h --time-range 24h
```

See the [Query Builder v5 docs](https://signoz.io/docs/userguide/query-builder-v5/) for the JSON schema.

### MCP server (AI assistants)

```shell
signoz mcp -p prod                  # read-only tools (default)
signoz mcp -p prod --allow-mutations
```

Point any MCP host at it (Claude Code `~/.claude.json`, Claude Desktop config):

```json
{"mcpServers": {"signoz": {"command": "signoz", "args": ["mcp", "-p", "prod"]}}}
```

34 tools: log/trace/metric search, PromQL/SQL, dashboards, alerts (+history/test),
services, views, channels, server status, doctor diagnostics, metric
cardinality, saved queries. Mutating tools (create/update/delete)
refuse unless started with `--allow-mutations`. Authentication comes from
profiles/env vars as usual — tokens are never tool arguments, so API keys
can't land in agent transcripts.

## Time Ranges

Most commands support flexible time parameters:

- `--time-range 30m`, `--time-range 2h`, `--time-range 7d`
- `--start 1697385600000 --end 1697472000000` (explicit millisecond timestamps)

Default range is 6 hours (1 hour for log search).

## Development

```shell
pip install -e '.[dev]'
pytest          # unit tests
ruff check .    # lint
```

## License

Apache 2.0
