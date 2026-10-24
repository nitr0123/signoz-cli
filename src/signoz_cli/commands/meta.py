"""Top-level meta commands: completion, status, explain."""

from __future__ import annotations

import click

from signoz_cli.commands.common import (
    SUPPORTED_SIGNOZ_RANGE,
    _common_options,
    _get_client,
    _handle_error,
    _has_url_userinfo,
    _is_supported_server,
    _is_truthy,
    _redact_userinfo,
    _resolve_credentials,
    _selected_profile_name,
    cli,
)
from signoz_cli.history import _history_path, _read_history
from signoz_cli.output import emit


@cli.command("completion")
@click.argument(
    "shell",
    type=click.Choice(["bash", "zsh", "fish"]),
    default="bash",
    required=False,
)
def completion(shell: str) -> None:
    """Print shell-completion setup for bash/zsh/fish.

    Add the printed line to your shell rc file, or run it once in the session.
    """
    click.echo(f"# Add this to your {shell} configuration:")
    click.echo(f'eval "$(_SIGNOZ_COMPLETE={shell}_source signoz)"')


_PROBES = ("readyz", "livez", "healthz")


def _probe_rows(client, probes: tuple[str, ...]) -> list[dict[str, str]]:
    """One row per probe: healthy yes/no plus any services reported."""
    rows = []
    for probe in probes:
        status, body = client.get_health(probe)
        data = body.get("data", body) if isinstance(body, dict) else {}
        data = data if isinstance(data, dict) else {}
        # livez answers a bare {"status": "success"}: no data means the
        # HTTP status is the verdict.
        healthy = status < 400 and bool(data.get("healthy", True))
        services = data.get("services") or {}
        detail = "; ".join(
            f"{state}: {', '.join(map(str, names))}"
            for state, names in sorted(services.items())
            if names
        ) if isinstance(services, dict) else ""
        rows.append({"probe": probe, "healthy": "yes" if healthy else "no",
                     "http": str(status), "services": detail})
    return rows


@cli.command("health")
@_common_options
@click.option("--probe", type=click.Choice(["all", *_PROBES]), default="readyz",
              show_default=True,
              help="readyz: ready to serve; livez: process alive; healthz: overall.")
def health_cmd(endpoint: str | None, token: str | None, fmt: str,
               profile: str | None, probe: str) -> None:
    """Check whether the SigNoz server is up (exit 1 when unhealthy).

    Uses the server's unauthenticated health probes, so it works before
    an API key is configured: only the endpoint is required. Made for
    scripts and CI, e.g. waiting for a fresh deploy:

    \b
      until signoz health >/dev/null; do sleep 5; done
    """
    try:
        _ep, tok = _resolve_credentials(endpoint, token, profile)
        client = _get_client(endpoint, tok or "unauthenticated", profile)
        client.max_retries = 0  # a 503 is the answer, not a transient error
        probes = _PROBES if probe == "all" else (probe,)
        rows = _probe_rows(client, probes)
        emit(rows, fmt)
        if any(r["healthy"] != "yes" for r in rows):
            raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as e:
        _handle_error(e)


@cli.command("doctor")
@_common_options
def doctor_cmd(endpoint: str | None, token: str | None, fmt: str,
               profile: str | None) -> None:
    """Diagnose local setup and server connectivity (support-bundle style).

    Runs config, credential, TLS, connectivity, auth and version checks
    and reports pass/warn/fail per check. Never prints secrets. Exits 1
    when any check fails (warnings alone still exit 0).
    """
    import os as _os
    import stat as _stat

    import httpx as _httpx

    from signoz_cli.commands.common import _mask_token as _mask
    from signoz_cli.commands.common import _resolve_credentials as _resolve
    from signoz_cli.config import (
        CONFIG_FILE,
        get_profiles,
        load_config,
        select_profile_name,
    )

    rows: list[dict[str, str]] = []

    def _row(check: str, status: str, detail: str) -> None:
        rows.append({"check": check, "status": status, "detail": detail})

    # 1. Config file.
    cfg = load_config()
    if not CONFIG_FILE.exists():
        _row("config", "warn",
             f"No config file yet ({CONFIG_FILE}); "
             "use 'signoz configure' or env vars.")
    elif not isinstance(cfg, dict):
        _row("config", "fail", f"{CONFIG_FILE} does not contain an object.")
    else:
        try:
            mode = oct(_stat.S_IMODE(CONFIG_FILE.stat().st_mode))
        except OSError as ex:
            mode = f"<unreadable: {ex}>"
        if mode == "0o600":
            _row("config", "pass", f"{CONFIG_FILE} readable, mode 0600.")
        else:
            _row("config", "warn",
                 f"{CONFIG_FILE} mode is {mode} (expected 0o600).")
    profiles = get_profiles(cfg) if isinstance(cfg, dict) else {}
    selected = None
    try:
        selected = select_profile_name(
            cfg if isinstance(cfg, dict) else {}, profile,
            _os.getenv("SIGNOZ_PROFILE"))
    except Exception:
        selected = None
    # Doctor must survive the corrupt configs it diagnoses: only trust
    # string profile names mapping to objects.
    prof = profiles.get(selected, {}) if isinstance(selected, str) else {}
    if not isinstance(prof, dict):
        prof = {}
    if selected is None:
        _row("profile", "pass",
             "No profile selected (flags/env/legacy config).")
    elif (isinstance(selected, str) and selected in profiles
            and isinstance(profiles[selected], dict)):
        _row("profile", "pass", f"Using profile '{selected}'.")
    else:
        _row("profile", "warn",
             f"Selected profile {selected!r} is not a valid profile.")

    # 2. Credentials resolvable (never echoed raw).
    try:
        ep, tok = _resolve(endpoint, token, profile)
    except Exception as ex:
        _row("credentials", "fail", str(ex))
        emit(rows, fmt)
        raise SystemExit(1) from None
    if not ep or not tok:
        _row("credentials", "fail",
             "Missing endpoint/token. Set via --endpoint/--token, "
             "SIGNOZ_* env vars, or 'signoz configure'.")
        emit(rows, fmt)
        raise SystemExit(1) from None
    _row("credentials", "pass",
         f"Endpoint {_redact_userinfo(ep)}, token {_mask(tok)}.")

    # 3. TLS posture.
    insecure = (_os.getenv("SIGNOZ_INSECURE", "") == "1"
                or _is_truthy(prof.get("insecure")))
    _row("tls", "warn" if insecure else "pass",
         "Certificate verification DISABLED (insecure)."
         if insecure else "Certificate verification enabled.")

    # 4-6. Connectivity, auth, version.
    try:
        client = _get_client(endpoint, token, profile)
    except Exception as ex:
        _row("connect", "fail", f"Cannot configure client: {ex}")
        emit(rows, fmt)
        raise SystemExit(1) from None
    try:
        me = client.verify()
        account = me.get("data", me) if isinstance(me, dict) else me
        name = account.get("name", "") if isinstance(account, dict) else ""
        _row("connect", "pass", f"Reached {client.endpoint}.")
        _row("auth", "pass",
             f"API key accepted{f' (service account {name!r})' if name else ''}.")
    except _httpx.ConnectError as ex:
        _row("connect", "fail", f"Cannot connect: {ex}")
        _row("auth", "fail", "Skipped (no connection).")
        emit(rows, fmt)
        raise SystemExit(1) from None
    except _httpx.TimeoutException:
        _row("connect", "fail", "Connection timed out (--timeout raises it).")
        _row("auth", "fail", "Skipped (no connection).")
        emit(rows, fmt)
        raise SystemExit(1) from None
    except Exception as ex:
        _row("connect", "pass", f"Reached {client.endpoint}.")
        _row("auth", "fail", f"API key rejected: {ex}")
        emit(rows, fmt)
        raise SystemExit(1) from None
    try:
        server_version = (client.get_server_version() or {}).get("version", "")
    except Exception:
        server_version = ""
    if not server_version:
        _row("version", "warn", "Server version unknown.")
    elif _is_supported_server(server_version):
        _row("version", "pass",
             f"Server {server_version} in tested range "
             f"({SUPPORTED_SIGNOZ_RANGE}).")
    else:
        _row("version", "warn",
             f"Server {server_version} outside tested range "
             f"({SUPPORTED_SIGNOZ_RANGE}).")
    try:
        client.max_retries = 0
        ready = _probe_rows(client, ("readyz",))[0]
        if ready["healthy"] == "yes":
            _row("health", "pass", "Server reports ready (readyz).")
        else:
            _row("health", "warn",
                 f"Server not ready (readyz HTTP {ready['http']})"
                 f"{': ' + ready['services'] if ready['services'] else ''}.")
    except Exception:
        _row("health", "warn", "Health probe unavailable (needs SigNoz 0.136+).")
    emit(rows, fmt)


@cli.command("status")
@_common_options
def status_cmd(endpoint: str | None, token: str | None, fmt: str,
               profile: str | None) -> None:
    """Show connectivity, server version and account info in one glance."""
    try:
        client = _get_client(endpoint, token, profile)
        me = client.verify()
        account = me.get("data", me) if isinstance(me, dict) else me
        server_version = ""
        try:
            # Best-effort: version powers only display fields; failures
            # here must not mask verify() above.
            server_version = (client.get_server_version() or {}).get("version", "")
        except Exception:
            server_version = ""
        status = {
            "endpoint": client.endpoint,
            "profile": _selected_profile_name(profile) or "<none>",
            "server_version": server_version or "<unknown>",
            "compatible": _is_supported_server(server_version),
            "account": account.get("name", "") if isinstance(account, dict) else "",
            "account_id": account.get("id", "") if isinstance(account, dict) else "",
            "org_id": account.get("orgId", "") if isinstance(account, dict) else "",
        }
        emit(status, fmt)
    except Exception as e:
        _handle_error(e)


@cli.command("explain")
@click.argument("topic", default="overview")
def explain(topic: str) -> None:
    """Explain query syntax, filters, and available signals.

    Topics: overview, filters, aggregations, metrics, logs, traces,
    workflows.

    Examples:

      signoz explain

      signoz explain filters

      signoz explain aggregations
    """
    topics = {
        "overview": """SigNoz CLI Query Reference
=========================

FILTER SYNTAX (used in --query, --filter)
  field="value"           Exact match
  field!="value"          Not equal
  field=~"regex"          Regex match
  field!~"regex"          Negated regex
  field in ["a","b"]      Set membership
  field>100               Numeric comparison
  field>=100              Greater than or equal
  AND / OR                Logical operators
  (expr)                  Grouping

  Examples:
    severity="ERROR"
    severity="ERROR" AND service.name="api-server"
    message=~"timeout|deadline"
    durationNano>1000000000

AGGREGATIONS
  --time-aggregation:   avg, sum, min, max, count, rate, increase,
                        p50, p75, p90, p95, p99
  --space-aggregation:  sum, avg, min, max, count
  --reduce-to:         sum, count, avg, min, max, last, median

SIGNALS (Query Builder v5)
  logs      — log lines (message, severity, service.name, timestamp)
  traces    — distributed trace spans (name, durationNano, hasError)
  metrics   — time-series metrics (container_*, http_*, etc.)

TIME RANGES
  30m, 1h, 2h, 6h, 12h, 24h, 2d, 7d, 30d
  Or explicit --start/--end in milliseconds.
""",
        "filters": """FILTER EXPRESSIONS
==================

Basic operators:
  field="value"       Exact match
  field!="value"      Not equal
  field=~"regex"      Regex match (RE2 syntax)
  field!~"regex"      Negated regex

Comparison operators:
  field>number        Greater than
  field>=number       Greater than or equal
  field<number        Less than
  field<=number       Less than or equal

Logical operators:
  expr AND expr       Both conditions must be true
  expr OR expr        Either condition can be true
  (expr)              Grouping

Common fields:
  severity            Log level (TRACE, DEBUG, INFO, WARN, ERROR, FATAL)
  message             Log message text
  service.name        Service identifier
  hasError            true/false (traces)
  durationNano        Span duration in nanoseconds
  traceID             Distributed trace identifier
  spanID              Span identifier

Examples:
  severity="ERROR"
  severity="ERROR" AND service.name="api-server"
  message=~"connection refused|timeout"
  durationNano>100000000 AND hasError=true
  severity IN ["ERROR","WARN"] AND service.name=~"payment"
""",
        "aggregations": """AGGREGATION REFERENCE
=====================

Time aggregations (--time-aggregation):
  avg       Arithmetic mean over the time window
  sum       Sum of all values
  min       Minimum value
  max       Maximum value
  count     Number of data points
  rate      Per-second rate of increase (counters)
  increase  Total increase over the window (counters)
  p50       50th percentile (median)
  p75       75th percentile
  p90       90th percentile
  p95       95th percentile
  p99       99th percentile

Space aggregations (--space-aggregation):
  sum       Sum across all dimension combinations
  avg       Average across dimensions
  min       Minimum across dimensions
  max       Maximum across dimensions
  count     Count of dimension combinations

Reduce to (--reduce-to, for metrics stats):
  sum       Sum of the reduced time series
  count     Count of data points
  avg       Average of the time series
  min       Minimum of the time series
  max       Maximum of the time series
  last      Last value in the time series
  median    Median of the time series

Metric temporality (--temporality):
  Unspecified  Default for gauge metrics
  Cumulative   Default for sum/counter metrics
  Delta        For delta counters
""",
        "metrics": """METRICS QUERIES
===============

List metric keys:
  signoz metrics list-keys
  signoz metrics search "container"

Get field info:
  signoz metrics fields
  signoz metrics field-values k8s.pod.name

Query time series:
  signoz metrics query container_memory_working_set_bytes \\
    --time-aggregation max --group-by k8s.pod.name

Summary stats (single value per group):
  signoz metrics stats container_memory_working_set_bytes \\
    --group-by k8s.pod.name --time-range 7d

PromQL:
  signoz promql 'rate(http_requests_total[5m])'

Raw Query Builder v5:
  signoz query payload.json
""",
        "logs": """LOGS QUERIES
============

Search logs:
  signoz logs search --service api-server --severity ERROR --time-range 1h
  signoz logs search --query 'message=~"timeout"' --time-range 24h

Error logs:
  signoz logs errors --service api-server --time-range 6h

Aggregate logs:
  signoz logs aggregate --aggregation count --group-by severity

Log fields:
  signoz logs fields
  signoz logs field-values severity --time-range 1h

Tail live logs:
  signoz logs tail --service api-server

By service:
  signoz logs by-service api-server --time-range 1h

Common fields:
  message, severity, service.name, timestamp, traceID
""",
        "traces": """TRACES QUERIES
==============

Search traces:
  signoz traces search --service api-server --time-range 1h
  signoz traces search --query 'durationNano>100000000' --time-range 6h

Error traces:
  signoz traces errors --service api-server --time-range 1h

Trace detail:
  signoz traces detail <traceID>

By service:
  signoz traces by-service api-server --time-range 1h

Top operations:
  signoz services top-operations api-server

Trace fields:
  signoz traces fields
  signoz traces field-values "service.name" --time-range 1h

Common fields:
  name, durationNano, hasError, traceID, spanID, service.name
""",
        "workflows": """WORKFLOWS (follow, rank, replay, migrate, maintain)
====================================================

Follow live telemetry (Ctrl-C stops; --interval sets poll seconds):
  signoz logs tail --service api-server
  signoz traces tail --service api-server
  signoz alerts watch

Rank and replay:
  signoz metrics top http_server_duration --group-by service.name
  signoz history                  # recent queries with indexes
  signoz rerun 3                  # re-execute history entry 3

Migrate between instances (profiles!):
  signoz export ./backup -p prod
  signoz import ./backup -p staging --dry-run
  signoz import ./backup -p staging --on-conflict overwrite --yes
  signoz dashboards diff <id> dashboard.json
  signoz alerts diff <rule-id> rule.json

Planned maintenance (mute alerts in a window):
  signoz alerts downtime create window.json
  signoz alerts downtime list
  signoz alerts downtime delete <id> --yes

Stay current:
  signoz update                   # check installed vs latest release
  signoz update --apply           # upgrade via your install method
""",
    }

    content = topics.get(topic)
    if content is None:
        available = ", ".join(sorted(topics.keys()))
        raise click.UsageError(
            f"Unknown topic '{topic}'. Available: {available}"
        )
    click.echo(content)




def _display_history_command(entry: dict) -> str:
    """Render a history entry for display with secrets redacted.

    Entries recorded by older releases may contain ``--token`` values or
    endpoint URLs with embedded credentials; never echo those back to
    the terminal (shoulder surfing, scrollback, tee'd logs).
    """
    out: list[str] = []
    skip_next = False
    for arg in entry.get("argv", []):
        if skip_next:
            out.append("<redacted>")
            skip_next = False
            continue
        if arg == "--token":
            out.append(arg)
            skip_next = True
        elif arg.startswith("--token="):
            out.append("--token=<redacted>")
        elif _has_url_userinfo(arg):
            out.append(_redact_userinfo(arg))
        else:
            out.append(arg)
    return "signoz " + " ".join(out)


def _history_entry_has_secret(argv: list) -> bool:
    """Whether a recorded argv carries raw secrets (legacy poisoned entry)."""
    it = iter(argv)
    for arg in it:
        if not isinstance(arg, str):
            continue
        if arg == "--token" or arg.startswith("--token="):
            return True
        if _has_url_userinfo(arg):
            return True
    return False


@cli.command("history")
@click.option("--clear", is_flag=True, help="Erase the recorded query history.")
def history_cmd(clear: bool) -> None:
    """List recent query invocations (newest last with their index)."""
    try:
        if clear:
            try:
                _history_path().unlink(missing_ok=True)
            except OSError as ex:
                raise click.UsageError(f"Cannot clear history: {ex}") from None
            click.echo("History cleared.")
            return
        entries = _read_history()
        emit(
            [
                {"index": i, "ts": e.get("ts", ""), "command": _display_history_command(e)}
                for i, e in enumerate(entries)
            ],
            "table",
        )
    except Exception as e:
        _handle_error(e)


@cli.command("rerun")
@click.argument("index", type=int)
def rerun_cmd(index: int) -> None:
    """Re-execute a recorded query by its `history` index (replaces process)."""
    try:
        entries = _read_history()
        if not entries:
            raise click.UsageError("No recorded history yet.")
        if index < 0 or index >= len(entries):
            raise click.UsageError(
                f"Index {index} out of range (0..{len(entries) - 1}). "
                "See 'signoz history'."
            )
        argv = entries[index].get("argv", [])
        if not argv:
            raise click.UsageError(f"History entry {index} has no command.")
        if _history_entry_has_secret(argv):
            # Legacy entry recorded before the never-store-secrets guard:
            # re-executing would splash the secret into the new process
            # argv (ps-visible). Refuse; the user can retype the command
            # with --token passed securely.
            raise click.UsageError(
                f"History entry {index} contains a raw secret (recorded by an "
                "older release). It will not be re-executed; retype the "
                "command instead, or run 'signoz history --clear'."
            )
        import os as _os

        try:
            _os.execvp("signoz", ["signoz", *argv])
        except OSError as ex:
            raise click.UsageError(
                f"Cannot re-execute (signoz not on PATH?): {ex}"
            ) from None
    except Exception as e:
        _handle_error(e)


@cli.command("watch")
@click.option("-n", "--interval", type=float, default=2.0, show_default=True,
              help="Seconds between runs.")
@click.argument("command", nargs=-1, required=True)
def watch_cmd(interval: float, command: tuple[str, ...]) -> None:
    """Re-run a signoz command every N seconds, like watch(1).

    The wrapped command re-executes in a child process (list-form, no
    shell), so each run starts clean. Relative --time-range windows
    re-anchor every run. Ctrl-C stops.

    Note: arguments are passed to the child verbatim and stay visible
    in process listings while it runs -- prefer profiles/env vars over
    --token/--endpoint secrets here, as everywhere else.

    \b
    Examples:
      signoz watch -n 5 -- alerts list
      signoz watch -- metrics top --time-range 1h
    """
    if interval <= 0:
        raise click.UsageError("--interval must be positive (seconds).")
    root = command[0] if command else ""
    if root in (
        "watch", "mcp", "completion", "update",
        "configure", "profile", "history", "rerun",
        "apply", "import", "export",
    ):
        raise click.UsageError(
            f"watch: refusing to wrap '{root}' "
            "(interactive, long-lived, credential/state-touching, "
            "self-modifying, or repeatedly mutating)."
        )
    import subprocess as _sp
    import sys as _sys
    import time as _time

    argv0 = _sys.argv[0]
    last_exit: int | None = None
    # Never echo raw secrets into the repeating header: mask --token
    # values (both forms), mirroring the history display redaction.
    shown: list[str] = []
    _skip_next = False
    for _arg in command:
        if _skip_next:
            shown.append("<redacted>")
            _skip_next = False
        elif _arg == "--token":
            shown.append(_arg)
            _skip_next = True
        elif _arg.startswith("--token="):
            shown.append("--token=<redacted>")
        elif _has_url_userinfo(_arg):
            shown.append(_redact_userinfo(_arg))
        else:
            shown.append(_arg)
    try:
        while True:
            stamp = _time.strftime("%Y-%m-%d %H:%M:%S")
            header = f"Every {interval:g}s: signoz {' '.join(shown)}  [{stamp}]"
            if last_exit not in (None, 0):
                header += f"  (last exit: {last_exit})"
            if _sys.stdout.isatty():
                click.clear()
            click.echo(header, err=True)
            proc = _sp.run([argv0, *command])
            last_exit = proc.returncode
            _time.sleep(interval)
    except KeyboardInterrupt:
        click.echo("Stopped.", err=True)
    except Exception as e:
        _handle_error(e)
