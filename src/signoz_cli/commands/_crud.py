"""Shared list/get/create/update/delete commands for simple JSON resources.

Many SigNoz resources (route policies, saved views, reduction rules, ...)
share one shape: list, get by id, create/replace from a JSON file, delete
by id. ``add_crud`` registers those commands on a group so each resource
only declares what differs. Client methods are named by string so the
command tests can mock the client by method name.
"""

from __future__ import annotations

from typing import Any

import click

from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    _read_json,
)
from signoz_cli.output import emit


def items_of(raw: Any, key: str | None = None) -> list[Any]:
    """Pull the item list out of a ``{"data": [...]}`` or ``{"data": {key: [...]}}`` reply."""
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if key and isinstance(data, dict):
        data = data.get(key)
    return data if isinstance(data, list) else []


def data_of(raw: Any) -> Any:
    """Unwrap the ``{"status": ..., "data": {...}}`` envelope for table output."""
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        return raw["data"]
    return raw


def add_crud(
    group: click.Group,
    *,
    resource: str,
    list_method: str | None = None,
    get_method: str | None = None,
    create_method: str | None = None,
    update_method: str | None = None,
    delete_method: str | None = None,
    list_key: str | None = None,
    columns: list[str] | None = None,
    create_example: str = "",
    note: str = "",
) -> None:
    """Register the standard commands for one resource on *group*."""
    suffix = f" ({note})" if note else ""

    if list_method:
        @group.command("list", help=f"List {resource}s{suffix}.")
        @_common_options
        def _list(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None) -> None:
            try:
                client = _get_client(endpoint, token, profile)
                items = items_of(getattr(client, list_method)(), list_key)
                if columns and fmt in ("table", "wide", "csv"):
                    items = [{c: i.get(c, "") for c in columns}
                             for i in items if isinstance(i, dict)]
                emit({"data": items}, fmt)
            except Exception as e:
                _handle_error(e)

    if get_method:
        @group.command("get", help=f"Get a {resource} by id{suffix}.")
        @_common_options
        @click.argument("item_id")
        def _get(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, item_id: str) -> None:
            try:
                client = _get_client(endpoint, token, profile)
                raw = getattr(client, get_method)(item_id)
                emit(raw if fmt in ("json", "yaml") else data_of(raw), fmt)
            except Exception as e:
                _handle_error(e)

    if create_method:
        example = f"\n\n\b\nExample file:\n  {create_example}" if create_example else ""

        @group.command("create",
                       help=f"Create a {resource} from a JSON file ('-' = stdin){suffix}."
                            f"{example}")
        @_common_options
        @click.argument("json_file", type=click.Path())
        def _create(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, json_file: str) -> None:
            try:
                body = _read_json(json_file)
                client = _get_client(endpoint, token, profile)
                raw = getattr(client, create_method)(body)
                emit(raw if fmt == "json" else data_of(raw), fmt)
            except Exception as e:
                _handle_error(e)

    if update_method:
        @group.command("update",
                       help=f"Replace a {resource} from a JSON file (full PUT){suffix}.")
        @_common_options
        @click.argument("item_id")
        @click.argument("json_file", type=click.Path())
        def _update(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, item_id: str, json_file: str) -> None:
            try:
                body = _read_json(json_file)
                client = _get_client(endpoint, token, profile)
                raw = getattr(client, update_method)(item_id, body)
                emit(raw if fmt == "json" else data_of(raw), fmt)
            except Exception as e:
                _handle_error(e)

    if delete_method:
        @group.command("delete", help=f"Delete a {resource}{suffix}.")
        @_common_options
        @click.argument("item_id")
        @click.option("--yes", is_flag=True, help="Skip confirmation.")
        @click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
        def _delete(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, item_id: str, yes: bool, dry_run: bool) -> None:
            try:
                if dry_run:
                    emit({"action": "delete", "resource": resource, "id": item_id}, fmt)
                    return
                if not yes:
                    _confirm(f"Delete {resource} {item_id}?")
                client = _get_client(endpoint, token, profile)
                emit(getattr(client, delete_method)(item_id), fmt)
            except Exception as e:
                _handle_error(e)
