"""infra monitoring commands (hosts, Kubernetes workloads, volumes)."""

from __future__ import annotations

from typing import Any

import click

from signoz_cli.commands._crud import data_of
from signoz_cli.commands.common import (
    _common_options,
    _get_client,
    _handle_error,
    _profile_time_range,
    _time_options,
    cli,
)
from signoz_cli.output import emit
from signoz_cli.timeutil import resolve_timestamps

# CLI name -> (API path segment, status filter field, help blurb)
_KINDS: dict[str, tuple[str, str | None, str]] = {
    "hosts": ("hosts", "filterByStatus", "Hosts with CPU, memory, disk, load (status: active|inactive)."),
    "pods": ("pods", "filterByPodStatus", "Kubernetes pods with CPU/memory vs requests and limits."),
    "nodes": ("nodes", "filterByPodStatus", "Kubernetes nodes with CPU/memory vs allocatable."),
    "deployments": ("deployments", "filterByPodStatus", "Kubernetes deployments."),
    "daemonsets": ("daemonsets", "filterByPodStatus", "Kubernetes daemonsets."),
    "statefulsets": ("statefulsets", "filterByPodStatus", "Kubernetes statefulsets."),
    "jobs": ("jobs", "filterByPodStatus", "Kubernetes jobs."),
    "namespaces": ("namespaces", "filterByPodStatus", "Kubernetes namespaces."),
    "clusters": ("clusters", "filterByPodStatus", "Kubernetes clusters."),
    "containers": ("kube_containers", "filterByContainerStatus", "Kubernetes containers."),
    "volumes": ("pvcs", None, "Persistent volume claims with capacity and usage."),
}

_CHECK_TYPES = ["hosts", "processes", "pods", "nodes", "deployments", "daemonsets",
                "statefulsets", "jobs", "namespaces", "clusters", "volumes", "kube_containers"]


@cli.group("infra")
def infra() -> None:
    """Infrastructure monitoring: hosts and Kubernetes resources.

    Each list shows current resource usage per entity. Narrow it with
    --filter (SigNoz search syntax on resource attributes), e.g.
    --filter "k8s.namespace.name = 'prod'".
    """


def _record_row(rec: dict[str, Any]) -> dict[str, Any]:
    """Flatten one record for table output: scalars first, then meta attributes."""
    row = {k: v for k, v in rec.items()
           if k != "meta" and not isinstance(v, dict | list)}
    meta = rec.get("meta")
    if isinstance(meta, dict):
        for k, v in meta.items():
            row.setdefault(k, v)
    return row


def _parse_order(order_by: str | None) -> dict[str, Any] | None:
    if not order_by:
        return None
    key, _, direction = order_by.partition(":")
    direction = (direction or "desc").lower()
    if direction not in ("asc", "desc"):
        raise click.UsageError("--order-by direction must be asc or desc (e.g. cpu:desc).")
    return {"key": {"name": key}, "direction": direction}


def _make_list_command(name: str, path: str, status_field: str | None, blurb: str) -> None:
    @infra.command(name, help=f"List {blurb}")
    @_common_options
    @_time_options(default_range="15m")
    @click.option("--filter", "filter_expr", default="",
                  help="Filter expression on resource attributes.")
    @click.option("--status", "statuses", multiple=True,
                  help="Only entities in this status (repeatable)."
                  if status_field else "Not supported for this kind.")
    @click.option("--group-by", "group_by", multiple=True,
                  help="Group by an attribute (repeatable), e.g. k8s.namespace.name.")
    @click.option("--order-by", default=None,
                  help="Sort key and direction, e.g. cpu:desc or memory:asc.")
    @click.option("--limit", type=int, default=50, show_default=True, help="Max rows (1-5000).")
    @click.option("--offset", type=int, default=0, show_default=True, help="Pagination offset.")
    def _cmd(endpoint: str | None, token: str | None, fmt: str, profile: str | None,
             time_range: str | None, start: int | None, end: int | None,
             filter_expr: str, statuses: tuple[str, ...], group_by: tuple[str, ...],
             order_by: str | None, limit: int, offset: int) -> None:
        try:
            if statuses and not status_field:
                raise click.UsageError(f"`infra {name}` has no status filter.")
            s, e = resolve_timestamps(start, end, time_range, default_range="15m",
                                      profile_time_range=_profile_time_range(profile))
            payload: dict[str, Any] = {"start": s, "end": e, "limit": limit, "offset": offset}
            flt: dict[str, Any] = {}
            if filter_expr:
                flt["expression"] = filter_expr
            if statuses and status_field:
                flt[status_field] = (statuses[0] if status_field == "filterByStatus"
                                     else list(statuses))
            if flt:
                payload["filter"] = flt
            if group_by:
                payload["groupBy"] = [{"name": g} for g in group_by]
            order = _parse_order(order_by)
            if order:
                payload["orderBy"] = order
            client = _get_client(endpoint, token, profile)
            raw = client.list_infra(path, payload)
            if fmt == "json":
                emit(raw, fmt)
                return
            data = data_of(raw)
            records = data.get("records") if isinstance(data, dict) else None
            rows = [_record_row(r) for r in (records or []) if isinstance(r, dict)]
            emit({"data": rows}, fmt)
            if isinstance(data, dict):
                total = data.get("total")
                if isinstance(total, int) and total > offset + len(rows):
                    click.echo(f"{total} total; next page: --offset {offset + len(rows)}",
                               err=True)
                if data.get("endTimeBeforeRetention"):
                    click.echo("Warning: the time range ends before data retention; "
                               "results may be empty.", err=True)
        except Exception as ex:
            _handle_error(ex)


for _name, (_path, _status, _blurb) in _KINDS.items():
    _make_list_command(_name, _path, _status, _blurb)


@infra.command("checks")
@_common_options
@click.argument("check_type", type=click.Choice(_CHECK_TYPES))
def infra_checks(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, check_type: str) -> None:
    """Check that the metrics and attributes a view needs are being collected.

    Lists what is missing (with a docs link) so you can fix the collector
    config when an infra list comes back empty.
    """
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_infra_checks(check_type)
        if fmt == "json":
            emit(raw, fmt)
            return
        data = data_of(raw)
        data = data if isinstance(data, dict) else {}
        rows = []
        # Missing sections first: they are what needs fixing.
        for section in sorted(data, key=lambda k: (not k.startswith("missing"), k)):
            entries = data[section]
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                items = entry.get("metrics") or entry.get("attributes") or []
                component = entry.get("associatedComponent")
                if isinstance(component, dict):
                    component = component.get("name") or component.get("type") or ""
                rows.append({
                    "status": "missing" if section.startswith("missing") else "present",
                    "check": section,
                    "component": component or "",
                    "items": ", ".join(map(str, items)),
                    "message": entry.get("message", ""),
                    "docs": entry.get("documentationLink", ""),
                })
        emit({"data": rows}, fmt)
        if "ready" in data:
            click.echo(f"ready: {'yes' if data.get('ready') else 'no'}", err=True)
    except Exception as e:
        _handle_error(e)
