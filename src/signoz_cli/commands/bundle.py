"""export / import bundle commands (migrate between instances)."""

from __future__ import annotations

import difflib
import json
import re
import time
from typing import Any

import click

from signoz_cli import __version__
from signoz_cli.commands.common import (
    _MAX_PAGES,
    _common_options,
    _get_client,
    _handle_error,
    cli,
)
from signoz_cli.output import emit

VOLATILE_KEYS = (
    "id",
    "createdAt",
    "updatedAt",
    "createdBy",
    "updatedBy",
    "orgId",
)


def _safe_name(name: str) -> str:
    name = (name or "unnamed").strip() or "unnamed"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]


def _strip_volatile(obj: Any) -> Any:
    """Remove server-managed keys so exports are portable across instances."""
    if isinstance(obj, dict):
        return {
            k: _strip_volatile(v)
            for k, v in obj.items()
            if k not in VOLATILE_KEYS
        }
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _item_name(kind: str, item: dict[str, Any]) -> str:
    if kind == "alerts":
        return str(item.get("alert") or item.get("name") or "unnamed")
    return str(item.get("name") or "unnamed")


def _fetch_full(client, kind: str, item: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch the full object for export (list endpoints return summaries).

    Returns None when the item cannot be fetched; the caller warns and
    continues with the remaining items.
    """
    item_id = item.get("id")
    if not item_id:
        return item
    try:
        if kind == "dashboards":
            data = client.get_dashboard_v2(item_id)
        elif kind == "alerts":
            data = client.get_alert(item_id)
        elif kind == "channels":
            data = client.get_channel(item_id)
        elif kind == "views":
            data = client.get_saved_view(item_id)
        else:  # pragma: no cover - guarded by _list_items
            return item
    except Exception as ex:
        from signoz_cli.output import _safe_plain

        click.echo(
            f"Warning: skipping {kind} '{_safe_plain(_item_name(kind, item))}' "
            f"({item_id}): {_safe_plain(ex)}",
            err=True,
        )
        return None
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else item


def _list_items(client, kind: str) -> list[dict[str, Any]]:
    """Fetch every item of a kind (paginating dashboards)."""
    if kind == "dashboards":
        items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            raw = client.list_dashboards_v2(limit=100, offset=offset)
            payload = raw.get("data", {}) if isinstance(raw, dict) else {}
            page = payload.get("dashboards") or []
            items.extend(page)
            if len(page) < 100:
                break
            offset += 100
        return [i for i in items if isinstance(i, dict)]
    if kind == "alerts":
        raw = client.list_rules()
        data = raw.get("data", {}) if isinstance(raw, dict) else {}
        rules = data.get("rules", data)
        if isinstance(rules, dict):
            rules = rules.get("rules", [])
        return [r for r in (rules or []) if isinstance(r, dict)]
    if kind == "channels":
        raw = client.list_channels()
        data = raw.get("data", []) if isinstance(raw, dict) else raw
        return [c for c in (data or []) if isinstance(c, dict)]
    if kind == "views":
        raw = client.list_saved_views()
        data = raw.get("data") if isinstance(raw, dict) else raw
        return [v for v in (data or []) if isinstance(v, dict)]
    raise click.UsageError(f"Unknown bundle kind '{kind}'.")


@cli.command("export")
@_common_options
@click.argument("directory", type=click.Path(file_okay=False))
@click.option(
    "--include", default="dashboards,alerts,channels,views", show_default=True,
    help="Comma-separated kinds to export.",
)
@click.option("--yes", is_flag=True, help="Write without confirming.")
def export_bundle(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, directory: str, include: str, yes: bool,
) -> None:
    """Export dashboards, alert rules, channels and views to a directory.

    Produces <dir>/manifest.json plus one JSON file per item, suitable
    for `signoz import` into another instance (dev -> prod migration).

    \b
    Examples:
      signoz export ./backup -p prod
      signoz export ./dash-only --include dashboards
    """
    import os

    try:
        kinds = [k.strip() for k in include.split(",") if k.strip()]
        for kind in kinds:
            if kind not in ("dashboards", "alerts", "channels", "views"):
                raise click.UsageError(
                    f"Unknown kind '{kind}'. "
                    "Choose from: dashboards, alerts, channels, views."
                )
        client = _get_client(endpoint, token, profile)
        try:
            server_version = (client.get_server_version() or {}).get("version", "")
        except Exception:
            server_version = ""
        if not yes:
            click.confirm(
                f"Export {', '.join(kinds)} from {client.endpoint} to {directory}?",
                abort=True,
            )
        manifest: dict[str, Any] = {
            "tool": f"signoz-cli {__version__}",
            "exported_from": client.endpoint,
            "server_version": server_version,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "counts": {},
        }
        summary = []
        for kind in kinds:
            items = _list_items(client, kind)
            kind_dir = os.path.join(directory, kind)
            os.makedirs(kind_dir, exist_ok=True)
            saved = 0
            for item in items:
                full = _fetch_full(client, kind, item)
                if full is None:
                    continue
                item_id = str(full.get("id", item.get("id", "noid")))
                fname = f"{_safe_name(_item_name(kind, full))}.{item_id[:8]}.json"
                with open(os.path.join(kind_dir, fname), "w", encoding="utf-8") as fh:
                    json.dump(full, fh, indent=2, default=str)
                    fh.write("\n")
                saved += 1
            manifest["counts"][kind] = saved
            summary.append({"kind": kind, "exported": saved})
        with open(os.path.join(directory, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
            fh.write("\n")
        emit(summary, fmt)
    except Exception as e:
        _handle_error(e)


@cli.command("import")
@_common_options
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
@click.option(
    "--on-conflict", type=click.Choice(["skip", "overwrite", "fail"]),
    default="skip", show_default=True,
    help="When an item with the same name exists: skip it, overwrite it, "
         "or fail. Views overwrite is delete + recreate (no update API).",
)
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def import_bundle(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, directory: str, dry_run: bool,
    on_conflict: str, yes: bool,
) -> None:
    """Import a bundle directory created by `signoz export`.

    Matches existing items by name. Rule channel references are imported
    as-is; remap channel IDs afterwards if the target instance differs.

    \b
    Examples:
      signoz import ./backup -p staging --dry-run
      signoz import ./backup -p prod --on-conflict overwrite --yes
    """
    import os

    try:
        client = _get_client(endpoint, token, profile)
        kinds = [
            k for k in ("channels", "views", "dashboards", "alerts")
            if os.path.isdir(os.path.join(directory, k))
        ]
        if not kinds:
            raise click.UsageError(
                f"No bundle kinds found in {directory}/ "
                "(expected subdirectories like dashboards/, alerts/)."
            )
        if not yes and not dry_run:
            click.confirm(
                f"Import {', '.join(kinds)} from {directory} "
                f"to {client.endpoint} (on-conflict={on_conflict})?",
                abort=True,
            )
        # Snapshot existing names once per kind for conflict handling.
        existing: dict[str, dict[str, dict[str, Any]]] = {}
        for kind in kinds:
            existing[kind] = {}
            for item in _list_items(client, kind):
                if isinstance(item, dict):
                    existing[kind][_item_name(kind, item)] = item
        report = []
        for kind in kinds:
            kind_dir = os.path.join(directory, kind)
            for fname in sorted(os.listdir(kind_dir)):
                if not fname.endswith(".json"):
                    continue
                with open(os.path.join(kind_dir, fname), encoding="utf-8") as fh:
                    doc = json.load(fh)
                name = _item_name(kind, doc) if isinstance(doc, dict) else fname
                clash = existing[kind].get(name)
                if clash is not None:
                    if on_conflict == "skip":
                        report.append(
                            {"kind": kind, "name": name, "action": "skipped",
                             "detail": "name exists"})
                        continue
                    if on_conflict == "fail":
                        raise click.UsageError(
                            f"Conflict: {kind} '{name}' already exists "
                            "(use --on-conflict skip|overwrite).")
                    action = _overwrite(client, kind, clash, doc, dry_run)
                    report.append({"kind": kind, "name": name, "action": action,
                                   "detail": "overwritten"})
                    continue
                if dry_run:
                    report.append({"kind": kind, "name": name, "action": "create",
                                   "detail": "dry-run"})
                    continue
                _create(client, kind, doc)
                report.append({"kind": kind, "name": name, "action": "created",
                               "detail": ""})
        emit(report, fmt)
    except Exception as e:
        _handle_error(e)


def _normalize_dashboard(doc: dict[str, Any]) -> dict[str, Any]:
    """Fill spec defaults required by newer servers (cross-version import).

    Older dashboards may lack keys the target version mandates
    (e.g. 0.138 requires spec.variables and spec.panels). Read-only
    top-level fields the create/update endpoints reject (locked, image,
    source) are dropped; kept out of the shared volatile set because
    e.g. rules use `source` meaningfully.
    """
    doc = dict(doc)
    for key in ("locked", "image", "source"):
        doc.pop(key, None)
    spec = doc.get("spec")
    if isinstance(spec, dict):
        spec = dict(spec)
        spec.setdefault("variables", [])
        spec.setdefault("panels", {})
        doc["spec"] = spec
    return doc


def _create(client, kind: str, doc: dict[str, Any]) -> None:
    """Create one bundle item on the target instance."""
    body = _strip_volatile(doc)
    if kind == "dashboards":
        client.create_dashboard_v2(_normalize_dashboard(body))
    elif kind == "alerts":
        client.create_rule(body)
    elif kind == "channels":
        client.create_channel(body)
    elif kind == "views":
        client.create_saved_view(body)


def _overwrite(
    client, kind: str, clash: dict[str, Any], doc: dict[str, Any], dry_run: bool
) -> str:
    """Overwrite the clashing item; returns the action label."""
    if dry_run:
        return "overwrite"
    body = _strip_volatile(doc)
    if kind == "dashboards":
        client.update_dashboard_v2(clash.get("id", ""), _normalize_dashboard(body))
    elif kind == "alerts":
        client.update_rule(clash.get("id", ""), body)
    elif kind == "channels":
        client.update_channel(clash.get("id", ""), body)
    elif kind == "views":
        clash_id = clash.get("id", "")
        client.delete_saved_view(clash_id)
        client.create_saved_view(body)
    return "overwritten"


def _diff_objects(old: Any, new: Any, fromfile: str, tofile: str) -> str:
    """Unified diff of two JSON-normalized objects (volatile keys stripped)."""
    old_lines = json.dumps(_strip_volatile(old), indent=2, sort_keys=True,
                           default=str).splitlines()
    new_lines = json.dumps(_strip_volatile(new), indent=2, sort_keys=True,
                           default=str).splitlines()
    return "\n".join(difflib.unified_diff(
        old_lines, new_lines, fromfile=fromfile, tofile=tofile, lineterm="",
    ))
