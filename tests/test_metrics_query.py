"""Tests for metrics/promql/sql payloads and time-series unwrapping."""

import pytest
from click.testing import CliRunner

from signoz_cli.main import _unwrap_time_series, _warn_v5
from signoz_cli.query import (
    build_metrics_query_payload,
    build_promql_query_payload,
    build_sql_query_payload,
)


@pytest.fixture()
def runner():
    return CliRunner()


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


class _MetaFake:
    """Fake client serving metric metadata; records whether it was consulted."""

    def __init__(self, entries=None, fail=False):
        self.entries = entries or []
        self.fail = fail
        self.consulted = False

    def search_metric_by_text(self, text):
        self.consulted = True
        if self.fail:
            raise RuntimeError("metadata backend down")
        return {"status": "success",
                "data": {"attributeKeys": self.entries}}


class TestResolveMetricTemporality:
    def _resolve(self, *args, **kwargs):
        from signoz_cli.commands.metrics import _resolve_metric_temporality
        return _resolve_metric_temporality(*args, **kwargs)

    def test_explicit_temporality_honoured_without_lookup(self):
        c = _MetaFake([{"key": "m", "type": "Sum"}])
        assert self._resolve(c, "m", "avg", "Delta", "avg") == ("avg", "Delta")
        assert not c.consulted

    def test_rate_sum_imply_cumulative_without_lookup(self):
        c = _MetaFake()
        assert self._resolve(c, "m", "rate", None, "avg") == ("rate", "Cumulative")
        assert self._resolve(c, "m", "sum", None, "max") == ("sum", "Cumulative")
        assert not c.consulted

    def test_sum_with_default_agg_upgraded(self):
        c = _MetaFake([{"key": "m", "type": "Sum"}])
        assert self._resolve(c, "m", "avg", None, "avg") == ("sum", "Cumulative")
        assert self._resolve(c, "m", "max", None, "max") == ("sum", "Cumulative")

    def test_gauge_keeps_defaults(self):
        c = _MetaFake([{"key": "m", "type": "Gauge"}])
        assert self._resolve(c, "m", "avg", None, "avg") == ("avg", "Unspecified")

    def test_unknown_metric_keeps_defaults(self):
        c = _MetaFake([{"key": "other", "type": "Sum"}])
        assert self._resolve(c, "m", "avg", None, "avg") == ("avg", "Unspecified")

    def test_metadata_failure_keeps_defaults(self):
        c = _MetaFake(fail=True)
        assert self._resolve(c, "m", "avg", None, "avg") == ("avg", "Unspecified")

    def test_explicit_nondefault_agg_kept_for_sum(self):
        c = _MetaFake([{"key": "m", "type": "Sum"}])
        assert self._resolve(c, "m", "p95", None, "avg") == ("p95", "Unspecified")


class TestCardinality:
    def _fake(self, monkeypatch):
        import signoz_cli.commands.common as cm

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                groups = payload["compositeQuery"]["queries"][0][
                    "spec"]["groupBy"]
                label = groups[0]["name"] if groups else "?"
                return {"status": "success", "data": {
                    "type": "time_series",
                    "data": {"results": [{"aggregations": [{"series": [
                        {"labels": [{"key": "k", "value": label + "=a"}],
                         "values": [{"timestamp": 1, "value": 5.0}]},
                        {"labels": [{"key": "k", "value": label + "=b"}],
                         "values": [{"timestamp": 1, "value": 2.0}]},
                    ]}]}]}}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)

    def test_groups_and_top(self, runner, monkeypatch):
        from signoz_cli.main import cli

        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
        monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
        monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)
        self._fake(monkeypatch)
        result = runner.invoke(
            cli, ["metrics", "cardinality", "m", "--by", "service.name"])
        assert result.exit_code == 0, result.output
        assert "service.name" in result.output
        assert "signoz-cli-e2e" not in result.output
        assert "2" in result.output  # groups count

    def test_top_truncates(self, runner, monkeypatch):
        from signoz_cli.main import cli

        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
        monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
        monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)
        self._fake(monkeypatch)
        result = runner.invoke(
            cli, ["metrics", "cardinality", "m", "--by", "a,b", "--top", "1",
                  "--json"])
        import json as _json

        rows = _json.loads(result.stdout)
        assert len(rows) == 2
        assert all(r["group"].endswith("=a") for r in rows)

    def test_bad_flags_rejected(self, runner):
        from signoz_cli.main import cli

        for args in [["metrics", "cardinality", "m", "--by", "x",
                      "--top", "0"],
                     ["metrics", "cardinality", "m", "--by", "  "]]:
            result = runner.invoke(cli, args)
            assert result.exit_code == 2, args


class TestAnomalies:
    def _rows(self):
        steady = [{"series": "a", "timestamp": i, "value": 10.0 + (i % 3) * 0.5}
                  for i in range(10)]
        steady.append({"series": "a", "timestamp": 10, "value": 100.0})
        return steady

    def test_spike_detected(self):
        from signoz_cli.commands.common import _detect_anomalies

        out = _detect_anomalies(self._rows())
        assert len(out) == 1
        assert out[0]["timestamp"] == 10
        assert out[0]["zscore"] > 3.0

    def test_flat_series_clean(self):
        from signoz_cli.commands.common import _detect_anomalies

        rows = [{"series": "a", "timestamp": i, "value": 5.0}
                for i in range(12)]
        assert _detect_anomalies(rows) == []

    def test_threshold_tunable(self):
        from signoz_cli.commands.common import _detect_anomalies

        assert _detect_anomalies(self._rows(), threshold=1000.0) == []
        flagged = _detect_anomalies(self._rows(), threshold=2.0)
        assert len(flagged) == 1
        assert flagged[0]["timestamp"] == 10

    def test_flag_validation(self, runner):
        from signoz_cli.main import cli

        for args in [["metrics", "query", "m", "--anomalies",
                      "--anomaly-window", "2"],
                     ["metrics", "query", "m", "--anomalies",
                      "--anomaly-threshold", "0"]]:
            result = runner.invoke(cli, args)
            assert result.exit_code == 2, args

    def test_flag_end_to_end(self, runner, monkeypatch):
        import signoz_cli.commands.common as cm
        from signoz_cli.main import cli

        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
        monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
        monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                return {"status": "success", "data": {
                    "type": "time_series",
                    "data": {"results": [{"aggregations": [{"series": [{
                        "labels": [],
                        "values": [{"timestamp": i,
                                    "value": 10.0 + (i % 3) * 0.5}
                                   for i in range(10)]
                        + [{"timestamp": 10, "value": 100.0}]}]}]}]}}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(
            cli, ["metrics", "query", "m", "--time-aggregation", "sum",
                  "--anomalies"])
        assert result.exit_code == 0, result.output
        assert "zscore" in result.output
