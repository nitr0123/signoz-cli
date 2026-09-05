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


class TestEmptyResults:
    def test_metrics_query_empty_warns_on_stderr(self, runner, monkeypatch):

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                return {"status": "success", "data": {"type": "time_series",
                        "data": {"results": [{"queryName": "A",
                                             "aggregations": None}]}}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["metrics", "query", "some.counter"])
        assert result.exit_code == 0, result.output
        assert "Warning: no points" in result.output

    def test_metrics_stats_empty_warns_on_stderr(self, runner, monkeypatch):
        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                return {"status": "success", "data": {"type": "scalar",
                        "data": {"results": [{"queryName": "A",
                                             "columns": None, "data": []}]}}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["metrics", "stats", "some.counter"])
        assert result.exit_code == 0, result.output
        assert "Warning: no points" in result.output

    def test_alerts_history_empty_says_no_results(self, runner, monkeypatch):
        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def get_alert_history_timeline(self, *a, **k):
                return {"status": "success",
                        "data": {"items": [], "total": 0}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["alerts", "history", "not-a-uuid"])
        assert result.exit_code == 0, result.output
        assert "no results" in result.output


class TestTailGuards:
    def test_unbounded_watch_helper_stays_removed(self):
        import signoz_cli.commands.common as common_mod

        assert not hasattr(common_mod, "_watch_loop")

    def test_interval_zero_rejected(self, runner, monkeypatch):
        result = runner.invoke(cli, ["logs", "tail", "--interval", "0"])
        assert result.exit_code == 2
        assert "--interval must be positive" in result.output

    def test_interval_negative_rejected(self, runner, monkeypatch):
        result = runner.invoke(cli, ["logs", "tail", "--interval", "-5"])
        assert result.exit_code == 2
        assert "--interval must be positive" in result.output

    def test_tail_auth_failure_exits_instead_of_spewing(self, runner, monkeypatch):
        from signoz_cli.client import SigNozApiError

        polls = {"n": 0}

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                polls["n"] += 1
                raise SigNozApiError(401, "unauthorized: bad key")

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["logs", "tail", "--service", "x"])
        assert result.exit_code == 1
        assert polls["n"] == 1, f"polled {polls['n']} times instead of failing fast"
        assert "401" in result.output

    def test_tail_429_keeps_retrying(self, runner, monkeypatch):
        from signoz_cli.client import SigNozApiError

        polls = {"n": 0}

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                polls["n"] += 1
                raise SigNozApiError(429, "slow down")

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        # Allow exactly one sleep, then stop the loop via KeyboardInterrupt.
        # (logs_tail imports time inside the function, so patch the
        # attribute on the real time module.)
        import time as _time_mod

        stopper = _StopAfterSleeps(1)
        monkeypatch.setattr(_time_mod, "sleep", stopper.sleep)
        result = runner.invoke(cli, ["logs", "tail", "--service", "x",
                                     "--interval", "0.01"])
        assert result.exit_code == 0, result.output
        assert polls["n"] == 2, "429 must be retried, not fatal"


class _StopTail(KeyboardInterrupt):
    pass


class _StopAfterSleeps:
    """sleep() replacement: allows N sleeps, then stops the tail loop."""

    def __init__(self, allowed_sleeps: int):
        self.allowed_sleeps = allowed_sleeps
        self.sleeps = 0

    def sleep(self, s):
        if self.sleeps >= self.allowed_sleeps:
            raise _StopTail()
        self.sleeps += 1


class TestStepValidation:
    @pytest.mark.parametrize("argv", [
        ["metrics", "query", "m", "--step", "0"],
        ["metrics", "query", "m", "--step", "-5"],
        ["metrics", "stats", "m", "--step", "0"],
        ["promql", "sum(x)", "--step", "-1"],
    ])
    def test_nonpositive_step_rejected(self, runner, argv):
        result = runner.invoke(cli, argv)
        assert result.exit_code == 2, result.output
        assert "--step must be positive" in result.output


class TestTimeoutValidation:
    def test_env_timeout_garbage_rejected(self, runner, monkeypatch):
        monkeypatch.setenv("SIGNOZ_TIMEOUT", "soon")
        result = runner.invoke(cli, ["services", "list"])
        assert result.exit_code == 2, result.output
        assert "SIGNOZ_TIMEOUT" in result.output

    def test_env_timeout_zero_rejected(self, runner, monkeypatch):
        monkeypatch.setenv("SIGNOZ_TIMEOUT", "0")
        result = runner.invoke(cli, ["services", "list"])
        assert result.exit_code == 2, result.output
        assert "must be positive" in result.output

    def test_flag_timeout_zero_rejected(self, runner):
        result = runner.invoke(cli, ["services", "list", "--timeout", "0"])
        assert result.exit_code == 2, result.output
        assert "must be positive" in result.output

    def test_profile_timeout_garbage_rejected(self, runner, monkeypatch):
        from signoz_cli.config import save_config

        save_config({"profiles": {"t": {
            "endpoint": "http://t:1", "token": "tok", "timeout": "soon",
        }}})
        result = runner.invoke(cli, ["services", "list", "-p", "t"])
        assert result.exit_code == 2, result.output
        assert "profile 't'" in result.output


class TestAlertsPatchDirect:
    def test_patch_goes_to_patch_endpoint(self, runner, monkeypatch, tmp_path):
        calls = {}

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def get_alert(self, rule_id):
                calls["get"] = True
                raise AssertionError("read-modify-write GET must not happen")

            def patch_rule(self, rule_id, patches):
                calls["patch"] = (rule_id, patches)
                return {"status": "success", "data": {"id": rule_id}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        import json as _json

        ops = [{"op": "replace", "path": "/name", "value": "new"}]
        pf = tmp_path / "patch.json"
        pf.write_text(_json.dumps(ops))
        result = runner.invoke(cli, ["alerts", "patch", "r1", str(pf), "--json"])
        assert result.exit_code == 0, result.output
        assert calls.get("patch") == ("r1", ops)
        assert "get" not in calls


class TestConfigureValidation:
    def test_bad_default_time_range_rejected(self, runner):
        result = runner.invoke(cli, [
            "configure", "-p", "x", "--endpoint", "http://x:1",
            "--token", "tok", "--default-time-range", "bogus",
        ])
        assert result.exit_code == 2, result.output
        assert "default-time-range" in result.output

    def test_good_default_time_range_accepted(self, runner):
        from signoz_cli.config import load_config

        result = runner.invoke(cli, [
            "configure", "-p", "x", "--endpoint", "http://x:1",
            "--token", "tok", "--default-time-range", "24h",
        ])
        assert result.exit_code == 0, result.output
        assert load_config()["profiles"]["x"]["default_time_range"] == "24h"
