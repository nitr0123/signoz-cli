"""SigNoz CLI -- query traces, logs, metrics, and alerts from SigNoz (0.138+)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import click
import httpx

from signoz_cli import __version__
from signoz_cli.client import SigNozApiError, SigNozClient
from signoz_cli.config import (
    CONFIG_FILE,
    get_profile_credentials,
    get_profiles,
    load_config,
    save_config,
    select_profile_name,
)
from signoz_cli.output import emit, set_output_file
from signoz_cli.query import (
    build_aggregate_query_payload,
    build_logs_query_payload,
    build_metrics_query_payload,
    build_traces_query_payload,
)
from signoz_cli.timeutil import parse_time_range, resolve_timestamps

# ---------------------------------------------------------------------------
# Shared options
# ---------------------------------------------------------------------------

_VALID_AGGREGATIONS = [
    "count",
    "count_distinct",
    "avg",
    "sum",
    "min",
    "max",
    "p50",
    "p75",
    "p90",
    "p95",
    "p99",
    "rate",
]
_NO_FIELD_AGGREGATIONS = {"count", "rate"}
SUPPORTED_SIGNOZ_MIN = (0, 136)
SUPPORTED_SIGNOZ_RANGE = ">=0.136"
_TRACE_TABLE_KEYS = [
    "timestamp",
    "service.name",
    "name",
    "durationNano",
    "hasError",
    "traceID",
]
_HISTORY_TABLE_KEYS = [
    "unixMilli",
    "ruleName",
    "state",
    "overallState",
    "value",
    "labels",
]


# Root-level --profile value (single-process CLI, so a module holder is safe).
# Precedence: leaf --profile flag > root --profile flag > SIGNOZ_PROFILE > default.
_ROOT_PROFILE: str | None = None
_TIMEOUT_HOLDER: dict[str, float | None] = {"value": None}
_INSECURE_HOLDER: dict[str, bool] = {"value": False}


def _set_timeout(ctx: click.Context, param: click.Parameter, value: float | None) -> None:
    _TIMEOUT_HOLDER["value"] = value


def _set_insecure(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    _INSECURE_HOLDER["value"] = value


def _parse_version(version: str) -> tuple[int, ...]:
    """Parse 'v0.136.1' into (0, 136, 1); unknown formats yield ()."""
    m = __import__("re").fullmatch(r"v?(\d+(?:\.\d+){0,3})", (version or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def _is_supported_server(version: str) -> bool:
    parsed = _parse_version(version)
    if not parsed:
        return False
    padded = parsed + (0,) * (len(SUPPORTED_SIGNOZ_MIN) - len(parsed))
    return padded[: len(SUPPORTED_SIGNOZ_MIN)] >= SUPPORTED_SIGNOZ_MIN


def _mask_token(token: str | None) -> str:
    if token and len(token) > 12:
        return f"{token[:8]}...{token[-4:]}"
    return "<not set>"


def _resolve_credentials(
    endpoint: str | None, token: str | None, profile_name: str | None
) -> tuple[str | None, str | None]:
    """Resolve (endpoint, token): flags > env vars > selected profile > legacy keys.

    Profile selection precedence: leaf --profile > root --profile >
    SIGNOZ_PROFILE env var > the config file's default profile.
    """
    cfg = load_config()
    selected = select_profile_name(
        cfg, profile_name or _ROOT_PROFILE, os.getenv("SIGNOZ_PROFILE")
    )
    prof_endpoint, prof_token = get_profile_credentials(cfg, selected)

    resolved_endpoint = endpoint or os.getenv("SIGNOZ_ENDPOINT") or prof_endpoint
    resolved_token = token or os.getenv("SIGNOZ_ACCESS_TOKEN") or prof_token

    # Remember which profile was used for error messages
    _resolve_credentials.selected_profile = (
        selected if (prof_endpoint or prof_token) else None
    )
    return resolved_endpoint, resolved_token


def _set_json_flag(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Eager callback: --json is shorthand for -f json (clig.dev output rule)."""
    if value:
        ctx.params["fmt"] = "json"


def _set_output_file(ctx: click.Context, param: click.Parameter, value: str | None) -> None:
    set_output_file(value)


def _common_options(f):
    """Decorate a command with --endpoint / --token / --profile / --format / --json."""
    f = click.option(
        "-o",
        "--output",
        type=click.Path(dir_okay=False, writable=True),
        default=None,
        expose_value=False,
        is_eager=True,
        callback=_set_output_file,
        help="Write output to a file instead of stdout.",
    )(f)
    f = click.option(
        "--endpoint",
        default=None,
        help="SigNoz instance URL (env: SIGNOZ_ENDPOINT). Overrides the profile.",
    )(f)
    f = click.option(
        "--token",
        default=None,
        help="SigNoz API key (env: SIGNOZ_ACCESS_TOKEN). Overrides the profile. "
        "Prefer env vars or profiles over flags to keep tokens out of shell history.",
    )(f)
    f = click.option(
        "-p",
        "--profile",
        default=None,
        help="Named connection profile from ~/.signoz/config.json "
        "(env: SIGNOZ_PROFILE; falls back to the default profile).",
    )(f)
    f = click.option(
        "-f",
        "--format",
        "fmt",
        type=click.Choice(["json", "table", "csv"]),
        default="table",
        show_default=True,
        help="Output format.",
    )(f)
    f = click.option(
        "--json",
        is_flag=True,
        expose_value=False,
        is_eager=True,
        callback=_set_json_flag,
        help="Shorthand for -f json.",
    )(f)
    f = click.option(
        "--timeout",
        type=float,
        default=None,
        expose_value=False,
        is_eager=True,
        callback=_set_timeout,
        help="Request timeout in seconds (env: SIGNOZ_TIMEOUT, default 120).",
    )(f)
    f = click.option(
        "--insecure",
        is_flag=True,
        default=None,
        expose_value=False,
        is_eager=True,
        callback=_set_insecure,
        help="Skip TLS certificate verification (env: SIGNOZ_INSECURE=1).",
    )(f)
    return f


def _time_options(default_range: str = "6h"):
    """Decorate a command with --time-range / --start / --end (decorator factory)."""

    def decorate(f):
        f = click.option(
            "--time-range",
            default=None,
            help=f"Relative time range (e.g. 30m, 2h, 7d). Default: {default_range}.",
        )(f)
        f = click.option("--start", type=int, default=None, help="Start timestamp in ms.")(
            f
        )
        f = click.option("--end", type=int, default=None, help="End timestamp in ms.")(f)
        return f

    return decorate


def _get_client(
    endpoint: str | None, token: str | None, profile: str | None = None
) -> SigNozClient:
    resolved_endpoint, resolved_token = _resolve_credentials(endpoint, token, profile)
    if not resolved_endpoint:
        raise click.UsageError(
            "Missing endpoint. Set via --endpoint/--profile, the SIGNOZ_ENDPOINT "
            "env var, or 'signoz configure --profile <name> ...'."
        )
    if not resolved_token:
        raise click.UsageError(
            "Missing token. Set via --token/--profile, the SIGNOZ_ACCESS_TOKEN "
            "env var, or 'signoz configure --profile <name> ...'."
        )

    timeout = (
        _TIMEOUT_HOLDER["value"]
        if _TIMEOUT_HOLDER["value"] is not None
        else (float(os.getenv("SIGNOZ_TIMEOUT")) if os.getenv("SIGNOZ_TIMEOUT") else 120.0)
    )
    insecure = _INSECURE_HOLDER["value"] or os.getenv("SIGNOZ_INSECURE", "") == "1"

    cfg = load_config()
    selected = select_profile_name(
        cfg, profile or _ROOT_PROFILE, os.getenv("SIGNOZ_PROFILE")
    )
    prof = get_profiles(cfg).get(selected or "", {})
    if "timeout" in prof and _TIMEOUT_HOLDER["value"] is None and not os.getenv("SIGNOZ_TIMEOUT"):
        timeout = float(prof["timeout"])
    if prof.get("insecure"):
        insecure = True

    return SigNozClient(resolved_endpoint, resolved_token, timeout=timeout, verify=not insecure)


def _handle_error(e: Exception) -> None:
    if isinstance(e, SigNozApiError):
        click.echo(f"Error: {e.message} (HTTP {e.status_code})", err=True)
    elif isinstance(e, httpx.ConnectError):
        click.echo(f"Error: cannot connect to endpoint ({e})", err=True)
    else:
        click.echo(f"Error: {e}", err=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group(context_settings={"help_option_names": ["-h", "--help"]},
             epilog="Docs: https://signoz.io/docs/  |  "
                    "Issues: https://github.com/SigNoz/signoz/issues")
@click.option("-p", "--profile", "root_profile", default=None,
              help="Named connection profile to use for this invocation "
                   "(env: SIGNOZ_PROFILE; falls back to the default profile). "
                   "Accepted before or after the subcommand.")
@click.version_option(version=__version__, prog_name="signoz-cli")
@click.pass_context
def cli(ctx: click.Context, root_profile: str | None) -> None:
    """SigNoz CLI -- query traces, logs, metrics, dashboards, alerts, and services.

    Targets SigNoz v0.138+ APIs (Query Builder v5, Dashboards v2, Alert History v2).

    Use named profiles to work with multiple SigNoz instances:

      signoz configure -p prod --endpoint https://signoz.example.com --token KEY

      signoz -p prod logs search

    \b
    Examples:
      signoz logs search --service payment-svc --severity ERROR --time-range 1h
      signoz traces aggregate --aggregation p99 --aggregate-on durationNano --group-by "service.name"
      signoz dashboards list --json
      signoz alerts history <rule-uuid> --time-range 24h
    """
    global _ROOT_PROFILE
    _ROOT_PROFILE = root_profile
    ctx.ensure_object(dict)


@cli.command()
@click.option("--profile", "-p", "profile_name", default="default", show_default=True,
              help="Profile name to save credentials under.")
@click.option("--endpoint", prompt="SigNoz endpoint URL", help="SigNoz instance URL.")
@click.option(
    "--token", prompt="SigNoz API token", hide_input=True, help="SigNoz API key."
)
@click.option("--set-default/--no-set-default", default=None,
              help="Make this profile the default (default: yes for the first profile).")
def configure(
    profile_name: str,
    endpoint: str,
    token: str,
    set_default: bool | None,
) -> None:
    """Save SigNoz credentials as a named profile in ~/.signoz/config.json.

    Store one profile per SigNoz instance, e.g.:

      signoz configure --profile local --endpoint http://localhost:8080

      signoz configure --profile prod --endpoint https://signoz.example.com
    """
    cfg = load_config()
    profiles = get_profiles(cfg)
    profiles[profile_name] = {
        "endpoint": endpoint.rstrip("/"),
        "token": token,
    }
    cfg["profiles"] = profiles
    if set_default is True or (
        set_default is None and cfg.get("default") is None
    ):
        cfg["default"] = profile_name
    save_config(cfg)
    click.echo(f"Profile '{profile_name}' saved to {CONFIG_FILE} "
               f"(endpoint: {profiles[profile_name]['endpoint']})")
    click.echo(f"Use it with: signoz -p {profile_name} <command>  "
               f"or export SIGNOZ_PROFILE={profile_name}")


@cli.group("profile")
def profile() -> None:
    """Manage named connection profiles (one per SigNoz instance)."""


@profile.command("list")
@click.option("-f", "--format", "fmt", type=click.Choice(["json", "table"]),
              default="table", show_default=True, help="Output format.")
@click.option("--json", is_flag=True, expose_value=False, is_eager=True,
              callback=_set_json_flag, help="Shorthand for -f json.")
def profile_list(fmt: str) -> None:
    """List configured profiles."""
    cfg = load_config()
    profiles = get_profiles(cfg)
    # legacy flat config acts as an implicit profile
    rows = []
    for name in sorted(profiles):
        p = profiles[name]
        rows.append({
            "name": name,
            "endpoint": p.get("endpoint", ""),
            "token": _mask_token(p.get("token")),
            "default": "*" if name == cfg.get("default") else "",
        })
    if not rows and cfg.get("endpoint"):
        rows.append({
            "name": "(legacy)",
            "endpoint": cfg["endpoint"],
            "token": _mask_token(cfg.get("token")),
            "default": "*",
        })
    emit({"data": rows}, fmt)


@profile.command("use")
@click.argument("name")
def profile_use(name: str) -> None:
    """Set the default profile used when none is given."""
    cfg = load_config()
    if name not in get_profiles(cfg):
        raise click.UsageError(f"Unknown profile '{name}'. See 'signoz profile list'.")
    cfg["default"] = name
    save_config(cfg)
    click.echo(f"Default profile set to '{name}'.")


@profile.command("show")
@click.argument("name")
def profile_show(name: str) -> None:
    """Show details of a single profile (token masked)."""
    cfg = load_config()
    prof = get_profiles(cfg).get(name)
    if prof is None:
        raise click.UsageError(f"Unknown profile '{name}'. See 'signoz profile list'.")
    emit({
        "name": name,
        "endpoint": prof.get("endpoint", ""),
        "token": _mask_token(prof.get("token")),
        "default": name == cfg.get("default"),
    }, "json")


@profile.command("remove")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def profile_remove(name: str, yes: bool) -> None:
    """Remove a profile from the config file."""
    cfg = load_config()
    profiles = get_profiles(cfg)
    if name not in profiles:
        raise click.UsageError(f"Unknown profile '{name}'. See 'signoz profile list'.")
    if not yes:
        click.confirm(f"Remove profile '{name}'?", abort=True)
    del profiles[name]
    cfg["profiles"] = profiles
    if cfg.get("default") == name:
        cfg["default"] = next(iter(profiles), None)
    save_config(cfg)
    click.echo(f"Profile '{name}' removed.")


@cli.group("auth")
def auth() -> None:
    """Authentication helpers."""


@auth.command("whoami")
@click.option("--endpoint", default=None, help="SigNoz instance URL.")
@click.option("--token", default=None, help="SigNoz API key.")
@click.option("-p", "--profile", "profile_name", default=None,
              help="Named connection profile to inspect.")
def auth_whoami(endpoint: str | None, token: str | None, profile_name: str | None) -> None:
    """Show effective configuration (endpoint, token masked).

    Resolution order: flags > env vars > selected profile > legacy config keys.
    """
    ep, tok = _resolve_credentials(endpoint, token, profile_name)
    cfg = load_config()
    selected = select_profile_name(cfg, profile_name, os.getenv("SIGNOZ_PROFILE"))
    click.echo(f"Endpoint: {ep or '<not set>'}")
    click.echo(f"Token:    {_mask_token(tok)}")
    click.echo(f"Profile:  {selected or '<none>'}")
    click.echo(f"Config:   {CONFIG_FILE}")


@auth.command("verify")
@_common_options
def auth_verify(endpoint: str | None, token: str | None, fmt: str,
    profile: str | None,) -> None:
    """Verify the API key against the live instance and report version compatibility."""
    try:
        client = _get_client(endpoint, token)
        data = client.verify()
        try:
            server_version = (client.get_server_version() or {}).get("version", "")
        except Exception: 
            server_version = ""
        result = {
            "service_account": data,
            "server_version": server_version or "<unknown>",
            "supported_range": SUPPORTED_SIGNOZ_RANGE,
            "compatible": _is_supported_server(server_version),
        }
        emit(result, fmt)
        if not result["compatible"]:
            click.echo(
                f"Warning: server version '{result['server_version']}' is outside "
                f"the tested range ({SUPPORTED_SIGNOZ_RANGE}). "
                "Commands may still work but are unverified.",
                err=True,
            )
    except Exception as e:
        _handle_error(e)


# ===================================================================
# METRICS
# ===================================================================


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


# ===================================================================
# ALERTS
# ===================================================================


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
        s, e = resolve_timestamps(start, end, time_range, default_range="6h")
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
        s, e = resolve_timestamps(start, end, time_range, default_range="24h")
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
        s, e = resolve_timestamps(start, end, time_range, default_range="24h")
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
        s, e = resolve_timestamps(start, end, time_range, default_range="24h")
        data = client.get_alert_history_top_contributors(rule_id, s, e)
        emit(_stringify_labels(data), fmt, table_keys=["fingerprint", "labels", "count"])
    except Exception as ex:
        _handle_error(ex)


# ===================================================================
# DASHBOARDS (v2 / Perses-compatible, required on 0.135+)
# ===================================================================


@cli.group()
def dashboards() -> None:
    """Manage SigNoz dashboards (v2 API)."""


@dashboards.command("list")
@_common_options
@click.option("--limit", type=int, default=50, show_default=True, help="Max results.")
@click.option(
    "--offset", type=int, default=0, show_default=True, help="Pagination offset."
)
@click.option("--query", default=None, help='Search query, e.g. "name contains prod".')
@click.option(
    "--all",
    "fetch_all",
    is_flag=True,
    help="Fetch every page (ignores --limit/--offset).",
)
def dashboards_list(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    limit: int,
    offset: int,
    query: str | None,
    fetch_all: bool,
) -> None:
    """List dashboards (v2 API with pagination and search)."""
    try:
        client = _get_client(endpoint, token, profile)
        if fetch_all:
            limit, offset, total = 100, 0, None
        raw = client.list_dashboards_v2(limit=limit, offset=offset, query=query)
        # Response shape: {"status", "data": {"dashboards": [...], "total": N, ...}}
        payload = raw.get("data", {}) if isinstance(raw, dict) else {}
        if isinstance(payload, dict) and "dashboards" in payload:
            items = list(payload.get("dashboards") or [])
            total = payload.get("total")
        elif isinstance(payload, list):
            items = payload
            total = None
        else:
            items = []
            total = None
        if fetch_all:
            # walk remaining pages
            while True:
                offset += limit
                raw_page = client.list_dashboards_v2(limit=limit, offset=offset, query=query)
                page_payload = raw_page.get("data", {}) if isinstance(raw_page, dict) else {}
                page_items = (page_payload.get("dashboards") or []) if isinstance(page_payload, dict) else []
                items.extend(page_items)
                if not page_items or (isinstance(page_payload, dict) and page_payload.get("total") is not None and offset + len(page_items) >= int(page_payload.get("total"))):
                    break
        simplified = [_dashboard_summary(d) for d in items]
        result: dict[str, Any] = {"data": simplified}
        if fetch_all:
            result["pagination"] = {
                "total": int(total) if total is not None else len(items),
                "offset": 0,
                "limit": len(items),
                "hasMore": False,
                "nextOffset": None,
            }
        else:
            if total is None:
                total = len(items)
            result["pagination"] = {
                "total": int(total),
                "offset": offset,
                "limit": limit,
                "hasMore": offset + limit < int(total),
                "nextOffset": offset + limit if offset + limit < int(total) else None,
            }
        emit(result, fmt)
    except Exception as e:
        _handle_error(e)


@dashboards.command("get")
@_common_options
@click.argument("dashboard_id")
def dashboards_get(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, dashboard_id: str
) -> None:
    """Get full details of a dashboard by id (v2 API)."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.get_dashboard_v2(dashboard_id)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@dashboards.command("create")
@_common_options
@click.argument("json_file", type=click.Path(exists=True))
def dashboards_create(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, json_file: str
) -> None:
    """Create a dashboard from a JSON file (v2 spec schema)."""
    try:
        with open(json_file) as f:
            dashboard = json.load(f)
        client = _get_client(endpoint, token, profile)
        data = client.create_dashboard_v2(dashboard)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@dashboards.command("update")
@_common_options
@click.argument("dashboard_id")
@click.argument("json_file", type=click.Path(exists=True))
def dashboards_update(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, dashboard_id: str, json_file: str
) -> None:
    """Replace a dashboard from a JSON file (full PUT, v2 API)."""
    try:
        with open(json_file) as f:
            dashboard = json.load(f)
        client = _get_client(endpoint, token, profile)
        data = client.update_dashboard_v2(dashboard_id, dashboard)
        emit(data or {"status": "updated"}, fmt)
    except Exception as e:
        _handle_error(e)


@dashboards.command("patch")
@_common_options
@click.argument("dashboard_id")
@click.argument("json_file", type=click.Path(exists=True))
def dashboards_patch(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, dashboard_id: str, json_file: str
) -> None:
    """Apply partial updates to a dashboard (JSON-Patch array, v2 API).

    Example patch file:
      [{"op": "add", "path": "/tags/-", "value": {"key": "env", "value": "prod"}}]
    """
    try:
        with open(json_file) as f:
            patches = json.load(f)
        if not isinstance(patches, list):
            raise click.UsageError("Patch file must contain a JSON array of operations.")
        client = _get_client(endpoint, token, profile)
        data = client.patch_dashboard_v2(dashboard_id, patches)
        emit(data or {"status": "patched"}, fmt)
    except Exception as e:
        _handle_error(e)


@dashboards.command("delete")
@_common_options
@click.argument("dashboard_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def dashboards_delete(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, dashboard_id: str, yes: bool
) -> None:
    """Delete a dashboard (v2 API)."""
    try:
        if not yes:
            click.confirm(f"Delete dashboard {dashboard_id}?", abort=True)
        client = _get_client(endpoint, token, profile)
        data = client.delete_dashboard_v2(dashboard_id)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


# ===================================================================
# SERVICES
# ===================================================================


@cli.group()
def services() -> None:
    """Query SigNoz services."""


@services.command("list")
@_common_options
@click.option("--time-range", default=None, help="Relative time range (e.g. 6h, 2d).")
@click.option("--start", type=int, default=None, help="Start timestamp in ns.")
@click.option("--end", type=int, default=None, help="End timestamp in ns.")
def services_list(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """List all services within a time range."""
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", unit="ns")
        data = client.list_services(str(s), str(e))
        emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


@services.command("top-operations")
@_common_options
@click.argument("service")
@click.option("--time-range", default=None, help="Relative time range (e.g. 6h, 2d).")
@click.option("--start", type=int, default=None, help="Start timestamp in ns.")
@click.option("--end", type=int, default=None, help="End timestamp in ns.")
@click.option("--tags", default=None, help="JSON array of tags.")
def services_top_operations(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    service: str,
    time_range: str | None,
    start: int | None,
    end: int | None,
    tags: str | None,
) -> None:
    """Get top operations for a specific service."""
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", unit="ns")
        parsed_tags = json.loads(tags) if tags else None
        data = client.get_service_top_operations(
            service, str(s), str(e), tags=parsed_tags
        )
        emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


# ===================================================================
# LOGS
# ===================================================================


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
        s, e = resolve_timestamps(start, end, time_range, default_range="1h")
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
            parts.append(f"service.name = '{service}'")
        filter_expr = " AND ".join(parts)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h")
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
        s, e = resolve_timestamps(start, end, time_range, default_range="6h")
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
        s, e = resolve_timestamps(start, end, time_range, default_range="1h")
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
        emit(data, fmt)
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
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


# ===================================================================
# TRACES
# ===================================================================


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
        s, e = resolve_timestamps(start, end, time_range, default_range="6h")
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
        s, e = resolve_timestamps(start, end, time_range, default_range="6h")
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
        s, e = resolve_timestamps(start, end, time_range, default_range="6h")
        filter_expr = f"traceID = '{trace_id}'"
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
            parts.append(f"service.name in ['{service}']")
        filter_expr = " AND ".join(parts)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h")
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
        s, e = resolve_timestamps(start, end, time_range, default_range="1h")
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


# ===================================================================
# QUERY (raw Query Builder v5)
# ===================================================================


@cli.command("query")
@_common_options
@click.argument("json_file", type=click.Path(exists=True))
def query_raw(endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, json_file: str) -> None:
    """Execute a raw Query Builder v5 query from a JSON file."""
    try:
        with open(json_file) as f:
            payload = json.load(f)
        client = _get_client(endpoint, token, profile)
        data = _unwrap_v5(client.query_builder_v5(payload))
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)



# ===================================================================
# METRICS QUERY (values)
# ===================================================================


def _unwrap_time_series(data: Any) -> list[dict[str, Any]]:
    """Flatten a v5 time_series response into (series, timestamp, value) rows.

    Actual shape: data.results[0].aggregations[*].series[*].values[*],
    with optional per-series ``labels``.
    """
    try:
        results = data["data"]["data"]["results"]
    except (KeyError, TypeError, IndexError, AttributeError):
        return []
    rows: list[dict[str, Any]] = []
    for result in results or []:
        for agg in result.get("aggregations") or []:
            alias = agg.get("alias") or ""
            for s in agg.get("series") or []:
                labels = s.get("labels") or []
                label_str = ", ".join(
                    f"{lb.get('key','')}={lb.get('value','')}" for lb in labels
                ) or alias or "A"
                for point in s.get("values") or []:
                    rows.append({
                        "series": label_str,
                        "timestamp": point.get("timestamp"),
                        "value": point.get("value"),
                    })
    return rows


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

    Examples:

      signoz metrics query app.payments.total --time-aggregation rate

      signoz metrics query system.cpu.time -g "host.name"
    """
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h")
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
        emit(_unwrap_time_series(resp), fmt,
             table_keys=["series", "timestamp", "value"])
    except Exception as ex:
        _handle_error(ex)


@cli.command("promql")
@_common_options
@click.argument("query")
@click.option("--step", type=int, default=60, show_default=True, help="Step seconds.")
@_time_options(default_range="1h")
def promql_query(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    query: str,
    step: int,
    time_range: str | None,
    start: int | None,
    end: int | None,
) -> None:
    """Run a PromQL query against metrics.

    Example: signoz promql 'rate(signoz_calls_total[5m])' --time-range 1h
    """
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h")
        resp = client.query_promql(query, s, e, step=step)
        _warn_v5(resp)
        emit(_unwrap_time_series(resp), fmt,
             table_keys=["series", "timestamp", "value"])
    except Exception as ex:
        _handle_error(ex)


@cli.command("sql")
@_common_options
@click.argument("query")
@_time_options(default_range="6h")
def sql_query(
    endpoint: str | None,
    token: str | None,
    fmt: str,
    profile: str | None,
    query: str,
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
        s, e = resolve_timestamps(start, end, time_range, default_range="6h")
        resp = client.query_sql(query, s, e)
        emit(_unwrap_v5(resp), fmt)
    except Exception as ex:
        _handle_error(ex)



# ===================================================================
# TRACE DETAIL / LOGS TAIL
# ===================================================================


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
    """Full span detail for a trace: attributes, events, exceptions."""
    try:
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="6h")
        data = client.get_trace_detail(trace_id, s, e)
        emit(data, fmt)
    except Exception as ex:
        _handle_error(ex)


@logs.command("tail")
@_common_options
@click.option("--service", default=None, help="Service name filter.")
@click.option("--severity", default=None, help="Minimum severity filter.")
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

    try:
        client = _get_client(endpoint, token, profile)
    except Exception as ex:
        _handle_error(ex)
        return

    filter_expr = _build_log_filter(None, service, severity, search_text)
    seen: set[str] = set()
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
            except Exception as ex:
                click.echo(f"tail: {ex}", err=True)
                _time.sleep(interval)
                continue
            if isinstance(rows, list):
                for row in reversed(rows):  # oldest first
                    row_id = str(row.get("id", ""))
                    if row_id and row_id in seen:
                        continue
                    seen.add(row_id)
                    click.echo(
                        f"{row.get('timestamp', '')} "
                        f"{row.get('severity_text', '')!s:5s} "
                        f"{row.get('service.name', '')}: {row.get('body', '')}"
                    )
            # bound memory
            if len(seen) > 50000:
                seen = set(list(seen)[-10000:])
            _time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("Stopped.", err=True)



# ===================================================================
# ALERT RULE MANAGEMENT (v2)
# ===================================================================


@alerts.command("create")
@_common_options
@click.argument("json_file", type=click.Path(exists=True))
def alerts_create(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, json_file: str) -> None:
    """Create an alert rule from a JSON file (v2 rule schema)."""
    try:
        with open(json_file) as f:
            rule = json.load(f)
        client = _get_client(endpoint, token, profile)
        data = client.create_rule(rule)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@alerts.command("update")
@_common_options
@click.argument("rule_id")
@click.argument("json_file", type=click.Path(exists=True))
def alerts_update(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, rule_id: str, json_file: str) -> None:
    """Replace an alert rule from a JSON file (full PUT)."""
    try:
        with open(json_file) as f:
            rule = json.load(f)
        client = _get_client(endpoint, token, profile)
        data = client.update_rule(rule_id, rule)
        emit(data or {"status": "updated"}, fmt)
    except Exception as e:
        _handle_error(e)


@alerts.command("patch")
@_common_options
@click.argument("rule_id")
@click.argument("json_file", type=click.Path(exists=True))
def alerts_patch(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, rule_id: str, json_file: str) -> None:
    """Apply partial updates to a rule (JSON-Patch array)."""
    try:
        with open(json_file) as f:
            patches = json.load(f)
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
def alerts_delete(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, rule_id: str, yes: bool) -> None:
    """Delete an alert rule."""
    try:
        if not yes:
            click.confirm(f"Delete alert rule {rule_id}?", abort=True)
        client = _get_client(endpoint, token, profile)
        emit(client.delete_rule(rule_id), fmt)
    except Exception as e:
        _handle_error(e)


@alerts.command("test")
@_common_options
@click.argument("json_file", type=click.Path(exists=True))
@click.option("--start", type=int, default=None, help="Test-window start (ms).")
@click.option("--end", type=int, default=None, help="Test-window end (ms).")
@click.option("--time-range", default="1h", show_default=True, help="Test window.")
def alerts_test(endpoint: str | None, token: str | None, fmt: str,
                profile: str | None, json_file: str,
                start: int | None, end: int | None, time_range: str) -> None:
    """Dry-run a rule payload and see what would fire."""
    try:
        with open(json_file) as f:
            rule = json.load(f)
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(start, end, time_range, default_range="1h")
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


@cli.group("channels")
def channels() -> None:
    """Manage notification channels (requires admin)."""


@channels.command("list")
@_common_options
def channels_list(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None) -> None:
    """List notification channels."""
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.list_channels(), fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("get")
@_common_options
@click.argument("channel_id")
def channels_get(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, channel_id: str) -> None:
    """Get a channel by id."""
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.get_channel(channel_id), fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("create")
@_common_options
@click.argument("json_file", type=click.Path(exists=True))
def channels_create(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, json_file: str) -> None:
    """Create a channel from a JSON file."""
    try:
        with open(json_file) as f:
            channel = json.load(f)
        client = _get_client(endpoint, token, profile)
        emit(client.create_channel(channel), fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("delete")
@_common_options
@click.argument("channel_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def channels_delete(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, channel_id: str, yes: bool) -> None:
    """Delete a channel."""
    try:
        if not yes:
            click.confirm(f"Delete channel {channel_id}?", abort=True)
        client = _get_client(endpoint, token, profile)
        emit(client.delete_channel(channel_id), fmt)
    except Exception as e:
        _handle_error(e)


# ===================================================================
# SAVED VIEWS (0.137+)
# ===================================================================


@cli.group("views")
def views() -> None:
    """Manage saved explorer views (SigNoz 0.137+)."""


@views.command("list")
@_common_options
@click.option("--source-page", default="logs", show_default=True,
              type=click.Choice(["logs", "traces", "metrics"]),
              help="Which explorer the view belongs to.")
def views_list(endpoint: str | None, token: str | None, fmt: str,
               profile: str | None, source_page: str) -> None:
    """List saved views."""
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.list_saved_views(source_page), fmt)
    except Exception as e:
        _handle_error(e)


@views.command("get")
@_common_options
@click.argument("view_id")
def views_get(endpoint: str | None, token: str | None, fmt: str,
              profile: str | None, view_id: str) -> None:
    """Get a saved view by id."""
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.get_saved_view(view_id), fmt)
    except Exception as e:
        _handle_error(e)


@views.command("delete")
@_common_options
@click.argument("view_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def views_delete(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, view_id: str, yes: bool) -> None:
    """Delete a saved view."""
    try:
        if not yes:
            click.confirm(f"Delete saved view {view_id}?", abort=True)
        client = _get_client(endpoint, token, profile)
        emit(client.delete_saved_view(view_id), fmt)
    except Exception as e:
        _handle_error(e)


# ===================================================================
# SERVICE ACCOUNTS (admin)
# ===================================================================


@cli.group("service-accounts")
def service_accounts() -> None:
    """Manage service accounts and roles (requires admin)."""


@service_accounts.command("list")
@_common_options
def service_accounts_list(endpoint: str | None, token: str | None, fmt: str,
                          profile: str | None) -> None:
    """List service accounts."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_service_accounts()
        items = raw.get("data") if isinstance(raw, dict) else raw
        if isinstance(items, dict):
            items = items.get("data") or items.get("accounts") or []
        simplified = [
            {
                "id": a.get("id", ""),
                "name": a.get("name", ""),
                "status": a.get("status", ""),
                "email": a.get("email", ""),
            }
            for a in (items or [])
            if isinstance(a, dict)
        ]
        emit({"data": simplified}, fmt)
    except Exception as e:
        _handle_error(e)


@service_accounts.command("create")
@_common_options
@click.argument("name")
@click.option("--role", "role_name", default=None,
              help="Assign this managed role after creation (e.g. viewer).")
def service_accounts_create(endpoint: str | None, token: str | None, fmt: str,
                            profile: str | None, name: str,
                            role_name: str | None) -> None:
    """Create a service account, optionally assigning a managed role."""
    try:
        import re as _re

        if not _re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", name):
            raise click.UsageError(
                "Name must be lowercase letters/numbers/hyphens, max 50 chars."
            )
        client = _get_client(endpoint, token, profile)
        role_ids: list[str] = []
        if role_name:
            roles_raw = client.list_roles()
            roles = roles_raw.get("data") if isinstance(roles_raw, dict) else roles_raw
            wanted = role_name.lower().replace("signoz-", "")
            for r in roles or []:
                if r.get("name", "").lower().replace("signoz-", "") == wanted:
                    role_ids.append(r["id"])
                    break
            if not role_ids:
                raise click.UsageError(f"Unknown role '{role_name}'.")
        data = client.create_service_account(name, role_ids)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@service_accounts.command("delete")
@_common_options
@click.argument("account_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def service_accounts_delete(endpoint: str | None, token: str | None, fmt: str,
                            profile: str | None, account_id: str, yes: bool) -> None:
    """Delete a service account (revokes all its keys)."""
    try:
        if not yes:
            click.confirm(f"Delete service account {account_id}?", abort=True)
        client = _get_client(endpoint, token, profile)
        emit(client.delete_service_account(account_id), fmt)
    except Exception as e:
        _handle_error(e)


@service_accounts.command("roles")
@_common_options
def service_accounts_roles(endpoint: str | None, token: str | None, fmt: str,
                           profile: str | None) -> None:
    """List available roles."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_roles()
        items = raw.get("data") if isinstance(raw, dict) else raw
        simplified = [
            {"id": r.get("id", ""), "name": r.get("name", ""),
             "type": r.get("type", ""), "description": r.get("description", "")}
            for r in (items or []) if isinstance(r, dict)
        ]
        emit({"data": simplified}, fmt)
    except Exception as e:
        _handle_error(e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_MAX_PAGES = 200


def _logs_query_all(
    client: SigNozClient,
    s: int,
    e: int,
    filter_expr: str,
    limit: int,
) -> list[Any]:
    """Fetch all log rows by walking the offset until exhausted."""
    rows: list[Any] = []
    offset = 0
    for _ in range(_MAX_PAGES):
        payload = build_logs_query_payload(
            s, e, filter_expr, limit=limit, offset=offset
        )
        page = _unwrap_v5(client.query_builder_v5(payload))
        if not isinstance(page, list):
            return page  # error passthrough
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return rows


def _traces_query_all(
    client: SigNozClient,
    s: int,
    e: int,
    filter_expr: str,
    limit: int,
) -> list[Any]:
    """Fetch all span rows by following the raw-result nextCursor."""
    rows: list[Any] = []
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        payload = build_traces_query_payload(s, e, filter_expr, limit=limit)
        resp = client.query_builder_v5(payload)
        nxt = None
        try:
            results = resp["data"]["data"]["results"]
            nxt = (results[0] or {}).get("nextCursor") or None
        except (KeyError, TypeError, IndexError):
            pass
        page = _unwrap_v5(resp)
        if not isinstance(page, list):
            return page
        rows.extend(page)
        if not nxt or nxt == cursor:
            break
        cursor = nxt
    return rows


def _warn_v5(data: Any) -> None:
    """Print server-side warnings from a v5 response (e.g. no-data notices)."""
    try:
        warnings = data["data"]["meta"]["warning"]["warnings"] or []
    except (KeyError, TypeError):
        return
    for w in warnings:
        msg = w.get("message", "") if isinstance(w, dict) else str(w)
        if msg:
            click.echo(f"Warning: {msg}", err=True)


def _unwrap_v5(data: Any, agg_label: str | None = None) -> Any:
    """Flatten Query Builder v5 responses into plain row dicts.

    - raw requests: rows live at data.results[*].rows[*].data
    - scalar requests: positional value arrays aligned to data.results[*].columns

    Returns the input untouched for anything else (e.g. time_series, errors).
    """
    if isinstance(data, dict) and isinstance(data.get("data"), dict) and "type" in data["data"]:
        # Strip the {"status": "success", "data": {...}} envelope if present
        return _unwrap_v5(data["data"], agg_label)
    if not isinstance(data, dict) or data.get("type") not in ("raw", "scalar"):
        return data
    payload = data.get("data")
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        return data

    rows: list[Any] = []
    if data["type"] == "raw":
        for r in results:
            if not isinstance(r, dict):
                continue
            for row in r.get("rows") or []:
                item = row.get("data", row) if isinstance(row, dict) else row
                if isinstance(item, dict):
                    item = dict(item)
                    cursor = r.get("nextCursor")
                    if cursor:
                        item.setdefault("nextCursor", cursor)
                rows.append(item)
    else:
        for r in results:
            if not isinstance(r, dict):
                continue
            cols = [c.get("name", "") for c in r.get("columns", [])]
            named = [
                agg_label if c.startswith("__result_") else c for c in cols
            ] if agg_label else cols
            for vals in r.get("data", []):
                if isinstance(vals, list) and len(vals) == len(named):
                    rows.append(dict(zip(named, vals)))
                else:
                    rows.append(vals)
    return rows


def _stringify_labels(data: Any) -> Any:
    """Convert v2 structured label objects ([{key: {name}, value}]) to strings."""
    rows = data
    if isinstance(data, dict):
        for probe in ("data", "items"):
            if isinstance(data.get(probe), list):
                rows = data[probe]
                break
    if not isinstance(rows, list):
        return data

    def _flat(labels: Any) -> Any:
        if not isinstance(labels, list):
            return labels
        out = []
        for lbl in labels:
            if isinstance(lbl, dict) and isinstance(lbl.get("key"), dict):
                out.append(f"{lbl['key'].get('name', '')}={lbl.get('value', '')}")
            else:
                out.append(str(lbl))
        return out

    new_rows = []
    for row in rows:
        if isinstance(row, dict) and "labels" in row:
            row = dict(row)
            row["labels"] = _flat(row["labels"])
            for link_key in ("relatedTracesLink", "relatedLogsLink"):
                row.pop(link_key, None)
        new_rows.append(row)
    return new_rows


def _unwrap_history_timeline(data: Any) -> Any:
    """Flatten a v2 timeline response: unwrap items, stringify labels, keep cursor."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        return data
    payload = data["data"]
    items = payload.get("items")
    if not isinstance(items, list):
        return data

    rows = []
    for item in items:
        if isinstance(item, dict):
            item = dict(item)
            labels = item.pop("labels", None)
            if isinstance(labels, list):
                flat = []
                for lbl in labels:
                    if isinstance(lbl, dict):
                        key = (lbl.get("key") or {}).get("name", "")
                        flat.append(f"{key}={lbl.get('value', '')}")
                    else:
                        flat.append(str(lbl))
                item["labels"] = flat
            # long URLs are noise in table output; keep them for json
            for link_key in ("relatedTracesLink", "relatedLogsLink"):
                item.pop(link_key, None)
        rows.append(item)

    out: dict[str, Any] = {"data": rows}
    out["total"] = payload.get("total")
    if payload.get("nextCursor"):
        out["nextCursor"] = payload["nextCursor"]
    return out


def _dashboard_summary(d: dict[str, Any]) -> dict[str, Any]:
    """Summarize a v2 dashboard record for list output."""
    if not isinstance(d, dict):
        return {}
    spec = d.get("spec", {}) or {}
    display = spec.get("display", {}) or {}
    tags = d.get("tags", []) or []
    tag_strs = [
        t if isinstance(t, str) else f"{t.get('key', '')}={t.get('value', '')}"
        for t in tags
    ]
    return {
        "id": d.get("id", ""),
        "name": display.get("name") or d.get("name", ""),
        "description": display.get("description", ""),
        "tags": tag_strs,
        "createdAt": d.get("createdAt", d.get("created_at", "")),
        "updatedAt": d.get("updatedAt", d.get("updated_at", "")),
    }


def _build_log_filter(
    query: str | None,
    service: str | None,
    severity: str | None,
    search_text: str | None,
) -> str:
    parts: list[str] = []
    if query:
        parts.append(query)
    if service:
        parts.append(f"service.name = '{service}'")
    if severity:
        parts.append(f"severity_text = '{severity}'")
    if search_text:
        parts.append(f"body CONTAINS '{search_text}'")
    return " AND ".join(parts)


def _build_trace_filter(
    query: str | None,
    service: str | None,
    operation: str | None,
    error_filter: str | None,
) -> str:
    parts: list[str] = []
    if query:
        parts.append(query)
    if service:
        parts.append(f"service.name = '{service}'")
    if operation:
        parts.append(f"name = '{operation}'")
    if error_filter == "true":
        parts.append("hasError = true")
    elif error_filter == "false":
        parts.append("hasError = false")
    return " AND ".join(parts)


def _append_filter(existing: str, clause: str) -> str:
    if existing:
        return f"{existing} AND {clause}"
    return clause


def _parse_group_by(group_by: str | None, signal: str) -> list[dict[str, str]]:
    if not group_by:
        return []
    fields = []
    for field in group_by.split(","):
        field = field.strip()
        if field:
            fields.append({"name": field, "signal": signal})
    return fields


def _parse_order_by(order_by: str | None, default_expr: str) -> tuple[str, str]:
    if not order_by:
        return default_expr, "desc"
    lower = order_by.strip().lower()
    if lower.endswith(" asc"):
        return order_by.strip()[:-4].strip(), "asc"
    if lower.endswith(" desc"):
        return order_by.strip()[:-5].strip(), "desc"
    return order_by.strip(), "desc"


def _extract_attribute_keys(data: Any) -> list[Any]:
    if isinstance(data, dict):
        d = data.get("data", {})
        if isinstance(d, dict):
            return d.get("attributeKeys", [])
    return []


def _extract_alerts(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    raw_alerts = data.get("data", [])
    result = []
    for a in raw_alerts:
        labels = a.get("labels", {})
        status = a.get("status", {})
        result.append(
            {
                "alertname": labels.get("alertname", ""),
                "ruleId": labels.get("ruleId", ""),
                "severity": labels.get("severity", ""),
                "startsAt": a.get("startsAt", ""),
                "endsAt": a.get("endsAt", ""),
                "state": status.get("state", ""),
            }
        )
    return result


def _pagination_meta(total: int, offset: int, limit: int) -> dict[str, Any]:
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "hasMore": offset + limit < total,
        "nextOffset": offset + limit if offset + limit < total else None,
    }


if __name__ == "__main__":
    cli()
