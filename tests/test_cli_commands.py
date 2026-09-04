"""End-to-end CLI tests for query commands (client mocked; no server)."""

import json

import pytest
from click.testing import CliRunner

import signoz_cli.commands.common as cm
from signoz_cli.client import SigNozApiError
from signoz_cli.main import cli


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".signoz"
    monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", cfg_dir / "config.json")
    monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
    monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
    monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)
    return cfg_dir


@pytest.fixture()
def runner():
    return CliRunner()


def _install_client(monkeypatch, **behaviour):
    """Patch the client constructor where it is looked up (commands.common).

    ``behaviour`` maps method name -> value (or an Exception instance to raise).
    """
    class Fake:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    for name, value in behaviour.items():
        def _make(v):
            def _method(self, *a, **k):
                if isinstance(v, Exception):
                    raise v
                return v
            return _method
        setattr(Fake, name, _make(value))

    monkeypatch.setattr(cm, "SigNozClient", Fake)
    return Fake


def _raw_page(rows):
    return {"status": "success", "data": {"type": "raw", "data": {"results": [
        {"queryName": "A", "rows": [{"data": r} for r in rows]}]}}}


def _ts_response(series):
    return {"status": "success", "data": {"type": "time_series",
            "meta": {"warning": {"warnings": []}},
            "data": {"results": [{"queryName": "A", "aggregations": [
                {"index": 0, "alias": "__result_0", "series": series}]}]}}}


LOG_ROWS = [
    {"timestamp": "2026-08-26T00:00:01Z", "severity_text": "ERROR",
     "service.name": "api", "body": "boom one"},
    {"timestamp": "2026-08-26T00:00:00Z", "severity_text": "INFO",
     "service.name": "api", "body": "hello two"},
]


class TestLogsSearch:
    def test_json_output(self, runner, monkeypatch):
        _install_client(monkeypatch, query_builder_v5=_raw_page(LOG_ROWS))
        result = runner.invoke(cli, ["logs", "search", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert [r["body"] for r in data] == ["boom one", "hello two"]

    def test_table_output(self, runner, monkeypatch):
        _install_client(monkeypatch, query_builder_v5=_raw_page(LOG_ROWS))
        result = runner.invoke(cli, ["logs", "search"])
        assert result.exit_code == 0, result.output
        assert "boom one" in result.output and "hello two" in result.output

    def test_csv_output(self, runner, monkeypatch):
        _install_client(monkeypatch, query_builder_v5=_raw_page(LOG_ROWS))
        result = runner.invoke(cli, ["logs", "search", "--csv"])
        assert result.exit_code == 0, result.output
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        assert lines[0].startswith("timestamp,severity_text")
        assert any("boom one" in ln for ln in lines)

    def test_output_file(self, runner, monkeypatch, tmp_path):
        _install_client(monkeypatch, query_builder_v5=_raw_page(LOG_ROWS))
        out = tmp_path / "logs.json"
        result = runner.invoke(cli, ["logs", "search", "--json", "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert "Output written to" in result.output
        assert [r["body"] for r in json.loads(out.read_text())] == ["boom one", "hello two"]

    def test_api_error_exits_nonzero(self, runner, monkeypatch):
        _install_client(monkeypatch,
                        query_builder_v5=SigNozApiError(403, "authz_forbidden: nope"))
        result = runner.invoke(cli, ["logs", "search"])
        assert result.exit_code == 1
        assert "Error:" in result.output and "403" in result.output


class TestTracesSearch:
    def test_table_output(self, runner, monkeypatch):
        rows = [{"traceID": "abc", "service.name": "api", "name": "GET /x",
                 "durationNano": 1234, "hasError": False, "timestamp": "t"}]
        _install_client(monkeypatch, query_builder_v5=_raw_page(rows))
        result = runner.invoke(cli, ["traces", "search", "--service", "api"])
        assert result.exit_code == 0, result.output
        assert "abc" in result.output and "GET /x" in result.output


class TestMetricsQuery:
    def test_time_series_rows(self, runner, monkeypatch):
        resp = _ts_response([
            {"labels": [{"key": "host.name", "value": "h1"}],
             "values": [{"timestamp": 1, "value": 2.5}]}])
        _install_client(monkeypatch, query_builder_v5=resp)
        result = runner.invoke(cli, ["metrics", "query", "cpu.usage", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data == [{"series": "host.name=h1", "timestamp": 1, "value": 2.5}]


class TestLogsTailDedup:
    def test_does_not_reprint_overlapping_rows(self, runner, monkeypatch):
        # Every poll returns the same window of rows; each line must print once.
        _install_client(monkeypatch, query_builder_v5=_raw_page(LOG_ROWS))

        import time as _t
        calls = {"n": 0}

        def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] >= 2:  # allow two polls, then stop
                raise KeyboardInterrupt
        monkeypatch.setattr(_t, "sleep", fake_sleep)

        result = runner.invoke(cli, ["logs", "tail", "--interval", "0.01"])
        assert result.exit_code == 0, result.output
        # Two polls over identical data, but each body appears exactly once.
        assert result.output.count("boom one") == 1
        assert result.output.count("hello two") == 1
