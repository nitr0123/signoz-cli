"""alerts commands (list, history, CRUD)."""

from __future__ import annotations

import click

from signoz_cli.commands.common import (
    _HISTORY_TABLE_KEYS,
    _MAX_PAGES,
    _common_options,
    _confirm,
    _extract_alerts,
    _get_client,
    _handle_error,
    _pagination_meta,
    _profile_time_range,
    _read_json,
    _stringify_labels,
    _time_options,
    _unwrap_history_timeline,
    cli,
)
from signoz_cli.output import emit
from signoz_cli.timeutil import resolve_timestamps


@cli.group()
def alerts() -> None:
    """Query SigNoz alerts."""


@alerts.command("list")
@_common_options
@click.option("--limit", type=int, default=50, show_default=True, help="Max results.")
@click.option(
    "--offset", type=int, default=0, show_default=True, help="Pagination offset."
)
def alerts_list(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, limit: int, offset: int
) -> None:
    """List all active alerts."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_alerts()
        alerts_data = _extract_alerts(raw)
        page = alerts_data[offset : offset + limit]
        result = {
            "data": page,
            "pagination": _pagination_meta(len(alerts_data), offset, limit),
        }
        emit(result, fmt)
    except Exception as e:
        _handle_error(e)


@alerts.command("get")
@_common_options
@click.argument("rule_id")
def alerts_get(endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, rule_id: str) -> None:
    """Get details of a specific alert rule."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.get_alert(rule_id)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@alerts.command("history")
@_common_options
@click.argument("rule_id")
@_time_options(default_range="6h")
@click.option("--limit", type=int, default=20, show_default=True, help="Page size.")
@click.option(
    "--order",
    type=click.Choice(["asc", "desc"]),
    default="desc",
    show_default=True,
    help="Sort order.",
)
@click.option(
    "--state",
    type=click.Choice(
        ["inactive", "pending", "recovering", "firing", "nodata", "disabled"]
    ),
    default=None,
    help="Filter by alert state.",
)
@click.option(
    "--filter-expression",
    default=None,
    help="Filter expression over history labels (v2 API), e.g. \"service.name = 'checkout'\".",
)
@click.option("--cursor", default=None, help="Cursor from a previous page (nextCursor).")
@click.option(
    "--all",
    "fetch_all",
    is_flag=True,
    help="Follow cursors and fetch every page.",
)
def alerts_history(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    rule_id: str,
    time_range: str | None,
    start: int | None,
    end: int | None,
    limit: int,
    order: str,
    state: str | None,
    filter_expression: str | None,
    cursor: str | None,
    fetch_all: bool,
) -> None:
    """Get alert history timeline for a rule (v2 API with cursor pagination)."""
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", profile_time_range=_profile_time_range(profile))
        data = client.get_alert_history_timeline(
            rule_id,
            s,
            e,
            limit=limit if not fetch_all else 100,
            order=order,
            state=state,
            filter_expression=filter_expression,
            cursor=cursor,
        )
        if fetch_all and isinstance(data, dict):
            items = list(data.get("data", {}).get("items") or [])
            nxt = data.get("data", {}).get("nextCursor")
            for _ in range(_MAX_PAGES):
                if not nxt:
                    break
                page = client.get_alert_history_timeline(
                    rule_id, s, e, limit=100, order=order, state=state,
                    filter_expression=filter_expression, cursor=nxt,
                )
                payload = page.get("data", {}) if isinstance(page, dict) else {}
                new_items = payload.get("items") or []
                items.extend(new_items)
                nxt = payload.get("nextCursor")
                if not new_items:
                    break
            data = {
                "data": {"items": items},
                "total": data.get("total"),
            }
        emit(_unwrap_history_timeline(data), fmt, table_keys=_HISTORY_TABLE_KEYS)
    except Exception as ex:
        _handle_error(ex)


@alerts.command("history-stats")
@_common_options
@click.argument("rule_id")
@_time_options(default_range="24h")
def alerts_history_stats(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    rule_id: str,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Get aggregate statistics for a rule's history (v2 API)."""
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="24h", profile_time_range=_profile_time_range(profile))
        data = client.get_alert_history_stats(rule_id, s, e)
        emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


@alerts.command("overall-status")
@_common_options
@click.argument("rule_id")
@_time_options(default_range="24h")
def alerts_overall_status(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    rule_id: str,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Get overall status of a rule over a time range (v2 API)."""
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="24h", profile_time_range=_profile_time_range(profile))
        data = client.get_alert_history_overall_status(rule_id, s, e)
        emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


@alerts.command("top-contributors")
@_common_options
@click.argument("rule_id")
@_time_options(default_range="24h")
def alerts_top_contributors(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    rule_id: str,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Get top contributing label sets for a rule's history (v2 API)."""
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="24h", profile_time_range=_profile_time_range(profile))
        data = client.get_alert_history_top_contributors(rule_id, s, e)
        emit(_stringify_labels(data), fmt, table_keys=["fingerprint", "labels", "count"])
    except Exception as ex:
        _handle_error(ex)


@alerts.command("create")
@_common_options
@click.argument("json_file", type=click.Path())
def alerts_create(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, json_file: str) -> None:
    """Create an alert rule from a JSON file (v2 rule schema)."""
    try:
        rule = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        data = client.create_rule(rule)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@alerts.command("update")
@_common_options
@click.argument("rule_id")
@click.argument("json_file", type=click.Path())
def alerts_update(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, rule_id: str, json_file: str) -> None:
    """Replace an alert rule from a JSON file (full PUT)."""
    try:
        rule = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        data = client.update_rule(rule_id, rule)
        emit(data or {"status": "updated"}, fmt)
    except Exception as e:
        _handle_error(e)


@alerts.command("patch")
@_common_options
@click.argument("rule_id")
@click.argument("json_file", type=click.Path())
def alerts_patch(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, rule_id: str, json_file: str) -> None:
    """Apply partial updates to a rule (JSON-Patch ops sent to the v2 API).

    Example patch file:
      [{"op": "replace", "path": "/name", "value": "new name"}]
    """
    try:
        patches = _read_json(json_file)
        if not isinstance(patches, list):
            raise click.UsageError("Patch file must contain a JSON array.")
        client = _get_client(endpoint, token, profile)
        data = client.patch_rule(rule_id, patches)
        emit(data or {"status": "patched"}, fmt)
    except Exception as e:
        _handle_error(e)


@alerts.command("delete")
@_common_options
@click.argument("rule_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def alerts_delete(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, rule_id: str, yes: bool, dry_run: bool) -> None:
    """Delete an alert rule."""
    try:
        if dry_run:
            emit({"action": "delete", "resource": "alert rule", "id": rule_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete alert rule {rule_id}?")
        client = _get_client(endpoint, token, profile)
        emit(client.delete_rule(rule_id), fmt)
    except Exception as e:
        _handle_error(e)


@alerts.command("test")
@_common_options
@click.argument("json_file", type=click.Path())
@click.option("--start", type=int, default=None, help="Test-window start (ms).")
@click.option("--end", type=int, default=None, help="Test-window end (ms).")
@click.option("--time-range", default="1h", show_default=True, help="Test window.")
def alerts_test(endpoint: str | None, token: str | None, fmt: str,
                profile: str | None, json_file: str,
                start: int | None, end: int | None, time_range: str) -> None:
    """Dry-run a rule payload and see what would fire."""
    try:
        rule = _read_json(json_file)
        if not isinstance(rule, dict):
            raise click.UsageError("Rule file must contain a JSON object.")
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h", profile_time_range=_profile_time_range(profile))
        rule = dict(rule)
        rule.setdefault("start", s)
        rule.setdefault("end", e)
        data = client.test_rule(rule)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


# ===================================================================
# NOTIFICATION CHANNELS (admin)
# ===================================================================


