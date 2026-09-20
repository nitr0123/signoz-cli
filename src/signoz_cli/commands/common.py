"""Shared CLI scaffolding: root group, options, client, helpers."""

from __future__ import annotations

import atexit
import difflib
import json
import os
import re
import sys
from typing import Any

import click
import httpx

from signoz_cli import __version__
from signoz_cli.client import SigNozApiError, SigNozClient
from signoz_cli.config import (
    CONFIG_DIR,
    get_profile_credentials,
    get_profile_option,
    get_profiles,
    load_config,
    select_profile_name,
)
from signoz_cli.output import (
    set_custom_columns,
    set_field_selector,
    set_jsonpath,
    set_no_color,
    set_no_headers,
    set_no_pager,
    set_output_file,
    set_sort_by,
    set_wide,
)
from signoz_cli.query import build_logs_query_payload, build_traces_query_payload
from signoz_cli.timeutil import parse_time_range

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
_NO_INPUT_HOLDER: dict[str, bool] = {"value": False}


def _set_timeout(ctx: click.Context, param: click.Parameter, value: float | None) -> None:
    _TIMEOUT_HOLDER["value"] = value


def _set_insecure(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    _INSECURE_HOLDER["value"] = value


def _set_no_input(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    _NO_INPUT_HOLDER["value"] = value


def _set_no_color(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    set_no_color(value)


def _set_no_pager(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    set_no_pager(value)


def _confirm(prompt: str) -> None:
    """Confirm a destructive action, honouring --no-input (clig.dev)."""
    if _NO_INPUT_HOLDER["value"]:
        raise click.UsageError(
            f"{prompt} (pass --yes to skip the prompt, or drop --no-input)"
        )
    click.confirm(prompt, abort=True)


def _read_json(path: str) -> Any:
    """Read a JSON payload from a file, or from stdin when path is '-'.

    clig.dev: support '-' for stdin where a file argument is expected.
    """
    if path == "-":
        return json.load(sys.stdin)
    with open(path) as fh:
        return json.load(fh)


def _parse_version(version: str) -> tuple[int, ...]:
    """Parse 'v0.136.1' into (0, 136, 1); unknown formats yield ()."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", (version or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def _is_supported_server(version: str) -> bool:
    parsed = _parse_version(version)
    if not parsed:
        return False
    padded = parsed + (0,) * (len(SUPPORTED_SIGNOZ_MIN) - len(parsed))
    return padded[: len(SUPPORTED_SIGNOZ_MIN)] >= SUPPORTED_SIGNOZ_MIN


def _apply_json_patch(obj: Any, ops: list[dict[str, Any]]) -> Any:
    """Apply a subset of RFC-6902 ops (add/replace/remove) to nested dicts."""
    import copy

    result = copy.deepcopy(obj)
    for op in ops:
        action = op.get("op")
        path = op.get("path", "")
        parts = [p.replace("~1", "/").replace("~0", "~") for p in path.strip("/").split("/") if p != ""]
        if not parts:
            if action == "replace":
                result = op.get("value")
                continue
            raise click.UsageError(f"Unsupported patch on root: {op}")
        parent = result
        for p in parts[:-1]:
            parent = parent[int(p)] if isinstance(parent, list) else parent.setdefault(p, {})
        last = parts[-1]
        if action in ("add", "replace"):
            if last == "-" and isinstance(parent, list):
                parent.append(op.get("value"))
            elif isinstance(parent, dict):
                parent[last] = op.get("value")
            elif isinstance(parent, list):
                idx = int(last)
                if action == "add":
                    parent.insert(idx, op.get("value"))
                else:
                    parent[idx] = op.get("value")
        elif action == "remove":
            if isinstance(parent, dict):
                parent.pop(last, None)
            elif isinstance(parent, list):
                parent.pop(int(last))
        else:
            raise click.UsageError(f"Unsupported patch op '{action}'.")
    return result


def _mask_token(token: str | None) -> str:
    if not token:
        return "<not set>"
    if len(token) > 12:
        return f"{token[:8]}...{token[-4:]}"
    # Short token: still set, but too short to show a prefix/suffix safely.
    return "***"


def _has_url_userinfo(text: str) -> bool:
    """Whether *text* looks like a URL embedding credentials (user:pass@host).

    Covers both full URLs (``https://bob:secret@host``) and bare
    ``bob:secret@host:port`` forms. Used to keep secrets out of history
    and error output.
    """
    if "@" not in text or " " in text:
        return False
    try:
        from urllib.parse import urlsplit as _urlsplit

        if "://" in text:
            # Full URL: even a bare username is credential material
            # (https://bob@host performs basic auth as bob).
            parts = _urlsplit(text)
            return bool(parts.username or parts.password)
        # Scheme-less text: only user:PASS@host counts. A bare
        # user@host (e.g. an email inside a PromQL/SQL filter) is not
        # a secret and must not suppress history recording.
        parts = _urlsplit(f"//{text}")
        return bool(parts.password)
    except ValueError:
        return False


def _redact_userinfo(text: str) -> str:
    """Replace URL-embedded credentials with `<redacted>` for display."""
    if "@" not in text:
        return text
    try:
        from urllib.parse import urlsplit as _urlsplit
        from urllib.parse import urlunsplit as _urlunsplit

        has_scheme = "://" in text
        parts = _urlsplit(text if has_scheme else f"//{text}")
        if not (parts.username or parts.password):
            return text
        netloc = parts.hostname or ""
        if parts.port:
            netloc += f":{parts.port}"
        rebuilt = _urlunsplit((
            parts.scheme, netloc, parts.path, parts.query, parts.fragment,
        ))
        redacted = rebuilt.replace("//", "//<redacted>@", 1)
        return redacted if has_scheme else redacted.lstrip("/")
    except ValueError:
        return text


def _reject_endpoint_userinfo(endpoint: str | None, *, source: str) -> None:
    """Fail fast when an endpoint URL embeds credentials.

    SigNoz authenticates via the API-key header (``--token``), never via
    URL userinfo. Credentials in the endpoint would leak into history,
    error messages, and status displays, so refuse them with guidance
    instead of propagating the secret.
    """
    if endpoint and _has_url_userinfo(endpoint):
        raise click.UsageError(
            f"Endpoint {source} must not embed credentials "
            f"({_redact_userinfo(endpoint)}). Pass the API key via --token, "
            "the SIGNOZ_ACCESS_TOKEN env var, or a stored profile instead."
        )


def _selected_profile_name(profile_name: str | None = None) -> str | None:
    """Resolve the effective profile name (leaf > root > env > config default)."""
    cfg = load_config()
    return select_profile_name(cfg, profile_name or _ROOT_PROFILE, os.getenv("SIGNOZ_PROFILE"))


def _resolve_credentials(
    endpoint: str | None, token: str | None, profile_name: str | None
) -> tuple[str | None, str | None]:
    """Resolve (endpoint, token): flags > env vars > selected profile > legacy keys.

    Profile selection precedence: leaf --profile > root --profile >
    SIGNOZ_PROFILE env var > the config file's default profile.
    """
    cfg = load_config()
    requested = profile_name or _ROOT_PROFILE or os.getenv("SIGNOZ_PROFILE")
    if requested and requested not in get_profiles(cfg):
        available = ", ".join(sorted(get_profiles(cfg))) or "<none configured>"
        raise click.UsageError(
            f"Unknown profile '{requested}'. Available: {available}. "
            "See 'signoz profile list'."
        )
    selected = _selected_profile_name(profile_name)
    prof_endpoint, prof_token = get_profile_credentials(cfg, selected)

    resolved_endpoint = endpoint or os.getenv("SIGNOZ_ENDPOINT") or prof_endpoint
    resolved_token = token or os.getenv("SIGNOZ_ACCESS_TOKEN") or prof_token
    _reject_endpoint_userinfo(resolved_endpoint, source="URL")
    if resolved_endpoint and "://" not in resolved_endpoint:
        # Bare "host:port" is the natural way to write a local endpoint.
        resolved_endpoint = f"http://{resolved_endpoint}"

    return resolved_endpoint, resolved_token


def _profile_time_range(profile_name: str | None) -> str | None:
    """Return the profile's default_time_range, or None if not configured.

    Values persisted by older releases are validated here so a stale typo
    fails with the profile/key named instead of a generic range error.
    """
    cfg = load_config()
    selected = _selected_profile_name(profile_name)
    val: str | None = get_profile_option(cfg, selected, "default_time_range")
    if val:
        try:
            parse_time_range(val)
        except ValueError:
            raise click.UsageError(
                f"Invalid default_time_range {val!r} on profile "
                f"'{selected}'. Fix with: signoz configure -p {selected} "
                "--endpoint <url> --token <key> --default-time-range <range> "
                "(e.g. 24h)."
            ) from None
    return val


def _format_default() -> str:
    """Default output format: SIGNOZ_FORMAT env > selected profile's `default_format` > table.

    At decoration time this returns 'table' (profile not resolved yet).
    The real resolution happens in _resolve_format_callback.
    """
    return "table"


def _resolve_format_callback(
    ctx: click.Context, param: click.Parameter, value: str
) -> str:
    """Callback for --format option: resolve profile defaults at runtime.

    Priority: --json/--csv flag > -f explicit value > SIGNOZ_FORMAT env > profile default > table.
    The --json/--csv eager callbacks set ctx.params["fmt"] before this runs,
    so we check for those first.
    """
    # If --json or --csv already set the format, honour that
    fmt_val: str = ctx.params.get("fmt", "table")
    if fmt_val in ("json", "csv"):
        return fmt_val

    # Explicit -f was passed (value != "table" default)
    if value != "table":
        return value

    # Check SIGNOZ_FORMAT env
    env_val = os.getenv("SIGNOZ_FORMAT")
    if env_val in ("json", "table", "csv"):
        return env_val

    # Check profile default_format.
    # At this point _ROOT_PROFILE may or may not be set (root callback runs first),
    # and ctx.params may have a leaf -p (processed after this callback).
    # Check both sources to cover all ordering cases.
    profile_name = ctx.params.get("profile") or _ROOT_PROFILE
    if profile_name:
        cfg = load_config()
        prof = get_profiles(cfg).get(profile_name, {})
        prof_fmt: str = prof.get("default_format", "")
        if prof_fmt in ("json", "table", "csv"):
            return prof_fmt

    return "table"


def _set_json_flag(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Eager callback: --json is shorthand for -f json (clig.dev output rule)."""
    if value:
        ctx.params["fmt"] = "json"


def _set_output_file(ctx: click.Context, param: click.Parameter, value: str | None) -> None:
    set_output_file(value)


def _set_output_mode(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> None:
    """Eager callback: parse -o value as format mode or file path."""
    if value is None:
        return
    known_formats = {"json", "yaml", "csv", "table", "wide"}
    if value in known_formats:
        if value in ("json", "yaml", "csv"):
            ctx.params["fmt"] = value
        elif value == "wide":
            set_wide(True)
            ctx.params["fmt"] = "wide"
        return
    # custom-columns=NAME:.field,...
    if value.startswith("custom-columns="):
        spec = value[len("custom-columns="):]
        cols = [c.strip() for c in spec.split(",") if c.strip()]
        set_custom_columns(cols)
        return
    # jsonpath='{.items[*].name}'
    if value.startswith("jsonpath="):
        set_jsonpath(value[len("jsonpath="):])
        return
    # Otherwise treat as file path
    set_output_file(value)


def _common_options(f):
    """Decorate a command with --endpoint / --token / --profile / --format / output controls."""
    f = click.option(
        "-o", "--output", "output_mode",
        default=None, expose_value=False, is_eager=True,
        callback=_set_output_mode,
        help="Output: json|yaml|csv|table|wide, custom-columns=COL:FIELD,..., "
        "jsonpath='{.path}', or a file path.",
    )(f)
    f = click.option(
        "--endpoint", default=None,
        help="SigNoz instance URL (env: SIGNOZ_ENDPOINT). Overrides the profile.",
    )(f)
    f = click.option(
        "--token", default=None,
        help="SigNoz API key (env: SIGNOZ_ACCESS_TOKEN). Overrides the profile.",
    )(f)
    f = click.option(
        "-p", "--profile", default=None,
        help="Named connection profile from ~/.signoz/config.json "
        "(env: SIGNOZ_PROFILE; falls back to the default profile).",
    )(f)
    f = click.option(
        "-f", "--format", "fmt",
        type=click.Choice(["json", "table", "csv"]),
        default="table", show_default="table",
        callback=_resolve_format_callback,
        help="Output format (default: SIGNOZ_FORMAT or profile `default_format`, else table).",
    )(f)
    f = click.option(
        "--json", is_flag=True, expose_value=False, is_eager=True,
        callback=_set_json_flag,
        help="Shorthand for -f json.",
    )(f)
    f = click.option(
        "--csv", is_flag=True, expose_value=False, is_eager=True,
        callback=lambda ctx, param, value: ctx.params.update({"fmt": "csv"}) if value else None,
        help="Shorthand for -f csv.",
    )(f)
    f = click.option(
        "--timeout", type=float, default=None, expose_value=False, is_eager=True,
        callback=_set_timeout,
        help="Request timeout in seconds (env: SIGNOZ_TIMEOUT, default 120).",
    )(f)
    f = click.option(
        "--insecure", is_flag=True, default=None, expose_value=False, is_eager=True,
        callback=_set_insecure,
        help="Skip TLS certificate verification (env: SIGNOZ_INSECURE=1).",
    )(f)
    f = click.option(
        "--no-color", is_flag=True, default=None, expose_value=False, is_eager=True,
        callback=_set_no_color,
        help="Disable ANSI colors in table output.",
    )(f)
    f = click.option(
        "--no-input", is_flag=True, default=None, expose_value=False, is_eager=True,
        callback=_set_no_input,
        help="Never prompt; fail instead of asking for confirmation.",
    )(f)
    f = click.option(
        "--no-pager", is_flag=True, default=None, expose_value=False, is_eager=True,
        callback=_set_no_pager,
        help="Disable pager for table output (env: NO_PAGER=1).",
    )(f)
    f = click.option(
        "--sort-by", default=None, expose_value=False,
        callback=lambda ctx, param, value: set_sort_by(
            value.lstrip("-"), reverse=value.startswith("-")
        ) if value else None,
        help="Sort output by a field (e.g. --sort-by=durationNano, --sort-by=-value for desc).",
    )(f)
    f = click.option(
        "--field-selector", "field_sel", default=None, expose_value=False,
        callback=lambda ctx, param, value: set_field_selector(value),
        help="Filter by field value (e.g. --field-selector severity=ERROR,hasError=true).",
    )(f)
    f = click.option(
        "--no-headers", is_flag=True, default=None, expose_value=False,
        callback=lambda ctx, param, value: set_no_headers(value) if value else None,
        help="Suppress table headers (for awk/cut pipelines).",
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

    env_timeout = os.getenv("SIGNOZ_TIMEOUT")

    def _parse_timeout(raw: Any, source: str) -> float | None:
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise click.UsageError(
                f"Invalid timeout {raw!r} from {source}: must be a number of seconds."
            ) from None
        if value <= 0:
            raise click.UsageError(
                f"Invalid timeout {raw!r} from {source}: must be positive."
            )
        return value

    timeout = _parse_timeout(_TIMEOUT_HOLDER["value"], "--timeout")
    if timeout is None and env_timeout:
        timeout = _parse_timeout(env_timeout, "SIGNOZ_TIMEOUT")
    if timeout is None:
        timeout = 120.0
    insecure = _INSECURE_HOLDER["value"] or os.getenv("SIGNOZ_INSECURE", "") == "1"

    cfg = load_config()
    selected = select_profile_name(
        cfg, profile or _ROOT_PROFILE, os.getenv("SIGNOZ_PROFILE")
    )
    prof = get_profiles(cfg).get(selected or "", {})
    if "timeout" in prof and timeout == 120.0 and not _TIMEOUT_HOLDER["value"] and not env_timeout:
        # Profile default applies only when nothing else set it.
        parsed = _parse_timeout(prof["timeout"], f"profile '{selected}'")
        if parsed is not None:
            timeout = parsed
    if prof.get("insecure"):
        insecure = True

    client = SigNozClient(
        resolved_endpoint, resolved_token, timeout=timeout, verify=not insecure
    )
    # Close the connection pool on interpreter exit (commands are short-lived and
    # do not otherwise close the client).
    atexit.register(client.close)
    return client


def _handle_error(e: Exception) -> None:
    if isinstance(e, click.UsageError):
        # misuse (bad option combination, --no-input confirm, ...) -> exit 2
        click.echo(f"Error: {e.message}", err=True)
        sys.exit(2)
    if isinstance(e, SigNozApiError):
        click.echo(f"Error: {e.message} (HTTP {e.status_code})", err=True)
    elif isinstance(e, httpx.ConnectError):
        click.echo(f"Error: cannot connect to endpoint ({e})", err=True)
    elif isinstance(e, httpx.TimeoutException):
        click.echo(
            "Error: request timed out. Increase --timeout (or SIGNOZ_TIMEOUT) "
            "or narrow the query time range.",
            err=True,
        )
    else:
        click.echo(f"Error: {e}", err=True)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


class SuggestGroup(click.Group):
    """A Group that suggests close command names on typos (clig.dev: guess intent)."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is None:
            close = difflib.get_close_matches(
                cmd_name, self.list_commands(ctx), n=1, cutoff=0.6
            )
            if close:
                ctx.fail(
                    f"No such command '{cmd_name}'. Did you mean '{close[0]}'?"
                )
        return cmd


@click.group(cls=SuggestGroup,
             context_settings={"help_option_names": ["-h", "--help"]},
             epilog="Docs: https://signoz.io/docs/  |  "
                    "Issues: https://github.com/SigNoz/signoz/issues")
@click.option("-p", "--profile", "root_profile", default=None,
              help="Named connection profile to use for this invocation "
                   "(env: SIGNOZ_PROFILE; falls back to the default profile). "
                   "Accepted before or after the subcommand.")
@click.option("--no-input", is_flag=True, expose_value=False, is_eager=True,
              callback=_set_no_input,
              help="Never prompt; fail instead of asking for confirmation.")
@click.option("--no-color", is_flag=True, expose_value=False, is_eager=True,
              callback=_set_no_color,
              help="Disable ANSI colors in table output.")
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
    # Full argv (not ctx.args, which drops already-parsed global flags
    # like -p that rerun needs to reproduce the invocation).
    _record_history(sys.argv[1:])
    maybe_notify_update()


UPDATE_REPO = "ssh://forgejo/hamza/signoz-cli.git"
UPDATE_CHECK_INTERVAL_S = 24 * 3600


def _update_cache_path():
    """Location of the last update-check result (next to the config)."""
    from pathlib import Path

    return Path(CONFIG_DIR) / ".update-check.json"


def _read_update_cache() -> dict[str, Any]:
    """Read the cached update check; {} when missing or corrupt."""
    try:
        data = json.loads(_update_cache_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _latest_remote_tag(repo: str) -> str | None:
    """Newest vX.Y.Z tag via git ls-remote (SSH, no API token needed).

    Returns None when git is missing, the host is unreachable, or no
    version tags exist. Never raises.
    """
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "ls-remote", "--tags", repo],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    best: tuple[int, ...] = ()
    best_tag: str | None = None
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].startswith("refs/tags/"):
            continue
        ref = parts[1][len("refs/tags/"):]
        if ref.endswith("^{}"):
            continue
        parsed = _parse_version(ref)
        if parsed and parsed > best:
            best, best_tag = parsed, ref
    return best_tag


def _update_available() -> str | None:
    """Newest remote tag if newer than the installed version, else None.

    Uses a 24h cache so normal invocations stay offline and fast.
    """
    if os.getenv("SIGNOZ_NO_UPDATE_CHECK"):
        return None
    repo = os.getenv("SIGNOZ_UPDATE_REPO", UPDATE_REPO)
    try:
        current = _parse_version(__version__)
    except Exception:
        return None
    if not current:
        return None
    import time as _time

    now = _time.time()
    try:
        cached = _read_update_cache()
        checked_at = float(cached.get("checked_at", 0))
        if now - checked_at < UPDATE_CHECK_INTERVAL_S:
            latest = cached.get("latest")
            if latest and _parse_version(latest) > current:
                return latest
            return None
    except (TypeError, ValueError):
        pass
    latest = _latest_remote_tag(repo)
    try:
        path = _update_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checked_at": now, "latest": latest}))
    except OSError:
        pass
    if latest and _parse_version(latest) > current:
        return latest
    return None


def maybe_notify_update() -> None:
    """Warn on stderr when a newer release exists (best-effort).

    Skipped for non-TTY stdout (scripts/pipes), when the check is
    disabled, and never allowed to break or slow the invocation.
    """
    try:
        if not sys.stdout.isatty():
            return
        # No point nagging inside the updater itself (position-independent
        # check; a missed nag on exotic values is harmless).
        if any(a in ("update", "completion") for a in sys.argv[1:]):
            return
        latest = _update_available()
    except Exception:
        return
    if latest:
        click.echo(
            f"Update available: signoz-cli {latest} "
            f"(installed: v{__version__}). Run 'signoz update' to upgrade.",
            err=True,
        )


_QUERY_HISTORY_COMMANDS = {"logs", "traces", "metrics", "query", "promql", "sql"}
_QUERY_HISTORY_LIMIT = 100


def _history_path():
    """History file lives next to the config (isolated in tests via CONFIG_DIR)."""
    from pathlib import Path

    return Path(CONFIG_DIR) / "history.json"


def _read_history() -> list[dict[str, Any]]:
    """Read recorded query invocations (newest last); never raises."""
    try:
        data = json.loads(_history_path().read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _record_history(args: list[str]) -> None:
    """Append a query invocation to the local history (best-effort).

    Only the read/query roots are recorded, and never --help runs.
    Invocations carrying `--token` are never recorded: persisting raw
    secrets to disk (and echoing them in `history`) would leak them.
    The same applies to endpoint URLs embedding userinfo credentials.
    Must never break the invoked command.
    """
    try:
        if not args or args[0] not in _QUERY_HISTORY_COMMANDS:
            return
        if "-h" in args or "--help" in args:
            return
        # Never persist raw secrets: --token in either space or equals
        # form disqualifies the whole invocation from recording.
        if any(a == "--token" or a.startswith("--token=") for a in args):
            return
        # Endpoint URLs with embedded credentials (https://user:pass@host)
        # are secrets too -- in any flag form.
        if any(_has_url_userinfo(a) for a in args):
            return
        import datetime

        entries = _read_history()
        entries.append({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "argv": list(args),
        })
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries[-_QUERY_HISTORY_LIMIT:], indent=2))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        pass


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


_MAX_PAGES = 200


def _warn_page_cap(kind: str, rows: int) -> None:
    """Tell the user results were truncated at the page cap (not silently)."""
    click.echo(
        f"Warning: stopped after {_MAX_PAGES} pages ({rows} {kind}); more may "
        "exist. Narrow --time-range or add filters to fetch the rest.",
        err=True,
    )


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
            return page  # type: ignore[return-value]  # error passthrough
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
    else:
        _warn_page_cap("logs", len(rows))
    return rows


def _traces_query_all(
    client: SigNozClient,
    s: int,
    e: int,
    filter_expr: str,
    limit: int,
) -> list[Any]:
    """Fetch all span rows by walking the offset until a short page is returned.

    Offset pagination mirrors :func:`_logs_query_all`; the previous cursor-based
    walk never fed the cursor back into the request, so it re-fetched the first
    page and could not advance.
    """
    rows: list[Any] = []
    offset = 0
    for _ in range(_MAX_PAGES):
        payload = build_traces_query_payload(s, e, filter_expr, limit=limit, offset=offset)
        page = _unwrap_v5(client.query_builder_v5(payload))
        if not isinstance(page, list):
            return page  # type: ignore[return-value]  # error passthrough
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
    else:
        _warn_page_cap("spans", len(rows))
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
                    rows.append(dict(zip(named, vals, strict=True)))
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


def _q(value: str) -> str:
    r"""Escape a value for use inside a single-quoted filter literal.

    Live-verified against SigNoz v0.138: the filter grammar rejects
    backslash-escaped quotes (``it\'s`` -> HTTP 400 syntax error) but accepts
    SQL-style doubled quotes (``it''s``). Only quotes are therefore escaped.

    Known limitation (server-side): filter *values* containing backslashes
    match inconsistently through the query DSL depending on surrounding text.
    Use ``--query`` with a hand-written expression for such values.

    Without escaping, a value containing an apostrophe (e.g. ``it's-broken``)
    produces a 400 from the server, and in principle allows filter injection.
    ``--query``/``--filter`` are passed through verbatim: those are whole
    expressions the user is writing on purpose, not values.
    """
    return value.replace("'", "''")


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
        parts.append(f"service.name = '{_q(service)}'")
    if severity:
        parts.append(f"severity_text = '{_q(severity)}'")
    if search_text:
        parts.append(f"body CONTAINS '{_q(search_text)}'")
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
        parts.append(f"service.name = '{_q(service)}'")
    if operation:
        parts.append(f"name = '{_q(operation)}'")
    if error_filter == "true":
        parts.append("hasError = true")
    elif error_filter == "false":
        parts.append("hasError = false")
    return " AND ".join(parts)


def _append_filter(existing: str, clause: str) -> str:
    if existing and clause:
        return f"{existing} AND {clause}"
    return existing or clause


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


def _flatten_field_keys(data: Any) -> list[dict[str, Any]]:
    """Normalize fields responses to [{key, type, dataType}] rows.

    Shapes differ per signal: metrics/traces use ``data.attributeKeys``,
    logs uses ``data.attributes``. Explicit nulls and unknown shapes yield
    no rows (rendered as "(no results)") instead of a dict-repr cell.
    """
    if not isinstance(data, dict):
        return []
    inner = data.get("data", {})
    if not isinstance(inner, dict):
        return []
    entries = inner.get("attributeKeys") or inner.get("attributes") or []
    if not isinstance(entries, list):
        return []
    return [
        {
            "key": e.get("key", ""),
            "type": e.get("type", ""),
            "dataType": e.get("dataType", ""),
        }
        for e in entries
        if isinstance(e, dict)
    ]


def _flatten_field_values(data: Any) -> list[dict[str, Any]]:
    """Normalize field-values responses to [{value}] rows.

    Shapes differ per signal: traces nests ``data.values.*Values`` lists,
    logs uses ``data.{string,number,bool}AttributeValues``, metrics maps
    ``data.keys`` attribute names to descriptors. Explicit nulls and
    unknown shapes yield no rows.
    """
    if not isinstance(data, dict):
        return []
    inner = data.get("data", {})
    if not isinstance(inner, dict):
        return []
    values: list[Any] = []
    nested = inner.get("values")
    if isinstance(nested, dict):
        for key, val in nested.items():
            if key.lower().endswith("values") and isinstance(val, list):
                values.extend(val)
    for key in (
        "stringAttributeValues",
        "numberAttributeValues",
        "boolAttributeValues",
    ):
        val = inner.get(key)
        if isinstance(val, list):
            values.extend(val)
    keys_map = inner.get("keys")
    if isinstance(keys_map, dict):
        values.extend(sorted(keys_map))
    return [{"value": v} for v in values]


def _extract_alerts(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    raw_alerts = data.get("data", []) or []
    result = []
    for a in raw_alerts:
        if not isinstance(a, dict):
            continue
        labels = a.get("labels")
        status = a.get("status")
        if not isinstance(labels, dict):
            labels = {}
        if not isinstance(status, dict):
            status = {}
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


def _add_skeleton_option(cmd: click.Command) -> click.Command:
    """Add --generate-cli-skeleton to a command."""
    cmd.params.append(
        click.Option(
            ["--generate-cli-skeleton"],
            is_flag=True,
            expose_value=False,
            is_eager=True,
            callback=_skeleton_callback,
            help="Print an example payload for this command and exit.",
        )
    )
    return cmd


def _skeleton_callback(
    ctx: click.Context, param: click.Parameter, value: bool
) -> None:
    if not value:
        return
    import json as _json
    cmd_name = ctx.command.name
    skeleton: dict[str, Any] = {}
    if cmd_name == "query":
        skeleton = {
            "schemaVersion": "v1",
            "start": 0,
            "end": 0,
            "requestType": "time_series",
            "compositeQuery": {
                "queries": [
                    {
                        "type": "builder_query",
                        "spec": {
                            "name": "A",
                            "signal": "logs",
                            "stepInterval": 60,
                            "disabled": False,
                            "aggregations": [
                                {
                                    "metricName": "",
                                    "temporality": "Unspecified",
                                    "timeAggregation": "count",
                                    "spaceAggregation": "sum",
                                }
                            ],
                            "filter": {"expression": 'severity="ERROR"'},
                            "groupBy": [
                                {"name": "service.name", "signal": "logs"}
                            ],
                        },
                    }
                ]
            },
            "formatOptions": {
                "formatTableResultForUI": False,
                "fillGaps": False,
            },
            "variables": {},
        }
    elif cmd_name == "sql":
        skeleton = {
            "query": (
                "SELECT service.name, count(*) as cnt "
                "FROM signoz_logs.distributed_logs "
                "WHERE timestamp > now() - INTERVAL 1 HOUR "
                "GROUP BY service.name "
                "ORDER BY cnt DESC "
                "LIMIT 10"
            )
        }
    elif cmd_name == "promql":
        skeleton = {
            "schemaVersion": "v1",
            "start": 0,
            "end": 0,
            "requestType": "time_series",
            "compositeQuery": {
                "queries": [
                    {
                        "type": "promql",
                        "spec": {
                            "name": "A",
                            "query": "sum(rate(http_server_requests_total[5m]))",
                            "step": 60,
                        },
                    }
                ]
            },
            "formatOptions": {
                "formatTableResultForUI": False,
                "fillGaps": False,
            },
            "variables": {},
        }
    else:
        skeleton = {"_note": f"No skeleton template for command '{cmd_name}'."}

    click.echo(_json.dumps(skeleton, indent=2))
    ctx.exit()


