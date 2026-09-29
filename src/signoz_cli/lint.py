"""Static validation for dashboards and alert rules (CI-friendly lint).

Every ERROR below is grounded in a live-verified server rejection
(probed against v0.138: missing schemaVersion/name/spec.variables/
spec.layouts for dashboards, missing condition for rules). WARNINGs
are portability and hygiene findings that the server tolerates.
"""

from __future__ import annotations

from typing import Any

from signoz_cli.inventory import VOLATILE_KEYS

# Top-level dashboard fields the create/update endpoints reject or reset
# (mirrors bundle._normalize_dashboard).
READONLY_DASHBOARD_KEYS = ("locked", "image", "source")


def _finding(level: str, check: str, message: str) -> dict[str, str]:
    return {"level": level, "check": check, "message": message}


def _is_static_panel(panel: dict[str, Any]) -> bool:
    """Whether a panel carries static content instead of queries.

    Text panels (SigNoz 0.142+) embed ``text``/``mode`` content, either
    inline or under a ``spec``/``plugin`` mapping keyed by panel kind
    (e.g. ``signoz/TextPanel``). Flagging those for missing ``queries``
    would be a false positive.
    """
    if "text" in panel:
        return True
    for key in ("plugin", "spec", "options"):
        nested = panel.get(key)
        if isinstance(nested, dict):
            kind = nested.get("kind", "")
            if isinstance(kind, str) and "TextPanel" in kind:
                return True
            if "text" in nested:
                return True
    return False


def lint_dashboard(doc: Any) -> list[dict[str, str]]:
    """Validate a v2 dashboard document; errors fail, warnings advise."""
    findings: list[dict[str, str]] = []
    if not isinstance(doc, dict):
        return [_finding("error", "is-object", "Dashboard must be a JSON object.")]
    if doc.get("schemaVersion") != "v6":
        findings.append(_finding(
            "error", "schema-version",
            f"schemaVersion must be \"v6\", got {doc.get('schemaVersion')!r}."))
    if not isinstance(doc.get("name"), str) or not doc.get("name", "").strip():
        findings.append(_finding(
            "error", "name", "Top-level name is required and non-empty."))
    spec = doc.get("spec")
    if not isinstance(spec, dict):
        findings.append(_finding(
            "error", "spec", "spec must be an object."))
        spec = {}
    if not isinstance(spec.get("variables"), list):
        findings.append(_finding(
            "error", "spec.variables",
            "spec.variables is required and must not be null (use [])."))
    if not isinstance(spec.get("layouts"), list):
        findings.append(_finding(
            "error", "spec.layouts",
            "spec.layouts is required and must not be null (use [])."))
    panels = spec.get("panels", {})
    if not panels:
        findings.append(_finding(
            "warning", "panels", "Dashboard defines no panels."))
    elif not isinstance(panels, dict):
        findings.append(_finding(
            "warning", "panels", "spec.panels should be a mapping of panels."))
    else:
        for panel_id, panel in panels.items():
            if not isinstance(panel, dict):
                findings.append(_finding(
                    "warning", "panel-shape",
                    f"Panel {panel_id!r} should be an object."))
                continue
            if _is_static_panel(panel):
                # Text/static panels (0.142+) carry content, not queries.
                continue
            if "queries" not in panel:
                findings.append(_finding(
                    "warning", "panel-queries",
                    f"Panel {panel_id!r} defines no queries."))
    for key in VOLATILE_KEYS:
        if key in doc:
            findings.append(_finding(
                "warning", "portability",
                f"Top-level {key!r} is server-managed and stripped on import."))
    for key in READONLY_DASHBOARD_KEYS:
        if key in doc:
            findings.append(_finding(
                "warning", "readonly-field",
                f"Top-level {key!r} is rejected or reset on create/update."))
    return findings


def lint_rule(doc: Any) -> list[dict[str, str]]:
    """Validate a v2 alert rule document; errors fail, warnings advise."""
    findings: list[dict[str, str]] = []
    if not isinstance(doc, dict):
        return [_finding("error", "is-object", "Rule must be a JSON object.")]
    condition = doc.get("condition")
    if not isinstance(condition, dict):
        findings.append(_finding(
            "error", "condition", "condition is required and must be an object."))
    if not isinstance(doc.get("alert"), str) or not doc.get("alert", "").strip():
        findings.append(_finding(
            "warning", "name", "Rule has no alert name."))
    labels = doc.get("labels")
    if isinstance(labels, dict):
        if "severity" not in labels:
            findings.append(_finding(
                "warning", "severity",
                "Rule labels define no severity (critical/warning/info)."))
    elif labels is not None:
        findings.append(_finding(
            "warning", "labels", "Rule labels should be an object."))
    for key in VOLATILE_KEYS:
        if key in doc:
            findings.append(_finding(
                "warning", "portability",
                f"Top-level {key!r} is server-managed and stripped on import."))
    return findings
