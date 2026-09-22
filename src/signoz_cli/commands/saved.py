"""Saved queries: named promql/sql/builder queries in local storage.

Queries live in ``CONFIG_DIR/queries.json`` (owner-only, capped at 100
entries) so teams can share them via dotfiles. Running re-resolves the
time window fresh; stored payloads never carry credentials.
"""

from __future__ import annotations

import datetime as _datetime
import json
import re
from typing import Any

import click

from signoz_cli.commands.common import (
    _common_options,
    _get_client,
    _handle_error,
    _maybe_emit_chart,
    _profile_time_range,
    _read_json,
    _time_options,
    _unwrap_time_series,
    _unwrap_v5,
    _warn_v5,
    cli,
)
from signoz_cli.config import CONFIG_DIR
from signoz_cli.output import emit
from signoz_cli.timeutil import resolve_timestamps

_QUERIES_LIMIT = 100
_NAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")
_KINDS = ("promql", "sql", "builder")
# Text query files share the JSON intake cap: a multi-GB file is a
# mistake (or hostile), never a query.
_TEXT_CAP_BYTES = 32 * 1024 * 1024


def _read_text_capped(path: str) -> str:
    """Read a text file with a size cap (see _MAX_JSON_BYTES rationale)."""
    import os as _os

    try:
        size = _os.path.getsize(path)
    except OSError as ex:
        raise click.UsageError(f"Cannot read {path}: {ex}") from None
    if size > _TEXT_CAP_BYTES:
        raise click.UsageError(
            f"Refusing to read {path} ({size} bytes > "
            f"{_TEXT_CAP_BYTES // (1024 * 1024)}MiB cap)."
        )
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _queries_path():
    """Storage file lives next to the config (isolated in tests via CONFIG_DIR)."""
    from pathlib import Path

    return Path(CONFIG_DIR) / "queries.json"


def _read_saved() -> list[dict[str, Any]]:
    """Read saved queries; never raises (corrupt files read as empty)."""
    try:
        data = json.loads(_queries_path().read_text())
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _write_saved(entries: list[dict[str, Any]]) -> None:
    """Persist saved queries (owner-only from birth)."""
    from signoz_cli.config import _ensure_config_dir, _write_private

    path = _queries_path()
    _ensure_config_dir()
    _write_private(path, json.dumps(entries[-_QUERIES_LIMIT:], indent=2))


def _find_saved(name: str) -> dict[str, Any] | None:
    for entry in _read_saved():
        if entry.get("name") == name:
            return entry
    return None


def _check_name(name: str) -> None:
    if not _NAME_RE.fullmatch(name):
        raise click.UsageError(
            f"Invalid query name {name!r}: use 1-80 characters of "
            "letters, digits, and _ . -"
        )


@cli.group("saved")
def saved() -> None:
    """Save and re-run named queries (local, shareable via dotfiles)."""


@saved.command("save")
@click.argument("name")
@click.option("--kind", type=click.Choice(["promql", "sql", "builder"]),
              required=True, help="Query language.")
@click.option("--query", "query_text", default=None,
              help="Query text (promql/sql) or JSON document (builder).")
@click.option("--file", "file_path", default=None,
              help="Read the query from a file instead of --query.")
@click.option("--step", type=int, default=60, show_default=True,
              help="Step seconds for promql runs.")
@click.option("--yes", is_flag=True, help="Overwrite an existing entry.")
def saved_save(name: str, kind: str, query_text: str | None,
               file_path: str | None, step: int, yes: bool) -> None:
    """Save a named query for later `saved run`.

    \b
    Examples:
      signoz saved save errors-1h --kind promql \\
        --query 'sum({__name__="app.errors.total"})'
      signoz saved save slow-ops --kind sql --file q.sql
      signoz saved save weekly --kind builder --file payload.json
    """
    try:
        _check_name(name)
        if (query_text is None) == (file_path is None):
            raise click.UsageError("Pass exactly one of --query or --file.")
        if step <= 0:
            raise click.UsageError("--step must be positive.")
        if file_path:
            if kind == "builder":
                payload: Any = _read_json(file_path)
                if not isinstance(payload, dict):
                    raise click.UsageError(
                        "Builder payloads must be JSON objects.")
            else:
                payload = _read_text_capped(file_path)
                if not payload.strip():
                    raise click.UsageError(f"{file_path} is empty.")
        else:
            assert query_text is not None
            if kind == "builder":
                try:
                    payload = json.loads(query_text)
                except ValueError:
                    raise click.UsageError(
                        "Builder --query must be a JSON document.") from None
                if not isinstance(payload, dict):
                    raise click.UsageError(
                        "Builder payloads must be JSON objects.")
            else:
                payload = query_text
                if not payload.strip():
                    raise click.UsageError("--query must not be empty.")
        entries = _read_saved()
        if any(e.get("name") == name for e in entries) and not yes:
            raise click.UsageError(
                f"Query '{name}' already exists (pass --yes to overwrite).")
        entries = [e for e in entries if e.get("name") != name]
        entries.append({
            "name": name, "kind": kind, "payload": payload, "step": step,
            "saved_at": _datetime.datetime.now(
                _datetime.timezone.utc).isoformat(),
        })
        _write_saved(entries)
        click.echo(f"Saved query '{name}' ({kind}).")
    except Exception as e:
        _handle_error(e)


@saved.command("list")
@click.option("-f", "--format", "fmt", type=click.Choice(["json", "table"]),
              default="table", show_default=True, help="Output format.")
def saved_list(fmt: str) -> None:
    """List saved queries."""
    try:
        emit([{"name": e.get("name", ""), "kind": e.get("kind", ""),
               "saved_at": e.get("saved_at", "")}
              for e in _read_saved()], fmt)
    except Exception as e:
        _handle_error(e)


@saved.command("show")
@click.argument("name")
def saved_show(name: str) -> None:
    """Show one saved query definition."""
    try:
        entry = _find_saved(name)
        if entry is None:
            raise click.UsageError(
                f"Unknown query '{name}'. See 'signoz saved list'.")
        emit(entry, "json")
    except Exception as e:
        _handle_error(e)


@saved.command("delete")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def saved_delete(name: str, yes: bool) -> None:
    """Delete a saved query."""
    from signoz_cli.commands.common import _confirm

    try:
        entries = _read_saved()
        if not any(e.get("name") == name for e in entries):
            raise click.UsageError(
                f"Unknown query '{name}'. See 'signoz saved list'.")
        if not yes:
            _confirm(f"Delete saved query '{name}'?")
        _write_saved([e for e in entries if e.get("name") != name])
        click.echo(f"Deleted saved query '{name}'.")
    except Exception as e:
        _handle_error(e)


@saved.command("run")
@_common_options
@click.argument("name")
@click.option("--chart", is_flag=True,
              help="Render time series as ASCII sparklines.")
@_time_options(default_range="1h")
def saved_run(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, name: str, chart: bool,
    time_range: str | None, start: int | None, end: int | None,
) -> None:
    """Run a saved query with a fresh time window.

    \b
    Examples:
      signoz saved run errors-1h
      signoz saved run errors-1h --time-range 24h --chart
    """
    try:
        entry = _find_saved(name)
        if entry is None:
            raise click.UsageError(
                f"Unknown query '{name}'. See 'signoz saved list'.")
        kind = entry.get("kind")
        payload = entry.get("payload")
        client = _get_client(endpoint, token, profile)
        s, e = resolve_timestamps(
            start, end, time_range, default_range="1h",
            profile_time_range=_profile_time_range(profile))
        if kind == "promql":
            if not isinstance(payload, str):
                raise click.UsageError(
                    f"Saved query '{name}' has a corrupt payload.")
            step = entry.get("step", 60)
            if not isinstance(step, int) or step <= 0:
                raise click.UsageError(
                    f"Saved query '{name}' has an invalid step.")
            resp = client.query_promql(payload, s, e, step=step)
            _warn_v5(resp)
            rows = _unwrap_time_series(resp)
        elif kind == "sql":
            if not isinstance(payload, str):
                raise click.UsageError(
                    f"Saved query '{name}' has a corrupt payload.")
            rows = _unwrap_v5(client.query_sql(payload, s, e))
        elif kind == "builder":
            if not isinstance(payload, dict):
                raise click.UsageError(
                    f"Saved query '{name}' has a corrupt payload.")
            body = dict(payload)
            body["start"], body["end"] = s, e
            rows = _unwrap_v5(client.query_builder_v5(body))
        else:
            raise click.UsageError(
                f"Saved query '{name}' has unknown kind {kind!r}.")
        if _maybe_emit_chart(rows, fmt, chart):
            return
        emit(rows, fmt)
    except Exception as ex:
        _handle_error(ex)
