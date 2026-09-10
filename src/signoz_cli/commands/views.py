"""saved views commands."""

from __future__ import annotations

import click

from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    _read_json,
    cli,
)
from signoz_cli.output import emit


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
        raw = client.list_saved_views(source_page)
        # v0.136 returns {"data": null} when empty; normalize to a list.
        items = raw.get("data") if isinstance(raw, dict) else raw
        emit({"data": items if isinstance(items, list) else []}, fmt)
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


@views.command("create")
@_common_options
@click.argument("json_file", type=click.Path())
def views_create(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, json_file: str) -> None:
    """Create a saved view from a JSON file."""
    try:
        view = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        emit(client.create_saved_view(view), fmt)
    except Exception as e:
        _handle_error(e)


@views.command("delete")
@_common_options
@click.argument("view_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def views_delete(endpoint: str | None, token: str | None, fmt: str,
                profile: str | None, view_id: str, yes: bool, dry_run: bool) -> None:
    """Delete a saved view."""
    try:
        if dry_run:
            emit({"action": "delete", "resource": "saved view", "id": view_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete saved view {view_id}?")
        client = _get_client(endpoint, token, profile)
        emit(client.delete_saved_view(view_id), fmt)
    except Exception as e:
        _handle_error(e)


