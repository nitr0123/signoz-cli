"""Top-level meta commands: completion, status, explain."""

from __future__ import annotations

import click

from signoz_cli.commands.common import (
    _common_options,
    _get_client,
    _handle_error,
    _is_supported_server,
    _selected_profile_name,
    cli,
)
from signoz_cli.output import emit


@cli.command("completion")
@click.argument(
    "shell",
    type=click.Choice(["bash", "zsh", "fish"]),
    default="bash",
    required=False,
)
def completion(shell: str) -> None:
    """Print shell-completion setup for bash/zsh/fish.

    Add the printed line to your shell rc file, or run it once in the session.
    """
    click.echo(f"# Add this to your {shell} configuration:")
    click.echo(f'eval "$(_SIGNOZ_COMPLETE={shell}_source signoz)"')


@cli.command("status")
@_common_options
def status_cmd(endpoint: str | None, token: str | None, fmt: str,
               profile: str | None) -> None:
    """Show connectivity, server version and account info in one glance."""
    try:
        client = _get_client(endpoint, token, profile)
        me = client.verify()
        account = me.get("data", me) if isinstance(me, dict) else me
        server_version = ""
        try:
            server_version = (client.get_server_version() or {}).get("version", "")
        except Exception:
            server_version = ""
        status = {
            "endpoint": client.endpoint,
            "profile": _selected_profile_name(profile) or "<none>",
            "server_version": server_version or "<unknown>",
            "compatible": _is_supported_server(server_version),
            "account": account.get("name", "") if isinstance(account, dict) else "",
            "account_id": account.get("id", "") if isinstance(account, dict) else "",
            "org_id": account.get("orgId", "") if isinstance(account, dict) else "",
        }
        emit(status, fmt)
    except Exception as e:
        _handle_error(e)


@cli.command("explain")
@click.argument("topic", default="overview")
def explain(topic: str) -> None:
    """Explain query syntax, filters, and available signals.

    Topics: overview, filters, aggregations, metrics, logs, traces.

    Examples:

      signoz explain

      signoz explain filters

      signoz explain aggregations
    """
    topics = {
        "overview": """SigNoz CLI Query Reference
=========================

FILTER SYNTAX (used in --query, --filter)
  field="value"           Exact match
  field!="value"          Not equal
  field=~"regex"          Regex match
  field!~"regex"          Negated regex
  field in ["a","b"]      Set membership
  field>100               Numeric comparison
  field>=100              Greater than or equal
  AND / OR                Logical operators
  (expr)                  Grouping

  Examples:
    severity="ERROR"
    severity="ERROR" AND service.name="api-server"
    message=~"timeout|deadline"
    durationNano>1000000000

AGGREGATIONS
  --time-aggregation:   avg, sum, min, max, count, rate, increase,
                        p50, p75, p90, p95, p99
  --space-aggregation:  sum, avg, min, max, count
  --reduce-to:         sum, count, avg, min, max, last, median

SIGNALS (Query Builder v5)
  logs      — log lines (message, severity, service.name, timestamp)
  traces    — distributed trace spans (name, durationNano, hasError)
  metrics   — time-series metrics (container_*, http_*, etc.)

TIME RANGES
  30m, 1h, 2h, 6h, 12h, 24h, 2d, 7d, 30d
  Or explicit --start/--end in milliseconds.
""",
        "filters": """FILTER EXPRESSIONS
==================

Basic operators:
  field="value"       Exact match
  field!="value"      Not equal
  field=~"regex"      Regex match (RE2 syntax)
  field!~"regex"      Negated regex

Comparison operators:
  field>number        Greater than
  field>=number       Greater than or equal
  field<number        Less than
  field<=number       Less than or equal

Logical operators:
  expr AND expr       Both conditions must be true
  expr OR expr        Either condition can be true
  (expr)              Grouping

Common fields:
  severity            Log level (TRACE, DEBUG, INFO, WARN, ERROR, FATAL)
  message             Log message text
  service.name        Service identifier
  hasError            true/false (traces)
  durationNano        Span duration in nanoseconds
  traceID             Distributed trace identifier
  spanID              Span identifier

Examples:
  severity="ERROR"
  severity="ERROR" AND service.name="api-server"
  message=~"connection refused|timeout"
  durationNano>100000000 AND hasError=true
  severity IN ["ERROR","WARN"] AND service.name=~"payment"
""",
        "aggregations": """AGGREGATION REFERENCE
=====================

Time aggregations (--time-aggregation):
  avg       Arithmetic mean over the time window
  sum       Sum of all values
  min       Minimum value
  max       Maximum value
  count     Number of data points
  rate      Per-second rate of increase (counters)
  increase  Total increase over the window (counters)
  p50       50th percentile (median)
  p75       75th percentile
  p90       90th percentile
  p95       95th percentile
  p99       99th percentile

Space aggregations (--space-aggregation):
  sum       Sum across all dimension combinations
  avg       Average across dimensions
  min       Minimum across dimensions
  max       Maximum across dimensions
  count     Count of dimension combinations

Reduce to (--reduce-to, for metrics stats):
  sum       Sum of the reduced time series
  count     Count of data points
  avg       Average of the time series
  min       Minimum of the time series
  max       Maximum of the time series
  last      Last value in the time series
  median    Median of the time series

Metric temporality (--temporality):
  Unspecified  Default for gauge metrics
  Cumulative   Default for sum/counter metrics
  Delta        For delta counters
""",
        "metrics": """METRICS QUERIES
===============

List metric keys:
  signoz metrics list-keys
  signoz metrics search "container"

Get field info:
  signoz metrics fields
  signoz metrics field-values k8s.pod.name

Query time series:
  signoz metrics query container_memory_working_set_bytes \\
    --time-aggregation max --group-by k8s.pod.name

Summary stats (single value per group):
  signoz metrics stats container_memory_working_set_bytes \\
    --group-by k8s.pod.name --time-range 7d

PromQL:
  signoz promql 'rate(http_requests_total[5m])'

Raw Query Builder v5:
  signoz query payload.json
""",
        "logs": """LOGS QUERIES
============

Search logs:
  signoz logs search --service api-server --severity ERROR --time-range 1h
  signoz logs search --query 'message=~"timeout"' --time-range 24h

Error logs:
  signoz logs errors --service api-server --time-range 6h

Aggregate logs:
  signoz logs aggregate --aggregation count --group-by severity

Log fields:
  signoz logs fields
  signoz logs field-values severity --time-range 1h

Tail live logs:
  signoz logs tail --service api-server

By service:
  signoz logs by-service api-server --time-range 1h

Common fields:
  message, severity, service.name, timestamp, traceID
""",
        "traces": """TRACES QUERIES
==============

Search traces:
  signoz traces search --service api-server --time-range 1h
  signoz traces search --query 'durationNano>100000000' --time-range 6h

Error traces:
  signoz traces errors --service api-server --time-range 1h

Trace detail:
  signoz traces detail <traceID>

By service:
  signoz traces by-service api-server --time-range 1h

Top operations:
  signoz services top-operations api-server

Trace fields:
  signoz traces fields
  signoz traces field-values "service.name" --time-range 1h

Common fields:
  name, durationNano, hasError, traceID, spanID, service.name
""",
    }

    content = topics.get(topic)
    if content is None:
        available = ", ".join(sorted(topics.keys()))
        raise click.UsageError(
            f"Unknown topic '{topic}'. Available: {available}"
        )
    click.echo(content)


# ===================================================================
# SKELETON — example payloads for query / sql
# ===================================================================

