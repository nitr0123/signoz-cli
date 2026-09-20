"""Output formatting helpers (JSON, table, CSV, YAML, wide, custom-columns, jsonpath)."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import click
from rich.console import Console
from rich.table import Table

_OUTPUT_FILE: str | None = None
_NO_COLOR: bool = False
_NO_PAGER: bool = os.getenv("NO_PAGER", "").strip() not in ("", "0", "false")
_WIDE: bool = False
_NO_HEADERS: bool = False
_SORT_BY: str | None = None
_SORT_REVERSE: bool = False
_CUSTOM_COLUMNS: list[str] | None = None
_JSONPATH: str | None = None
_FIELD_SELECTOR: dict[str, str] = {}


def set_output_file(path: str | None) -> None:
    """Route all subsequent emit() output to *path* (--output flag)."""
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


def set_wide(enabled: bool) -> None:
    """Enable wide output mode (-o wide): extra columns, no truncation."""
    global _WIDE  # noqa: PLW0603
    _WIDE = enabled


def set_no_headers(enabled: bool) -> None:
    """Suppress table headers (--no-headers)."""
    global _NO_HEADERS  # noqa: PLW0603
    _NO_HEADERS = enabled


def set_sort_by(field: str | None, reverse: bool = False) -> None:
    """Set sort key and direction (--sort-by)."""
    global _SORT_BY, _SORT_REVERSE  # noqa: PLW0603
    _SORT_BY = field
    _SORT_REVERSE = reverse


def set_custom_columns(columns: list[str] | None) -> None:
    """Set custom column definitions (-o custom-columns=NAME:.field,...).

    Each entry can be:
      FIELD          — bare field name (column header = field name)
      NAME:FIELD     — column header NAME, display field FIELD
      NAME:.path     — column header NAME, nested field path
    """
    global _CUSTOM_COLUMNS  # noqa: PLW0603
    _CUSTOM_COLUMNS = columns


def set_jsonpath(expression: str | None) -> None:
    """Set JSONPath extraction expression (-o jsonpath='{...}')."""
    global _JSONPATH  # noqa: PLW0603
    _JSONPATH = expression


def set_field_selector(selector: str | None) -> None:
    """Parse and set field selector (--field-selector key=value,...)."""
    global _FIELD_SELECTOR  # noqa: PLW0603
    if not selector:
        _FIELD_SELECTOR = {}
        return
    result: dict[str, str] = {}
    for part in selector.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise click.UsageError(
                f"Invalid --field-selector {part!r}: expected key=value pairs "
                "separated by commas."
            )
        k, v = part.split("=", 1)
        result[k.strip()] = v.strip()
    _FIELD_SELECTOR = result


def _pager_enabled() -> bool:
    """Check if pager should be used: TTY stdout, not --output, not --no-pager."""
    if _NO_PAGER:
        return False
    if _OUTPUT_FILE:
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    return True


@contextmanager
def _maybe_pager(fmt: str) -> Iterator[None]:
    """Wrap table output in a pager (less -FRX) when appropriate."""
    if fmt not in ("table", "wide") or not _pager_enabled():
        yield None
        return
    buf = io.StringIO()
    import signoz_cli.output as _mod  # noqa: PLW0603
    _orig = _mod._write  # noqa: PLW0603
    _mod._write = buf.write  # type: ignore[assignment]
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
    try:
        # shlex (not str.split) so quoted args and paths with spaces work.
        args = shlex.split(pager_cmd, posix=True)
    except ValueError:
        sys.stdout.write(text)
        return
    if not args:
        sys.stdout.write(text)
        return
    if args[0] in ("less", "/usr/bin/less"):
        args += ["-FRX"]
    try:
        proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=sys.stdout, stderr=sys.stderr,
        )
        proc.communicate(input=text.encode("utf-8"))
    except (FileNotFoundError, OSError):
        sys.stdout.write(text)


def _write(text: str) -> None:
    if _OUTPUT_FILE:
        with open(_OUTPUT_FILE, "w", encoding="utf-8") as fh:
            fh.write(text)
        click.echo(f"Output written to {_OUTPUT_FILE}", err=True)
    else:
        click.echo(text, nl=False)


# Save reference to detect pager context (pager replaces _write with buf.write)
_real_write = _write


# ---------------------------------------------------------------------------
# JSONPath extraction
# ---------------------------------------------------------------------------

def _jsonpath_extract(data: Any, path: str) -> Any:
    """Minimal JSONPath extraction supporting:
      $                       — whole object
      $[0]                    — first element
      $[*]                    — all elements (identity for arrays)
      $.field                 — top-level field
      $[*].field              — map field over array
      $.items[*].field        — map field over nested array
      $.items[0].field        — index into nested array, then field

    NOTE (roadmap): this intentionally covers only the subset above. If
    full JSONPath is ever needed (filters like ``[?(@.x > 1)]``,
    recursive descent ``..``), replace this with the ``jsonpath-ng``
    dependency instead of extending the hand-rolled grammar.
    """
    if not path.startswith("$"):
        raise click.UsageError(f"JSONPath must start with $: {path!r}")

    rest = path[1:]
    if not rest:
        return data

    current = data

    # Handle top-level array index: $[0], $[*]
    m_arr = re.fullmatch(r"\[(\d+|\*)\](.*)", rest)
    if m_arr:
        idx_or_star = m_arr.group(1)
        remainder = m_arr.group(2)
        if idx_or_star == "*":
            current = data
            # remainder like ".name" or ".field.sub" applies to each element
            if remainder.startswith("."):
                sub = remainder[1:]
                if isinstance(current, list):
                    return [_jsonpath_extract(item, f"$.{sub}") for item in current]
                return None
            elif remainder:
                # e.g. $[0].field — but we matched *, shouldn't reach here with non-dot
                pass
            return current
        else:
            idx = int(idx_or_star)
            if isinstance(data, list) and idx < len(data):
                current = data[idx]
            else:
                return None
            rest = remainder

    # Process dot-separated segments on scalar objects
    segments = [s for s in rest.split(".") if s]
    for i, seg in enumerate(segments):
        if current is None:
            return None
        # Array wildcard: field[*], optionally followed by more segments
        # which are applied to each element (e.g. items[*].name).
        m_wild = re.fullmatch(r"(.+)\[\*\](.*)", seg)
        if m_wild:
            container_key = m_wild.group(1)
            remainder = m_wild.group(2)
            if container_key and isinstance(current, dict):
                arr = current.get(container_key)
            elif isinstance(current, list):
                arr = current
            else:
                return None
            if not isinstance(arr, list):
                return None
            tail = remainder
            if tail.startswith("."):
                tail = tail[1:]
            rest_segs = ([tail] if tail else []) + segments[i + 1 :]
            if not rest_segs:
                return arr
            sub = ".".join(rest_segs)
            return [_jsonpath_extract(item, f"$.{sub}") for item in arr]
        # Array index: field[N], optionally followed by more segments.
        m_idx = re.fullmatch(r"(.+)\[(\d+)\](.*)", seg)
        if m_idx:
            container_key, idx_s, remainder = m_idx.groups()
            if container_key and isinstance(current, dict):
                arr = current.get(container_key)
            elif isinstance(current, list):
                arr = current
            else:
                return None
            if not isinstance(arr, list):
                return None
            idx = int(idx_s)
            if idx >= len(arr):
                return None
            current = arr[idx]
            tail = remainder
            if tail.startswith("."):
                tail = tail[1:]
            rest_segs = ([tail] if tail else []) + segments[i + 1 :]
            if not rest_segs:
                return current
            return _jsonpath_extract(current, "$." + ".".join(rest_segs))
        # Plain field
        if isinstance(current, dict):
            current = current.get(seg)
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# Field selector filtering
# ---------------------------------------------------------------------------

def _apply_field_selector(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter rows by field selector (key=value pairs)."""
    if not _FIELD_SELECTOR:
        return rows
    result: list[dict[str, Any]] = []
    for row in rows:
        match = True
        for key, expected in _FIELD_SELECTOR.items():
            # First try exact key (handles "service.name" with literal dot)
            if key in row:
                val = row[key]
            else:
                # Fall back to dot-notation traversal
                val = row
                for part in key.split("."):
                    if isinstance(val, dict):
                        val = val.get(part)
                    else:
                        val = None
                        break
            if str(val) != expected:
                match = False
                break
        if match:
            result.append(row)
    return result


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort rows by _SORT_BY field if set."""
    if not _SORT_BY or not rows:
        return rows

    def _sort_key(row: dict[str, Any]) -> Any:
        # First try exact key
        if _SORT_BY in row:
            val = row[_SORT_BY]
        else:
            val = row
            for part in _SORT_BY.split("."):
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    return ""
        # Consistent type: always return string for comparison
        return str(val).lower()

    return sorted(rows, key=_sort_key, reverse=_SORT_REVERSE)


# ---------------------------------------------------------------------------
# YAML output (PyYAML)
# ASCII charts (sparklines for time series)
# ---------------------------------------------------------------------------

_SPARK_LEVELS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[float], width: int) -> str:
    """Render values as a block-element sparkline exactly *width* wide.

    Downsamples by bucket mean when there are more points than columns;
    repeats the last value when fewer. A flat series renders mid-level.
    """
    if width <= 0:
        return ""
    if not values:
        return " " * width
    n = len(values)
    if n >= width:
        sampled = []
        for i in range(width):
            lo_i = int(i * n / width)
            hi_i = max(lo_i + 1, int((i + 1) * n / width))
            chunk = values[lo_i:hi_i]
            sampled.append(sum(chunk) / len(chunk))
    else:
        sampled = list(values) + [values[-1]] * (width - n)
    lo, hi = min(sampled), max(sampled)
    if hi == lo:
        return "▄" * width
    span = hi - lo
    return "".join(
        _SPARK_LEVELS[min(7, int((v - lo) / span * 8))] for v in sampled
    )


def render_chart(rows: list[dict[str, Any]], width: int | None = None) -> str:
    """Render (series, timestamp, value) rows as labelled sparklines.

    One line per series: ``<series> <spark> last=<v> min=<v> max=<v>``.
    Series labels are sanitized for plain terminal output; rows missing
    numeric values are skipped. Raises ``ValueError`` when no plottable
    rows remain (caller maps to a usage error).
    """
    import shutil as _shutil

    grouped: dict[str, list[tuple[Any, float]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        series = row.get("series", "")
        raw_value = row.get("value")
        if raw_value is None:
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        grouped.setdefault(str(series), []).append((row.get("timestamp"), value))
    if not grouped:
        raise ValueError(
            "Nothing plottable: --chart needs rows with "
            "series/timestamp/numeric-value fields."
        )
    term_width = width or _shutil.get_terminal_size((80, 24)).columns
    label_width = min(32, max(len(s) for s in grouped) + 1)
    spark_width = max(10, term_width - label_width - 34)
    lines = []
    for series in sorted(grouped):
        points = sorted(grouped[series], key=lambda p: (p[0] is None, p[0]))
        values = [v for _, v in points]
        last, lo, hi = values[-1], min(values), max(values)
        lines.append(
            f"{_safe_plain(series)[:label_width - 1]:<{label_width}}"
            f"{_sparkline(values, spark_width)} "
            f"last={last:g} min={lo:g} max={hi:g}"
        )
    return "\n".join(lines) + "\n"


def emit_chart(rows: list[dict[str, Any]]) -> None:
    """Write chart rendering through the standard output routing."""
    _write(render_chart(rows))


# ---------------------------------------------------------------------------
# YAML output (PyYAML)
# ---------------------------------------------------------------------------

def _yaml_sanitize(data: Any) -> Any:
    """Reduce *data* to PyYAML-representable scalars/containers.

    API payloads are JSON-native, but defensive stringification of exotic
    values (datetimes, sets, custom objects) keeps emission total instead
    of raising ``RepresenterError`` on surprising server responses.
    Mapping keys outside the scalar set are stringified; everything else
    passes through untouched so types round-trip exactly.
    """
    if data is None or isinstance(data, (bool, int, float, str)):
        return data
    if isinstance(data, dict):
        return {
            k if k is None or isinstance(k, (bool, int, float, str)) else str(k):
            _yaml_sanitize(v)
            for k, v in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [_yaml_sanitize(v) for v in data]
    return str(data)


def _to_yaml(data: Any) -> str:
    """Render *data* as block-style YAML via PyYAML (insertion order kept)."""
    import yaml

    return yaml.safe_dump(
        _yaml_sanitize(data),
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=4096,
    )


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def _to_csv(rows: list[dict[str, Any]]) -> str:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                keys.append(k)
                seen.add(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys, restval="", lineterminator="\n")
    if not _NO_HEADERS:
        writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in keys})
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Unwrapping helpers
# ---------------------------------------------------------------------------

def _unwrap_to_rows(data: Any) -> list[dict[str, Any]]:
    """Extract a list of dicts from any SigNoz API response envelope."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return data
        return [{"value": item} for item in data]
    if isinstance(data, dict):
        for key in ("data", "result", "items"):
            val = data.get(key)
            if isinstance(val, list):
                if not val:
                    # Empty envelope (e.g. {"data": [], "total": 0}): no
                    # rows, so downstream renders "(no results)".
                    return []
                if isinstance(val[0], dict):
                    return val
            elif isinstance(val, dict):
                # Nested envelope holding an empty list, e.g.
                # {"data": {"items": [], "total": 0}} -> no rows either.
                if _unwrap_to_rows(val) == []:
                    return []
        # Single dict → key-value table
        return [data]
        # Single dict → key-value table
        return [data]
    return [{"value": data}]


# ---------------------------------------------------------------------------
# Main emit function
# ---------------------------------------------------------------------------

def emit(
    data: Any,
    fmt: str,
    table_keys: list[str] | None = None,
    wide_keys: list[str] | None = None,
) -> None:
    """Print *data* in the requested format.

    Supported formats: json, yaml, csv, table, wide.
    Post-processing (--sort-by, --field-selector, -o custom-columns, -o jsonpath)
    is applied to all formats where meaningful.
    """
    # Unwrap envelopes first for post-processing
    rows = _unwrap_to_rows(data)

    # Apply field selector
    if _FIELD_SELECTOR:
        rows = _apply_field_selector(rows)

    # Apply sort
    if _SORT_BY:
        rows = _sort_rows(rows)

    # Apply JSONPath extraction
    if _JSONPATH:
        extracted = _jsonpath_extract(rows, _JSONPATH)
        _write(json.dumps(extracted, indent=2, default=str) + "\n")
        return

    # Apply custom columns filter
    if _CUSTOM_COLUMNS and rows and isinstance(rows[0], dict):
        parsed: list[tuple[str, str]] = []
        for col in _CUSTOM_COLUMNS:
            if ":" in col:
                header, field = col.split(":", 1)
                parsed.append((header.strip(), field.strip()))
            else:
                parsed.append((col.strip(), col.strip()))
        new_rows: list[dict[str, Any]] = []
        for row in rows:
            r: dict[str, Any] = {}
            for header, field in parsed:
                # First try exact key match (handles "service.name" with literal dot)
                if field in row:
                    r[header] = row[field]
                else:
                    # Fall back to dot-notation traversal
                    val: Any = row
                    for part in field.split("."):
                        if isinstance(val, dict):
                            val = val.get(part)
                        else:
                            val = None
                            break
                    r[header] = val if val is not None else ""
            new_rows.append(r)
        rows = new_rows

    if fmt == "json":
        # For JSON, output the original structure if no post-processing
        if not _FIELD_SELECTOR and not _SORT_BY and not _CUSTOM_COLUMNS:
            _write(json.dumps(data, indent=2, default=str) + "\n")
        else:
            _write(json.dumps(rows, indent=2, default=str) + "\n")
    elif fmt == "yaml":
        # YAML needs unwrapped rows for clean output
        _write(_to_yaml(rows))
    elif fmt == "csv":
        # CSV needs special handling: reject non-tabular data
        csv_data = data
        if isinstance(csv_data, dict):
            for probe in ("data", "items"):
                if isinstance(csv_data.get(probe), list):
                    csv_data = csv_data[probe]
                    break
        if not isinstance(csv_data, list) or not all(isinstance(r, dict) for r in csv_data):
            raise click.UsageError(
                "CSV output requires tabular rows; this command returned a "
                "non-tabular structure. Use -f json instead."
            )
        if not csv_data:
            raise click.UsageError("No rows to write.")
        # Use processed rows (custom columns, sort, field selector already applied)
        if _CUSTOM_COLUMNS or _SORT_BY or _FIELD_SELECTOR:
            if not rows:
                raise click.UsageError("No rows to write.")
            _write(_to_csv(rows))
        else:
            _write(_to_csv(csv_data))
    elif fmt == "wide":
        with _maybe_pager(fmt):
            effective_keys = None if _CUSTOM_COLUMNS else table_keys
            _print_table(rows, effective_keys, wide=True, wide_keys=wide_keys)
    else:
        with _maybe_pager(fmt):
            # When custom columns are active, rows have renamed keys;
            # don't pass table_keys which references old keys.
            effective_keys = None if _CUSTOM_COLUMNS else table_keys
            _print_table(rows, effective_keys)


def _print_table(
    data: Any,
    table_keys: list[str] | None = None,
    wide: bool = False,
    wide_keys: list[str] | None = None,
) -> None:
    """Best-effort rich table rendering for arbitrary JSON structures."""
    # When pager is active, _write is redirected to buffer; use StringIO for Rich too
    import signoz_cli.output as _mod
    pager_active = _mod._write is not _mod._real_write
    if pager_active:
        console = Console(file=io.StringIO(), color_system=None if _NO_COLOR else None)
    elif _OUTPUT_FILE:
        console = Console(file=io.StringIO(), color_system=None if _NO_COLOR else None)
    else:
        console = Console(color_system=None) if _NO_COLOR else Console()

    if isinstance(data, list):
        if not data:
            # Empty-result contract: keep stdout machine-clean (JSON/YAML
            # branches already emit []); the human notice goes to stderr.
            if _OUTPUT_FILE:
                _write("")
            click.echo("(no results)", err=True)
            return
        if isinstance(data[0], dict):
            if table_keys:
                # Prune to known columns, but never render a row empty: if
                # none of the keys match (e.g. an unexpected envelope shape),
                # fall back to the full row instead of printing nothing.
                data = [
                    {k: row[k] for k in table_keys if k in row} or dict(row)
                    for row in data
                ]
            _dict_list_table(console, data, wide=wide, wide_keys=wide_keys)
        else:
            _write("\n".join(str(item) for item in data) + "\n")
    elif isinstance(data, dict):
        table = Table(show_header=not _NO_HEADERS, header_style="bold")
        table.add_column("Key")
        table.add_column("Value")
        for k, v in data.items():
            table.add_row(_safe_text(k), _trunc(v, max_len=9999 if wide else 120))
        console.print(table)
    else:
        _write(f"{data}\n")

    if pager_active and isinstance(console.file, io.StringIO):
        _write(console.file.getvalue())

    if _OUTPUT_FILE and isinstance(console.file, io.StringIO):
        with open(_OUTPUT_FILE, "w", encoding="utf-8") as fh:
            fh.write(console.file.getvalue())


def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten one level of nested dicts so table columns stay readable."""
    flat: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, dict) and v and all(
            isinstance(x, (str, int, float, bool)) for x in v.values()
        ):
            for k2, v2 in v.items():
                flat[f"{k}.{k2}"] = v2
        else:
            flat[k] = v
    return flat


def _dict_list_table(
    console: Console,
    rows: list[dict[str, Any]],
    wide: bool = False,
    wide_keys: list[str] | None = None,
) -> None:
    """Render a list of dicts as a table."""
    rows = [_flatten(r) if isinstance(r, dict) else {"value": r} for r in rows]

    # For wide mode, override console to wide width so Rich doesn't clip
    if wide:
        console = Console(
            file=console.file,
            width=9999,
            color_system=None if _NO_COLOR else None,
        )

    # Determine columns
    if wide and wide_keys:
        keys = [k for k in wide_keys if any(k in row for row in rows)]
    elif wide:
        # Wide mode: show ALL columns (no truncation)
        keys = []
        seen: set[str] = set()
        for row in rows:
            for k in row:
                if k not in seen:
                    keys.append(k)
                    seen.add(k)
    else:
        keys = []
        seen = set()
        for row in rows:
            for k in row:
                if k not in seen:
                    keys.append(k)
                    seen.add(k)

    table = Table(show_header=not _NO_HEADERS, header_style="bold")
    for k in keys:
        table.add_column(_safe_text(k), no_wrap=wide)
    for row in rows:
        max_len = 9999 if wide else 120
        table.add_row(*(str(_trunc(row.get(k, ""), max_len=max_len)) for k in keys))
    console.print(table)


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _safe_text(v: Any) -> str:
    """Neutralize terminal-injection payloads in human-readable output.

    Telemetry values/keys are untrusted: strip C0 controls (kills every
    ESC-based sequence: OSC title-set, OSC-8 hyperlinks, CSI) and escape
    Rich markup brackets (kills ``[link=...]`` phishing via styling).
    Display is preserved (``[prod]`` still shows as ``[prod]``).

    Applies to TABLE rendering only -- JSON/YAML/CSV stay byte-faithful
    for pipelines by design.
    """
    s = str(v).replace("\x1b", "").replace("\x07", "")
    s = _CONTROL_CHARS_RE.sub("", s)
    return s.replace("[", "\\[")


def _safe_plain(v: Any) -> str:
    """Strip terminal control bytes for plain (non-Rich) output paths.

    ``click.echo`` writes text verbatim: Rich ``[markup]`` is inert there,
    so brackets are preserved for readability (log bodies are full of
    them) while ESC/BEL/C0 controls -- the bytes that actually drive
    terminal escape sequences -- are removed. Use for stderr notices,
    warnings, error text, and diffs carrying server-controlled content.
    """
    s = str(v).replace("\x1b", "").replace("\x07", "")
    return _CONTROL_CHARS_RE.sub("", s)


def _trunc(v: Any, max_len: int = 120) -> str:
    s = _safe_text(v)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s
