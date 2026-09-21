"""Raw query commands: query, promql, sql."""

from __future__ import annotations

import click

from signoz_cli.commands.common import (
    _apply_anomalies,
    _common_options,
    _get_client,
    _handle_error,
    _join_compare,
    _maybe_emit_chart,
    _profile_time_range,
    _read_json,
    _time_options,
    _unwrap_time_series,
    _unwrap_v5,
    _warn_v5,
    cli,
)
from signoz_cli.output import emit
from signoz_cli.timeutil import resolve_timestamps


@cli.command("query")
@_common_options
@click.argument("json_file", type=click.Path(), required=False, default=None)
def query_raw(endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, json_file: str | None) -> None:
    """Execute a raw Query Builder v5 query from a JSON file."""
    if not json_file:
        raise click.UsageError("JSON_FILE is required (or use --generate-cli-skeleton).")
    try:
        payload = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        data = _unwrap_v5(client.query_builder_v5(payload))
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@cli.command("promql")
@_common_options
@click.argument("query")
@click.option("--step", type=int, default=60, show_default=True, help="Step seconds.")
@click.option("--chart", is_flag=True,
              help="Render time series as ASCII sparklines.")
@click.option("--compare", default=None,
              help="Compare against the window shifted back by this range "
                   "(e.g. --compare 7d adds previous/change_pct columns).")
@click.option("--anomalies", is_flag=True,
              help="Show only z-score outlier points.")
@click.option("--anomaly-window", type=int, default=10, show_default=True,
              help="Trailing points per outlier baseline.")
@click.option("--anomaly-threshold", type=float, default=3.0, show_default=True,
              help="Minimum |z-score| to flag.")
@_time_options(default_range="1h")
def promql_query(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    query: str,
    step: int,
    chart: bool,
    compare: str | None,
    anomalies: bool,
    anomaly_window: int,
    anomaly_threshold: float,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Run a PromQL query against metrics.

    OTel metrics keep dotted names: use brace selectors, e.g.

      signoz promql 'sum({__name__="app.payments.total"})' --time-range 1h
    """
    if step <= 0:
        raise click.UsageError("--step must be positive (seconds per bucket).")
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h", profile_time_range=_profile_time_range(profile))
        resp = client.query_promql(query, s, e, step=step)
        _warn_v5(resp)
        rows = _unwrap_time_series(resp)
        table_keys = ["series", "timestamp", "value"]
        if compare:
            from signoz_cli.timeutil import parse_time_range

            try:
                delta = parse_time_range(compare)
            except ValueError:
                raise click.UsageError(
                    f"Invalid --compare '{compare}'. "
                    "Use formats like '24h', '7d'."
                ) from None
            rows = _join_compare(rows, _unwrap_time_series(
                client.query_promql(query, s - delta, e - delta, step=step)))
            table_keys = ["series", "timestamp", "value", "previous", "change_pct"]
        rows, anomaly_keys = _apply_anomalies(
            rows, anomalies, anomaly_window, anomaly_threshold)
        table_keys = table_keys + anomaly_keys
        if _maybe_emit_chart(rows, fmt, chart):
            return
        emit(rows, fmt, table_keys=table_keys)
    except Exception as ex:
        _handle_error(ex)


@cli.command("sql")
@_common_options
@click.argument("query")
@click.option("--chart", is_flag=True,
              help="Render series/timestamp/value rows as ASCII sparklines.")
@_time_options(default_range="6h")
def sql_query(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    query: str,
    chart: bool,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Run raw ClickHouse SQL (admin-level permission may be required).

    The start/end range is applied via the usual telemetry time window.

    Example: signoz sql "SELECT count() FROM signoz_logs.logs_v2" --time-range 1h
    """
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", profile_time_range=_profile_time_range(profile))
        resp = client.query_sql(query, s, e)
        rows = _unwrap_v5(resp)
        if _maybe_emit_chart(rows, fmt, chart):
            return
        emit(rows, fmt)
    except Exception as ex:
        _handle_error(ex)


