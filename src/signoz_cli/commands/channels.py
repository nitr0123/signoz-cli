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
@click.option("--api", "api_version", type=click.Choice(["v1", "v2"]),
              default="v1", show_default=True,
              help="Channel API version. v2 (SigNoz 0.141+) lists identity "
                   "summaries with search, kind filter and pagination.")
@click.option("--query", default=None, help="v2 only: case-insensitive name search.")
@click.option("--kind", default=None,
              help="v2 only: kind filter (slack, email, webhook, pagerduty, "
                   "opsgenie, msteams, googlechat, jira, jsmops, incidentio, "
                   "telegram).")
@click.option("--limit", type=int, default=None, help="v2 only: page size.")
@click.option("--offset", type=int, default=None, help="v2 only: page offset.")
def channels_list(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, api_version: str,
                  query: str | None, kind: str | None,
                  limit: int | None, offset: int | None) -> None:
    """List notification channels."""
    try:
        client = _get_client(endpoint, token, profile)
        if api_version == "v2":
            emit(client.list_channels_v2(
                limit=limit, offset=offset, query=query, kind=kind), fmt)
        else:
            emit(client.list_channels(), fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("get")
@_common_options
@click.argument("channel_id")
@click.option("--api", "api_version", type=click.Choice(["v1", "v2"]),
              default="v1", show_default=True,
              help="Channel API version (v2 needs SigNoz 0.141+).")
def channels_get(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, channel_id: str,
                 api_version: str) -> None:
    """Get a channel by id."""
    try:
        client = _get_client(endpoint, token, profile)
        if api_version == "v2":
            emit(client.get_channel_v2(channel_id), fmt)
        else:
            emit(client.get_channel(channel_id), fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("create")
@_common_options
@click.argument("json_file", type=click.Path())
@click.option("--api", "api_version", type=click.Choice(["v1", "v2"]),
              default="v1", show_default=True,
              help="Channel API version. v2 documents use "
                   "{name, displayName, config} (SigNoz 0.141+).")
def channels_create(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, json_file: str,
                    api_version: str) -> None:
    """Create a channel from a JSON file."""
    try:
        channel = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        if api_version == "v2":
            emit(client.create_channel_v2(channel), fmt)
        else:
            emit(client.create_channel(channel), fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("update")
@_common_options
@click.argument("channel_id")
@click.argument("json_file", type=click.Path())
@click.option("--api", "api_version", type=click.Choice(["v1", "v2"]),
              default="v1", show_default=True,
              help="Channel API version (v2 needs SigNoz 0.141+).")
def channels_update(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, channel_id: str, json_file: str,
                    api_version: str) -> None:
    """Replace a channel from a JSON file (full PUT)."""
    try:
        channel = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        if api_version == "v2":
            data = client.update_channel_v2(channel_id, channel)
        else:
            data = client.update_channel(channel_id, channel)
        emit(data or {"status": "updated"}, fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("delete")
@_common_options
@click.argument("channel_id")
@click.option("--api", "api_version", type=click.Choice(["v1", "v2"]),
              default="v1", show_default=True,
              help="Channel API version (v2 needs SigNoz 0.141+).")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def channels_delete(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, channel_id: str, api_version: str,
                    yes: bool, dry_run: bool) -> None:
    """Delete a channel."""
    try:
        if dry_run:
            emit({"action": "delete", "resource": "channel", "id": channel_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete channel {channel_id}?")
        client = _get_client(endpoint, token, profile)
        if api_version == "v2":
            emit(client.delete_channel_v2(channel_id), fmt)
        else:
            emit(client.delete_channel(channel_id), fmt)
    except Exception as e:
        _handle_error(e)




@channels.command("test")
@_common_options
@click.argument("name")
def channels_test(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, name: str) -> None:
    """Fire a test notification through a channel (by name).

    The server resolves the receiver by name and reports the delivery
    outcome (unknown names fail cleanly). Needs a key allowed to use
    notification channels.
    """
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.test_channel(name), fmt)
    except Exception as e:
        _handle_error(e)


@channels.command("repair")
@_common_options
@click.argument("channel_id")
@click.option("--apply", is_flag=True,
              help="Actually fix the channel (default: report only).")
def channels_repair(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, channel_id: str,
                    apply: bool) -> None:
    """Diagnose (and optionally fix) a channel the v2 API cannot read.

    Needs SigNoz 0.141+. Without --apply the server only reports what it
    would do (split multi-config channels, delete unmodellable kinds,
    rewrite empty stored types).
    """
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.repair_channel_v2(channel_id, apply=apply), fmt)
    except Exception as e:
        _handle_error(e)
