"""Output formatting helpers (JSON, table, CSV, pager)."""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from typing import Any

import click
from rich.console import Console
from rich.table import Table

_OUTPUT_FILE: str | None = None
_NO_COLOR: bool = False
_NO_PAGER: bool = os.getenv("NO_PAGER", "").strip() not in ("", "0", "false")


def set_output_file(path: str | None) -> None:
    """Route all subsequent emit() output to *path* (-o/--output flag)."""
    global _OUTPUT_FILE  # noqa: PLW0603
    _OUTPUT_FILE = path


def set_no_color(enabled: bool) -> None:
    """Explicitly disable ANSI colors (--no-color / clig.dev color rules)."""
    global _NO_COLOR  # noqa: PLW0603
    _NO_COLOR = enabled


def set_no_pager(enabled: bool) -> None:
    """Disable pager for table output (--no-pager / NO_PAGER=1)."""
    global _NO_PAGER  # noqa: PLW0603
    _NO_PAGER = enabled


def _pager_enabled() -> bool:
    """Check if pager should be used: TTY stdout, not -o/--output, not --no-pager."""
    if _NO_PAGER:
        return False
    if _OUTPUT_FILE:
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    pager = os.getenv("PAGER")
    if pager:
        return True
    # Default: less (or cat fallback)
    return True


@contextmanager  # type: ignore[misc]
def _maybe_pager(fmt: str):  # type: ignore[no-untyped-def]
    """Wrap table output in a pager (less -FRX) when appropriate."""
    if fmt != "table" or not _pager_enabled():
        yield None
        return
    # Capture all _write calls into a buffer
    buf = io.StringIO()
    old_write = globals().get("_write")
    _orig_write = old_write

    def _capture_write(text: str) -> None:
        buf.write(text)

    # Monkey-patch _write for this context
    import signoz_cli.output as _mod  # noqa: PLW0603
    _orig = _mod._write  # noqa: PLW0603
    _mod._write = _capture_write  # type: ignore[assignment]
    try:
        yield None
    finally:
        _mod._write = _orig  # type: ignore[assignment]
        content = buf.getvalue()
        if content:
            _spawn_pager(content)


def _spawn_pager(text: str) -> None:
    """Pipe *text* through $PAGER or `less -FRX`."""
    pager_cmd = os.getenv("PAGER") or "less"
    args = pager_cmd.split()
    # Add -FRX if using less (quit-if-short, raw-chars, no-init)
    if args[0] in ("less", "/usr/bin/less"):
        args += ["-FRX"]
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        proc.communicate(input=text.encode("utf-8"))
    except (FileNotFoundError, OSError):
        # Pager not found or failed; fall back to plain output
        sys.stdout.write(text)


def _write(text: str) -> None:
    if _OUTPUT_FILE:
        with open(_OUTPUT_FILE, "w", encoding="utf-8") as fh:
            fh.write(text)
        click.echo(f"Output written to {_OUTPUT_FILE}", err=True)
    else:
        click.echo(text, nl=False)


def _to_csv(rows: list[dict[str, Any]]) -> str:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                keys.append(k)
                seen.add(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys, restval="")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in keys})
    return buf.getvalue()


def emit(data: Any, fmt: str, table_keys: list[str] | None = None) -> None:
    """Print *data* in the requested format ('json', 'table' or 'csv')."""
    if fmt == "json":
        _write(json.dumps(data, indent=2, default=str) + "\n")
    elif fmt == "csv":
        if isinstance(data, dict):
            for probe in ("data", "items"):
                if isinstance(data.get(probe), list):
                    data = data[probe]
                    break
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise click.UsageError(
                "CSV output requires tabular rows; this command returned a "
                "non-tabular structure. Use -f json instead."
            )
        if not data:
            raise click.UsageError("No rows to write.")
        _write(_to_csv([r for r in data]))
    else:
        with _maybe_pager(fmt):
            _print_table(data, table_keys)


def _print_table(data: Any, table_keys: list[str] | None = None) -> None:
    """Best-effort rich table rendering for arbitrary JSON structures."""
    console = Console(file=io.StringIO(), color_system=None if _NO_COLOR else None) if _OUTPUT_FILE else (
        Console(color_system=None) if _NO_COLOR else Console()
    )

    # Unwrap common SigNoz envelopes
    if isinstance(data, dict):
        for key in ("data", "result", "items"):
            if key in data and not isinstance(data[key], (str, int, float, bool)):
                data = data[key]
                break

    if isinstance(data, list):
        if not data:
            _write("(no results)\n")
            return
        if isinstance(data[0], dict):
            if table_keys:
                data = [
                    {k: row[k] for k in table_keys if k in row} for row in data
                ]
            _dict_list_table(console, data)
        else:
            _write("\n".join(str(item) for item in data) + "\n")
    elif isinstance(data, dict):
        table = Table(show_header=True, header_style="bold")
        table.add_column("Key")
        table.add_column("Value")
        for k, v in data.items():
            table.add_row(str(k), _trunc(v))
        console.print(table)
    else:
        _write(f"{data}\n")

    if _OUTPUT_FILE and isinstance(console.file, io.StringIO):
        with open(_OUTPUT_FILE, "w", encoding="utf-8") as fh:
            fh.write(console.file.getvalue())


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
