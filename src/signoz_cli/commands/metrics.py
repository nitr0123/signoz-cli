"""metrics commands."""

from __future__ import annotations

from typing import Any

import click

from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _extract_attribute_keys,
    _finish_time_series,
    _flatten_field_keys,
    _flatten_field_values,
    _get_client,
    _handle_error,
    _pagination_meta,
    _profile_time_range,
    _time_options,
    _time_series_options,
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
        emit({"data": _flatten_field_keys(data)}, fmt)
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
        emit({"data": _flatten_field_values(data)}, fmt)
    except Exception as e:
        _handle_error(e)


def _lookup_metric_type(client, metric_name: str) -> str | None:
    """Return the metric's data type (Gauge/Sum/...) or None if unknown.

    Never raises: metadata lookup is best-effort and must not break queries.
    """
    try:
        data = client.search_metric_by_text(metric_name)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    inner = data.get("data", {})
    keys = inner.get("attributeKeys", []) if isinstance(inner, dict) else []
    for entry in keys:
        if isinstance(entry, dict) and entry.get("key") == metric_name:
            return entry.get("type")
    return None


def _resolve_metric_temporality(
    client, metric_name: str, time_aggregation: str,
    temporality: str | None, default_aggregation: str,
) -> tuple[str, str]:
    """Pick (time_aggregation, temporality), auto-detecting counters.

    Explicit --temporality is always honoured. Otherwise rate/sum imply
    Cumulative (as before); a default aggregation on a Sum metric is
    upgraded to sum/Cumulative, since avg/Unspecified returns no rows
    for counters.
    """
    if temporality is not None:
        return time_aggregation, temporality
    if time_aggregation in ("rate", "sum"):
        return time_aggregation, "Cumulative"
    if time_aggregation == default_aggregation:
        if _lookup_metric_type(client, metric_name) == "Sum":
            return "sum", "Cumulative"
    return time_aggregation, "Unspecified"


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
@_time_series_options
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
    chart: bool,
    compare: str | None,
    anomalies: bool,
    anomaly_window: int,
    anomaly_threshold: float,
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
        time_aggregation, temporality = _resolve_metric_temporality(
            client, metric_name, time_aggregation, temporality, "avg"
        )
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

        def _refetch(s2: int, e2: int) -> Any:
            payload2 = build_metrics_query_payload(
                s2, e2, metric_name,
                temporality=temporality,
                time_aggregation=time_aggregation,
                space_aggregation=space_aggregation,
                group_by=group_by_fields,
                filter_expression=filter_expr,
                step_interval=step,
            )
            return _unwrap_time_series(client.query_builder_v5(payload2))

        _finish_time_series(
            rows, fmt, s, e, chart=chart, compare=compare,
            anomalies=anomalies, anomaly_window=anomaly_window,
            anomaly_threshold=anomaly_threshold, refetch=_refetch,
        )
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
        time_aggregation, temporality = _resolve_metric_temporality(
            client, metric_name, time_aggregation, temporality, "max"
        )
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
        # Empty-result contract: emit a machine-readable empty payload so
        # --json stays parseable; the human notice goes to stderr.
        emit([], fmt)
        return
    emit(rows, fmt)




@metrics.command("top")
@_common_options
@click.argument("metric_name")
@click.option("--top", "top_n", type=int, default=10, show_default=True,
              help="Show the top N series.")
@click.option("--group-by", default=None, help="Comma-separated attribute keys.")
@click.option("--filter", "filter_expr", default="", help="Filter expression.")
@_time_options(default_range="1h")
def metrics_top(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    metric_name: str,
    top_n: int,
    group_by: str | None,
    filter_expr: str,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Rank series by latest value (sum over the window, Cumulative).

    Uses sum/Cumulative fixed: this ranks by total volume, not by gauge
    level. For other aggregations use `metrics query` with --json and sort.
    """
    if top_n <= 0:
        raise click.UsageError("--top must be positive.")
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h", profile_time_range=_profile_time_range(profile))
        group_by_fields = [g.strip() for g in group_by.split(",") if g.strip()] if group_by else []
        payload = build_metrics_query_payload(
            s, e, metric_name,
            temporality="Cumulative",
            time_aggregation="sum",
            space_aggregation="sum",
            group_by=group_by_fields,
            filter_expression=filter_expr,
            step_interval=60,
        )
        resp = client.query_builder_v5(payload)
        _warn_v5(resp)
        rows = _unwrap_time_series(resp)
        # No empty-metrics hint here: top already queries sum/Cumulative,
        # so the generic counters hint would mislead.
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            series = str(row.get("series", ""))
            ts = row.get("timestamp")
            # Tolerant ordering key: numbers sort naturally, anything else
            # falls back to string order instead of raising on mixed types.
            order = (0, ts) if isinstance(ts, (int, float)) else (1, str(ts))
            prev = latest.get(series)
            prev_order = prev["_order"] if prev else None
            if prev is None or (prev_order is not None and order > prev_order):
                latest[series] = {"series": series, "timestamp": ts,
                                  "value": row.get("value"), "_order": order}
        ranked = sorted(
            latest.values(),
            key=lambda r: (r["value"] is None,
                           -(r["value"] or 0) if isinstance(r["value"], (int, float)) else 0,
                           str(r["value"])),
        )
        out = [{k: r[k] for k in ("series", "timestamp", "value")} for r in ranked[:top_n]]
        emit(out, fmt, table_keys=["series", "timestamp", "value"])
    except Exception as ex:
        _handle_error(ex)


@metrics.command("cardinality")
@_common_options
@click.argument("metric_name")
@click.option("--by", "group_by", required=True,
              help="Comma-separated attribute keys; one probe per key.")
@click.option("--top", "top_n", type=int, default=10, show_default=True,
              help="Show the top N groups per attribute.")
@click.option("--temporality", default=None,
              type=click.Choice(["Unspecified", "Delta", "Cumulative"]),
              help="Metric temporality (default Cumulative for counts).")
@click.option("--filter", "filter_expr", default="", help="Filter expression.")
@click.option("--step", type=int, default=300, show_default=True,
              help="Step interval in seconds.")
@_time_options(default_range="24h")
def metrics_cardinality(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    metric_name: str,
    group_by: str,
    top_n: int,
    temporality: str | None,
    filter_expr: str,
    step: int,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Show per-attribute series cardinality and top groups by volume.

    Runs one count query per --by key: `groups` is the number of distinct
    series for that attribute (the cardinality driver to watch for cost),
    `events` the window total per group. Defaults to Cumulative, which is
    what sample counts need; override with --temporality if you know why.
    """
    if top_n <= 0:
        raise click.UsageError("--top must be positive.")
    if step <= 0:
        raise click.UsageError("--step must be positive (seconds per bucket).")
    labels = [g.strip() for g in group_by.split(",") if g.strip()]
    if not labels:
        raise click.UsageError("--by needs at least one attribute key.")
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="24h", profile_time_range=_profile_time_range(profile))
        rows: list[dict[str, Any]] = []
        for label in labels:
            payload = build_metrics_query_payload(
                s, e, metric_name,
                temporality=temporality or "Cumulative",
                time_aggregation="count",
                space_aggregation="sum",
                group_by=[label],
                filter_expression=filter_expr,
                step_interval=step,
            )
            resp = client.query_builder_v5(payload)
            _warn_v5(resp)
            totals: dict[str, float] = {}
            for row in _unwrap_time_series(resp):
                if not isinstance(row, dict):
                    continue
                raw_value = row.get("value")
                if raw_value is None:
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if value != value:  # NaN carries no volume information
                    continue
                series = str(row.get("series", ""))
                totals[series] = totals.get(series, 0.0) + value
            ranked = sorted(totals.items(), key=lambda kv: -kv[1])[:top_n]
            for series, total in ranked:
                group = series.split("=", 1)[-1] if "=" in series else series
                rows.append({"label": label, "groups": len(totals),
                             "group": group, "events": total})
        emit(rows, fmt, table_keys=["label", "groups", "group", "events"])
    except Exception as ex:
        _handle_error(ex)


def _label_text(labels: Any) -> str:
    parts = []
    for lab in labels or []:
        if not isinstance(lab, dict):
            continue
        key = lab.get("key")
        name = key.get("name", "") if isinstance(key, dict) else str(key or "")
        parts.append(f"{name}={lab.get('value', '')}")
    return ", ".join(parts)


@metrics.command("inspect")
@_common_options
@click.argument("metric_name")
@_time_options(default_range="30m")
@click.option("--filter", "filter_expr", default="", help="Filter expression on labels.")
def metrics_inspect(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, metric_name: str, time_range: str | None,
                    start: int | None, end: int | None, filter_expr: str) -> None:
    """Show the raw data points of a metric, one row per series.

    Useful to see exactly what a collector is sending before building a
    query on it. --format json returns every point.
    """
    try:
        s, e = resolve_timestamps(start, end, time_range, default_range="30m",
                                  profile_time_range=_profile_time_range(profile))
        client = _get_client(endpoint, token, profile)
        raw = client.inspect_metric(metric_name, s, e, filter_expr)
        if fmt == "json":
            emit(raw, fmt)
            return
        data = raw.get("data", raw) if isinstance(raw, dict) else {}
        rows = []
        for series in (data.get("series") if isinstance(data, dict) else None) or []:
            if not isinstance(series, dict):
                continue
            values = [v for v in (series.get("values") or []) if isinstance(v, dict)]
            last = values[-1] if values else {}
            rows.append({"labels": _label_text(series.get("labels")),
                         "points": len(values),
                         "lastTimestamp": last.get("timestamp", ""),
                         "lastValue": last.get("value", "")})
        emit({"data": rows}, fmt)
    except Exception as ex:
        _handle_error(ex)


@metrics.command("treemap")
@_common_options
@_time_options(default_range="1h")
@click.option("--by", "mode", type=click.Choice(["timeseries", "samples"]),
              default="timeseries", show_default=True,
              help="Rank metrics by series count (cardinality) or by samples ingested.")
@click.option("--limit", type=int, default=20, show_default=True, help="Top N metrics.")
@click.option("--filter", "filter_expr", default="", help="Filter expression.")
def metrics_treemap(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, time_range: str | None, start: int | None,
                    end: int | None, mode: str, limit: int, filter_expr: str) -> None:
    """Rank metrics by share of total series or samples (find what costs most)."""
    try:
        s, e = resolve_timestamps(start, end, time_range, default_range="1h",
                                  profile_time_range=_profile_time_range(profile))
        client = _get_client(endpoint, token, profile)
        raw = client.get_metrics_treemap(s, e, mode=mode, limit=limit,
                                         filter_expression=filter_expr)
        if fmt == "json":
            emit(raw, fmt)
            return
        data = raw.get("data", raw) if isinstance(raw, dict) else {}
        entries = (data.get(mode) if isinstance(data, dict) else None) or []
        rows = [{"metric": t.get("metricName", ""),
                 "percent": round(float(t.get("percentage") or 0), 2),
                 mode: t.get("totalValue", 0)}
                for t in entries if isinstance(t, dict)]
        emit({"data": rows}, fmt)
    except Exception as ex:
        _handle_error(ex)


@metrics.group("reduction-rules")
def reduction_rules() -> None:
    """Drop or keep metric labels at ingest to cut series count.

    Needs SigNoz Cloud or a licensed enterprise server; others answer
    HTTP 501 ("metric volume control is not enabled"). Preview a rule's
    effect with `preview` before creating it.
    """


def _rule_body(metric: str | None, match: str, labels: tuple[str, ...]) -> dict[str, Any]:
    body: dict[str, Any] = {"matchType": match, "labels": list(labels)}
    if metric:
        body["metricName"] = metric
    return body


def _rule_row(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r.get("id", ""), "metric": r.get("metricName", ""),
            "match": r.get("matchType", ""), "labels": ", ".join(r.get("labels") or []),
            "active": r.get("active", ""), "ingestedSeries": r.get("ingestedSeries", ""),
            "retainedSeries": r.get("retainedSeries", "")}


@reduction_rules.command("list")
@_common_options
@click.option("--metric", default=None, help="Only rules for this metric.")
@click.option("--search", default=None, help="Search metric names.")
@click.option("--order-by", type=click.Choice(
    ["metric", "ingested_volume", "reduced_volume", "last_updated"]), default=None)
@click.option("--order", type=click.Choice(["asc", "desc"]), default=None)
@click.option("--limit", type=int, default=50, show_default=True)
@click.option("--offset", type=int, default=0, show_default=True)
def reduction_rules_list(endpoint: str | None, token: str | None, fmt: str,
                         profile: str | None, metric: str | None, search: str | None,
                         order_by: str | None, order: str | None,
                         limit: int, offset: int) -> None:
    """List metric reduction rules with their series savings."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_reduction_rules(metricName=metric, search=search, orderBy=order_by,
                                          order=order, limit=limit, offset=offset)
        if fmt == "json":
            emit(raw, fmt)
            return
        data = raw.get("data", raw) if isinstance(raw, dict) else {}
        rules = (data.get("rules") if isinstance(data, dict) else None) or []
        emit({"data": [_rule_row(r) for r in rules if isinstance(r, dict)]}, fmt)
    except Exception as ex:
        _handle_error(ex)


@reduction_rules.command("get")
@_common_options
@click.argument("rule_id")
def reduction_rules_get(endpoint: str | None, token: str | None, fmt: str,
                        profile: str | None, rule_id: str) -> None:
    """Get a metric reduction rule."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_reduction_rule(rule_id)
        data = raw.get("data") if isinstance(raw, dict) else None
        emit(raw if fmt == "json" or not isinstance(data, dict) else _rule_row(data), fmt)
    except Exception as ex:
        _handle_error(ex)


def _rule_options(f):
    f = click.option("--label", "labels", multiple=True, required=True,
                     help="Label the rule drops or keeps (repeatable).")(f)
    f = click.option("--match", type=click.Choice(["drop", "keep"]), required=True,
                     help="drop: remove the listed labels; keep: keep only them.")(f)
    return f


@reduction_rules.command("preview")
@_common_options
@click.argument("metric_name")
@_rule_options
@click.option("--lookback", default="24h", show_default=True,
              help="How much recent data to estimate from.")
def reduction_rules_preview(endpoint: str | None, token: str | None, fmt: str,
                            profile: str | None, metric_name: str, match: str,
                            labels: tuple[str, ...], lookback: str) -> None:
    """Estimate a rule's series reduction and the dashboards/alerts it affects."""
    from signoz_cli.timeutil import parse_time_range

    try:
        body = _rule_body(metric_name, match, labels)
        body["lookbackMs"] = parse_time_range(lookback)
        client = _get_client(endpoint, token, profile)
        raw = client.preview_reduction_rule(body)
        if fmt == "json":
            emit(raw, fmt)
            return
        data = raw.get("data", raw) if isinstance(raw, dict) else {}
        data = data if isinstance(data, dict) else {}
        emit({"metric": metric_name,
              "ingestedSeries": data.get("ingestedSeries", ""),
              "retainedSeries": data.get("retainedSeries", ""),
              "reductionPercent": data.get("reductionPercent", ""),
              "droppedLabels": ", ".join(data.get("droppedLabels") or []),
              "affectedAssets": len(data.get("affectedAssets") or [])}, fmt)
        for asset in data.get("affectedAssets") or []:
            if isinstance(asset, dict):
                click.echo(f"affects {asset.get('type', '')} '{asset.get('name', '')}' "
                           f"({', '.join(asset.get('impactedLabels') or [])})", err=True)
    except Exception as ex:
        _handle_error(ex)


@reduction_rules.command("create")
@_common_options
@click.argument("metric_name")
@_rule_options
def reduction_rules_create(endpoint: str | None, token: str | None, fmt: str,
                           profile: str | None, metric_name: str, match: str,
                           labels: tuple[str, ...]) -> None:
    """Create a metric reduction rule (applies to newly ingested data)."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.create_reduction_rule(_rule_body(metric_name, match, labels))
        data = raw.get("data") if isinstance(raw, dict) else None
        emit(raw if fmt == "json" or not isinstance(data, dict) else _rule_row(data), fmt)
    except Exception as ex:
        _handle_error(ex)


@reduction_rules.command("update")
@_common_options
@click.argument("rule_id")
@_rule_options
def reduction_rules_update(endpoint: str | None, token: str | None, fmt: str,
                           profile: str | None, rule_id: str, match: str,
                           labels: tuple[str, ...]) -> None:
    """Replace a rule's match type and labels."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.update_reduction_rule(rule_id, _rule_body(None, match, labels))
        data = raw.get("data") if isinstance(raw, dict) else None
        emit(raw if fmt == "json" or not isinstance(data, dict) else _rule_row(data), fmt)
    except Exception as ex:
        _handle_error(ex)


@reduction_rules.command("delete")
@_common_options
@click.argument("rule_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def reduction_rules_delete(endpoint: str | None, token: str | None, fmt: str,
                           profile: str | None, rule_id: str, yes: bool,
                           dry_run: bool) -> None:
    """Delete a metric reduction rule (full series are ingested again)."""
    try:
        if dry_run:
            emit({"action": "delete", "resource": "metric reduction rule", "id": rule_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete metric reduction rule {rule_id}?")
        client = _get_client(endpoint, token, profile)
        emit(client.delete_reduction_rule(rule_id), fmt)
    except Exception as ex:
        _handle_error(ex)


@reduction_rules.command("stats")
@_common_options
def reduction_rules_stats(endpoint: str | None, token: str | None, fmt: str,
                          profile: str | None) -> None:
    """Totals across all rules: series/samples before and after, estimated savings."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_reduction_stats()
        emit(raw if fmt == "json" else raw.get("data", raw), fmt)
    except Exception as ex:
        _handle_error(ex)
