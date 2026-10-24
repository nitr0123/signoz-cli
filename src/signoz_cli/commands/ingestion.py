"""ingestion key commands (SigNoz Cloud ingestion gateway)."""

from __future__ import annotations

import time
from typing import Any

import click

from signoz_cli.commands._crud import data_of
from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    cli,
)
from signoz_cli.output import emit
from signoz_cli.timeutil import parse_time_range


def _expiry(expires_in: str | None) -> str | None:
    """'90d' -> RFC 3339 timestamp 90 days from now; None/'never' -> None."""
    if not expires_in or expires_in == "never":
        return None
    try:
        secs = parse_time_range(expires_in) // 1000
    except ValueError as ex:
        raise click.UsageError(f"{ex} Or 'never'.") from None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + secs))


def _key_body(name: str, tags: tuple[str, ...], expires_in: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if tags:
        body["tags"] = list(tags)
    expires_at = _expiry(expires_in)
    if expires_at:
        body["expires_at"] = expires_at
    return body


def _limit_config(day_count: int | None, day_size: int | None,
                  second_count: int | None, second_size: int | None) -> dict[str, Any]:
    config: dict[str, Any] = {}
    for window, count, size in (("day", day_count, day_size),
                                ("second", second_count, second_size)):
        value = {k: v for k, v in (("count", count), ("size", size)) if v is not None}
        if value:
            config[window] = value
    if not config:
        raise click.UsageError(
            "Give at least one of --day-count, --day-size, --second-count, --second-size.")
    return config


def _limit_options(f):
    f = click.option("--second-size", type=int, default=None,
                     help="Max bytes per second.")(f)
    f = click.option("--second-count", type=int, default=None,
                     help="Max records per second.")(f)
    f = click.option("--day-size", type=int, default=None, help="Max bytes per day.")(f)
    f = click.option("--day-count", type=int, default=None, help="Max records per day.")(f)
    f = click.option("--tag", "tags", multiple=True, help="Tag (repeatable).")(f)
    return f


@cli.group("ingestion-keys")
def ingestion_keys() -> None:
    """Manage ingestion keys and their rate limits (SigNoz Cloud).

    Self-hosted servers have no ingestion gateway and answer HTTP 451.
    """


@ingestion_keys.command("list")
@_common_options
@click.option("--search", "name", default="", help="Only keys whose name matches.")
@click.option("--page", type=int, default=1, show_default=True, help="Page number.")
@click.option("--per-page", type=int, default=50, show_default=True, help="Keys per page.")
def ingestion_keys_list(endpoint: str | None, token: str | None, fmt: str,
                        profile: str | None, name: str, page: int, per_page: int) -> None:
    """List ingestion keys (values masked in table output)."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_ingestion_keys(page=page, per_page=per_page, name=name)
        if fmt == "json":
            emit(raw, fmt)
            return
        data = data_of(raw)
        keys = data.get("keys") if isinstance(data, dict) else None
        rows = []
        for k in keys or []:
            if not isinstance(k, dict):
                continue
            value = str(k.get("value", ""))
            rows.append({
                "id": k.get("id", ""), "name": k.get("name", ""),
                "value": f"{value[:6]}..." if len(value) > 6 else value,
                "tags": ", ".join(k.get("tags") or []),
                "limits": len(k.get("limits") or []),
                "expiresAt": k.get("expires_at", ""),
            })
        emit({"data": rows}, fmt)
        pagination = data.get("_pagination") if isinstance(data, dict) else None
        if isinstance(pagination, dict) and (pagination.get("pages") or 0) > page:
            click.echo(f"page {page} of {pagination['pages']}; next: --page {page + 1}",
                       err=True)
    except Exception as e:
        _handle_error(e)


@ingestion_keys.command("get")
@_common_options
@click.argument("key_id")
def ingestion_keys_get(endpoint: str | None, token: str | None, fmt: str,
                       profile: str | None, key_id: str) -> None:
    """Get an ingestion key, including its value and limits."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_ingestion_key(key_id)
        emit(raw if fmt == "json" else data_of(raw), fmt)
    except Exception as e:
        _handle_error(e)


@ingestion_keys.command("create")
@_common_options
@click.argument("name")
@click.option("--tag", "tags", multiple=True, help="Tag (repeatable).")
@click.option("--expires-in", default="never", show_default=True,
              help="Lifetime like 90d, or 'never'.")
def ingestion_keys_create(endpoint: str | None, token: str | None, fmt: str,
                          profile: str | None, name: str, tags: tuple[str, ...],
                          expires_in: str) -> None:
    """Create an ingestion key and print its value."""
    try:
        body = _key_body(name, tags, expires_in)
        client = _get_client(endpoint, token, profile)
        raw = client.create_ingestion_key(body)
        emit(raw if fmt == "json" else data_of(raw), fmt)
    except Exception as e:
        _handle_error(e)


@ingestion_keys.command("update")
@_common_options
@click.argument("key_id")
@click.option("--name", default=None, help="New name. Default: keep the current one.")
@click.option("--tag", "tags", multiple=True,
              help="Tag (repeatable; replaces existing tags). Default: keep them.")
@click.option("--expires-in", default=None,
              help="New lifetime from now, like 90d. Default: keep the current expiry.")
def ingestion_keys_update(endpoint: str | None, token: str | None, fmt: str,
                          profile: str | None, key_id: str, name: str | None,
                          tags: tuple[str, ...], expires_in: str | None) -> None:
    """Rename an ingestion key, or change its tags or expiry.

    Unset options keep their current values (the server forwards every
    field, so omitting one would clear it).
    """
    try:
        if name is None and not tags and expires_in is None:
            raise click.UsageError("Nothing to change: pass --name, --tag or --expires-in.")
        client = _get_client(endpoint, token, profile)
        current = data_of(client.get_ingestion_key(key_id))
        current = current if isinstance(current, dict) else {}
        body: dict[str, Any] = {"name": name or current.get("name", "")}
        body["tags"] = list(tags) if tags else (current.get("tags") or [])
        expires_at = _expiry(expires_in) if expires_in else current.get("expires_at")
        if expires_at:
            body["expires_at"] = expires_at
        client.update_ingestion_key(key_id, body)
        emit({"id": key_id, **body}, fmt)
    except Exception as e:
        _handle_error(e)


@ingestion_keys.command("delete")
@_common_options
@click.argument("key_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def ingestion_keys_delete(endpoint: str | None, token: str | None, fmt: str,
                          profile: str | None, key_id: str, yes: bool, dry_run: bool) -> None:
    """Delete an ingestion key (collectors using it are rejected)."""
    try:
        if dry_run:
            emit({"action": "delete", "resource": "ingestion key", "id": key_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete ingestion key {key_id}? Data sent with it will be rejected.")
        client = _get_client(endpoint, token, profile)
        emit(client.delete_ingestion_key(key_id), fmt)
    except Exception as e:
        _handle_error(e)


@ingestion_keys.group("limits")
def limits() -> None:
    """Per-signal rate limits on an ingestion key."""


@limits.command("list")
@_common_options
@click.argument("key_id")
def limits_list(endpoint: str | None, token: str | None, fmt: str,
                profile: str | None, key_id: str) -> None:
    """List the limits on an ingestion key."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_ingestion_key_limits(key_id)
        if fmt == "json":
            emit(raw, fmt)
            return
        items = raw.get("data") if isinstance(raw, dict) else raw
        rows = []
        for lim in items or []:
            if not isinstance(lim, dict):
                continue
            cfg = lim.get("config") or {}
            rows.append({
                "id": lim.get("id", ""), "signal": lim.get("signal", ""),
                "dayCount": (cfg.get("day") or {}).get("count", ""),
                "daySize": (cfg.get("day") or {}).get("size", ""),
                "secondCount": (cfg.get("second") or {}).get("count", ""),
                "secondSize": (cfg.get("second") or {}).get("size", ""),
            })
        emit({"data": rows}, fmt)
    except Exception as e:
        _handle_error(e)


@limits.command("create")
@_common_options
@click.argument("key_id")
@click.option("--signal", required=True, type=click.Choice(["logs", "traces", "metrics"]),
              help="Signal the limit applies to.")
@_limit_options
def limits_create(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, key_id: str, signal: str, tags: tuple[str, ...],
                  day_count: int | None, day_size: int | None,
                  second_count: int | None, second_size: int | None) -> None:
    """Add a rate limit to an ingestion key (SigNoz 0.141+)."""
    try:
        body: dict[str, Any] = {
            "keyId": key_id, "signal": signal,
            "config": _limit_config(day_count, day_size, second_count, second_size),
        }
        if tags:
            body["tags"] = list(tags)
        client = _get_client(endpoint, token, profile)
        raw = client.create_ingestion_limit(body)
        emit(raw if fmt == "json" else data_of(raw), fmt)
    except Exception as e:
        _handle_error(e)


@limits.command("update")
@_common_options
@click.argument("limit_id")
@_limit_options
def limits_update(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, limit_id: str, tags: tuple[str, ...],
                  day_count: int | None, day_size: int | None,
                  second_count: int | None, second_size: int | None) -> None:
    """Change a limit's thresholds (SigNoz 0.141+).

    Thresholds and tags you don't pass keep their current values.
    """
    try:
        changes = _limit_config(day_count, day_size, second_count, second_size)
        client = _get_client(endpoint, token, profile)
        current = data_of(client.get_ingestion_limit(limit_id))
        current = current if isinstance(current, dict) else {}
        config = {w: dict(v) for w, v in (current.get("config") or {}).items()
                  if isinstance(v, dict)}
        for window, values in changes.items():
            config.setdefault(window, {}).update(values)
        body: dict[str, Any] = {
            "config": config,
            "tags": list(tags) if tags else (current.get("tags") or []),
        }
        client.update_ingestion_limit(limit_id, body)
        emit({"id": limit_id, **body}, fmt)
    except Exception as e:
        _handle_error(e)


@limits.command("delete")
@_common_options
@click.argument("limit_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def limits_delete(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, limit_id: str, yes: bool) -> None:
    """Remove a rate limit (SigNoz 0.141+)."""
    try:
        if not yes:
            _confirm(f"Delete ingestion limit {limit_id}?")
        client = _get_client(endpoint, token, profile)
        emit(client.delete_ingestion_limit(limit_id), fmt)
    except Exception as e:
        _handle_error(e)
