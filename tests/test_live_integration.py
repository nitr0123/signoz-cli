"""Live integration tests — run against a real SigNoz instance.

These are opt-in and skipped unless environment variables are provided:

    export SIGNOZ_LIVE_URL=http://localhost:8080
    export SIGNOZ_LIVE_TOKEN=<service-account API key>

Then:

    pytest -m live

They only exercise read paths (plus their own scratch dashboard, which they
delete afterwards) so they are safe to run against any instance the key can
read.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from signoz_cli.client import SigNozApiError

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("SIGNOZ_LIVE_URL") or not os.getenv("SIGNOZ_LIVE_TOKEN"),
        reason="SIGNOZ_LIVE_URL / SIGNOZ_LIVE_TOKEN not set",
    ),
]


@pytest.fixture(scope="module")
def client() -> SigNozClient:  # noqa: F821
    from signoz_cli.client import SigNozClient

    return SigNozClient(
        os.environ["SIGNOZ_LIVE_URL"],
        os.environ["SIGNOZ_LIVE_TOKEN"],
        timeout=30,
    )


def test_server_version_supported(client):
    from signoz_cli.main import _is_supported_server

    version = client.get_server_version().get("version", "")
    assert version, "server did not report a version"
    assert _is_supported_server(version), f"{version} outside supported range"


def test_auth_verify(client):
    me = client.verify()
    assert me.get("data", {}).get("id")


def test_v5_logs_query(client):
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
    resp = client.query_builder_v5(payload)
    results = resp["data"]["data"]["results"][0]
    # rows is None on some versions when there are no matches
    assert results.get("rows") is None or isinstance(results.get("rows"), list)


def test_dashboard_lifecycle(client):
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


def test_alerts_list_shape(client):
    raw = client.list_alerts()
    assert isinstance(raw, dict)
    assert "data" in raw or "status" in raw
