"""dashboards commands."""

from __future__ import annotations

from typing import Any

import click

from signoz_cli.commands.common import (
    _MAX_PAGES,
    _common_options,
    _confirm,
    _dashboard_summary,
    _get_client,
    _handle_error,
    _read_json,
    cli,
)
from signoz_cli.inventory import _diff_objects
from signoz_cli.output import emit


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
            # walk remaining pages (bounded to avoid loops on bad servers)
            for _ in range(_MAX_PAGES):
                offset += limit
                raw_page = client.list_dashboards_v2(limit=limit, offset=offset, query=query)
                page_payload = raw_page.get("data", {}) if isinstance(raw_page, dict) else {}
                page_items = (page_payload.get("dashboards") or []) if isinstance(page_payload, dict) else []
                items.extend(page_items)
                page_total = page_payload.get("total") if isinstance(page_payload, dict) else None
                if not page_items or (
                    isinstance(page_payload, dict)
                    and page_total is not None
                    and offset + len(page_items) >= int(page_total)
                ):
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
@click.argument("json_file", type=click.Path())
def dashboards_create(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, json_file: str
) -> None:
    """Create a dashboard from a JSON file (v2 spec schema)."""
    try:
        dashboard = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        data = client.create_dashboard_v2(dashboard)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)


@dashboards.command("update")
@_common_options
@click.argument("dashboard_id")
@click.argument("json_file", type=click.Path())
def dashboards_update(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, dashboard_id: str, json_file: str
) -> None:
    """Replace a dashboard from a JSON file (full PUT, v2 API)."""
    try:
        dashboard = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        data = client.update_dashboard_v2(dashboard_id, dashboard)
        emit(data or {"status": "updated"}, fmt)
    except Exception as e:
        _handle_error(e)


@dashboards.command("patch")
@_common_options
@click.argument("dashboard_id")
@click.argument("json_file", type=click.Path())
def dashboards_patch(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, dashboard_id: str, json_file: str
) -> None:
    """Apply partial updates to a dashboard (JSON-Patch array, v2 API).

    Example patch file:
      [{"op": "add", "path": "/tags/-", "value": {"key": "env", "value": "prod"}}]
    """
    try:
        patches = _read_json(json_file)
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
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def dashboards_delete(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, dashboard_id: str, yes: bool, dry_run: bool
) -> None:
    """Delete a dashboard (v2 API)."""
    try:
        if dry_run:
            emit({"action": "delete", "resource": "dashboard", "id": dashboard_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete dashboard {dashboard_id}?")
        client = _get_client(endpoint, token, profile)
        data = client.delete_dashboard_v2(dashboard_id)
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)




@dashboards.command("diff")
@_common_options
@click.argument("dashboard_id")
@click.argument("json_file", type=click.Path())
def dashboards_diff(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, dashboard_id: str, json_file: str
) -> None:
    """Show differences between the server dashboard and a local file.

    Server-managed keys (id, timestamps, org) are ignored. Prints
    "(no changes)" when identical. Pair with `dashboards update`.
    """
    try:
        wanted = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        current = client.get_dashboard_v2(dashboard_id)
        if isinstance(current, dict):
            current = current.get("data", current)
        delta = _diff_objects(
            current, wanted,
            f"server:{dashboard_id}", f"file:{json_file}",
        )
        if delta:
            from signoz_cli.output import _safe_plain

            click.echo(_safe_plain(delta))
        else:
            click.echo("(no changes)")
    except Exception as e:
        _handle_error(e)


@dashboards.command("lint")
@click.argument("files", nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=False))
@click.option("-f", "--format", "fmt", type=click.Choice(["json", "table"]),
              default="table", show_default=True, help="Output format.")
@click.option("--strict", is_flag=True,
              help="Fail on warnings as well as errors.")
def dashboards_lint(files: tuple[str, ...], fmt: str, strict: bool) -> None:
    """Validate dashboard files offline (CI-friendly, exit 1 on errors).

    Checks the live-verified v2 requirements (schemaVersion v6, name,
    spec.variables/layouts) plus portability warnings. Needs no server.

    \b
    Examples:
      signoz dashboards lint ./dashboards/*.json
      signoz dashboards lint bundle/dashboards/ --strict
    """
    from signoz_cli.commands.common import _lint_files
    from signoz_cli.lint import lint_dashboard

    _lint_files(files, fmt, strict, lint_dashboard)


@dashboards.command("diff-remote")
@_common_options
@click.option("--name", required=True, help="Item name on both instances.")
@click.option("--from-profile", required=True, help="Source profile.")
@click.option("--to-profile", required=True, help="Target profile.")
def dashboards_diff_remote(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, name: str, from_profile: str, to_profile: str,
) -> None:
    """Diff a dashboard between two instances by name (no files).

    Server-managed keys are ignored. Prints "(no changes)" when
    identical. The ambient --profile/--endpoint is ignored: both sides
    are explicit so the direction is never ambiguous.
    """
    from signoz_cli.inventory import _fetch_by_name
    from signoz_cli.output import _safe_plain

    if from_profile == to_profile:
        raise click.UsageError("--from-profile and --to-profile differ.")
    try:
        old = _fetch_by_name(
            _get_client(None, None, from_profile), "dashboards",
            name, from_profile)
        new = _fetch_by_name(
            _get_client(None, None, to_profile), "dashboards",
            name, to_profile)
        delta = _diff_objects(
            old, new,
            f"{from_profile}:{name}", f"{to_profile}:{name}",
        )
        if delta:
            click.echo(_safe_plain(delta))
        else:
            click.echo("(no changes)")
    except Exception as e:
        _handle_error(e)
