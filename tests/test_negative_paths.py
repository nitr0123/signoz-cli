"""Negative-path CLI tests: errors surface cleanly, dry-runs touch nothing.

The Fake client raises instead of returning, exercising ``_handle_error``
end to end through real CLI invocations (no live server needed).
"""

import httpx
import pytest
from click.testing import CliRunner

import signoz_cli.commands.common as cm
from signoz_cli.client import SigNozApiError
from signoz_cli.main import cli


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".signoz"
    monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", cfg_dir / "config.json")
    monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
    monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
    monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)


@pytest.fixture()
def runner():
    return CliRunner()


def _raise_client(monkeypatch, exc):
    class Fake:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)

            def _boom(*a, **k):
                raise exc

            return _boom

    monkeypatch.setattr(cm, "SigNozClient", Fake)


class TestApiErrors:
    def test_401_surfaces_status(self, runner, monkeypatch):
        _raise_client(monkeypatch, SigNozApiError(401, "unauthorized: bad key"))
        result = runner.invoke(cli, ["traces", "search"])
        assert result.exit_code == 1
        assert "401" in result.output

    def test_403_surfaces_status(self, runner, monkeypatch):
        _raise_client(monkeypatch, SigNozApiError(403, "authz_forbidden"))
        result = runner.invoke(cli, ["logs", "search"])
        assert result.exit_code == 1
        assert "403" in result.output

    def test_500_surfaces_status(self, runner, monkeypatch):
        _raise_client(monkeypatch, SigNozApiError(500, "boom"))
        result = runner.invoke(cli, ["metrics", "query", "cpu.usage"])
        assert result.exit_code == 1
        assert "500" in result.output

    def test_connect_error_hints_at_endpoint(self, runner, monkeypatch):
        _raise_client(monkeypatch, httpx.ConnectError("refused"))
        result = runner.invoke(cli, ["dashboards", "list"])
        assert result.exit_code == 1
        assert "cannot connect" in result.output

    def test_timeout_suggests_flags(self, runner, monkeypatch):
        _raise_client(monkeypatch, httpx.TimeoutException("slow"))
        result = runner.invoke(cli, ["traces", "search"])
        assert result.exit_code == 1
        assert "--timeout" in result.output

    def test_unexpected_error_still_exits_1(self, runner, monkeypatch):
        _raise_client(monkeypatch, ValueError("weird"))
        result = runner.invoke(cli, ["services", "list"])
        assert result.exit_code == 1
        assert "weird" in result.output


class TestRetryIntegration:
    def test_429_then_success(self, runner, monkeypatch):
        """Real client over a flapping transport: CLI succeeds after retry."""
        from signoz_cli.client import SigNozClient

        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(429, json={"message": "slow down"})
            return httpx.Response(200, json={"status": "success", "data": []})

        real = SigNozClient

        def factory(endpoint, token, **kwargs):
            return real(
                endpoint, token,
                transport=httpx.MockTransport(handler), max_retries=5,
            )

        monkeypatch.setattr(cm, "SigNozClient", factory)
        result = runner.invoke(cli, ["alerts", "list", "--json"])
        assert result.exit_code == 0, result.output
        assert calls["n"] == 3

    def test_500_exhausts_retries(self, runner, monkeypatch):
        from signoz_cli.client import SigNozClient

        real = SigNozClient

        def factory(endpoint, token, **kwargs):
            def handler(request):
                return httpx.Response(500, json={"message": "down"})

            return real(
                endpoint, token,
                transport=httpx.MockTransport(handler), max_retries=2,
            )

        monkeypatch.setattr(cm, "SigNozClient", factory)
        result = runner.invoke(cli, ["alerts", "list"])
        assert result.exit_code == 1
        assert "500" in result.output


class TestDryRunDeletes:
    """Every destructive delete supports --dry-run that performs no API call."""

    CASES = [
        (["dashboards", "delete", "dash-1"], "dashboard"),
        (["alerts", "delete", "rule-1"], "alert rule"),
        (["channels", "delete", "ch-1"], "channel"),
        (["views", "delete", "view-1"], "saved view"),
        (["service-accounts", "delete", "sa-1"], "service account"),
    ]

    @pytest.mark.parametrize(
        "argv,resource",
        CASES,
        ids=[c[1] for c in CASES],
    )
    def test_dry_run_calls_nothing(self, runner, monkeypatch, argv, resource):
        import json

        called = {}

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def __getattr__(self, name):
                if name.startswith("__"):
                    raise AttributeError(name)

                def _recorder(*a, **k):
                    called[name] = True
                    return {"status": "ok"}

                return _recorder

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, [*argv, "--dry-run", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["action"] == "delete"
        assert payload["resource"] == resource
        assert called == {}, f"dry-run performed API calls: {called}"

    def test_no_input_without_yes_fails_with_exit_2(self, runner, monkeypatch):
        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def delete_dashboard_v2(self, dashboard_id):
                raise AssertionError("must not be called")

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(
            cli, ["dashboards", "delete", "abc", "--no-input", "--json"]
        )
        assert result.exit_code == 2
        assert "--yes" in result.output
