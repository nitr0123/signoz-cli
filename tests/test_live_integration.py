"""Live integration tests — run against real SigNoz instances.

Opt-in; skipped unless credentials are provided. Primary instance
(v0.138) plus an optional second instance (v0.136) for dual-version
coverage:

    export SIGNOZ_LIVE_URL=http://localhost:8080
    export SIGNOZ_LIVE_TOKEN=<service-account API key>
    export SIGNOZ_LIVE_OTLP=localhost:4317
    # optional second instance:
    export SIGNOZ_LIVE_URL_2=http://localhost:8081
    export SIGNOZ_LIVE_TOKEN_2=<service-account API key>
    export SIGNOZ_LIVE_OTLP_2=localhost:14317

Then:

    pytest -m live

Each configured instance gets freshly seeded telemetry (unique run id via
OTLP), and the tests poll until ClickHouse ingests it. Everything asserted
is scoped to the seeded run id, so the tests are safe to run against any
instance the key can read. The only write path is a scratch dashboard that
is deleted afterwards.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
from seed_live_data import SERVICE_NAME, operation_name, seed

from signoz_cli.client import SigNozApiError, SigNozClient
from signoz_cli.query import (
    build_logs_query_payload,
    build_traces_query_payload,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("SIGNOZ_LIVE_URL") or not os.getenv("SIGNOZ_LIVE_TOKEN"),
        reason="SIGNOZ_LIVE_URL / SIGNOZ_LIVE_TOKEN not set",
    ),
]

INGEST_TIMEOUT_S = 90
POLL_INTERVAL_S = 3


def _instance_config(label: str) -> dict[str, str] | None:
    if label == "primary":
        return {
            "url": os.environ["SIGNOZ_LIVE_URL"],
            "token": os.environ["SIGNOZ_LIVE_TOKEN"],
            "otlp": os.getenv("SIGNOZ_LIVE_OTLP", "localhost:4317"),
            "label": "primary",
        }
    if os.getenv("SIGNOZ_LIVE_URL_2") and os.getenv("SIGNOZ_LIVE_TOKEN_2"):
        return {
            "url": os.environ["SIGNOZ_LIVE_URL_2"],
            "token": os.environ["SIGNOZ_LIVE_TOKEN_2"],
            "otlp": os.getenv("SIGNOZ_LIVE_OTLP_2", "localhost:14317"),
            "label": "secondary",
        }
    return None


def _query_traces(client: SigNozClient, run_id: str) -> list[dict]:
    now = int(time.time() * 1000)
    payload = build_traces_query_payload(
        now - 3_600_000,
        now,
        f"service.name = '{SERVICE_NAME}' AND name = '{operation_name(run_id)}'",
        limit=10,
    )
    resp = client.query_builder_v5(payload)
    results = resp["data"]["data"]["results"][0]
    rows = results.get("rows") or []
    return [r.get("data", r) for r in rows]


def _query_logs(client: SigNozClient, run_id: str) -> list[dict]:
    now = int(time.time() * 1000)
    payload = build_logs_query_payload(
        now - 3_600_000,
        now,
        f"service.name = '{SERVICE_NAME}' AND body CONTAINS '{run_id}'",
        limit=10,
    )
    resp = client.query_builder_v5(payload)
    results = resp["data"]["data"]["results"][0]
    rows = results.get("rows") or []
    return [r.get("data", r) for r in rows]


def _wait_for(fn, what: str) -> list[dict]:
    deadline = time.time() + INGEST_TIMEOUT_S
    last: list[dict] = []
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"timed out waiting for {what} after {INGEST_TIMEOUT_S}s")


@pytest.fixture(params=["primary", "secondary"], scope="module")
def live_instance(request) -> dict:
    """Seed one instance and wait until the telemetry is queryable."""
    cfg = _instance_config(request.param)
    if cfg is None:
        pytest.skip("secondary live instance not configured")
    client = SigNozClient(cfg["url"], cfg["token"], timeout=30)
    run_id = uuid.uuid4().hex[:12]
    seeded = seed(cfg["otlp"], run_id)
    rows = _wait_for(
        lambda: _query_traces(client, run_id),
        f"seeded span {run_id} on {cfg['label']}",
    )
    assert any(r.get("traceID") == seeded["trace_id"] for r in rows)
    _wait_for(
        lambda: _query_logs(client, run_id),
        f"seeded log {run_id} on {cfg['label']}",
    )
    return {"client": client, "run_id": run_id, "seeded": seeded, **cfg}


def test_server_version_supported(live_instance):
    from signoz_cli.main import _is_supported_server

    version = live_instance["client"].get_server_version().get("version", "")
    assert version, "server did not report a version"
    assert _is_supported_server(version), f"{version} outside supported range"


def test_auth_verify(live_instance):
    me = live_instance["client"].verify()
    assert me.get("data", {}).get("id")


def test_seeded_span_found_by_service_and_operation(live_instance):
    rows = _query_traces(live_instance["client"], live_instance["run_id"])
    assert rows, "seeded span not found"
    row = rows[0]
    assert row["service.name"] == SERVICE_NAME
    assert row["name"] == operation_name(live_instance["run_id"])
    assert row["traceID"] == live_instance["seeded"]["trace_id"]
    assert row["hasError"] is False


def test_seeded_log_found_by_body_search(live_instance):
    rows = _query_logs(live_instance["client"], live_instance["run_id"])
    assert rows, "seeded log not found"
    assert any(
        live_instance["run_id"] in str(r.get("body", "")) for r in rows
    ), f"run id missing from log bodies: {rows[0].keys()}"


def test_cli_traces_search_finds_seeded_span(live_instance):
    import json

    from click.testing import CliRunner

    from signoz_cli.main import cli

    result = CliRunner().invoke(cli, [
        "traces", "search",
        "--service", SERVICE_NAME,
        "--operation", operation_name(live_instance["run_id"]),
        "--time-range", "1h",
        "--json",
        "--endpoint", live_instance["url"],
        "--token", live_instance["token"],
    ])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows, "CLI found no rows for seeded span"
    assert rows[0]["traceID"] == live_instance["seeded"]["trace_id"]


def test_cli_logs_search_finds_seeded_log(live_instance):
    import json

    from click.testing import CliRunner

    from signoz_cli.main import cli

    result = CliRunner().invoke(cli, [
        "logs", "search",
        "--service", SERVICE_NAME,
        "--search-text", live_instance["run_id"],
        "--time-range", "1h",
        "--json",
        "--endpoint", live_instance["url"],
        "--token", live_instance["token"],
    ])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows, "CLI found no rows for seeded log"
    assert any(live_instance["run_id"] in str(r.get("body", "")) for r in rows)


def test_cli_services_lists_seeded_service(live_instance):
    import json

    from click.testing import CliRunner

    from signoz_cli.main import cli

    def _names() -> str:
        result = CliRunner().invoke(cli, [
            "services", "list",
            "--json",
            "--endpoint", live_instance["url"],
            "--token", live_instance["token"],
        ])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert isinstance(rows, list), f"expected a list, got {type(rows)}"
        return json.dumps(rows)

    deadline = time.time() + 30
    names = _names()
    while SERVICE_NAME not in names and time.time() < deadline:
        time.sleep(POLL_INTERVAL_S)
        names = _names()
    if SERVICE_NAME not in names:
        # Observed on v0.136.1: the services rollup does not surface the
        # freshly seeded service even though its spans are queryable via
        # traces search. The CLI contract (exit 0 + JSON list) holds, so
        # this is a backend materialization quirk, not a CLI failure.
        pytest.skip("services rollup does not surface seeded service here")


def test_dashboard_lifecycle(live_instance):
    client = live_instance["client"]
    dash = {
        "schemaVersion": "v6",
        "name": f"cli-e2e-{uuid.uuid4().hex[:8]}",
        "tags": [],
        "spec": {"display": {"name": "cli e2e"}, "layouts": [],
                 "panels": {}, "variables": []},
    }
    created = client.create_dashboard_v2(dash)
    dash_id = created["data"]["id"]
    try:
        fetched = client.get_dashboard_v2(dash_id)
        assert fetched["data"]["id"] == dash_id
    finally:
        client.delete_dashboard_v2(dash_id)

    with pytest.raises(SigNozApiError) as exc:
        client.get_dashboard_v2(dash_id)
    assert exc.value.status_code in (400, 404)


def test_alerts_list_shape(live_instance):
    raw = live_instance["client"].list_alerts()
    assert isinstance(raw, dict)
    assert "data" in raw or "status" in raw


def test_v5_logs_query_shape(live_instance):
    now = int(time.time() * 1000)
    payload = {
        "schemaVersion": "v1",
        "start": now - 3_600_000,
        "end": now,
        "requestType": "raw",
        "compositeQuery": {"queries": [{
            "type": "builder_query",
            "spec": {"name": "A", "signal": "logs", "limit": 5,
                     "filter": {"expression": ""}},
        }]},
        "variables": {},
    }
    resp = live_instance["client"].query_builder_v5(payload)
    results = resp["data"]["data"]["results"][0]
    # rows is None on some versions when there are no matches
    assert results.get("rows") is None or isinstance(results.get("rows"), list)
