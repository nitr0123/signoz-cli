"""Tests for v5/history response unwrapping helpers."""

from signoz_cli.main import (
    _stringify_labels,
    _unwrap_history_timeline,
    _unwrap_v5,
)


class TestUnwrapV5:
    def test_raw_with_envelope(self):
        data = {
            "status": "success",
            "data": {
                "type": "raw",
                "data": {
                    "results": [
                        {
                            "queryName": "A",
                            "rows": [{"data": {"body": "hi"}, "timestamp": 1}],
                            "nextCursor": "",
                        }
                    ]
                },
            },
        }
        rows = _unwrap_v5(data)
        assert rows == [{"body": "hi"}]

    def test_raw_keeps_next_cursor(self):
        data = {
            "data": {
                "type": "raw",
                "data": {
                    "results": [
                        {
                            "rows": [{"data": {"a": 1}}],
                            "nextCursor": "CUR",
                        }
                    ]
                },
            }
        }
        rows = _unwrap_v5(data)
        assert rows == [{"a": 1, "nextCursor": "CUR"}]

    def test_rows_none_is_tolerated(self):
        data = {"data": {"type": "raw", "data": {"results": [{"rows": None}]}}}
        assert _unwrap_v5(data) == []

    def test_scalar_positional(self):
        data = {
            "data": {
                "type": "scalar",
                "data": {
                    "results": [
                        {
                            "columns": [
                                {"name": "service.name"},
                                {"name": "__result_0"},
                            ],
                            "data": [["svc", 10]],
                        }
                    ]
                },
            }
        }
        rows = _unwrap_v5(data, agg_label="count()")
        assert rows == [{"service.name": "svc", "count()": 10}]

    def test_passthrough_other_types(self):
        ts = {"type": "time_series"}
        assert _unwrap_v5(ts) is ts
        assert _unwrap_v5("junk") == "junk"


class TestHistoryTimeline:
    def test_flatten(self):
        data = {
            "status": "success",
            "data": {
                "items": [
                    {
                        "ruleId": "r1",
                        "state": "firing",
                        "labels": [
                            {"key": {"name": "severity"}, "value": "critical"},
                            "rawlabel",
                        ],
                        "relatedLogsLink": "long-url",
                    }
                ],
                "total": 1,
                "nextCursor": "C2",
            },
        }
        out = _unwrap_history_timeline(data)
        assert out["total"] == 1
        assert out["nextCursor"] == "C2"
        row = out["data"][0]
        assert row["labels"] == ["severity=critical", "rawlabel"]
        assert "relatedLogsLink" not in row

    def test_non_dict(self):
        assert _unwrap_history_timeline("x") == "x"


class TestStringifyLabels:
    def test_top_level_list(self):
        rows = [{"fingerprint": 1, "labels": [{"key": {"name": "k"}, "value": "v"}]}]
        out = _stringify_labels(rows)
        assert out[0]["labels"] == ["k=v"]

    def test_wrapped_in_data(self):
        data = {"data": [{"labels": []}]}
        assert _stringify_labels(data) == [{"labels": []}]
