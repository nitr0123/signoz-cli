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

    def test_metrics_stats_empty_json_stays_parseable(self, runner, monkeypatch):
        import json as _json

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
        result = runner.invoke(cli, ["metrics", "stats", "some.counter", "--json"])
        assert result.exit_code == 0, result.output
        # Contract: empty JSON output must be a parseable empty payload,
        # never a "(no results)" string (breaks `| jq` consumers).
        assert _json.loads(result.stdout) == []

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


class TestFieldSelectorValidation:
    def test_malformed_part_rejected(self, runner):
        result = runner.invoke(cli, ["traces", "search", "--field-selector", "hasError"])
        assert result.exit_code == 2, result.output
        assert "key=value" in result.output

    def test_mixed_valid_and_malformed_rejected(self, runner):
        result = runner.invoke(
            cli, ["traces", "search", "--field-selector", "a=b,oops"])
        assert result.exit_code == 2, result.output

    def test_value_may_contain_equals(self, runner, monkeypatch):
        from signoz_cli import output as _output

        result = runner.invoke(
            cli, ["traces", "search", "--field-selector", "k=a=b", "--json"])
        # Parsing must succeed (exit 2 would mean rejection); the query
        # itself fails without a server, but not on selector parsing.
        assert result.exit_code != 2 or "key=value" not in result.output
        assert _output._FIELD_SELECTOR == {"k": "a=b"}


class TestUnknownProfile:
    def test_typo_profile_fails_fast(self, runner, monkeypatch):
        from signoz_cli.config import save_config

        save_config({"profiles": {"prod": {
            "endpoint": "http://prod:1", "token": "tok",
        }}})
        result = runner.invoke(cli, ["traces", "search", "-p", "prodd"])
        assert result.exit_code == 2, result.output
        assert "prodd" in result.output

    def test_env_profile_typo_fails_fast(self, runner, monkeypatch):
        from signoz_cli.config import save_config

        save_config({"profiles": {"prod": {
            "endpoint": "http://prod:1", "token": "tok",
        }}})
        monkeypatch.setenv("SIGNOZ_PROFILE", "prodd")
        result = runner.invoke(cli, ["traces", "search"])
        assert result.exit_code == 2, result.output
        assert "prodd" in result.output


class TestAlertsTestValidation:
    def test_list_rule_file_rejected(self, runner, tmp_path):
        pf = tmp_path / "rule.json"
        pf.write_text("[1, 2]")
        result = runner.invoke(cli, ["alerts", "test", str(pf)])
        assert result.exit_code == 2, result.output
        assert "JSON object" in result.output


class TestStaleProfileRange:
    def test_bad_stored_range_names_profile_and_key(self, runner):
        from signoz_cli.config import load_config, save_config

        cfg = load_config()
        cfg.setdefault("profiles", {}).setdefault("stale", {}).update({
            "endpoint": "http://x:1",
            "token": "tok",
            "default_time_range": "24hours",
        })
        save_config(cfg)
        result = runner.invoke(cli, ["traces", "search", "-p", "stale"])
        assert result.exit_code == 2, result.output
        assert "default_time_range" in result.output
        assert "stale" in result.output


class TestCrudCompletion:
    def test_views_create_posts_payload(self, runner, monkeypatch, tmp_path):
        import json as _json

        calls = {}

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def create_saved_view(self, view):
                calls["view"] = view
                return {"status": "success", "data": {"id": "v1"}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        pf = tmp_path / "view.json"
        pf.write_text(_json.dumps({"name": "v", "sourcePage": "logs"}))
        result = runner.invoke(cli, ["views", "create", str(pf), "--json"])
        assert result.exit_code == 0, result.output
        assert calls["view"] == {"name": "v", "sourcePage": "logs"}
        assert "v1" in result.output

    def test_channels_update_puts_payload(self, runner, monkeypatch, tmp_path):
        import json as _json

        calls = {}

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def update_channel(self, channel_id, channel):
                calls["update"] = (channel_id, channel)
                return {"status": "success"}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        pf = tmp_path / "ch.json"
        pf.write_text(_json.dumps({"name": "c"}))
        result = runner.invoke(cli, ["channels", "update", "ch1", str(pf), "--json"])
        assert result.exit_code == 0, result.output
        assert calls["update"] == ("ch1", {"name": "c"})


class TestBundle:
    def _client(self, monkeypatch):
        import signoz_cli.commands.common as cm

        calls = {"create": [], "update": [], "delete": []}

        class Fake:
            def __init__(self, *a, **k):
                self.endpoint = a[0] if a else ""

            def close(self):
                pass

            def get_server_version(self):
                return {"version": "v0.138.0"}

            def list_dashboards_v2(self, limit=None, offset=None, query=None):
                return {"status": "success",
                        "data": {"dashboards": [
                            {"id": "d1", "name": "Dash",
                             "createdAt": "t", "orgId": "o"}], "total": 1}}

            def list_rules(self):
                return {"status": "success",
                        "data": {"rules": [
                            {"id": "r1", "alert": "Rule",
                             "createdAt": "t"}]}}

            def list_channels(self):
                return {"status": "success", "data": []}

            def list_saved_views(self, source_page="logs"):
                return {"status": "success", "data": []}

            def get_dashboard_v2(self, dashboard_id):
                return {"status": "success",
                        "data": {"id": "d1", "name": "Dash", "createdAt": "t"}}

            def get_alert(self, rule_id):
                return {"status": "success",
                        "data": {"id": "r1", "alert": "Rule", "createdAt": "t"}}

            def get_channel(self, channel_id):
                return {"status": "success",
                        "data": {"id": "ch1", "name": "Ch"}}

            def get_saved_view(self, view_id):
                return {"status": "success",
                        "data": {"id": "v1", "name": "V"}}

            def create_dashboard_v2(self, doc):
                calls["create"].append(("dashboards", doc))
                return {"status": "success", "data": {"id": "d9"}}

            def update_dashboard_v2(self, dashboard_id, doc):
                calls["update"].append(("dashboards", dashboard_id, doc))
                return {"status": "success"}

            def create_rule(self, doc):
                calls["create"].append(("alerts", doc))
                return {"status": "success", "data": {"id": "r9"}}

            def update_rule(self, rule_id, doc):
                calls["update"].append(("alerts", rule_id, doc))
                return {"status": "success"}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        return calls

    def test_export_writes_files_and_manifest(self, runner, monkeypatch, tmp_path):
        import json as _json

        self._client(monkeypatch)
        target = tmp_path / "backup"
        result = runner.invoke(cli, ["export", str(target), "--yes"])
        assert result.exit_code == 0, result.output
        manifest = _json.loads((target / "manifest.json").read_text())
        assert manifest["counts"] == {"dashboards": 1, "alerts": 1,
                                      "channels": 0, "views": 0}
        assert manifest["server_version"] == "v0.138.0"
        assert "exported_from" in manifest
        dash_files = list((target / "dashboards").glob("*.json"))
        assert len(dash_files) == 1
        assert "Dash" in dash_files[0].name

    def test_import_dry_run_calls_nothing(self, runner, monkeypatch, tmp_path):
        import json as _json

        calls = self._client(monkeypatch)
        target = tmp_path / "backup"
        (target / "dashboards").mkdir(parents=True)
        (target / "dashboards" / "Dash.d1.json").write_text(
            _json.dumps({"id": "d1", "name": "Dash"}))
        result = runner.invoke(cli, ["import", str(target), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert calls == {"create": [], "update": [], "delete": []}
        assert "skipped" in result.output  # name clash with server Dash

    def test_import_creates_new_items(self, runner, monkeypatch, tmp_path):
        import json as _json

        calls = self._client(monkeypatch)
        target = tmp_path / "backup"
        (target / "dashboards").mkdir(parents=True)
        (target / "dashboards" / "New.zz.json").write_text(
            _json.dumps({"name": "New", "createdAt": "t", "orgId": "o"}))
        result = runner.invoke(cli, ["import", str(target), "--yes"])
        assert result.exit_code == 0, result.output
        assert calls["create"] == [("dashboards", {"name": "New"})]
        assert "created" in result.output

    def test_import_conflict_fail(self, runner, monkeypatch, tmp_path):
        import json as _json

        self._client(monkeypatch)
        target = tmp_path / "backup"
        (target / "dashboards").mkdir(parents=True)
        (target / "dashboards" / "Dash.d1.json").write_text(
            _json.dumps({"id": "d1", "name": "Dash"}))
        result = runner.invoke(
            cli, ["import", str(target), "--yes", "--on-conflict", "fail"])
        assert result.exit_code != 0
        assert "Conflict" in result.output

    def test_import_overwrite_updates(self, runner, monkeypatch, tmp_path):
        import json as _json

        calls = self._client(monkeypatch)
        target = tmp_path / "backup"
        (target / "dashboards").mkdir(parents=True)
        (target / "dashboards" / "Dash.d1.json").write_text(
            _json.dumps({"id": "d1", "name": "Dash", "createdAt": "t"}))
        result = runner.invoke(
            cli, ["import", str(target), "--yes", "--on-conflict", "overwrite"])
        assert result.exit_code == 0, result.output
        assert calls["update"] == [("dashboards", "d1", {"name": "Dash"})]
        assert "overwritten" in result.output

    def test_dashboards_diff_shows_changes(self, runner, monkeypatch, tmp_path):
        import json as _json

        self._client(monkeypatch)
        pf = tmp_path / "dash.json"
        pf.write_text(_json.dumps({"id": "d1", "name": "Changed",
                                   "createdAt": "other"}))
        result = runner.invoke(cli, ["dashboards", "diff", "d1", str(pf)])
        assert result.exit_code == 0, result.output
        assert "-  \"name\": \"Dash\"" in result.output
        assert "+  \"name\": \"Changed\"" in result.output

    def test_dashboards_diff_no_changes(self, runner, monkeypatch, tmp_path):
        import json as _json

        self._client(monkeypatch)
        pf = tmp_path / "dash.json"
        pf.write_text(_json.dumps({"id": "DIFFERENT", "name": "Dash",
                                   "createdAt": "other"}))
        result = runner.invoke(cli, ["dashboards", "diff", "d1", str(pf)])
        assert result.exit_code == 0, result.output
        assert "no changes" in result.output

    def test_alerts_diff_shows_changes(self, runner, monkeypatch, tmp_path):
        import json as _json

        self._client(monkeypatch)
        pf = tmp_path / "rule.json"
        pf.write_text(_json.dumps({"id": "r1", "alert": "Renamed"}))
        result = runner.invoke(cli, ["alerts", "diff", "r1", str(pf)])
        assert result.exit_code == 0, result.output
        assert "+  \"alert\": \"Renamed\"" in result.output


class TestBundleHelpers:
    def test_normalize_dashboard_fills_spec_defaults(self):
        from signoz_cli.commands.bundle import _normalize_dashboard

        doc = {"id": "d1", "name": "D", "spec": {"panels": {"a": 1}}}
        out = _normalize_dashboard(doc)
        assert out["spec"]["variables"] == []
        assert out["spec"]["panels"] == {"a": 1}
        # Input not mutated, volatile untouched here (strip is separate).
        assert "variables" not in doc["spec"]

    def test_normalize_dashboard_without_spec(self):
        from signoz_cli.commands.bundle import _normalize_dashboard

        assert _normalize_dashboard({"name": "D"}) == {"name": "D"}

    def test_fetch_full_unwraps_envelope(self):
        from signoz_cli.commands.bundle import _fetch_full

        class Fake:
            def get_dashboard_v2(self, dashboard_id):
                return {"status": "success",
                        "data": {"id": "d1", "name": "Dash"}}

        assert _fetch_full(Fake(), "dashboards", {"id": "d1"}) == {
            "id": "d1", "name": "Dash"}

    def test_fetch_full_skips_on_error(self, capsys):
        from signoz_cli.commands.bundle import _fetch_full

        class Fake:
            def get_alert(self, rule_id):
                raise RuntimeError("gone")

        assert _fetch_full(Fake(), "alerts", {"id": "r1", "alert": "R"}) is None
        assert "skipping" in capsys.readouterr().err


class TestBundleNormalize:
    def test_normalize_drops_readonly_dashboard_fields(self):
        from signoz_cli.commands.bundle import _normalize_dashboard

        doc = {"id": "d1", "name": "D", "locked": True, "image": "x",
               "source": "y", "spec": {}}
        out = _normalize_dashboard(doc)
        assert out == {"id": "d1", "name": "D",
                       "spec": {"variables": [], "panels": {}}}

    def test_normalize_keeps_rule_source(self):
        # `source` is meaningful on rules; only dashboards drop it.
        from signoz_cli.commands.bundle import _strip_volatile

        doc = {"id": "r1", "alert": "R", "source": "logs",
               "createdAt": "t"}
        assert _strip_volatile(doc) == {"alert": "R", "source": "logs"}


class TestTracesTail:
    def test_prints_new_spans_once(self, runner, monkeypatch):
        import time as _t

        rows = [
            {"traceID": "t1", "spanID": "s1", "timestamp": "2024-01-01",
             "service.name": "svc", "name": "op", "durationNano": 1000},
            {"traceID": "t2", "spanID": "s2", "timestamp": "2024-01-02",
             "service.name": "svc", "name": "op", "durationNano": 2000},
        ]

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                return {"status": "success", "data": {"type": "raw", "data": {
                    "results": [{"queryName": "A",
                                 "rows": [{"data": r} for r in rows]}]}}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        calls = {"n": 0}

        def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(_t, "sleep", fake_sleep)
        result = runner.invoke(cli, ["traces", "tail", "--service", "svc"])
        assert result.exit_code == 0, result.output
        assert result.output.count("t1") == 1
        assert result.output.count("t2") == 1

    def test_tail_auth_failure_exits(self, runner, monkeypatch):
        from signoz_cli.client import SigNozApiError

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                raise SigNozApiError(403, "forbidden")

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["traces", "tail"])
        assert result.exit_code == 1
        assert "403" in result.output

    def test_tail_interval_zero_rejected(self, runner):
        result = runner.invoke(cli, ["traces", "tail", "--interval", "0"])
        assert result.exit_code == 2


class TestAlertsWatch:
    def test_prints_new_alerts_once(self, runner, monkeypatch):
        import time as _t

        alerts = [
            {"alertname": "HighErr", "ruleId": "r1", "severity": "critical",
             "startsAt": "2024-01-01", "state": "firing"},
        ]

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def list_alerts(self):
                return {"status": "success", "data": [
                    {"labels": {"alertname": a["alertname"],
                                "ruleId": a["ruleId"],
                                "severity": a["severity"]},
                     "status": {"state": a["state"]},
                     "startsAt": a["startsAt"], "endsAt": ""}
                    for a in alerts]}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        calls = {"n": 0}

        def fake_sleep(_seconds):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(_t, "sleep", fake_sleep)
        result = runner.invoke(cli, ["alerts", "watch"])
        assert result.exit_code == 0, result.output
        assert result.output.count("HighErr") == 1

    def test_watch_auth_failure_exits(self, runner, monkeypatch):
        from signoz_cli.client import SigNozApiError

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def list_alerts(self):
                raise SigNozApiError(401, "unauthorized")

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["alerts", "watch"])
        assert result.exit_code == 1
        assert "401" in result.output


class TestMetricsTop:
    def _install(self, monkeypatch):
        response = {
            "status": "success",
            "data": {
                "type": "time_series",
                "data": {
                    "results": [
                        {
                            "queryName": "A",
                            "aggregations": [
                                {
                                    "index": 0,
                                    "alias": "__r",
                                    "series": [
                                        {"labels": [{"key": "s", "value": "small"}],
                                         "values": [{"timestamp": 2,
                                                     "value": 5.0}]},
                                        {"labels": [{"key": "s", "value": "big"}],
                                         "values": [{"timestamp": 2,
                                                     "value": 50.0}]},
                                    ],
                                }
                            ],
                        }
                    ]
                },
            },
        }

        class Fake:
            def __init__(self, *a, **k):
                self.captured = None

            def close(self):
                pass

            def query_builder_v5(self, payload, _response=response):
                self.captured = payload
                return _response

        fake = Fake()
        monkeypatch.setattr(cm, "SigNozClient", lambda *a, **k: fake)
        return fake

    def test_ranks_by_latest_value(self, runner, monkeypatch):
        import json as _json

        self._install(monkeypatch)
        result = runner.invoke(cli, ["metrics", "top", "m", "--json"])
        assert result.exit_code == 0, result.output
        rows = _json.loads(result.stdout)
        assert [r["series"] for r in rows] == ["s=big", "s=small"]

    def test_top_n_limits(self, runner, monkeypatch):
        import json as _json

        self._install(monkeypatch)
        result = runner.invoke(cli, ["metrics", "top", "m", "--top", "1", "--json"])
        assert result.exit_code == 0, result.output
        assert len(_json.loads(result.stdout)) == 1

    def test_top_validates_n(self, runner):
        result = runner.invoke(cli, ["metrics", "top", "m", "--top", "0"])
        assert result.exit_code == 2


class TestHistory:
    def test_records_query_invocations(self, runner, monkeypatch, tmp_path):
        import json as _json
        import sys as _sys

        from signoz_cli.config import CONFIG_FILE  # noqa: F401

        monkeypatch.setattr(
            "signoz_cli.commands.common.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            _sys, "argv",
            ["signoz", "logs", "search", "--service", "svc"])

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                return {"status": "success", "data": {"type": "raw",
                        "data": {"results": [{"queryName": "A", "rows": []}]}}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["logs", "search", "--service", "svc"])
        assert result.exit_code == 0, result.output
        entries = _json.loads((tmp_path / "history.json").read_text())
        assert entries[-1]["argv"] == ["logs", "search", "--service", "svc"]

    def test_help_not_recorded(self, runner, monkeypatch, tmp_path):
        import sys as _sys

        monkeypatch.setattr(
            "signoz_cli.commands.common.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            _sys, "argv", ["signoz", "logs", "search", "--help"])
        runner.invoke(cli, ["logs", "search", "--help"])
        assert not (tmp_path / "history.json").exists()

    def test_rerun_executes_recorded_argv(self, runner, monkeypatch, tmp_path):
        import json as _json

        monkeypatch.setattr(
            "signoz_cli.commands.common.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_DIR", tmp_path)
        (tmp_path / "history.json").write_text(_json.dumps([
            {"ts": "t", "argv": ["traces", "search", "--limit", "3"]},
        ]))
        seen = {}
        monkeypatch.setattr(
            "os.execvp",
            lambda prog, args: seen.update(prog=prog, args=args),
        )
        result = runner.invoke(cli, ["rerun", "0"])
        assert result.exit_code == 0, result.output
        assert seen == {"prog": "signoz",
                        "args": ["signoz", "traces", "search", "--limit", "3"]}

    def test_rerun_bad_index(self, runner, monkeypatch, tmp_path):
        import json as _json

        monkeypatch.setattr(
            "signoz_cli.commands.common.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_DIR", tmp_path)
        (tmp_path / "history.json").write_text(_json.dumps([]))
        result = runner.invoke(cli, ["rerun", "5"])
        assert result.exit_code != 0

    def test_history_clear(self, runner, monkeypatch, tmp_path):
        import json as _json

        monkeypatch.setattr(
            "signoz_cli.commands.common.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_DIR", tmp_path)
        (tmp_path / "history.json").write_text(_json.dumps(
            [{"ts": "t", "argv": ["x"]}]))
        result = runner.invoke(cli, ["history", "--clear"])
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "history.json").exists()


class TestHistorySecrets:
    def test_token_invocations_never_recorded(self, runner, monkeypatch, tmp_path):
        import sys as _sys

        monkeypatch.setattr(
            "signoz_cli.commands.common.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            _sys, "argv",
            ["signoz", "logs", "search", "--token", "SUPERSECRET"])

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                return {"status": "success", "data": {"type": "raw",
                        "data": {"results": [{"queryName": "A", "rows": []}]}}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["logs", "search", "--token", "x"])
        assert result.exit_code == 0, result.output
        assert not (tmp_path / "history.json").exists()
