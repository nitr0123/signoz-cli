"""Tests for metrics/promql/sql payloads and time-series unwrapping."""


from signoz_cli.main import _unwrap_time_series, _warn_v5
from signoz_cli.query import (
    build_metrics_query_payload,
    build_promql_query_payload,
    build_sql_query_payload,
)


class TestMetricsPayload:
    def test_shape(self):
        p = build_metrics_query_payload(1, 2, "cpu.usage", temporality="Cumulative",
                                        time_aggregation="rate", space_aggregation="sum",
                                        group_by=["host.name"], filter_expression="env='p'",
                                        step_interval=120)
        assert p["requestType"] == "time_series"
        q = p["compositeQuery"]["queries"][0]["spec"]
        assert q["signal"] == "metrics"
        assert q["stepInterval"] == 120
        assert q["aggregations"] == [{
            "metricName": "cpu.usage", "temporality": "Cumulative",
            "timeAggregation": "rate", "spaceAggregation": "sum",
        }]
        assert q["groupBy"] == [{"name": "host.name", "signal": "metrics"}]
        assert q["filter"]["expression"] == "env='p'"

    def test_no_group_by_defaults_empty(self):
        spec = build_metrics_query_payload(1, 2, "m")["compositeQuery"]["queries"][0]["spec"]
        assert spec["groupBy"] == []


class TestPromqlSqlPayloads:
    def test_promql_has_no_querytype_field(self):
        """0.138 rejects unknown fields; envelope type alone discriminates."""
        p = build_promql_query_payload(1, 2, "rate(x[5m])", step_interval=60)
        assert "queryType" not in p["compositeQuery"]
        q = p["compositeQuery"]["queries"][0]
        assert q["type"] == "promql"
        assert q["spec"]["query"] == "rate(x[5m])"

    def test_sql_has_no_querytype_field(self):
        p = build_sql_query_payload(1, 2, "SELECT 1")
        assert "queryType" not in p["compositeQuery"]
        q = p["compositeQuery"]["queries"][0]
        assert q["type"] == "clickhouse_sql"
        assert q["spec"]["query"] == "SELECT 1"


def _ts_response(series, warnings=None):
    return {
        "status": "success",
        "data": {
            "type": "time_series",
            "meta": {"warning": {"warnings": warnings or []}},
            "data": {
                "results": [
                    {
                        "queryName": "A",
                        "aggregations": [
                            {
                                "index": 0,
                                "alias": "__result_0",
                                "series": series,
                            }
                        ],
                    }
                ]
            },
        },
    }


class TestUnwrapTimeSeries:
    def test_real_shape_from_0138(self):
        resp = _ts_response([
            {"values": [{"timestamp": 100, "value": 15}]},
        ])
        rows = _unwrap_time_series(resp)
        assert rows == [{"series": "__result_0", "timestamp": 100, "value": 15}]

    def test_labels_preferred_over_alias(self):
        resp = _ts_response([
            {"labels": [{"key": "service.name", "value": "api"}],
             "values": [{"timestamp": 1, "value": 1.5},
                        {"timestamp": 2, "value": 2.5}]},
        ])
        rows = _unwrap_time_series(resp)
        assert len(rows) == 2
        assert all(r["series"] == "service.name=api" for r in rows)
        assert [r["value"] for r in rows] == [1.5, 2.5]

    def test_multiple_series_and_aggregations(self):
        resp = _ts_response([
            {"labels": [{"key": "k", "value": "a"}], "values": [{"timestamp": 1, "value": 1}]},
            {"labels": [], "values": [{"timestamp": 2, "value": 2}]},
        ])
        # second aggregation entry
        resp["data"]["data"]["results"][0]["aggregations"].append(
            {"index": 1, "alias": "__result_1", "series": []}
        )
        rows = _unwrap_time_series(resp)
        assert len(rows) == 2
        assert {r["series"] for r in rows} == {"k=a", "__result_0"}

    def test_garbage_passthrough(self):
        assert _unwrap_time_series({}) == []
        assert _unwrap_time_series("x") == []

    def test_warn_v5_prints_to_stderr(self, capsys):
        resp = _ts_response([], warnings=[{"message": "no data found for the metric m"}])
        _warn_v5(resp)
        err = capsys.readouterr().err
        assert "no data found for the metric m" in err

    def test_warn_v5_silent_when_none(self, capsys):
        _warn_v5(_ts_response([]))
        assert capsys.readouterr().err == ""
