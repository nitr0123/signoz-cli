"""Response-shape contracts: real recorded server responses per version.

Fixtures in ``tests/fixtures/`` are captured from live instances by
``tests/record_fixtures.py`` (v138 + v136). These tests pin the wire
contract our unwrappers depend on, so a server upgrade that reshapes an
envelope fails here -- in unit CI, without any live backend -- instead of
in production. Re-record when the supported server range changes.
"""

import json
import os

import pytest

from signoz_cli.commands.common import (
    _dashboard_summary,
    _unwrap_history_timeline,
    _unwrap_time_series,
    _unwrap_v5,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
VERSIONS = ["v138", "v136"]


def _load(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


@pytest.mark.parametrize("version", VERSIONS)
class TestTracesContract:
    def test_rows_unwrap_with_core_columns(self, version):
        data = _load(f"traces_raw_{version}.json")
        assert data["data"]["type"] == "raw"
        rows = _unwrap_v5(data)
        assert isinstance(rows, list) and rows, "no seeded span rows recorded"
        row = rows[0]
        for key in ("service.name", "name", "traceID", "spanID", "durationNano"):
            assert key in row, f"missing column {key} on {version}"
        assert row["service.name"] == "signoz-cli-e2e"
        assert row["name"].startswith("e2e-operation-")
        assert len(row["traceID"]) == 32


@pytest.mark.parametrize("version", VERSIONS)
class TestLogsContract:
    def test_rows_unwrap_with_body(self, version):
        data = _load(f"logs_raw_{version}.json")
        assert data["data"]["type"] == "raw"
        rows = _unwrap_v5(data)
        assert isinstance(rows, list) and rows, "no seeded log rows recorded"
        assert any("e2e log marker" in str(r.get("body", "")) for r in rows)


@pytest.mark.parametrize("version", VERSIONS)
class TestMetricsContract:
    def test_timeseries_unwraps_to_numeric_points(self, version):
        data = _load(f"metrics_timeseries_{version}.json")
        assert data["data"]["type"] == "time_series"
        rows = _unwrap_time_series(data)
        assert rows, "no metric points recorded"
        for row in rows:
            assert isinstance(row["value"], (int, float))
            assert isinstance(row["timestamp"], (int, float, str))

    def test_scalar_response_carries_columns_and_data(self, version):
        data = _load(f"metrics_scalar_{version}.json")
        assert data["data"]["type"] == "scalar"
        result = data["data"]["data"]["results"][0]
        assert result.get("data"), "no scalar data recorded"
        first = result["data"][0]
        assert isinstance(first, list) and first, "scalar row shape changed"


@pytest.mark.parametrize("version", VERSIONS)
class TestServicesContract:
    def test_list_shape(self, version):
        rows = _load(f"services_list_{version}.json")
        assert isinstance(rows, list)
        if not rows:
            pytest.skip(f"{version} returned no services in fixture window")
        assert "serviceName" in rows[0]


@pytest.mark.parametrize("version", VERSIONS)
class TestEnvelopes:
    def test_alerts_list_envelope(self, version):
        data = _load(f"alerts_list_{version}.json")
        assert data["status"] == "success"
        assert isinstance(data["data"], list)

    def test_dashboards_list_envelope(self, version):
        data = _load(f"dashboards_list_{version}.json")
        assert data["status"] == "success"
        assert isinstance(data["data"].get("dashboards"), list)

    def test_dashboard_summary_tolerates_entries(self, version):
        dashboards = _load(f"dashboards_list_{version}.json")["data"]["dashboards"]
        for dash in dashboards:
            summary = _dashboard_summary(dash)
            assert "id" in summary and "name" in summary


class TestHistoryTimelineContract:
    def test_empty_list_passthrough(self):
        assert _unwrap_history_timeline({"data": []}) == {"data": []}

    def test_non_dict_passthrough(self):
        assert _unwrap_history_timeline("x") == "x"
