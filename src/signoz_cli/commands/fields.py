"""Field configuration commands: promoted paths and explorer quick filters."""

from __future__ import annotations

from typing import Any

import click

from signoz_cli.commands._crud import data_of, items_of
from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    _read_json,
    cli,
)
from signoz_cli.output import emit

# The only promotion targets the server supports (signal -> JSON column context).
_DEFAULT_CONTEXT = {"logs": "body", "traces": "attribute"}


@cli.group("promoted-paths")
def promoted_paths() -> None:
    """Promote JSON fields to their own columns so queries on them are fast
    (SigNoz 0.147+).

    Logs promote paths inside the JSON body; traces promote span
    attributes. Optionally add a skip index to speed up filtering further.
    """


def _path_row(p: dict[str, Any]) -> dict[str, Any]:
    indexes = [f"{i.get('type', '')} ({i.get('fieldDataType', '')})"
               for i in (p.get("indexes") or []) if isinstance(i, dict)]
    return {"signal": p.get("signal", ""), "context": p.get("context", ""),
            "path": p.get("path", ""), "promoted": p.get("promote", False),
            "indexes": "; ".join(indexes)}


@promoted_paths.command("list")
@_common_options
@click.option("--signal", type=click.Choice(["logs", "traces"]), default=None)
@click.option("--context", type=click.Choice(["body", "attribute"]), default=None)
@click.option("--promoted/--not-promoted", default=None,
              help="Only promoted (or only not-promoted) paths.")
@click.option("--indexed/--not-indexed", default=None,
              help="Only paths with (or without) an index.")
def promoted_paths_list(endpoint: str | None, token: str | None, fmt: str,
                        profile: str | None, signal: str | None, context: str | None,
                        promoted: bool | None, indexed: bool | None) -> None:
    """List promoted and indexed JSON paths."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_promoted_paths(signal=signal, context=context,
                                         promoted=promoted, indexes=indexed)
        if fmt == "json":
            emit(raw, fmt)
            return
        emit({"data": [_path_row(p) for p in items_of(raw) if isinstance(p, dict)]}, fmt)
    except Exception as e:
        _handle_error(e)


@promoted_paths.command("add")
@_common_options
@click.argument("paths", nargs=-1)
@click.option("--signal", type=click.Choice(["logs", "traces"]), default="logs",
              show_default=True)
@click.option("--context", type=click.Choice(["body", "attribute"]), default=None,
              help="Default: body for logs, attribute for traces.")
@click.option("--index", "index_types", multiple=True,
              help="Skip index to add, e.g. 'ngrambf_v1(4, 1024, 2, 0)' or "
                   "'bloom_filter(0.01)' (repeatable).")
@click.option("--index-data-type", default="string", show_default=True,
              type=click.Choice(["string", "int64", "float64", "bool"]),
              help="Value type the index is built for.")
@click.option("--granularity", type=int, default=1, show_default=True,
              help="Index granularity.")
@click.option("--index-only", is_flag=True,
              help="Add the index without promoting the path to a column.")
@click.option("--file", "json_file", type=click.Path(), default=None,
              help="Read a JSON list of path objects instead ('-' = stdin).")
def promoted_paths_add(endpoint: str | None, token: str | None, fmt: str,
                       profile: str | None, paths: tuple[str, ...], signal: str,
                       context: str | None, index_types: tuple[str, ...],
                       index_data_type: str, granularity: int, index_only: bool,
                       json_file: str | None) -> None:
    """Promote (and optionally index) one or more JSON paths.

    \b
    Examples:
      signoz promoted-paths add user.id order.status
      signoz promoted-paths add http.route --signal traces
      signoz promoted-paths add user.name --index 'ngrambf_v1(4, 1024, 2, 0)'
    """
    try:
        if json_file:
            if paths:
                raise click.UsageError("Pass paths as arguments or --file, not both.")
            body = _read_json(json_file)
            if not isinstance(body, list):
                raise click.UsageError("--file must hold a JSON list of path objects.")
        else:
            if not paths:
                raise click.UsageError("Give at least one path (or --file).")
            if index_only and not index_types:
                raise click.UsageError("--index-only needs at least one --index.")
            ctx = context or _DEFAULT_CONTEXT[signal]
            indexes = [{"type": t, "fieldDataType": index_data_type,
                        "granularity": granularity} for t in index_types]
            body = []
            for path in paths:
                item: dict[str, Any] = {"signal": signal, "context": ctx, "path": path,
                                        "promote": not index_only}
                if indexes:
                    item["indexes"] = indexes
                body.append(item)
        client = _get_client(endpoint, token, profile)
        client.promote_paths(body)
        emit({"data": [_path_row(p) for p in body if isinstance(p, dict)]}, fmt)
    except Exception as e:
        _handle_error(e)


@promoted_paths.command("index-materialized")
@_common_options
@click.option("--signal", type=click.Choice(["logs", "traces"]), required=True)
@click.option("--dry-run", is_flag=True, help="Show what would be indexed; change nothing.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def promoted_paths_index_materialized(endpoint: str | None, token: str | None, fmt: str,
                                      profile: str | None, signal: str, dry_run: bool,
                                      yes: bool) -> None:
    """Add indexes for every already-materialized attribute of a signal."""
    try:
        if not dry_run and not yes:
            _confirm(f"Index all materialized {signal} attributes?")
        client = _get_client(endpoint, token, profile)
        raw = client.index_materialized_paths(signal, dry_run=dry_run)
        if fmt == "json":
            emit(raw, fmt)
            return
        data = data_of(raw)
        rows = []
        for outcome in ("indexed", "skipped"):
            for p in (data.get(outcome) if isinstance(data, dict) else None) or []:
                if isinstance(p, dict):
                    rows.append({"result": ("would index" if dry_run and outcome == "indexed"
                                            else outcome), **_path_row(p)})
        emit({"data": rows}, fmt)
    except Exception as e:
        _handle_error(e)


_QF_SOURCES = ["traces", "logs", "api_monitoring", "exceptions", "meter", "ai_observability"]


@cli.group("quick-filters")
def quick_filters() -> None:
    """Configure the quick-filter sidebar of the explorers (SigNoz 0.141+)."""


def _filter_row(source: str, f: dict[str, Any]) -> dict[str, Any]:
    return {"source": source, "name": f.get("name", ""),
            "context": f.get("fieldContext", ""), "type": f.get("fieldDataType", ""),
            "signal": f.get("signal", "")}


@quick_filters.command("list")
@_common_options
@click.argument("source", required=False, type=click.Choice(_QF_SOURCES))
def quick_filters_list(endpoint: str | None, token: str | None, fmt: str,
                       profile: str | None, source: str | None) -> None:
    """List quick filters, for every explorer or one source."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_quick_filters(source) if source else client.list_quick_filters()
        if fmt == "json":
            emit(raw, fmt)
            return
        groups = [data_of(raw)] if source else items_of(raw)
        rows = [_filter_row(g.get("source", ""), f)
                for g in groups if isinstance(g, dict)
                for f in (g.get("filters") or []) if isinstance(f, dict)]
        emit({"data": rows}, fmt)
    except Exception as e:
        _handle_error(e)


@quick_filters.command("set")
@_common_options
@click.argument("source", type=click.Choice(_QF_SOURCES))
@click.argument("json_file", type=click.Path())
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def quick_filters_set(endpoint: str | None, token: str | None, fmt: str,
                      profile: str | None, source: str, json_file: str, yes: bool) -> None:
    """Replace a source's quick filters from a JSON file ('-' = stdin).

    The file holds a list of field keys, or {"filters": [...]}, e.g.
    [{"name": "service.name", "fieldContext": "resource",
      "fieldDataType": "string"}]. Export the current list first with
    `quick-filters list SOURCE --format json`.
    """
    try:
        body = _read_json(json_file)
        # Accept a bare list, {"filters": [...]}, or `list SOURCE --format json`.
        if isinstance(body, dict) and isinstance(body.get("data"), dict):
            body = body["data"]
        filters = body.get("filters") if isinstance(body, dict) else body
        if not isinstance(filters, list):
            raise click.UsageError("Expected a JSON list of filters or {\"filters\": [...]}.")
        if not yes:
            _confirm(f"Replace the {source} quick filters with {len(filters)} entries?")
        client = _get_client(endpoint, token, profile)
        client.set_quick_filters(source, filters)
        emit({"data": [_filter_row(source, f) for f in filters if isinstance(f, dict)]}, fmt)
    except Exception as e:
        _handle_error(e)
