"""export / import bundle commands (migrate between instances)."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import click

from signoz_cli import __version__
from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    cli,
)
from signoz_cli.inventory import (
    VOLATILE_KEYS as VOLATILE_KEYS,
)
from signoz_cli.inventory import (
    _diff_objects as _diff_objects,
)
from signoz_cli.inventory import (
    _fetch_by_name as _fetch_by_name,
)
from signoz_cli.inventory import (
    _fetch_full as _fetch_full,
)
from signoz_cli.inventory import (
    _item_name as _item_name,
)
from signoz_cli.inventory import (
    _list_items as _list_items,
)
from signoz_cli.inventory import (
    _strip_volatile as _strip_volatile,
)
from signoz_cli.output import emit


def _safe_name(name: str) -> str:
    name = (name or "unnamed").strip() or "unnamed"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]


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
            _confirm(
                f"Export {', '.join(kinds)} from {client.endpoint} to {directory}?",
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
            seen_names: set[str] = set()
            for item in items:
                full = _fetch_full(client, kind, item)
                if full is None:
                    continue
                item_id = str(full.get("id", item.get("id", "noid")))
                fname = f"{_safe_name(_item_name(kind, full))}.{item_id[:8]}.json"
                # Two items can sanitize to the same filename (same name
                # + same id prefix): suffix instead of silently dropping
                # the first export.
                base, n = fname, 1
                while fname in seen_names:
                    n += 1
                    stem = base[: -len(".json")]
                    fname = f"{stem}.{n}.json"
                seen_names.add(fname)
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
            _confirm(
                f"Import {', '.join(kinds)} from {directory} "
                f"to {client.endpoint} (on-conflict={on_conflict})?",
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
            for _fname, doc in _iter_bundle_docs(directory, kind):
                name = _item_name(kind, doc) if isinstance(doc, dict) else _fname
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
    if not isinstance(doc, dict):
        raise click.UsageError(
            f"Cannot create {kind} item from a non-object document "
            f"({type(doc).__name__}); bundle files must hold JSON objects.")
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
        # No update API: delete + recreate. Recreate can fail after the
        # delete has committed (transient 5xx), which would lose the view,
        # so snapshot the old doc first and restore it on failure.
        try:
            old_full = client.get_saved_view(clash_id)
            if isinstance(old_full, dict) and isinstance(old_full.get("data"), dict):
                old_full = old_full["data"]
        except Exception:
            old_full = clash
        client.delete_saved_view(clash_id)
        try:
            client.create_saved_view(body)
        except Exception:
            try:
                if isinstance(old_full, dict):
                    client.create_saved_view(_strip_volatile(old_full))
            except Exception:
                pass
            raise
    return "overwritten"




def _iter_bundle_docs(directory: str, kind: str):
    """Yield (filename, parsed doc) for a bundle kind directory.

    Skips non-JSON names and entries that are not regular files inside
    the directory (symlink escape guard): reading those would POST
    outside files to the target instance.
    """
    import os as _os

    kind_dir = _os.path.join(directory, kind)
    kind_real = _os.path.realpath(kind_dir)
    for fname in sorted(_os.listdir(kind_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = _os.path.realpath(_os.path.join(kind_dir, fname))
        if (_os.path.dirname(fpath) != kind_real
                or not _os.path.isfile(fpath)):
            click.echo(
                f"Warning: skipping {kind}/{fname}: not a regular "
                "file inside the bundle directory.",
                err=True,
            )
            continue
        with open(fpath, encoding="utf-8") as fh:
            yield fname, json.load(fh)


def _delete_item(client, kind: str, item_id: str) -> None:
    """Delete one item of a kind by id."""
    if kind == "dashboards":
        client.delete_dashboard_v2(item_id)
    elif kind == "alerts":
        client.delete_rule(item_id)
    elif kind == "channels":
        client.delete_channel(item_id)
    elif kind == "views":
        client.delete_saved_view(item_id)
    else:  # pragma: no cover - guarded by kind discovery
        raise click.UsageError(f"Unknown bundle kind '{kind}'.")


def _is_subset(wanted: Any, current: Any) -> bool:
    """Whether every key/value declared in *wanted* matches *current*.

    Mappings recurse per key (extra server keys are ignored); lists must
    match pairwise; scalars compare by equality. Non-container wanted
    values compare directly.
    """
    if isinstance(wanted, dict):
        return (isinstance(current, dict)
                and all(k in current and _is_subset(v, current[k])
                        for k, v in wanted.items()))
    if isinstance(wanted, list):
        return (isinstance(current, list) and len(wanted) == len(current)
                and all(_is_subset(w, c)
                        for w, c in zip(wanted, current, strict=True)))
    return bool(wanted == current)


def _docs_equal(kind: str, current: Any, wanted: Any) -> bool:
    """Whether the live object already satisfies the bundle doc.

    Subset semantics: the bundle is the source of truth for the keys it
    declares; server-managed keys (ids, timestamps) and server defaults
    the bundle never mentions do not count as drift. Both sides still go
    through volatile-stripping (and dashboard spec normalization) first.
    """
    if not isinstance(current, dict) or not isinstance(wanted, dict):
        return False
    if kind == "dashboards":
        current = _normalize_dashboard(dict(current))
        wanted = _normalize_dashboard(dict(wanted))
    return _is_subset(_strip_volatile(wanted), _strip_volatile(current))


@cli.command("apply")
@_common_options
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
@click.option("--prune", is_flag=True,
              help="Delete server items absent from the bundle (GitOps).")
@click.option(
    "--on-conflict", type=click.Choice(["overwrite", "skip", "fail"]),
    default="overwrite", show_default=True,
    help="When an item differs: overwrite it, skip it, or fail.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def apply_bundle(
    endpoint: str | None, token: str | None, fmt: str,
    profile: str | None, directory: str, dry_run: bool,
    prune: bool, on_conflict: str, yes: bool,
) -> None:
    """Declaratively sync a bundle directory into an instance (GitOps).

    Beyond `import`: items already matching the bundle are left alone
    (no spurious writes), and with --prune, server items missing from
    the bundle are deleted. Everything is previewable with --dry-run.

    \b
    Examples:
      signoz apply ./dashboards-prod -p prod --dry-run
      signoz apply ./dashboards-prod -p prod --prune --yes
    """
    import os as _os

    try:
        client = _get_client(endpoint, token, profile)
        kinds = [
            k for k in ("channels", "views", "dashboards", "alerts")
            if _os.path.isdir(_os.path.join(directory, k))
        ]
        if not kinds:
            raise click.UsageError(
                f"No bundle kinds found in {directory}/ "
                "(expected subdirectories like dashboards/, alerts/)."
            )
        if not yes and not dry_run:
            _confirm(
                f"Apply bundle {directory} to {client.endpoint} "
                f"(kinds: {', '.join(kinds)}"
                f"{', PRUNE enabled' if prune else ''})?",
            )
        existing: dict[str, dict[str, dict[str, Any]]] = {}
        for kind in kinds:
            existing[kind] = {}
            for item in _list_items(client, kind):
                if isinstance(item, dict):
                    existing[kind][_item_name(kind, item)] = item
        wanted_names: dict[str, set[str]] = {k: set() for k in kinds}
        report = []
        for kind in kinds:
            for _fname, doc in _iter_bundle_docs(directory, kind):
                name = _item_name(kind, doc) if isinstance(doc, dict) else _fname
                wanted_names[kind].add(name)
                clash = existing[kind].get(name)
                if clash is None:
                    if dry_run:
                        action, detail = "create", "dry-run"
                    else:
                        _create(client, kind, doc)
                        action, detail = "created", ""
                    report.append({"kind": kind, "name": name,
                                   "action": action, "detail": detail})
                    continue
                current = _fetch_full(client, kind, clash)
                if current is not None and _docs_equal(kind, current, doc):
                    report.append({"kind": kind, "name": name,
                                   "action": "unchanged", "detail": ""})
                    continue
                if on_conflict == "skip":
                    report.append({"kind": kind, "name": name,
                                   "action": "skipped", "detail": "differs"})
                    continue
                if on_conflict == "fail":
                    raise click.UsageError(
                        f"Conflict: {kind} '{name}' differs from bundle "
                        "(use --on-conflict overwrite|skip).")
                if dry_run:
                    action, detail = "overwrite", "dry-run"
                else:
                    action = _overwrite(client, kind, clash, doc, dry_run)
                    detail = "overwritten"
                report.append({"kind": kind, "name": name, "action": action,
                               "detail": detail})
        total_wanted = sum(len(v) for v in wanted_names.values())
        if prune and total_wanted == 0:
            raise click.UsageError(
                "Refusing to prune against an empty bundle (that would "
                "delete everything). Remove --prune or add bundle files.")
        if prune:
            for kind in kinds:
                for name, item in sorted(existing[kind].items()):
                    if name in wanted_names[kind]:
                        continue
                    item_id = item.get("id", "") if isinstance(item, dict) else ""
                    if not item_id:
                        # Never DELETE a collection path: an id-less entry
                        # would target the bare kind URL. Skip loudly.
                        report.append({"kind": kind, "name": name,
                                       "action": "skipped",
                                       "detail": "missing server id"})
                        continue
                    if dry_run:
                        action, detail = "delete", "dry-run"
                    else:
                        _delete_item(client, kind, item_id)
                        action, detail = "deleted", ""
                    report.append({"kind": kind, "name": name,
                                   "action": action, "detail": detail})
        emit(report, fmt)
    except Exception as e:
        _handle_error(e)
