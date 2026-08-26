"""Tests for filter builders and dashboard summary helpers in main.py."""

import pytest

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

    def test_apostrophe_in_value_is_escaped(self):
        # A value with a quote must not break out of the string literal.
        f = _build_log_filter(None, "it's-svc", None, None)
        assert f == r"service.name = 'it\'s-svc'"

    def test_backslash_in_value_is_escaped(self):
        f = _build_log_filter(None, None, None, r"a\b")
        assert f == r"body CONTAINS 'a\\b'"

    def test_query_passthrough_not_escaped(self):
        # --query is a whole expression the user wrote; leave it verbatim.
        assert _build_log_filter("service.name = 'x'", None, None, None) == "service.name = 'x'"


class TestTraceFilter:
    def test_error_true(self):
        f = _build_trace_filter(None, "checkout", "/pay", "true")
        assert f == "service.name = 'checkout' AND name = '/pay' AND hasError = true"

    def test_error_false(self):
        f = _build_trace_filter(None, None, None, "false")
        assert f == "hasError = false"

    def test_query_passthrough(self):
        assert _build_trace_filter("durationNano > 5", None, None, None) == "durationNano > 5"

    def test_operation_apostrophe_escaped(self):
        f = _build_trace_filter(None, None, "GET /o'brien", None)
        assert f == r"name = 'GET /o\'brien'"


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


class TestApplyJsonPatch:
    def _patch(self):
        from signoz_cli.main import _apply_json_patch
        return _apply_json_patch

    def test_add_and_replace_nested(self):
        f = self._patch()
        obj = {"a": {"b": 1}, "disabled": False}
        out = f(obj, [
            {"op": "replace", "path": "/disabled", "value": True},
            {"op": "add", "path": "/a/c", "value": 2},
        ])
        assert out["disabled"] is True
        assert out["a"]["c"] == 2
        assert obj["disabled"] is False  # original untouched

    def test_remove(self):
        f = self._patch()
        out = f({"a": 1, "b": 2}, [{"op": "remove", "path": "/b"}])
        assert out == {"a": 1}

    def test_array_append_with_dash(self):
        f = self._patch()
        out = f({"tags": [1]}, [{"op": "add", "path": "/tags/-", "value": 2}])
        assert out["tags"] == [1, 2]

    def test_rejects_unknown_op(self):
        import click
        with pytest.raises(click.UsageError):
            self._patch()({}, [{"op": "move", "path": "/a", "from": "/b"}])

    def test_replace_through_list_index(self):
        # Traversing *into* a list element by a numeric index must work; the
        # intermediate segment used to be indexed with a string and raised
        # "list indices must be integers".
        f = self._patch()
        obj = {"widgets": [{"title": "old"}, {"title": "keep"}]}
        out = f(obj, [{"op": "replace", "path": "/widgets/0/title", "value": "new"}])
        assert out["widgets"][0]["title"] == "new"
        assert out["widgets"][1]["title"] == "keep"
