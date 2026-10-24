"""Shared raw-data export (POST /api/v1/export_raw_data) for logs and traces."""

from __future__ import annotations

import os
import sys
from typing import Any

import click

# Server-side caps (SigNoz pkg/modules/rawdataexport): default 10k rows, max 50k.
EXPORT_MAX_ROWS = 50_000


def export_options(f):
    f = click.option("--limit", type=int, default=10_000, show_default=True,
                     help=f"Max rows (server maximum {EXPORT_MAX_ROWS}).")(f)
    f = click.option("--as", "as_fmt", type=click.Choice(["csv", "jsonl"]), default=None,
                     help="File format. Default: from the --out extension, else csv.")(f)
    f = click.option("--out", "out_path", required=True,
                     help="File to write ('-' = stdout).")(f)
    return f


def run_export(client: Any, payload: dict[str, Any], out_path: str,
               as_fmt: str | None, limit: int) -> None:
    """Strip the display-only select list, cap the limit, stream to *out_path*."""
    if not 1 <= limit <= EXPORT_MAX_ROWS:
        raise click.UsageError(f"--limit must be between 1 and {EXPORT_MAX_ROWS}.")
    fmt = as_fmt or ("jsonl" if out_path.endswith((".jsonl", ".ndjson")) else "csv")
    for query in payload.get("compositeQuery", {}).get("queries", []):
        spec = query.get("spec", {})
        # An empty select list exports every column, not just the table view's.
        spec.pop("selectFields", None)
        spec["limit"] = limit
        spec.pop("offset", None)
    if out_path == "-":
        client.export_raw_data(payload, sys.stdout.buffer, fmt)
        sys.stdout.buffer.flush()
        return
    tmp_path = f"{out_path}.partial"
    try:
        with open(tmp_path, "wb") as fh:
            written = client.export_raw_data(payload, fh, fmt)
        os.replace(tmp_path, out_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    click.echo(f"Wrote {written} bytes ({fmt}) to {out_path}", err=True)
