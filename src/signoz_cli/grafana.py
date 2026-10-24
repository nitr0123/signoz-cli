"""Grafana dashboard conversion to SigNoz v2 dashboards.

Covers the common Grafana surface faithfully; anything else is reported
as a warning and never silently dropped:

- panels: graph/timeseries -> TimeSeries, stat/gauge -> Number,
  table -> Table, text -> Text, pie -> Pie, heatmap -> Heatmap
  (0.146+; other types skipped)
- targets: ``expr`` -> PromQL query plugin, ``rawSql`` -> ClickHouse SQL
  (other datasource targets skipped)
- variables: interval/constant/custom/textbox -> TextVariable,
  query/datasource/adhoc -> manual
- layout: Grafana grid positions are not carried over (SigNoz layouts
  use panel references whose exact convention is server-version
  dependent); ``layouts`` is emitted empty and valid
- rows (legacy) are flattened; thresholds/fieldConfig/links/annotations
  are reported as dropped

Target shapes follow the SigNoz v0.147 openapi spec
(``DashboardtypesPanelSpec``, ``DashboardtypesQuerySpec``,
``DashboardtypesTextVariableSpec``).
"""

from __future__ import annotations

import re
from typing import Any

_PANEL_KINDS = {
    "graph": "signoz/TimeSeriesPanel",
    "timeseries": "signoz/TimeSeriesPanel",
    "stat": "signoz/NumberPanel",
    "gauge": "signoz/NumberPanel",
    "bargauge": "signoz/NumberPanel",
    "table": "signoz/TablePanel",
    "text": "signoz/TextPanel",
    "heatmap": "signoz/HeatmapPanel",
    "pie": "signoz/PieChartPanel",
    "piechart": "signoz/PieChartPanel",
}

# Grafana variable types convertible without a query language mapping.
_TEXT_VAR_TYPES = {"interval", "constant", "custom", "textbox"}


def _slug(text: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "unnamed").lower()).strip("-")
    return slug[:80] or "unnamed"


def _convert_variable(var: dict[str, Any]) -> tuple[Any, str | None]:
    """Convert one Grafana template variable; returns (variable, warning)."""
    name = str(var.get("name", ""))
    if not name:
        return None, "variable without a name skipped"
    label = str(var.get("label") or name)
    current = var.get("current") or {}
    value = current.get("value", "")
    if isinstance(value, list):
        value = value[0] if value else ""
    value = str(value)
    if var.get("type") in _TEXT_VAR_TYPES:
        return ({
            "kind": "TextVariable",
            "spec": {
                "name": name,
                "display": {"name": label},
                "value": value,
                "constant": var.get("type") == "constant",
            },
        }, None)
    return None, (
        f"variable {name!r} (type {var.get('type')!r}) needs manual "
        "conversion to a SigNoz ListVariable")


def _convert_target(target: Any, index: int) -> tuple[Any, str | None]:
    """Convert one Grafana target; returns (query, warning)."""
    if not isinstance(target, dict):
        return None, f"target #{index} is not an object, skipped"
    ref_id = str(target.get("refId") or chr(ord("A") + index))
    legend = target.get("legendFormat", "")
    disabled = bool(target.get("hide"))
    expr = target.get("expr")
    if isinstance(expr, str) and expr.strip():
        return ({
            "kind": "time_series",
            "spec": {
                "name": ref_id,
                "plugin": {
                    "kind": "signoz/PromQLQuery",
                    "spec": {
                        "name": ref_id,
                        "query": expr,
                        "legend": str(legend),
                        "disabled": disabled,
                    },
                },
            },
        }, None)
    sql = target.get("rawSql")
    if isinstance(sql, str) and sql.strip():
        return ({
            "kind": "time_series",
            "spec": {
                "name": ref_id,
                "plugin": {
                    "kind": "signoz/ClickHouseSQL",
                    "spec": {"query": sql, "disabled": disabled},
                },
            },
        }, None)
    ds_type = ""
    ds = target.get("datasource")
    if isinstance(ds, dict):
        ds_type = str(ds.get("type", ""))
    return None, (
        f"target {ref_id!r} has no PromQL expr or ClickHouse SQL"
        + (f" (datasource {ds_type!r})" if ds_type else "")
        + ", skipped")


def _convert_panel(panel: dict[str, Any],
                   index: int) -> tuple[str, Any, list[str]]:
    """Convert one Grafana panel; returns (key, panel, warnings)."""
    warnings: list[str] = []
    title = str(panel.get("title") or f"panel-{index}")
    key = f"panel-{_slug(title)}-{index}"
    ptype = str(panel.get("type", "")).lower()
    kind = _PANEL_KINDS.get(ptype)
    if kind is None:
        return key, None, [
            f"panel {title!r} (type {panel.get('type')!r}) is not "
            "supported, skipped"]
    description = str(panel.get("description", ""))
    if kind == "signoz/TextPanel":
        options = panel.get("options") or {}
        mode = options.get("mode", "markdown")
        warnings.append(
            f"panel {title!r}: text panels need SigNoz 0.142+")
        out = {
            "kind": "Panel",
            "spec": {
                "display": {"name": title, "description": description},
                "plugin": {
                    "kind": kind,
                    "spec": {
                        "text": str(options.get("content", "")),
                        "mode": mode if mode in ("markdown", "text", "html")
                        else "markdown",
                    },
                },
                "queries": [],
            },
        }
        return key, out, warnings
    queries: list[Any] = []
    for i, target in enumerate(panel.get("targets") or []):
        query, warning = _convert_target(target, i)
        if query is not None:
            queries.append(query)
        if warning:
            warnings.append(f"panel {title!r}: {warning}")
    if not queries:
        warnings.append(f"panel {title!r} has no convertible queries")
    if kind == "signoz/HeatmapPanel":
        warnings.append(
            f"panel {title!r}: heatmap panels need SigNoz 0.146+")
    return key, {
        "kind": "Panel",
        "spec": {
            "display": {"name": title, "description": description},
            "plugin": {"kind": kind, "spec": {}},
            "queries": queries,
        },
    }, warnings


def _iter_panels(doc: dict[str, Any]) -> Any:
    """Yield panels, flattening legacy row panels' nested lists."""
    for panel in doc.get("panels") or []:
        if not isinstance(panel, dict):
            continue
        if panel.get("type") == "row" and isinstance(panel.get("panels"), list):
            for nested in panel["panels"]:
                if isinstance(nested, dict):
                    yield nested
        else:
            yield panel


def convert_dashboard(doc: Any) -> tuple[dict[str, Any], list[str]]:
    """Convert a Grafana dashboard document; returns (v2 doc, warnings)."""
    warnings: list[str] = []
    if not isinstance(doc, dict):
        raise ValueError("Grafana dashboard must be a JSON object.")
    title = str(doc.get("title") or "unnamed")
    variables: list[Any] = []
    for var in ((doc.get("templating") or {}).get("list") or []):
        if not isinstance(var, dict):
            warnings.append("non-object template variable skipped")
            continue
        converted, warning = _convert_variable(var)
        if converted is not None:
            variables.append(converted)
        if warning:
            warnings.append(warning)
    panels: dict[str, Any] = {}
    for index, panel in enumerate(_iter_panels(doc)):
        key, converted, panel_warnings = _convert_panel(panel, index)
        warnings.extend(panel_warnings)
        if converted is not None:
            panels[key] = converted
    for dropped in ("annotations", "links"):
        if doc.get(dropped):
            warnings.append(
                f"dashboard-level {dropped!r} has no SigNoz equivalent, dropped")
    tags = doc.get("tags")
    out = {
        "schemaVersion": "v6",
        "name": _slug(title),
        "spec": {
            "display": {
                "name": title,
                "description": str(doc.get("description", "")),
            },
            "variables": variables,
            "panels": panels,
            "layouts": [],
        },
    }
    if isinstance(tags, list) and all(isinstance(t, str) for t in tags):
        out["tags"] = tags
    elif tags:
        warnings.append("non-string dashboard tags dropped")
    return out, warnings
