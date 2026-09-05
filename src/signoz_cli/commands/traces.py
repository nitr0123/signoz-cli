"""traces commands."""

from __future__ import annotations

import click

from signoz_cli.commands.common import (
    _NO_FIELD_AGGREGATIONS,
    _TRACE_TABLE_KEYS,
    _VALID_AGGREGATIONS,
    _append_filter,
    _build_trace_filter,
    _common_options,
    _get_client,
    _handle_error,
    _parse_group_by,
    _parse_order_by,
    _profile_time_range,
    _q,
    _time_options,
    _traces_query_all,
    _unwrap_v5,
    cli,
)
from signoz_cli.output import emit
from signoz_cli.query import build_aggregate_query_payload, build_traces_query_payload
from signoz_cli.timeutil import resolve_timestamps


@cli.group()
def traces() -> None:
    """Query SigNoz traces."""


@traces.command("search")
@_common_options
@click.option("--service", default=None, help="Service name filter.")
@click.option("--operation", default=None, help="Operation name filter.")
@click.option(
    "--error",
    "error_filter",
    type=click.Choice(["true", "false"]),
    default=None,
    help="Filter by error status.",
)
@click.option("--min-duration", type=int, default=None, help="Minimum duration in ns.")
@click.option("--max-duration", type=int, default=None, help="Maximum duration in ns.")
@click.option("--time-range", default=None, help="Relative time range. Default: 6h.")
@click.option("--start", type=int, default=None, help="Start timestamp in ms.")
@click.option("--end", type=int, default=None, help="End timestamp in ms.")
@click.option("--limit", type=int, default=100, show_default=True, help="Max results.")
@click.option(
    "--all",
    "fetch_all",
    is_flag=True,
    help="Fetch all matching spans (follows nextCursor).",
)
def traces_search(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    service: str | None,
    operation: str | None,
    error_filter: str | None,
    min_duration: int | None,
    max_duration: int | None,
    time_range: str | None,
    start: int | None,
    end: int | None,
    limit: int,
    fetch_all: bool,
) -> None:
    """Search traces with flexible filtering."""
    try:
        client = _get_client(endpoint, token, profile)
        filter_expr = _build_trace_filter(None, service, operation, error_filter)
        if min_duration is not None:
            filter_expr = _append_filter(filter_expr, f"durationNano >= {min_duration}")
        if max_duration is not None:
            filter_expr = _append_filter(filter_expr, f"durationNano <= {max_duration}")
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", profile_time_range=_profile_time_range(profile))
        if fetch_all:
            data = _traces_query_all(client, s, e, filter_expr, limit=100)
        else:
            payload = build_traces_query_payload(s, e, filter_expr, limit=limit)
            data = _unwrap_v5(client.query_builder_v5(payload))
        emit(data, fmt, table_keys=_TRACE_TABLE_KEYS)
    except Exception as ex:
        _handle_error(ex)


@traces.command("by-service")
@_common_options
@click.argument("service")
@click.option("--operation", default=None, help="Operation name filter.")
@click.option(
    "--error",
    "error_filter",
    type=click.Choice(["true", "false"]),
    default=None,
    help="Filter by error status.",
)
@click.option("--min-duration", type=int, default=None, help="Min duration in ns.")
@click.option("--max-duration", type=int, default=None, help="Max duration in ns.")
@click.option("--time-range", default=None, help="Relative time range. Default: 6h.")
@click.option("--start", type=int, default=None, help="Start timestamp in ms.")
@click.option("--end", type=int, default=None, help="End timestamp in ms.")
@click.option("--limit", type=int, default=100, show_default=True, help="Max results.")
def traces_by_service(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    service: str,
    operation: str | None,
    error_filter: str | None,
    min_duration: int | None,
    max_duration: int | None,
    time_range: str | None,
    start: int | None,
    end: int | None,
    limit: int,
) -> None:
    """Search traces for a specific service."""
    try:
        client = _get_client(endpoint, token, profile)
        filter_expr = _build_trace_filter(None, service, operation, error_filter)
        if min_duration is not None:
            filter_expr = _append_filter(filter_expr, f"durationNano >= {min_duration}")
        if max_duration is not None:
            filter_expr = _append_filter(filter_expr, f"durationNano <= {max_duration}")
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", profile_time_range=_profile_time_range(profile))
        payload = build_traces_query_payload(s, e, filter_expr, limit=limit)
        data = _unwrap_v5(client.query_builder_v5(payload))
        emit(data, fmt, table_keys=_TRACE_TABLE_KEYS)
    except Exception as ex:
        _handle_error(ex)


@traces.command("get")
@_common_options
@click.argument("trace_id")
@click.option("--time-range", default=None, help="Relative time range. Default: 6h.")
@click.option("--start", type=int, default=None, help="Start timestamp in ms.")
@click.option("--end", type=int, default=None, help="End timestamp in ms.")
def traces_get(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    trace_id: str,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Get all spans of a trace by trace ID."""
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", profile_time_range=_profile_time_range(profile))
        filter_expr = f"traceID = '{_q(trace_id)}'"
        payload = build_traces_query_payload(s, e, filter_expr, limit=1000)
        data = _unwrap_v5(client.query_builder_v5(payload))
        emit(data, fmt, table_keys=_TRACE_TABLE_KEYS)
    except Exception as ex:
        _handle_error(ex)


@traces.command("errors")
@_common_options
@click.option("--service", default=None, help="Service name filter.")
@click.option("--time-range", default=None, help="Relative time range. Default: 6h.")
@click.option("--start", type=int, default=None, help="Start timestamp in ms.")
@click.option("--end", type=int, default=None, help="End timestamp in ms.")
@click.option("--limit", type=int, default=1000, show_default=True, help="Max results.")
def traces_errors(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    service: str | None,
    time_range: str | None,
    start: int | None,
    end: int | None,
    limit: int,
) -> None:
    """Find error spans in traces."""
    try:
        client = _get_client(endpoint, token, profile)
        parts = ["hasError = true"]
        if service:
            parts.append(f"service.name in ['{_q(service)}']")
        filter_expr = " AND ".join(parts)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", profile_time_range=_profile_time_range(profile))
        payload = build_traces_query_payload(s, e, filter_expr, limit=limit)
        data = _unwrap_v5(client.query_builder_v5(payload))
        emit(data, fmt, table_keys=_TRACE_TABLE_KEYS)
    except Exception as ex:
        _handle_error(ex)


@traces.command("aggregate")
@_common_options
@click.option(
    "--aggregation",
    required=True,
    type=click.Choice(_VALID_AGGREGATIONS),
    help="Aggregation function.",
)
@click.option(
    "--aggregate-on", default=None, help="Field to aggregate on (e.g. durationNano)."
)
@click.option("--group-by", default=None, help="Comma-separated fields to group by.")
@click.option("--filter", "filter_expr", default=None, help="Filter expression.")
@click.option("--service", default=None, help="Service name shortcut filter.")
@click.option("--operation", default=None, help="Operation name shortcut filter.")
@click.option(
    "--error",
    "error_filter",
    type=click.Choice(["true", "false"]),
    default=None,
    help="Error status filter.",
)
@click.option(
    "--order-by",
    default=None,
    help="Order expression and direction (e.g. 'avg(durationNano) desc').",
)
@click.option("--time-range", default=None, help="Relative time range. Default: 1h.")
@click.option("--start", type=int, default=None, help="Start timestamp in ms.")
@click.option("--end", type=int, default=None, help="End timestamp in ms.")
@click.option("--limit", type=int, default=10, show_default=True, help="Max groups.")
def traces_aggregate(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    aggregation: str,
    aggregate_on: str | None,
    group_by: str | None,
    filter_expr: str | None,
    service: str | None,
    operation: str | None,
    error_filter: str | None,
    order_by: str | None,
    time_range: str | None,
    start: int | None,
    end: int | None,
    limit: int,
) -> None:
    """Aggregate traces (count, avg, percentiles, etc.) with optional grouping."""
    try:
        client = _get_client(endpoint, token, profile)
        if aggregation not in _NO_FIELD_AGGREGATIONS and not aggregate_on:
            raise click.UsageError(
                f"--aggregate-on is required for '{aggregation}' aggregation."
            )

        agg_expr = (
            f"{aggregation}({aggregate_on})" if aggregate_on else f"{aggregation}()"
        )
        combined_filter = _build_trace_filter(
            filter_expr, service, operation, error_filter
        )
        group_by_fields = _parse_group_by(group_by, "traces")
        order_expr, order_dir = _parse_order_by(order_by, agg_expr)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h", profile_time_range=_profile_time_range(profile))
        payload = build_aggregate_query_payload(
            "traces",
            s,
            e,
            agg_expr,
            combined_filter,
            group_by_fields,
            order_expr,
            order_dir,
            limit,
        )
        data = _unwrap_v5(client.query_builder_v5(payload), agg_label=agg_expr)
        emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


@traces.command("fields")
@_common_options
@click.option("--search", default="", help="Filter field names by text.")
def traces_fields(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, search: str
) -> None:
    """List available field names for trace queries."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.get_trace_available_fields(search)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@traces.command("field-values")
@_common_options
@click.argument("field_name")
@click.option("--search", default="", help="Filter values by text.")
def traces_field_values(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, field_name: str, search: str
) -> None:
    """Get available values for a trace field."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.get_trace_field_values(field_name, search)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@traces.command("detail")
@_common_options
@click.argument("trace_id")
@_time_options(default_range="6h")
def traces_detail(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    trace_id: str,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Full span detail for a trace: attributes, events, exceptions.

    Table mode shows one row per span (indented by depth); --format json
    returns the complete waterfall payload.
    """
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", profile_time_range=_profile_time_range(profile))
        data = client.get_trace_detail(trace_id, s, e)
        if fmt == "table":
            spans = (data.get("data") or {}).get("spans") or []
            rows = []
            for span in spans:
                depth = int(span.get("level") or 0)
                res = span.get("resource") or {}
                attrs = span.get("attributes") or {}
                dur_ms = int(span.get("duration_nano", 0)) / 1e6
                rows.append({
                    "span": f"{'  ' * depth}{span.get('name', '')}",
                    "service": res.get("service.name", ""),
                    "kind": span.get("kind_string", ""),
                    "duration_ms": round(dur_ms, 2),
                    "error": "Y" if span.get("has_error") else "",
                    "attrs": ", ".join(f"{k}={v}" for k, v in list(attrs.items())[:4]),
                    "events": len(span.get("events") or []),
                })
            emit({"data": rows}, "table",
                 table_keys=["span", "service", "duration_ms", "error", "attrs", "events"])
        else:
            emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


