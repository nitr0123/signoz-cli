# signoz-cli

A command-line tool for querying traces, logs, metrics, alerts, dashboards, and services from [SigNoz](https://signoz.io/) **v0.138+**.

Built for the current SigNoz API surface:

- Query Builder **v5** (`/api/v5/query_range`) for logs / traces / metrics
- Dashboards **v2** (`/api/v2/dashboards`, Perses-compatible schema — the v1 dashboard APIs were retired in 0.135)
- Alert history **v2** (`/api/v2/rules/{id}/history/*` — the v1 history endpoints were deprecated in 0.136)
- Service-account API keys (`SIGNOZ-API-KEY` header)

## Installation

```shell
# From source (development)
git clone <this-repo> && cd signoz-cli
pip install .

# Editable with dev tools
pip install -e '.[dev]'
```

## Configuration

Credentials are resolved in this order:

1. Command-line flags (`--endpoint`, `--token`)
2. Environment variables (`SIGNOZ_ENDPOINT`, `SIGNOZ_ACCESS_TOKEN`)
3. The selected **profile** in `~/.signoz/config.json` (see below)
4. Legacy flat config keys (older configs without profiles)

On self-hosted SigNoz, create a key under **Settings → Service Accounts → Keys**
(the account needs at least the Viewer role; Admin creates service accounts).
Verify credentials any time with `signoz auth verify`.

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

### Raw Query Builder v5

```shell
signoz query my-query.json
```

See the [Query Builder v5 docs](https://signoz.io/docs/userguide/query-builder-v5/) for the JSON schema.

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
