"""Output formatting helpers (JSON and table)."""

from __future__ import annotations

import json
from typing import Any

import click
from rich.console import Console
from rich.table import Table


def emit(data: Any, fmt: str, table_keys: list[str] | None = None) -> None:
    """Print *data* in the requested format ('json' or 'table').

    ``table_keys`` optionally restricts which columns are shown in table mode
    (rows keep all fields under --format json).
    """
    if fmt == "json":
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        _print_table(data, table_keys)


def _print_table(data: Any, table_keys: list[str] | None = None) -> None:
    """Best-effort rich table rendering for arbitrary JSON structures."""
    console = Console()

    # Unwrap common SigNoz envelopes
    if isinstance(data, dict):
        for key in ("data", "result", "items"):
            if key in data and not isinstance(data[key], (str, int, float, bool)):
                data = data[key]
                break

    if isinstance(data, list):
        if not data:
            click.echo("(no results)")
            return
        if isinstance(data[0], dict):
            if table_keys:
                data = [
                    {k: row[k] for k in table_keys if k in row} for row in data
                ]
            _dict_list_table(console, data)
        else:
            for item in data:
                click.echo(item)
    elif isinstance(data, dict):
        table = Table(show_header=True, header_style="bold")
        table.add_column("Key")
        table.add_column("Value")
        for k, v in data.items():
            table.add_row(str(k), _trunc(v))
        console.print(table)
    else:
        click.echo(data)


def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten one level of nested dicts so table columns stay readable."""
    flat: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, dict) and v and all(isinstance(x, (str, int, float, bool)) for x in v.values()):
            for k2, v2 in v.items():
                flat[f"{k}.{k2}"] = v2
        else:
            flat[k] = v
    return flat


def _dict_list_table(console: Console, rows: list[dict[str, Any]]) -> None:
    """Render a list of dicts as a table."""
    rows = [_flatten(r) if isinstance(r, dict) else {"value": r} for r in rows]

    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                keys.append(k)
                seen.add(k)

    table = Table(show_header=True, header_style="bold")
    for k in keys:
        table.add_column(k)
    for row in rows:
        table.add_row(*(str(_trunc(row.get(k, ""))) for k in keys))
    console.print(table)


def _trunc(v: Any, max_len: int = 120) -> str:
    s = str(v)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s
