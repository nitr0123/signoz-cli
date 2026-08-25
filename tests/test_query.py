"""Tests for Query Builder v5 payload constructors."""

from signoz_cli.query import (
    build_aggregate_query_payload,
    build_logs_query_payload,
    build_traces_query_payload,
)


class TestLogsPayload:
    def test_shape(self):
        p = build_logs_query_payload(1000, 2000, "service.name = 'x'", limit=10, offset=5)
        assert p["start"] == 1000
        assert p["end"] == 2000
        assert p["requestType"] == "raw"
        q = p["compositeQuery"]["queries"][0]
        assert q["type"] == "builder_query"
        spec = q["spec"]
        assert spec["signal"] == "logs"
        assert spec["filter"]["expression"] == "service.name = 'x'"
        assert spec["limit"] == 10
        assert spec["offset"] == 5
        names = [f["name"] for f in spec["selectFields"]]
        assert {"timestamp", "severity_text", "body", "service.name"} <= set(names)

    def test_defaults(self):
        p = build_logs_query_payload(1, 2, "")
        spec = p["compositeQuery"]["queries"][0]["spec"]
        assert spec["limit"] == 100
        assert spec["offset"] == 0


class TestTracesPayload:
    def test_shape(self):
        p = build_traces_query_payload(1, 2, "hasError = true", limit=7)
        spec = p["compositeQuery"]["queries"][0]["spec"]
        assert spec["signal"] == "traces"
        assert spec["filter"]["expression"] == "hasError = true"
        assert spec["limit"] == 7
        names = [f["name"] for f in spec["selectFields"]]
        assert {"traceID", "spanID", "durationNano", "hasError"} <= set(names)


class TestAggregatePayload:
    def test_shape(self):
        group_by = [{"name": "service.name", "signal": "traces"}]
        p = build_aggregate_query_payload(
            "traces", 1, 2, "count()", "hasError = true",
            group_by, "count()", "desc", 5,
        )
        assert p["requestType"] == "scalar"
        spec = p["compositeQuery"]["queries"][0]["spec"]
        assert spec["aggregations"] == [{"expression": "count()"}]
        assert spec["groupBy"] == group_by
        assert spec["order"] == [{"key": {"name": "count()"}, "direction": "desc"}]
        assert spec["limit"] == 5

    def test_no_group_by(self):
        p = build_aggregate_query_payload(
            "logs", 1, 2, "count()", "", [], "count()", "asc", 3,
        )
        spec = p["compositeQuery"]["queries"][0]["spec"]
        assert spec["groupBy"] == []
