"""MCP server: expose SigNoz operations as Model Context Protocol tools.

Run with ``signoz mcp`` (stdio transport) and point any MCP host at it::

    {"command": "signoz", "args": ["mcp", "-p", "prod"]}

Design notes:

- Tool functions are plain sync functions over the existing client and
  query helpers; the SDK derives JSON Schema from their annotations.
- Authentication never travels through tool arguments (keys would land
  in agent transcripts): the connection comes from ``--profile`` /
  ``--endpoint`` flags, ``SIGNOZ_*`` env vars, or stored profiles.
- Mutating tools refuse unless the server was started with
  ``--allow-mutations``; anticipated failures raise the SDK's
  ``ToolError`` so clients see the message without a traceback.
- The ``mcp`` package is imported lazily so plain CLI usage never pays
  its import cost and never breaks when it is missing.
"""

from __future__ import annotations

from typing import Any, NoReturn

import click

from signoz_cli.commands.common import (
    _MAX_PAGES,
    _append_filter,
    _build_log_filter,
    _build_trace_filter,
    _dashboard_summary,
    _extract_alerts,
    _get_client,
    _handle_error,
    _is_supported_server,
    _logs_query_all,
    _pagination_meta,
    _profile_time_range,
    _q,
    _selected_profile_name,
    _traces_query_all,
    _unwrap_history_timeline,
    _unwrap_time_series,
    _unwrap_v5,
    _warn_v5,
    cli,
)
from signoz_cli.commands.metrics import _resolve_metric_temporality
from signoz_cli.query import (
    build_logs_query_payload,
    build_metrics_query_payload,
    build_traces_query_payload,
)
from signoz_cli.timeutil import resolve_timestamps

_ALLOW_MUTATIONS = False
_DEFAULT_PROFILE: str | None = None
_DEFAULT_ENDPOINT: str | None = None


def _fail(message: str) -> NoReturn:
    """Raise a client-facing tool error (traceback-free when SDK present)."""
    try:
        from mcp.server.mcpserver.exceptions import ToolError

        raise ToolError(message)
    except ImportError:
        raise RuntimeError(message) from None


def _require_mutations(tool_name: str) -> None:
    """Refuse mutating tools unless the server opted in."""
    if not _ALLOW_MUTATIONS:
        _fail(
            f"Tool '{tool_name}' mutates the SigNoz instance and this "
            "server runs read-only. Restart with "
            "'signoz mcp --allow-mutations' to enable it."
        )


def _client(profile: str | None, endpoint: str | None):
    """Build a client from tool overrides, falling back to server defaults.

    Credential/endpoint problems surface as client-facing tool errors,
    never as server-side crashes with tracebacks.
    """
    try:
        return _get_client(endpoint or _DEFAULT_ENDPOINT, None, profile or _DEFAULT_PROFILE)
    except click.UsageError as ex:
        _fail(f"Invalid arguments: {ex.message}")


def _clean(data: Any) -> Any:
    """Ensure MCP-safe plain JSON (stringify exotic server values)."""
    import json as _json

    return _json.loads(_json.dumps(data, default=str))


def _window(
    start: int | None,
    end: int | None,
    time_range: str | None,
    default_range: str,
    profile: str | None,
    unit: str = "ms",
) -> tuple[int, int]:
    for label, val in (("start", start), ("end", end)):
        if val is not None and (isinstance(val, bool)
                                or not isinstance(val, (int, float))):
            _fail(f"Invalid {label}: expected a millisecond timestamp number.")
    try:
        return resolve_timestamps(
            start, end, time_range,
            default_range=default_range, unit=unit,
            profile_time_range=_profile_time_range(profile),
        )
    except ValueError as ex:
        _fail(f"Invalid time arguments: {ex}")
    except click.UsageError as ex:
        _fail(f"Invalid arguments: {ex.message}")


def _call(fn, *args, **kwargs) -> Any:
    """Run a client call, mapping errors to client-facing tool errors."""
    from signoz_cli.client import SigNozApiError

    try:
        return _clean(fn(*args, **kwargs))
    except SigNozApiError as ex:
        _fail(f"SigNoz API error (HTTP {ex.status_code}): {ex.message}")
    except click.UsageError as ex:
        _fail(f"Invalid arguments: {ex.message}")
    except Exception as ex:  # noqa: BLE001 - transport errors surface as text
        _fail(f"Request failed: {ex}")


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

def logs_search(
    service: str | None = None,
    severity: str | None = None,
    search_text: str | None = None,
    query: str | None = None,
    filter_expression: str | None = None,
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    limit: int = 100,
    fetch_all: bool = False,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Search SigNoz logs with service/severity/text filters over a time window."""
    client = _client(profile, endpoint)
    try:
        filt = _build_log_filter(query, service, severity, search_text)
        if filter_expression:
            filt = f"{filt} AND {filter_expression}" if filt else filter_expression
        s, e = _window(start, end, time_range, "1h", profile)
        if fetch_all:
            data = _logs_query_all(client, s, e, filt, limit=100)
        else:
            payload = build_logs_query_payload(s, e, filt, limit=limit)
            data = _unwrap_v5(_call(client.query_builder_v5, payload))
        return _clean(data)
    finally:
        client.close()


def traces_search(
    service: str | None = None,
    operation: str | None = None,
    error_filter: str | None = None,
    min_duration_ns: int | None = None,
    max_duration_ns: int | None = None,
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    limit: int = 100,
    fetch_all: bool = False,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Search SigNoz trace spans with service/operation/error filters."""
    client = _client(profile, endpoint)
    try:
        filt = _build_trace_filter(None, service, operation, error_filter)
        if min_duration_ns is not None:
            filt = _append_filter(filt, f"durationNano >= {min_duration_ns}")
        if max_duration_ns is not None:
            filt = _append_filter(filt, f"durationNano <= {max_duration_ns}")
        s, e = _window(start, end, time_range, "6h", profile)
        if fetch_all:
            data = _traces_query_all(client, s, e, filt, limit=100)
        else:
            payload = build_traces_query_payload(s, e, filt, limit=limit)
            data = _unwrap_v5(_call(client.query_builder_v5, payload))
        return _clean(data)
    finally:
        client.close()


def traces_get(
    trace_id: str,
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Get all spans of one trace by trace ID."""
    client = _client(profile, endpoint)
    try:
        s, e = _window(start, end, time_range, "6h", profile)
        payload = build_traces_query_payload(
            s, e, f"traceID = '{_q(trace_id)}'", limit=1000)
        return _clean(_unwrap_v5(_call(client.query_builder_v5, payload)))
    finally:
        client.close()


def metrics_search(
    search_text: str = "",
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Find metric names matching free text."""
    client = _client(profile, endpoint)
    try:
        return _call(client.search_metric_by_text, search_text)
    finally:
        client.close()


def metrics_query(
    metric_name: str,
    time_aggregation: str = "avg",
    space_aggregation: str = "sum",
    temporality: str | None = None,
    group_by: str | None = None,
    filter_expression: str = "",
    step: int = 60,
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Query metric values over time as (series, timestamp, value) rows."""
    if step <= 0:
        _fail("--step must be positive (seconds per bucket).")
    client = _client(profile, endpoint)
    try:
        s, e = _window(start, end, time_range, "1h", profile)
        time_aggregation, temporality = _resolve_metric_temporality(
            client, metric_name, time_aggregation, temporality, "avg")
        groups = [g.strip() for g in group_by.split(",") if g.strip()] if group_by else []
        payload = build_metrics_query_payload(
            s, e, metric_name, temporality=temporality,
            time_aggregation=time_aggregation,
            space_aggregation=space_aggregation, group_by=groups,
            filter_expression=filter_expression, step_interval=step)
        resp = _call(client.query_builder_v5, payload)
        _warn_v5(resp)
        return _clean(_unwrap_time_series(resp))
    finally:
        client.close()


def promql_query(
    query: str,
    step: int = 60,
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Run a PromQL query against SigNoz metrics."""
    if step <= 0:
        _fail("--step must be positive (seconds per bucket).")
    client = _client(profile, endpoint)
    try:
        s, e = _window(start, end, time_range, "1h", profile)
        resp = _call(client.query_promql, query, s, e, step=step)
        _warn_v5(resp)
        return _clean(_unwrap_time_series(resp))
    finally:
        client.close()


def sql_query(
    query: str,
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Run raw ClickHouse SQL against SigNoz telemetry tables."""
    client = _client(profile, endpoint)
    try:
        s, e = _window(start, end, time_range, "6h", profile)
        return _clean(_unwrap_v5(_call(client.query_sql, query, s, e)))
    finally:
        client.close()


def dashboards_list(
    limit: int = 100,
    offset: int = 0,
    query: str | None = None,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """List dashboards as id/name/description/tags summaries with pagination."""
    client = _client(profile, endpoint)
    try:
        raw = _call(
            client.list_dashboards_v2, limit=limit, offset=offset, query=query)
        payload = raw.get("data", {}) if isinstance(raw, dict) else {}
        items = list(payload.get("dashboards") or []) if isinstance(payload, dict) else []
        total = payload.get("total", len(items)) if isinstance(payload, dict) else len(items)
        return {
            "data": [_dashboard_summary(d) for d in items],
            "pagination": _pagination_meta(int(total), offset, limit),
        }
    finally:
        client.close()


def dashboards_get(
    dashboard_id: str,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Get the full JSON document of one dashboard by id."""
    client = _client(profile, endpoint)
    try:
        return _call(client.get_dashboard_v2, dashboard_id)
    finally:
        client.close()


def alerts_list(
    limit: int = 100,
    offset: int = 0,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """List firing/active alerts with severity, state and rule references."""
    client = _client(profile, endpoint)
    try:
        rows = _extract_alerts(_call(client.list_alerts))
        return {
            "data": rows[offset:offset + limit],
            "pagination": _pagination_meta(len(rows), offset, limit),
        }
    finally:
        client.close()


def alerts_get(
    rule_id: str,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Get one alert rule definition by id."""
    client = _client(profile, endpoint)
    try:
        return _call(client.get_alert, rule_id)
    finally:
        client.close()


def alerts_history(
    rule_id: str,
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    limit: int = 20,
    order: str = "desc",
    state: str | None = None,
    filter_expression: str | None = None,
    fetch_all: bool = False,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Get the firing timeline of an alert rule over a time window."""
    if order not in ("asc", "desc"):
        _fail("order must be 'asc' or 'desc'.")
    client = _client(profile, endpoint)
    try:
        s, e = _window(start, end, time_range, "6h", profile)
        data = _call(
            client.get_alert_history_timeline, rule_id, s, e,
            limit=limit if not fetch_all else 100, order=order,
            state=state, filter_expression=filter_expression)
        if fetch_all and isinstance(data, dict):
            items = list(data.get("data", {}).get("items") or [])
            nxt = data.get("data", {}).get("nextCursor")
            for _ in range(_MAX_PAGES):
                if not nxt:
                    break
                page = _call(
                    client.get_alert_history_timeline, rule_id, s, e,
                    limit=100, order=order, state=state,
                    filter_expression=filter_expression, cursor=nxt)
                payload = page.get("data", {}) if isinstance(page, dict) else {}
                new_items = payload.get("items") or []
                items.extend(new_items)
                nxt = payload.get("nextCursor")
                if not new_items:
                    break
            data = {"data": {"items": items}, "total": data.get("total")}
        return _clean(_unwrap_history_timeline(data))
    finally:
        client.close()


def alerts_test(
    rule: dict[str, Any],
    time_range: str = "1h",
    start: int | None = None,
    end: int | None = None,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Dry-run an alert rule payload and see what would fire (no changes)."""
    client = _client(profile, endpoint)
    try:
        s, e = _window(start, end, time_range, "1h", profile)
        body = dict(rule)
        body.setdefault("start", s)
        body.setdefault("end", e)
        return _call(client.test_rule, body)
    finally:
        client.close()


def services_list(
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """List monitored services in a time window."""
    client = _client(profile, endpoint)
    try:
        s, e = _window(start, end, time_range, "6h", profile, unit="ns")
        return _call(client.list_services, str(s), str(e))
    finally:
        client.close()


def views_list(
    source_page: str = "logs",
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """List saved explorer views for logs, traces or metrics."""
    if source_page not in ("logs", "traces", "metrics"):
        _fail("source_page must be one of: logs, traces, metrics.")
    client = _client(profile, endpoint)
    try:
        raw = _call(client.list_saved_views, source_page)
        items = raw.get("data") if isinstance(raw, dict) else raw
        return {"data": items if isinstance(items, list) else []}
    finally:
        client.close()


def views_get(
    view_id: str,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Get one saved view by id."""
    client = _client(profile, endpoint)
    try:
        return _call(client.get_saved_view, view_id)
    finally:
        client.close()


def channels_list(
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """List notification channels."""
    client = _client(profile, endpoint)
    try:
        return _call(client.list_channels)
    finally:
        client.close()


def channels_get(
    channel_id: str,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Get one notification channel by id."""
    client = _client(profile, endpoint)
    try:
        return _call(client.get_channel, channel_id)
    finally:
        client.close()


def server_status(
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Check connectivity, server version and compatibility in one call."""
    client = _client(profile, endpoint)
    try:
        me = _call(client.verify)
        account = me.get("data", me) if isinstance(me, dict) else me
        try:
            server_version = (client.get_server_version() or {}).get("version", "")
        except Exception:
            server_version = ""
        return {
            "endpoint": client.endpoint,
            "profile": _selected_profile_name(profile) or "<none>",
            "server_version": server_version or "<unknown>",
            "compatible": _is_supported_server(server_version),
            "account": account.get("name", "") if isinstance(account, dict) else "",
        }
    finally:
        client.close()


def doctor_check(
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Run config/credential/TLS/connectivity/auth/version diagnostics.

    Returns pass/warn/fail rows; secrets are never included.
    """
    import stat as _stat

    from signoz_cli.config import (
        CONFIG_FILE,
        load_config,
        select_profile_name,
    )

    rows: list[dict[str, str]] = []
    cfg = load_config()
    if not CONFIG_FILE.exists():
        rows.append({"check": "config", "status": "warn",
                     "detail": "No config file yet."})
    elif not isinstance(cfg, dict):
        rows.append({"check": "config", "status": "fail",
                     "detail": "Config file does not contain an object."})
    else:
        try:
            mode = oct(_stat.S_IMODE(CONFIG_FILE.stat().st_mode))
        except OSError:
            mode = "unknown"
        rows.append({"check": "config", "status": "pass"
                     if mode == "0o600" else "warn",
                     "detail": f"Config mode is {mode}."})
    try:
        selected = select_profile_name(
            cfg if isinstance(cfg, dict) else {}, profile, None)
    except Exception:
        selected = None
        rows.append({"check": "profile", "status": "pass",
                     "detail": f"Using profile '{selected}'."
                     if selected else "No profile selected."})
    try:
        client = _client(profile, endpoint)
    except Exception as ex:
        rows.append({"check": "credentials", "status": "fail", "detail": str(ex)})
        return _clean(rows)
    try:
        me = _call(client.verify)
        account = me.get("data", me) if isinstance(me, dict) else me
        name = account.get("name", "") if isinstance(account, dict) else ""
        rows.append({"check": "auth", "status": "pass",
                     "detail": f"API key accepted{f' ({name})' if name else ''}."})
    except Exception as ex:
        rows.append({"check": "auth", "status": "fail", "detail": str(ex)})
        return _clean(rows)
    finally:
        client.close()
    try:
        server_version = (client.get_server_version() or {}).get("version", "")
    except Exception:
        server_version = ""
    rows.append({"check": "version", "status": "pass"
                 if _is_supported_server(server_version) else "warn",
                 "detail": f"Server {server_version or '<unknown>'}"})
    return _clean(rows)


def metrics_cardinality(
    metric_name: str,
    group_by: str,
    top_n: int = 10,
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Per-attribute series cardinality and top groups by volume."""
    if top_n <= 0:
        _fail("--top must be positive.")
    from signoz_cli.query import build_metrics_query_payload

    labels = [g.strip() for g in group_by.split(",") if g.strip()]
    if not labels:
        _fail("--by needs at least one attribute key.")
    client = _client(profile, endpoint)
    try:
        s, e = _window(start, end, time_range, "24h", profile)
        rows: list[dict[str, Any]] = []
        for label in labels:
            payload = build_metrics_query_payload(
                s, e, metric_name, temporality="Cumulative",
                time_aggregation="count", space_aggregation="sum",
                group_by=[label], filter_expression="", step_interval=300)
            totals: dict[str, float] = {}
            for row in _unwrap_time_series(
                    _call(client.query_builder_v5, payload)):
                if not isinstance(row, dict):
                    continue
                raw_value = row.get("value")
                if raw_value is None:
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if value != value:
                    continue
                series = str(row.get("series", ""))
                totals[series] = totals.get(series, 0.0) + value
            for series, total in sorted(
                    totals.items(), key=lambda kv: -kv[1])[:top_n]:
                group = series.split("=", 1)[-1] if "=" in series else series
                rows.append({"label": label, "groups": len(totals),
                             "group": group, "events": total})
        return _clean(rows)
    finally:
        client.close()


def saved_list() -> Any:
    """List locally saved named queries (names, kinds, timestamps)."""
    from signoz_cli.commands.saved import _read_saved

    return _clean([{"name": e.get("name", ""), "kind": e.get("kind", ""),
                    "saved_at": e.get("saved_at", "")}
                   for e in _read_saved()])


def saved_show(name: str) -> Any:
    """Show one saved query definition by name."""
    from signoz_cli.commands.saved import _find_saved

    entry = _find_saved(name)
    if entry is None:
        _fail(f"Unknown query '{name}'.")
    return _clean(entry)


def saved_run(
    name: str,
    time_range: str | None = None,
    start: int | None = None,
    end: int | None = None,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Run a locally saved query with a fresh time window."""
    from signoz_cli.commands.saved import _find_saved

    entry = _find_saved(name)
    if entry is None:
        _fail(f"Unknown query '{name}'.")
    assert isinstance(entry, dict)
    kind = entry.get("kind")
    payload = entry.get("payload")
    client = _client(profile, endpoint)
    try:
        s, e = _window(start, end, time_range, "1h", profile)
        if kind == "promql":
            if not isinstance(payload, str):
                _fail(f"Saved query '{name}' has a corrupt payload.")
            step = entry.get("step", 60)
            if not isinstance(step, int) or step <= 0:
                _fail(f"Saved query '{name}' has an invalid step.")
            resp = _call(client.query_promql, payload, s, e, step=step)
            _warn_v5(resp)
            return _clean(_unwrap_time_series(resp))
        if kind == "sql":
            if not isinstance(payload, str):
                _fail(f"Saved query '{name}' has a corrupt payload.")
            return _clean(_unwrap_v5(_call(client.query_sql, payload, s, e)))
        if kind == "builder":
            if not isinstance(payload, dict):
                _fail(f"Saved query '{name}' has a corrupt payload.")
            body = dict(payload)
            body["start"], body["end"] = s, e
            return _clean(_unwrap_v5(_call(client.query_builder_v5, body)))
        _fail(f"Saved query '{name}' has unknown kind {kind!r}.")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Mutation tools (gated behind --allow-mutations)
# ---------------------------------------------------------------------------

def dashboards_create(
    dashboard: dict[str, Any],
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Create a dashboard from a v2-spec JSON document."""
    _require_mutations("dashboards_create")
    client = _client(profile, endpoint)
    try:
        return _call(client.create_dashboard_v2, dashboard)
    finally:
        client.close()


def dashboards_update(
    dashboard_id: str,
    dashboard: dict[str, Any],
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Replace a dashboard document by id."""
    _require_mutations("dashboards_update")
    client = _client(profile, endpoint)
    try:
        return _call(client.update_dashboard_v2, dashboard_id, dashboard)
    finally:
        client.close()


def dashboards_delete(
    dashboard_id: str,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Delete a dashboard by id. Irreversible."""
    _require_mutations("dashboards_delete")
    client = _client(profile, endpoint)
    try:
        return _call(client.delete_dashboard_v2, dashboard_id)
    finally:
        client.close()


def alerts_create(
    rule: dict[str, Any],
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Create an alert rule from a JSON document. Prefer alerts_test first."""
    _require_mutations("alerts_create")
    client = _client(profile, endpoint)
    try:
        return _call(client.create_rule, rule)
    finally:
        client.close()


def alerts_update(
    rule_id: str,
    rule: dict[str, Any],
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Replace an alert rule by id."""
    _require_mutations("alerts_update")
    client = _client(profile, endpoint)
    try:
        return _call(client.update_rule, rule_id, rule)
    finally:
        client.close()


def alerts_delete(
    rule_id: str,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Delete an alert rule by id. Irreversible."""
    _require_mutations("alerts_delete")
    client = _client(profile, endpoint)
    try:
        return _call(client.delete_rule, rule_id)
    finally:
        client.close()


def channels_create(
    channel: dict[str, Any],
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Create a notification channel from a JSON document."""
    _require_mutations("channels_create")
    client = _client(profile, endpoint)
    try:
        return _call(client.create_channel, channel)
    finally:
        client.close()


def channels_delete(
    channel_id: str,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Delete a notification channel by id. Irreversible."""
    _require_mutations("channels_delete")
    client = _client(profile, endpoint)
    try:
        return _call(client.delete_channel, channel_id)
    finally:
        client.close()


def views_create(
    view: dict[str, Any],
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Create a saved explorer view from a JSON document."""
    _require_mutations("views_create")
    client = _client(profile, endpoint)
    try:
        return _call(client.create_saved_view, view)
    finally:
        client.close()


def views_delete(
    view_id: str,
    profile: str | None = None,
    endpoint: str | None = None,
) -> Any:
    """Delete a saved view by id. Irreversible."""
    _require_mutations("views_delete")
    client = _client(profile, endpoint)
    try:
        return _call(client.delete_saved_view, view_id)
    finally:
        client.close()


_READ_TOOLS = [
    logs_search, traces_search, traces_get, metrics_search, metrics_query,
    promql_query, sql_query, dashboards_list, dashboards_get, alerts_list,
    alerts_get, alerts_history, alerts_test, services_list, views_list,
    views_get, channels_list, channels_get, server_status, doctor_check,
    metrics_cardinality, saved_list, saved_show, saved_run,
]
_MUTATION_TOOLS = [
    dashboards_create, dashboards_update, dashboards_delete,
    alerts_create, alerts_update, alerts_delete,
    channels_create, channels_delete, views_create, views_delete,
]


def build_server():
    """Create the MCP server with read tools (mutations gated at call time)."""
    from mcp.server import MCPServer

    server = MCPServer(
        "signoz",
        instructions=(
            "SigNoz observability: search logs/traces, query metrics "
            "(PromQL/SQL/builder), inspect dashboards, alerts, services, "
            "views and channels. Mutating tools refuse unless the server "
            "was started with --allow-mutations."
        ),
    )
    for fn in _READ_TOOLS + _MUTATION_TOOLS:
        server.tool(name=fn.__name__)(fn)  # type: ignore[type-var]
    return server


@cli.command("mcp")
@click.option(
    "--endpoint", default=None,
    help="SigNoz instance URL (env: SIGNOZ_ENDPOINT). Overrides the profile.",
)
@click.option(
    "-p", "--profile", "profile_name", default=None,
    help="Named connection profile (env: SIGNOZ_PROFILE).",
)
@click.option(
    "--allow-mutations", is_flag=True,
    help="Enable create/update/delete tools (default: read-only).",
)
def mcp_cmd(
    endpoint: str | None, profile_name: str | None, allow_mutations: bool,
) -> None:
    """Serve SigNoz operations over MCP stdio for AI assistants.

    Connection comes from --profile/--endpoint, SIGNOZ_* env vars, or
    stored profiles (same resolution as every other command). No token
    is accepted here or by any tool: keys must live in the environment
    or the config file, never in agent transcripts.

    \b
    Examples:
      signoz mcp -p prod                  # read-only (default)
      signoz mcp -p prod --allow-mutations
    """
    global _ALLOW_MUTATIONS, _DEFAULT_PROFILE, _DEFAULT_ENDPOINT  # noqa: PLW0603
    _ALLOW_MUTATIONS = allow_mutations
    _DEFAULT_PROFILE = profile_name
    _DEFAULT_ENDPOINT = endpoint
    try:
        server = build_server()
        server.run()
    except Exception as e:
        _handle_error(e)
