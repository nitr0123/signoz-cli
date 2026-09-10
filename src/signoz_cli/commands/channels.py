"""notification channels commands."""

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
@click.argument("json_file", type=click.Path())
def channels_create(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, json_file: str) -> None:
    """Create a channel from a JSON file."""
    try:
        channel = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        emit(client.create_channel(channel), fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("update")
@_common_options
@click.argument("channel_id")
@click.argument("json_file", type=click.Path())
def channels_update(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, channel_id: str, json_file: str) -> None:
    """Replace a channel from a JSON file (full PUT)."""
    try:
        channel = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        data = client.update_channel(channel_id, channel)
        emit(data or {"status": "updated"}, fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("delete")
@_common_options
@click.argument("channel_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def channels_delete(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, channel_id: str, yes: bool, dry_run: bool) -> None:
    """Delete a channel."""
    try:
        if dry_run:
            emit({"action": "delete", "resource": "channel", "id": channel_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete channel {channel_id}?")
        client = _get_client(endpoint, token, profile)
        emit(client.delete_channel(channel_id), fmt)
    except Exception as e:
        _handle_error(e)


