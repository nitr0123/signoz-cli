# signoz-cli

A command-line tool for querying traces, logs, metrics, alerts, dashboards, and services from [SigNoz](https://signoz.io/).

**Supported SigNoz versions:** `>= 0.136` — verified live against **v0.136.1** and **v0.138.0**.
`signoz auth verify` reports your server's version and whether it is in the tested range.

Built for the current SigNoz API surface:

- Query Builder **v5** (`/api/v5/query_range`) for logs / traces / metrics
- Dashboards **v2** (`/api/v2/dashboards`, Perses-compatible schema — the v1 dashboard APIs were retired in 0.135)
- Alert history **v2** (`/api/v2/rules/{id}/history/*` — the v1 history endpoints were deprecated in 0.136)
- Service-account API keys (`SIGNOZ-API-KEY` header)

## Installation

Requires Python 3.10+.

### Package registry (recommended)

The releases publish to the built-in PyPI registry. One-time credential
setup (personal access token with `read:package`):

```shell
export PIP_INDEX_URL="https://hamza:$FORGEJO_TOKEN@git.example.com/api/packages/hamza/pypi/simple"
export PIP_EXTRA_INDEX_URL="https://pypi.org/simple"
```

then install or upgrade a pinned release (pins protect against
dependency-confusion lookalikes):

```shell
pipx install "signoz-cli==0.5.14"
# or plain pip:
pip install "signoz-cli==0.5.14"
```

Without pipx, or for the newest tag without looking it up, `install.sh`
does the same with a pip fallback:

```shell
SIGNOZ_PYPI_INDEX="https://hamza:$FORGEJO_TOKEN@git.example.com/api/packages/hamza/pypi/simple" \
  sh install.sh            # newest release
SIGNOZ_PYPI_INDEX="..." sh install.sh 0.5.14   # pinned
```

### From git (no registry token needed)

Needs SSH access to the Forgejo host instead:

```shell
pipx install --force "git+ssh://forgejo/hamza/signoz-cli.git@v0.5.14"
sh install.sh              # newest tag via git ls-remote
sh install.sh v0.5.14      # pinned
```

### Helper script

`install.sh` wraps the above: it resolves the newest tag, prefers pipx with
a pip fallback, and verifies with `signoz --version`.

```shell
sh install.sh [version]   # defaults to newest release tag
```

Because this Forgejo instance sits behind SSO, downloading the script needs
your API token (or just copy it out of a clone):

```shell
curl -fsSL -H "Authorization: token $FORGEJO_TOKEN" \
  "http://localhost:3001/api/v1/repos/hamza/signoz-cli/contents/install.sh?ref=main" |
  python3 -c "import sys,json,base64; print(base64.b64decode(json.load(sys.stdin)['content']).decode())" | sh
```

### From a release wheel

```shell
curl -sL -H "Authorization: token $FORGEJO_TOKEN" -O \
  "https://git.example.com/hamza/signoz-cli/releases/download/v0.5.8/signoz_cli-0.5.8-py3-none-any.whl"
pip install ./signoz_cli-0.5.8-py3-none-any.whl
```

### From source

```shell
git clone <this-repo> && cd signoz-cli
pip install .

# Editable with dev tools
pip install -e '.[dev]'
```

Tags are immutable — `@v0.5.8` always gives you exactly the v0.5.8 build.
See [releases](https://git.example.com/hamza/signoz-cli/releases) for available versions.

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
signoz promql 'sum({__name__="app.payments.total"})' --time-range 1h
```

OTel metrics keep dotted names, so PromQL queries need brace selectors.

### More

```shell
signoz traces detail TRACE_ID        # spans with attributes/events
signoz traces waterfall TRACE_ID     # ASCII timeline (raw: detail -f json)
signoz metrics query e2e.requests.total --time-aggregation sum --compare 7d
signoz logs tail --service payment-svc
signoz alerts create rule.json       # v2 rule schema; also update/patch/delete/test
signoz channels list                 # admin only
signoz service-accounts list         # admin only
signoz views list                    # saved views
signoz watch -n 5 -- alerts list     # re-run any command (secrets redacted)
signoz logs search --all             # auto-paginate everything
signoz logs search -f csv -o out.csv # CSV to file
signoz --timeout 30 --insecure auth verify   # TLS/proxy options; retries on 429/5xx are automatic
eval "$(_SIGNOZ_COMPLETE=bash_source signoz)"  # bash completion (zsh/fish variants too)
```

### Raw Query Builder v5

```shell
signoz query my-query.json
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

29 tools: log/trace/metric search, PromQL/SQL, dashboards, alerts (+history/test),
services, views, channels, server status. Mutating tools (create/update/delete)
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
