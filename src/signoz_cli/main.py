"""SigNoz CLI -- query traces, logs, metrics, and alerts from SigNoz (0.138+)."""

from __future__ import annotations

# Import command modules for their registration side effects (each one
# attaches its commands to the shared ``cli`` group defined in
# signoz_cli.commands.common).
from signoz_cli.commands import (  # noqa: F401
    accounts,
    alerts,
    auth,
    channels,
    dashboards,
    logs,
    meta,
    metrics,
    raw,
    services,
    traces,
    views,
)
from signoz_cli.commands.common import (
    _add_skeleton_option,
    _append_filter,
    _apply_json_patch,
    _build_log_filter,
    _build_trace_filter,
    _dashboard_summary,
    _extract_alerts,
    _extract_attribute_keys,
    _get_client,
    _handle_error,
    _is_supported_server,
    _logs_query_all,
    _mask_token,
    _pagination_meta,
    _parse_group_by,
    _parse_order_by,
    _parse_version,
    _resolve_credentials,
    _resolve_format_callback,
    _stringify_labels,
    _traces_query_all,
    _unwrap_history_timeline,
    _unwrap_time_series,
    _unwrap_v5,
    _warn_v5,
    cli,
)
from signoz_cli.commands.raw import promql_query, query_raw, sql_query

__all__ = [
    "cli",
    "promql_query",
    "query_raw",
    "sql_query",
    "_add_skeleton_option",
    "_append_filter",
    "_apply_json_patch",
    "_build_log_filter",
    "_build_trace_filter",
    "_dashboard_summary",
    "_extract_alerts",
    "_extract_attribute_keys",
    "_get_client",
    "_handle_error",
    "_is_supported_server",
    "_logs_query_all",
    "_mask_token",
    "_pagination_meta",
    "_parse_group_by",
    "_parse_order_by",
    "_parse_version",
    "_resolve_credentials",
    "_resolve_format_callback",
    "_stringify_labels",
    "_traces_query_all",
    "_unwrap_history_timeline",
    "_unwrap_time_series",
    "_unwrap_v5",
    "_warn_v5",
]

# Wire skeleton option to raw query commands
_add_skeleton_option(query_raw)
_add_skeleton_option(sql_query)
_add_skeleton_option(promql_query)

if __name__ == "__main__":
    cli()
