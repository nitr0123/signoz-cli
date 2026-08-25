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

Set your SigNoz endpoint and API token via environment variables (recommended) or CLI flags:

```shell
export SIGNOZ_ENDPOINT="http://localhost:8080"
export SIGNOZ_ACCESS_TOKEN="your-api-key-here"
```

Alternatively, pass them on every command:

```shell
signoz --endpoint https://... --token your-key ...
```

Or save them once:

```shell
signoz configure --endpoint http://localhost:8080 --token <key>
```

On self-hosted SigNoz, create a key under **Settings → Service Accounts → Keys**
(the account needs at least the Viewer role; Admin creates service accounts).
Verify credentials any time with `signoz auth verify`.

## Output Formats

Every query command supports `--format json` (machine-readable) or `--format table` (default).

```shell
signoz alerts list --format json
```

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
