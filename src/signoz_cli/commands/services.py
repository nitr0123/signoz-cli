"""services commands."""

from __future__ import annotations

import json

import click

from signoz_cli.commands.common import (
    _common_options,
    _get_client,
    _handle_error,
    _profile_time_range,
    cli,
)
from signoz_cli.output import emit
from signoz_cli.timeutil import resolve_timestamps


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
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", unit="ns", profile_time_range=_profile_time_range(profile))
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
        s, e = resolve_timestamps(start, end, time_range, default_range="6h", unit="ns", profile_time_range=_profile_time_range(profile))
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


