"""metrics commands."""

from __future__ import annotations

from typing import Any

import click

from signoz_cli.commands.common import (
    _common_options,
    _extract_attribute_keys,
    _get_client,
    _handle_error,
    _pagination_meta,
    _profile_time_range,
    _time_options,
    _unwrap_time_series,
    _warn_v5,
    cli,
)
from signoz_cli.output import emit
from signoz_cli.query import build_metrics_query_payload, build_metrics_stats_payload
from signoz_cli.timeutil import resolve_timestamps


@cli.group()
def metrics() -> None:
    """Query SigNoz metrics."""


@metrics.command("list-keys")
@_common_options
@click.option("--limit", type=int, default=50, show_default=True, help="Max results.")
@click.option(
    "--offset", type=int, default=0, show_default=True, help="Pagination offset."
)
def metrics_list_keys(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, limit: int, offset: int
) -> None:
    """List all available metric keys."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.list_metric_keys()
        keys = _extract_attribute_keys(data)
        page = keys[offset : offset + limit]
        result = {
            "data": page,
            "pagination": _pagination_meta(len(keys), offset, limit),
        }
        emit(result, fmt)
    except Exception as e:
        _handle_error(e)


@metrics.command("search")
@_common_options
@click.argument("search_text")
def metrics_search(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, search_text: str
) -> None:
    """Search metrics by text (substring autocomplete)."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.search_metric_by_text(search_text)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@metrics.command("fields")
@_common_options
@click.option("--search", default="", help="Filter field names by text.")
def metrics_fields(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, search: str
) -> None:
    """List available field names for metric queries."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.get_metrics_available_fields(search)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@metrics.command("field-values")
@_common_options
@click.argument("field_name")
@click.option("--search", default="", help="Filter values by text.")
def metrics_field_values(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, field_name: str, search: str
) -> None:
    """Get available values for a metric field."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.get_metrics_field_values(field_name, search)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@metrics.command("query")
@_common_options
@click.argument("metric_name")
@click.option("--time-aggregation", default="avg", show_default=True,
              type=click.Choice(["avg", "sum", "min", "max", "count", "rate",
                                 "increase", "p50", "p75", "p90", "p95", "p99"]),
              help="Temporal aggregation.")
@click.option("--space-aggregation", default="sum", show_default=True,
              type=click.Choice(["sum", "avg", "min", "max", "count"]),
              help="Spatial aggregation across dimensions.")
@click.option("--temporality", default=None,
              type=click.Choice(["Unspecified", "Delta", "Cumulative"]),
              help="Metric temporality. Defaults: Gauge=Unspecified, Sum=Cumulative.")
@click.option("--group-by", default=None, help="Comma-separated attribute keys.")
@click.option("--filter", "filter_expr", default="", help="Filter expression.")
@click.option("--step", type=int, default=60, show_default=True,
              help="Step interval in seconds.")
@_time_options(default_range="1h")
def metrics_query(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    metric_name: str,
    time_aggregation: str,
    space_aggregation: str,
    temporality: str | None,
    group_by: str | None,
    filter_expr: str,
    step: int,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Query metric VALUES over time (v5 time series).

    Notes: rate/increase need enough samples in the window. Gauge metrics
    default to Unspecified temporality, counters to Cumulative -- override
    with --temporality Delta for delta counters.

    Examples:

      signoz metrics query app.payments.total --time-aggregation sum

      signoz metrics query system.cpu.time --group-by "host.name"
    """
    if step <= 0:
        raise click.UsageError("--step must be positive (seconds per bucket).")
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h", profile_time_range=_profile_time_range(profile))
        if temporality is None:
            temporality = "Cumulative" if time_aggregation in ("rate", "sum") else "Unspecified"
        group_by_fields = [g.strip() for g in group_by.split(",") if g.strip()] if group_by else []
        payload = build_metrics_query_payload(
            s, e, metric_name,
            temporality=temporality,
            time_aggregation=time_aggregation,
            space_aggregation=space_aggregation,
            group_by=group_by_fields,
            filter_expression=filter_expr,
            step_interval=step,
        )
        resp = client.query_builder_v5(payload)
        _warn_v5(resp)
        rows = _unwrap_time_series(resp)
        if not rows:
            _warn_empty_metrics(metric_name)
        emit(rows, fmt,
             table_keys=["series", "timestamp", "value"])
    except Exception as ex:
        _handle_error(ex)


@metrics.command("stats")
@_common_options
@click.argument("metric_name")
@click.option("--time-aggregation", default="max", show_default=True,
              type=click.Choice(["avg", "sum", "min", "max", "count", "rate",
                                 "increase", "p50", "p75", "p90", "p95", "p99"]),
              help="Temporal aggregation per step.")
@click.option("--reduce-to", default="max", show_default=True,
              type=click.Choice(["sum", "count", "avg", "min", "max", "last", "median"]),
              help="How to collapse the time series into a single value.")
@click.option("--temporality", default=None,
              type=click.Choice(["Unspecified", "Delta", "Cumulative"]),
              help="Metric temporality. Defaults: Gauge=Unspecified, Sum=Cumulative.")
@click.option("--group-by", default=None,
              help="Comma-separated attribute keys to group by (e.g. k8s.pod.name).")
@click.option("--filter", "filter_expr", default="", help="Filter expression.")
@click.option("--step", type=int, default=300, show_default=True,
              help="Step interval in seconds (300 = 5min).")
@_time_options(default_range="24h")
def metrics_stats(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    metric_name: str,
    time_aggregation: str,
    reduce_to: str,
    temporality: str | None,
    group_by: str | None,
    filter_expr: str,
    step: int,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Compute summary statistics for a metric (p95/max/avg over a window).

    Uses Query Builder v5 ``scalar`` mode with ``reduceTo`` to collapse a time
    series into a single value per group-by combination.

    Examples:

      signoz metrics stats container_memory_working_set_bytes --group-by k8s.pod.name

      signoz metrics stats http_request_duration --time-aggregation p95 --reduce-to max --time-range 7d

      signoz metrics stats cpu_usage --time-aggregation avg --reduce-to avg --group-by host.name
    """
    if step <= 0:
        raise click.UsageError("--step must be positive (seconds per bucket).")
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="24h",
                                  profile_time_range=_profile_time_range(profile))
        if temporality is None:
            temporality = "Cumulative" if time_aggregation in ("rate", "sum") else "Unspecified"
        group_by_fields = [g.strip() for g in group_by.split(",") if g.strip()] if group_by else []
        payload = build_metrics_stats_payload(
            s, e, metric_name,
            temporality=temporality,
            time_aggregation=time_aggregation,
            reduce_to=reduce_to,
            group_by=group_by_fields,
            filter_expression=filter_expr,
            step_interval=step,
        )
        resp = client.query_builder_v5(payload)
        _warn_v5(resp)
        if _scalar_results_empty(resp):
            _warn_empty_metrics(metric_name)
        _emit_scalar_stats(resp, fmt)
    except Exception as ex:
        _handle_error(ex)


def _warn_empty_metrics(metric_name: str) -> None:
    """Hint when a metrics query returns no points (often temporality).

    Counters usually need --time-aggregation sum (Cumulative temporality
    is auto-selected for sum/rate); gauges work with the default avg.
    """
    click.echo(
        f"Warning: no points for '{metric_name}'. Counters usually need "
        "--time-aggregation sum (Cumulative temporality is auto-selected "
        "for sum/rate); gauges work with the default avg.",
        err=True,
    )


def _scalar_results_empty(data: Any) -> bool:
    """True when a scalar v5 response carries no data rows."""
    try:
        results = data["data"]["data"]["results"]
    except (KeyError, TypeError, IndexError):
        return True
    return not any(isinstance(r, dict) and r.get("data") for r in results or [])


def _emit_scalar_stats(data: dict[str, Any], fmt: str) -> None:
    """Unwrap a scalar Query Builder v5 response into a flat table."""
    try:
        results = data["data"]["data"]["results"]
    except (KeyError, TypeError, IndexError, AttributeError):
        emit(data, fmt)
        return
    rows: list[dict[str, Any]] = []
    for result in results or []:
        # Servers send explicit nulls (see recorded fixtures); tolerate them.
        columns = result.get("columns") or []
        data_rows = result.get("data") or []
        group_cols = [c["name"] for c in columns if c.get("columnType") == "group"]
        # Use a readable label instead of __result_N
        agg_label = "value"
        for row in data_rows:
            r: dict[str, Any] = {}
            for i, col_name in enumerate(group_cols):
                r[col_name] = row[i] if i < len(row) else ""
            val = row[len(group_cols)] if len(group_cols) < len(row) else None
            r[agg_label] = val
            rows.append(r)
    if not rows:
        emit("(no results)", fmt)
        return
    emit(rows, fmt)


