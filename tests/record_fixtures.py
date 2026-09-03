"""Record live SigNoz responses into tests/fixtures/ for contract tests.

Regenerate whenever the supported server range changes:

    export SIGNOZ_LIVE_URL=http://localhost:8080
    export SIGNOZ_LIVE_TOKEN=<key>
    export SIGNOZ_LIVE_OTLP=localhost:4317
    # optional second instance:
    export SIGNOZ_LIVE_URL_2=http://localhost:8081
    export SIGNOZ_LIVE_TOKEN_2=<key>
    export SIGNOZ_LIVE_OTLP_2=localhost:14317

    python tests/record_fixtures.py

Seeds deterministic telemetry (run id ``fixture``) first so the recorded
traces/logs rows are stable and attributable. Only read paths are used.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signoz_cli.client import SigNozClient  # noqa: E402
from signoz_cli.query import (  # noqa: E402
    build_logs_query_payload,
    build_metrics_query_payload,
    build_metrics_stats_payload,
    build_traces_query_payload,
)
from signoz_cli.timeutil import resolve_timestamps  # noqa: E402
from tests.seed_live_data import METRIC_NAME as METRIC  # noqa: E402
from tests.seed_live_data import (  # noqa: E402
    SERVICE_NAME,
    operation_name,
    send_metric,  # noqa: E402
)
from tests.seed_live_data import seed as seed_telemetry  # noqa: E402

FIXTURE_RUN_ID = "fixture"
WINDOW_MS = 3_600_000


def _targets() -> list[dict[str, str]]:
    targets = [{
        "tag": "v138",
        "url": os.environ["SIGNOZ_LIVE_URL"],
        "token": os.environ["SIGNOZ_LIVE_TOKEN"],
        "otlp": os.getenv("SIGNOZ_LIVE_OTLP", "localhost:4317"),
    }]
    if os.getenv("SIGNOZ_LIVE_URL_2") and os.getenv("SIGNOZ_LIVE_TOKEN_2"):
        targets.append({
            "tag": "v136",
            "url": os.environ["SIGNOZ_LIVE_URL_2"],
            "token": os.environ["SIGNOZ_LIVE_TOKEN_2"],
            "otlp": os.getenv("SIGNOZ_LIVE_OTLP_2", "localhost:14317"),
        })
    return targets


def _wait_for(fn, what: str, timeout: int = 90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = fn()
        if out:
            return out
        time.sleep(3)
    raise SystemExit(f"timed out waiting for {what}")


def _traces_rows(client: SigNozClient) -> list:
    now = int(time.time() * 1000)
    payload = build_traces_query_payload(
        now - WINDOW_MS, now,
        f"service.name = '{SERVICE_NAME}' AND name = '{operation_name(FIXTURE_RUN_ID)}'",
        limit=5,
    )
    resp = client.query_builder_v5(payload)
    return resp["data"]["data"]["results"][0].get("rows") or []


def _logs_rows(client: SigNozClient) -> list:
    now = int(time.time() * 1000)
    payload = build_logs_query_payload(
        now - WINDOW_MS, now,
        f"service.name = '{SERVICE_NAME}' AND body CONTAINS '{FIXTURE_RUN_ID}'",
        limit=5,
    )
    resp = client.query_builder_v5(payload)
    return resp["data"]["data"]["results"][0].get("rows") or []


def _metrics_ready(client: SigNozClient) -> bool:
    now = int(time.time() * 1000)
    payload = build_metrics_query_payload(
        now - WINDOW_MS, now, METRIC,
        temporality="Cumulative",
        time_aggregation="sum", space_aggregation="sum",
    )
    resp = client.query_builder_v5(payload)
    aggs = resp["data"]["data"]["results"][0].get("aggregations")
    return bool(aggs and aggs[0].get("series"))


def _scalar_ready(client: SigNozClient) -> bool:
    now = int(time.time() * 1000)
    payload = build_metrics_stats_payload(
        now - WINDOW_MS, now, METRIC,
        temporality="Cumulative",
        time_aggregation="max", reduce_to="max",
    )
    resp = client.query_builder_v5(payload)
    return bool(resp["data"]["data"]["results"][0].get("data"))


def _ensure_metric_points(client: SigNozClient, otlp: str) -> None:
    """Top up counter points until timeseries AND scalar queries hit.

    The dev backend materializes metrics tens of minutes after traces/logs
    (vs seconds), and fresh bursts query more reliably than old points, so
    re-seed (with a fresh run id each round) until both payload shapes
    return data. Slow but this script runs rarely and by hand.
    """
    for attempt in range(6):
        if _metrics_ready(client) and _scalar_ready(client):
            return
        print(f"  metrics not queryable yet, seeding round {attempt}...")
        send_metric(otlp, f"{FIXTURE_RUN_ID}-m{attempt}")
        deadline = time.time() + 600
        while time.time() < deadline:
            if _metrics_ready(client) and _scalar_ready(client):
                return
            time.sleep(15)
    raise SystemExit("metrics never became queryable")


def _raw(client: SigNozClient, kind: str):
    now = int(time.time() * 1000)
    if kind == "traces":
        payload = build_traces_query_payload(
            now - WINDOW_MS, now,
            f"service.name = '{SERVICE_NAME}' AND name = '{operation_name(FIXTURE_RUN_ID)}'",
            limit=5,
        )
    elif kind == "logs":
        payload = build_logs_query_payload(
            now - WINDOW_MS, now,
            f"service.name = '{SERVICE_NAME}' AND body CONTAINS '{FIXTURE_RUN_ID}'",
            limit=5,
        )
    elif kind == "timeseries":
        payload = build_metrics_query_payload(
            now - WINDOW_MS, now, METRIC,
            temporality="Cumulative",
            time_aggregation="sum", space_aggregation="sum",
        )
    else:  # scalar
        payload = build_metrics_stats_payload(
            now - WINDOW_MS, now, METRIC,
            temporality="Cumulative",
            time_aggregation="max", reduce_to="max",
        )
    return client.query_builder_v5(payload)


def main() -> None:
    outdir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "fixtures"
    )
    os.makedirs(outdir, exist_ok=True)
    # Seed every target first: metrics need tens of minutes to materialize,
    # so their clock starts while traces/logs are recorded below.
    clients = []
    for target in _targets():
        client = SigNozClient(target["url"], target["token"], timeout=30)
        version = client.get_server_version().get("version", "unknown")
        print(f"--- {target['tag']} ({version}): seeding ---")
        seed_telemetry(target["otlp"], FIXTURE_RUN_ID)
        clients.append((target, client))
    for target, client in clients:
        print(f"--- {target['tag']}: recording ---")
        _wait_for(lambda c=client: _traces_rows(c), "seeded span")
        _wait_for(lambda c=client: _query_logs(c), "seeded log")
        # Metrics materialize minutes after traces/logs on a quiet backend.
        _ensure_metric_points(client, target["otlp"])
        s, e = resolve_timestamps(None, None, "6h", unit="ns")
        fixtures = {
            f"traces_raw_{target['tag']}.json": _raw(client, "traces"),
            f"logs_raw_{target['tag']}.json": _raw(client, "logs"),
            f"metrics_timeseries_{target['tag']}.json": _raw(client, "timeseries"),
            f"metrics_scalar_{target['tag']}.json": _raw(client, "scalar"),
            f"services_list_{target['tag']}.json": client.list_services(str(s), str(e)),
            f"alerts_list_{target['tag']}.json": client.list_alerts(),
            f"dashboards_list_{target['tag']}.json": client.list_dashboards_v2(),
        }
        for name, data in fixtures.items():
            if name.startswith("metrics_timeseries"):
                aggs = data["data"]["data"]["results"][0].get("aggregations")
                assert aggs and aggs[0].get("series"), f"{name} recorded empty!"
            if name.startswith("metrics_scalar"):
                assert data["data"]["data"]["results"][0].get("data"), (
                    f"{name} recorded empty!"
                )
            path = os.path.join(outdir, name)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=str)
            print(f"wrote {name} ({len(json.dumps(data))} bytes)")


def _query_logs(client: SigNozClient) -> list:
    return _logs_rows(client)


if __name__ == "__main__":
    main()
