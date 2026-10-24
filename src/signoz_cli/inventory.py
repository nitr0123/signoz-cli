"""Shared inventory helpers: list/fetch/diff every bundle kind (dashboards, alerts, ...).

Used by the bundle commands (`export`/`import`/`apply`), the per-kind
`diff`/`diff-remote` commands, and lint. Lives here (instead of inside
`commands/bundle.py`) so readers don't need migration logic, and test
code has one stable import home.
"""

from __future__ import annotations

import difflib
import json
from typing import Any

import click

from signoz_cli.commands.common import _MAX_PAGES

VOLATILE_KEYS = (
    "id",
    "createdAt",
    "updatedAt",
    "createdBy",
    "updatedBy",
    "orgId",
)


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


# Every bundle kind, in dependency order for import/apply: route policies
# reference channels by name, so channels go first.
ALL_KINDS = ("channels", "route-policies", "views", "rule-views", "dashboard-views",
             "quick-filters", "dashboards", "alerts")
# Exported when --include is not given (newer kinds need newer servers).
DEFAULT_KINDS = ("dashboards", "alerts", "channels", "views")
# Kinds the server cannot delete (apply --prune skips them).
NO_DELETE_KINDS = frozenset({"quick-filters"})


def _item_name(kind: str, item: dict[str, Any]) -> str:
    if kind == "alerts":
        return str(item.get("alert") or item.get("name") or "unnamed")
    if kind == "quick-filters":
        return str(item.get("source") or "unnamed")
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
        elif kind == "route-policies":
            data = client.get_route_policy(item_id)
        else:
            # rule-views, dashboard-views, quick-filters: the list
            # endpoint already returns complete objects.
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
    if kind in ("route-policies", "rule-views", "dashboard-views", "quick-filters"):
        method, key = {
            "route-policies": ("list_route_policies", None),
            "rule-views": ("list_rule_views", "views"),
            "dashboard-views": ("list_dashboard_views", "views"),
            "quick-filters": ("list_quick_filters", None),
        }[kind]
        raw = getattr(client, method)()
        data = raw.get("data") if isinstance(raw, dict) else raw
        if key and isinstance(data, dict):
            data = data.get(key)
        return [v for v in (data or []) if isinstance(v, dict)]
    raise click.UsageError(f"Unknown bundle kind '{kind}'.")


def _fetch_by_name(client, kind: str, name: str, profile: str) -> Any:
    """Fetch the full object named *name* of a kind, or raise UsageError."""
    for item in _list_items(client, kind):
        if isinstance(item, dict) and _item_name(kind, item) == name:
            full = _fetch_full(client, kind, item)
            if full is None:
                raise click.UsageError(
                    f"Could not fetch {kind} '{name}' "
                    f"from profile '{profile}'.")
            return full
    raise click.UsageError(
        f"No {kind} named '{name}' on profile '{profile}'.")


def _diff_objects(old: Any, new: Any, fromfile: str, tofile: str) -> str:
    """Unified diff of two JSON-normalized objects (volatile keys stripped)."""
    old_lines = json.dumps(_strip_volatile(old), indent=2, sort_keys=True,
                           default=str).splitlines()
    new_lines = json.dumps(_strip_volatile(new), indent=2, sort_keys=True,
                           default=str).splitlines()
    return "\n".join(difflib.unified_diff(
        old_lines, new_lines, fromfile=fromfile, tofile=tofile, lineterm="",
    ))
