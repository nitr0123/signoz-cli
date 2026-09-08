"""logs commands."""

from __future__ import annotations

from collections import OrderedDict

import click

from signoz_cli.client import SigNozApiError
from signoz_cli.commands.common import (
    _NO_FIELD_AGGREGATIONS,
    _VALID_AGGREGATIONS,
    _build_log_filter,
    _common_options,
    _flatten_field_keys,
    _flatten_field_values,
    _get_client,
    _handle_error,
    _logs_query_all,
    _parse_group_by,
    _parse_order_by,
    _profile_time_range,
    _q,
    _unwrap_v5,
    cli,
)
from signoz_cli.output import emit
from signoz_cli.query import build_aggregate_query_payload, build_logs_query_payload
from signoz_cli.timeutil import parse_time_range, resolve_timestamps


@cli.group()
def logs() -> None:
    """Query SigNoz logs."""


@logs.command("search")
@_common_options
@click.option("--query", default=None, help="Filter expression (SigNoz search syntax).")
@click.option("--service", default=None, help="Service name filter.")
@click.option(
    "--severity",
    default=None,
    help="Severity filter (DEBUG, INFO, WARN, ERROR, FATAL).",
)
@click.option(
    "--search-text", default=None, help="Text to search in log body (CONTAINS match)."
)
@click.option(
    "--time-range", default=None, help="Relative time range (e.g. 1h, 6h). Default: 1h."
)
@click.option("--start", type=int, default=None, help="Start timestamp in ms.")
@click.option("--end", type=int, default=None, help="End timestamp in ms.")
@click.option("--limit", type=int, default=100, show_default=True, help="Max results.")
@click.option(
    "--offset", type=int, default=0, show_default=True, help="Pagination offset."
)
@click.option(
    "--all",
    "fetch_all",
    is_flag=True,
    help="Fetch all matching rows (ignores --limit/--offset).",
)
def logs_search(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    query: str | None,
    service: str | None,
    severity: str | None,
    search_text: str | None,
    time_range: str | None,
    start: int | None,
    end: int | None,
    limit: int,
    offset: int,
    fetch_all: bool,
) -> None:
    """Search logs with flexible filtering across all services."""
    try:
        client = _get_client(endpoint, token, profile)
        filter_expr = _build_log_filter(query, service, severity, search_text)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h", profile_time_range=_profile_time_range(profile))
        if fetch_all:
            data = _logs_query_all(client, s, e, filter_expr, limit=100)
        else:
            payload = build_logs_query_payload(
                s, e, filter_expr, limit=limit, offset=offset
            )
            data = _unwrap_v5(client.query_builder_v5(payload))
        emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


@logs.command("errors")
@_common_options
@click.option("--service", default=None, help="Service name filter.")
@click.option("--time-range", default=None, help="Relative time range. Default: 6h.")
@click.option("--start", type=int, default=None, help="Start timestamp in ms.")
@click.option("--end", type=int, default=None, help="End timestamp in ms.")
@click.option("--limit", type=int, default=100, show_default=True, help="Max results.")
def logs_errors(
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
    """Get logs with ERROR or FATAL severity."""
    try:
        client = _get_client(endpoint, token, profile)
        parts = ["severity_text IN ['ERROR', 'FATAL']"]
        if service:
            parts.append(f"service.name = '{_q(service)}'")
        filter_expr = " AND ".join(parts)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", profile_time_range=_profile_time_range(profile))
        payload = build_logs_query_payload(s, e, filter_expr, limit=limit)
        data = _unwrap_v5(client.query_builder_v5(payload))
        emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


@logs.command("by-service")
@_common_options
@click.argument("service")
@click.option("--severity", default=None, help="Severity filter.")
@click.option("--search-text", default=None, help="Text to search in log body.")
@click.option("--time-range", default=None, help="Relative time range. Default: 6h.")
@click.option("--start", type=int, default=None, help="Start timestamp in ms.")
@click.option("--end", type=int, default=None, help="End timestamp in ms.")
@click.option("--limit", type=int, default=100, show_default=True, help="Max results.")
def logs_by_service(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    service: str,
    severity: str | None,
    search_text: str | None,
    time_range: str | None,
    start: int | None,
    end: int | None,
    limit: int,
) -> None:
    """Search logs for a specific service."""
    try:
        client = _get_client(endpoint, token, profile)
        filter_expr = _build_log_filter(None, service, severity, search_text)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", profile_time_range=_profile_time_range(profile))
        payload = build_logs_query_payload(s, e, filter_expr, limit=limit)
        data = _unwrap_v5(client.query_builder_v5(payload))
        emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


@logs.command("aggregate")
@_common_options
@click.option(
    "--aggregation",
    required=True,
    type=click.Choice(_VALID_AGGREGATIONS),
    help="Aggregation function.",
)
@click.option(
    "--aggregate-on",
    default=None,
    help="Field to aggregate on (required for non-count).",
)
@click.option("--group-by", default=None, help="Comma-separated fields to group by.")
@click.option("--filter", "filter_expr", default=None, help="Filter expression.")
@click.option("--service", default=None, help="Service name shortcut filter.")
@click.option("--severity", default=None, help="Severity shortcut filter.")
@click.option(
    "--order-by",
    default=None,
    help="Order expression and direction (e.g. 'count() desc').",
)
@click.option("--time-range", default=None, help="Relative time range. Default: 1h.")
@click.option("--start", type=int, default=None, help="Start timestamp in ms.")
@click.option("--end", type=int, default=None, help="End timestamp in ms.")
@click.option("--limit", type=int, default=10, show_default=True, help="Max groups.")
def logs_aggregate(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    aggregation: str,
    aggregate_on: str | None,
    group_by: str | None,
    filter_expr: str | None,
    service: str | None,
    severity: str | None,
    order_by: str | None,
    time_range: str | None,
    start: int | None,
    end: int | None,
    limit: int,
) -> None:
    """Aggregate logs (count, avg, sum, percentiles, etc.) with optional grouping."""
    try:
        client = _get_client(endpoint, token, profile)
        if aggregation not in _NO_FIELD_AGGREGATIONS and not aggregate_on:
            raise click.UsageError(
                f"--aggregate-on is required for '{aggregation}' aggregation."
            )

        agg_expr = (
            f"{aggregation}({aggregate_on})" if aggregate_on else f"{aggregation}()"
        )
        combined_filter = _build_log_filter(filter_expr, service, severity, None)
        group_by_fields = _parse_group_by(group_by, "logs")
        order_expr, order_dir = _parse_order_by(order_by, agg_expr)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h", profile_time_range=_profile_time_range(profile))
        payload = build_aggregate_query_payload(
            "logs",
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


@logs.command("fields")
@_common_options
@click.option("--search", default="", help="Filter field names by text.")
def logs_fields(endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, search: str) -> None:
    """List available field names for log queries."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.get_logs_available_fields(search)
        emit({"data": _flatten_field_keys(data)}, fmt)
    except Exception as e:
        _handle_error(e)


@logs.command("field-values")
@_common_options
@click.argument("field_name")
@click.option("--search", default="", help="Filter values by text.")
def logs_field_values(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, field_name: str, search: str
) -> None:
    """Get available values for a log field."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.get_logs_field_values(field_name, search)
        emit({"data": _flatten_field_values(data)}, fmt)
    except Exception as e:
        _handle_error(e)


@logs.command("tail")
@_common_options
@click.option("--service", default=None, help="Service name filter.")
@click.option("--severity", default=None, help="Severity filter (exact match, e.g. ERROR).")
@click.option("--search-text", default=None, help="Body text filter (CONTAINS).")
@click.option("--interval", type=float, default=3.0, show_default=True,
              help="Poll interval in seconds.")
@click.option("--window", default="30s", show_default=True,
              help="Look-back window per poll.")
def logs_tail(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    service: str | None,
    severity: str | None,
    search_text: str | None,
    interval: float,
    window: str,
) -> None:
    """Follow new logs as they arrive (poll-based; Ctrl-C to stop)."""
    import time as _time

    if interval <= 0:
        raise click.UsageError("--interval must be positive (seconds between polls).")
    try:
        client = _get_client(endpoint, token, profile)
    except Exception as ex:
        _handle_error(ex)
        return

    filter_expr = _build_log_filter(None, service, severity, search_text)
    # Ordered set of already-printed rows. Keyed on the server id when present,
    # otherwise a composite of timestamp+service+body -- the log select fields
    # do not include an id, so keying on "id" alone never dedupes and every
    # overlapping poll would reprint the same lines.
    seen: OrderedDict[str, None] = OrderedDict()
    click.echo("Tailing logs... Ctrl-C to stop.", err=True)
    try:
        while True:
            now_ms = int(_time.time() * 1000)
            win_ms = parse_time_range(window)
            payload = build_logs_query_payload(
                now_ms - win_ms, now_ms, filter_expr, limit=100
            )
            try:
                rows = _unwrap_v5(client.query_builder_v5(payload))
            except SigNozApiError as ex:
                if ex.status_code in (400, 401, 403, 404):
                    # Auth/permission/bad-request will never resolve by
                    # retrying: fail instead of spewing forever.
                    _handle_error(ex)
                    return
                click.echo(f"tail: {ex} (retrying)", err=True)
                _time.sleep(interval)
                continue
            except Exception as ex:
                click.echo(f"tail: {ex} (retrying)", err=True)
                _time.sleep(interval)
                continue
            if isinstance(rows, list):
                for row in reversed(rows):  # oldest first
                    key = str(
                        row.get("id")
                        or f"{row.get('timestamp', '')}|"
                        f"{row.get('service.name', '')}|{row.get('body', '')}"
                    )
                    if key in seen:
                        continue
                    seen[key] = None
                    click.echo(
                        f"{row.get('timestamp', '')} "
                        f"{row.get('severity_text', '')!s:5s} "
                        f"{row.get('service.name', '')}: {row.get('body', '')}"
                    )
            # bound memory: evict oldest keys first (insertion order preserved)
            while len(seen) > 50000:
                seen.popitem(last=False)
            _time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("Stopped.", err=True)


