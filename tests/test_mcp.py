"""Tests for the MCP server (tools, mutation gating, stdio protocol)."""

import json as _json

import pytest

import signoz_cli.commands.mcp_server as mcp_server

pytest.importorskip("mcp")


@pytest.fixture(autouse=True)
def _reset_server_state(monkeypatch):
    monkeypatch.setattr(mcp_server, "_ALLOW_MUTATIONS", False)
    monkeypatch.setattr(mcp_server, "_DEFAULT_PROFILE", None)
    monkeypatch.setattr(mcp_server, "_DEFAULT_ENDPOINT", None)
    # Tool functions resolve auth like the CLI; pin dummy credentials so
    # tests never depend on ambient env or a real ~/.signoz config.
    monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
    monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
    monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)


@pytest.fixture()
def fake_client(monkeypatch):
    import signoz_cli.commands.common as cm

    calls = {}

    class Fake:
        endpoint = "http://sig.test:8080"

        def __init__(self, *a, **k):
            calls["init"] = (a, k)

        def close(self):
            pass

        def query_builder_v5(self, payload):
            calls["payload"] = payload
            return {
                "status": "success",
                "data": {
                    "type": "raw",
                    "data": {"results": [{"rows": [{"data": {"a": 1}}]}]},
                },
            }

        def list_dashboards_v2(self, limit=None, offset=None, query=None):
            return {"status": "success",
                    "data": {"dashboards": [{"id": "d1", "name": "D",
                                             "spec": {"display": {"name": "D"}}}]},
                             "total": 1}

        def delete_dashboard_v2(self, dashboard_id):
            calls["deleted"] = dashboard_id
            return {"status": "ok"}

        def verify(self):
            return {"status": "success", "data": {"name": "svc", "id": "1"}}

        def get_server_version(self):
            return {"version": "v0.138.0"}

    monkeypatch.setattr(cm, "SigNozClient", Fake)
    return calls


class TestToolInventory:
    def test_expected_tool_count(self):
        assert len(mcp_server._READ_TOOLS) == 19
        assert len(mcp_server._MUTATION_TOOLS) == 10

    def test_no_token_in_any_schema(self):
        import asyncio as _asyncio

        async def _collect():
            server = mcp_server.build_server()
            return await server.list_tools()

        for tool in _asyncio.run(_collect()):
            props = tool.input_schema.get("properties", {})
            assert "token" not in props, tool.name

    def test_server_builds(self):
        server = mcp_server.build_server()
        assert server is not None


class TestReadTools:
    def test_logs_search_returns_rows(self, fake_client):
        rows = mcp_server.logs_search(service="svc", time_range="1h")
        assert rows == [{"a": 1}]
        assert fake_client["payload"]["compositeQuery"]

    def test_step_validation_is_client_facing(self, fake_client):
        from mcp.server.mcpserver.exceptions import ToolError

        with pytest.raises(ToolError, match="positive"):
            mcp_server.metrics_query("m", step=0)
        with pytest.raises(ToolError, match="positive"):
            mcp_server.promql_query("up", step=-1)

    def test_server_status_shape(self, fake_client):
        status = mcp_server.server_status()
        assert status["server_version"] == "v0.138.0"
        assert status["compatible"] is True
        assert status["account"] == "svc"

    def test_dashboards_list_summarized(self, fake_client):
        out = mcp_server.dashboards_list()
        assert out["data"][0]["name"] == "D"
        assert out["pagination"]["total"] == 1


class TestMutationGating:
    def test_delete_refused_by_default(self, fake_client):
        from mcp.server.mcpserver.exceptions import ToolError

        with pytest.raises(ToolError, match="--allow-mutations"):
            mcp_server.dashboards_delete("d1")
        assert "deleted" not in fake_client

    def test_delete_allowed_with_flag(self, fake_client, monkeypatch):
        monkeypatch.setattr(mcp_server, "_ALLOW_MUTATIONS", True)
        out = mcp_server.dashboards_delete("d1")
        assert out == {"status": "ok"}
        assert fake_client["deleted"] == "d1"


class TestStdioProtocol:
    @staticmethod
    def _signoz_bin():
        """Locate the installed signoz entry point (PATH, venv, repo-local)."""
        import os as _os
        import shutil as _shutil
        import sys as _sys

        found = _shutil.which("signoz")
        if found:
            return found
        for candidate in (
            _os.path.join(_sys.prefix, "bin", "signoz"),
            _os.path.join(".venv", "bin", "signoz"),
        ):
            if _os.path.isfile(candidate) and _os.access(candidate, _os.X_OK):
                return candidate
        return None

    def test_initialize_and_list_tools(self):
        import subprocess as _sp

        import signoz_cli.commands.mcp_server as _m

        assert _m.build_server is not None
        binary = self._signoz_bin()
        if binary is None:
            pytest.skip("signoz entry point not installed")
        proc = _sp.Popen(
            [binary, "mcp"],
            stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.DEVNULL,
            text=True, bufsize=1,
        )
        try:
            def _rpc(id_, method, params=None):
                proc.stdin.write(_json.dumps(
                    {"jsonrpc": "2.0", "id": id_, "method": method,
                     "params": params or {}}) + "\n")
                proc.stdin.flush()
                return _json.loads(proc.stdout.readline())

            init = _rpc(1, "initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"}})
            assert "result" in init, init
            proc.stdin.write(_json.dumps(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.flush()
            listed = _rpc(2, "tools/list")
            names = {t["name"] for t in listed["result"]["tools"]}
            assert "logs_search" in names
            assert "dashboards_delete" in names
            assert len(names) == 29
            # Mutation refusal arrives without any connection made: v2
            # reports tool failures as result.isError (not top-level error).
            refused = _rpc(3, "tools/call", {
                "name": "dashboards_delete",
                "arguments": {"dashboard_id": "d1"}})
            assert refused.get("result", {}).get("isError") is True, refused
            assert "--allow-mutations" in str(refused)
        finally:
            proc.kill()
            proc.wait(timeout=10)
