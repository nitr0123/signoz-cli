"""Tests for filter builders and dashboard summary helpers in main.py."""

from signoz_cli.main import (
    _build_log_filter,
    _build_trace_filter,
    _dashboard_summary,
    _parse_group_by,
    _parse_order_by,
)


class TestLogFilter:
    def test_all_parts(self):
        f = _build_log_filter("a = 1", "svc", "ERROR", "timeout")
        assert f == "a = 1 AND service.name = 'svc' AND severity_text = 'ERROR' AND body CONTAINS 'timeout'"

    def test_empty(self):
        assert _build_log_filter(None, None, None, None) == ""

    def test_service_only(self):
        assert _build_log_filter(None, "api", None, None) == "service.name = 'api'"


class TestTraceFilter:
    def test_error_true(self):
        f = _build_trace_filter(None, "checkout", "/pay", "true")
        assert f == "service.name = 'checkout' AND name = '/pay' AND hasError = true"

    def test_error_false(self):
        f = _build_trace_filter(None, None, None, "false")
        assert f == "hasError = false"

    def test_query_passthrough(self):
        assert _build_trace_filter("durationNano > 5", None, None, None) == "durationNano > 5"


class TestOrderBy:
    def test_default(self):
        assert _parse_order_by(None, "count()") == ("count()", "desc")

    def test_asc(self):
        assert _parse_order_by("avg(x) asc", "count()") == ("avg(x)", "asc")

    def test_desc(self):
        assert _parse_order_by("avg(x) desc", "count()") == ("avg(x)", "desc")

    def test_no_direction(self):
        assert _parse_order_by("p99(durationNano)", "count()") == ("p99(durationNano)", "desc")


class TestGroupBy:
    def test_multi(self):
        assert _parse_group_by("service.name, severity_text", "logs") == [
            {"name": "service.name", "signal": "logs"},
            {"name": "severity_text", "signal": "logs"},
        ]

    def test_none(self):
        assert _parse_group_by(None, "traces") == []


class TestDashboardSummary:
    def test_v2_shape(self):
        d = {
            "id": "abc-123",
            "tags": [{"key": "env", "value": "prod"}],
            "spec": {
                "display": {"name": "My Dashboard", "description": "d"},
                "layouts": [],
                "panels": {},
            },
            "createdAt": "2026-08-01T00:00:00Z",
        }
        s = _dashboard_summary(d)
        assert s["id"] == "abc-123"
        assert s["name"] == "My Dashboard"
        assert s["tags"] == ["env=prod"]
        assert s["createdAt"] == "2026-08-01T00:00:00Z"

    def test_string_tags(self):
        d = {"id": "x", "tags": ["a"], "spec": {"display": {}}}
        assert _dashboard_summary(d)["tags"] == ["a"]

    def test_non_dict(self):
        assert _dashboard_summary("nope") == {}
