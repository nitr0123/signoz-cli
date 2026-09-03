"""End-to-end CLI tests for profiles using click's CliRunner (no server)."""

import json

import click
import httpx
import pytest
from click.testing import CliRunner

from signoz_cli import __version__
from signoz_cli.main import cli


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".signoz"
    monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", cfg_dir / "config.json")
    for var in ("SIGNOZ_ENDPOINT", "SIGNOZ_ACCESS_TOKEN", "SIGNOZ_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    return cfg_dir


@pytest.fixture()
def runner():
    return CliRunner()


class TestBasics:
    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_help_lists_groups(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        for group in ("alerts", "auth", "dashboards", "logs", "metrics",
                      "profile", "query", "services", "traces"):
            assert group in result.output


class TestMissingCredentials:
    def test_error_without_any_config(self, runner):
        result = runner.invoke(cli, ["logs", "search"])
        assert result.exit_code != 0
        assert "Missing endpoint" in result.output

    def test_error_without_token(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://x:8080")
        result = runner.invoke(cli, ["logs", "search"])
        assert result.exit_code != 0
        assert "Missing token" in result.output


class TestConfigureAndProfiles:
    def test_configure_creates_default_profile(self, runner):
        result = runner.invoke(cli, [
            "configure", "--profile", "local",
            "--endpoint", "http://localhost:8080", "--token", "secret-token-123456",
        ])
        assert result.exit_code == 0, result.output

        from signoz_cli.config import load_config
        cfg = load_config()
        assert cfg["default"] == "local"
        assert cfg["profiles"]["local"]["endpoint"] == "http://localhost:8080"
        assert cfg["profiles"]["local"]["token"] == "secret-token-123456"

    def test_second_profile_does_not_change_default(self, runner):
        runner.invoke(cli, ["configure", "-p", "a", "--endpoint", "http://a:1", "--token", "t1"])
        runner.invoke(cli, ["configure", "-p", "b", "--endpoint", "http://b:2", "--token", "t2"])
        from signoz_cli.config import load_config
        cfg = load_config()
        assert cfg["default"] == "a"
        assert set(cfg["profiles"]) == {"a", "b"}

    def test_explicit_set_default(self, runner):
        runner.invoke(cli, ["configure", "-p", "a", "--endpoint", "http://a:1", "--token", "t"])
        result = runner.invoke(cli, [
            "configure", "-p", "b", "--endpoint", "http://b:2", "--token", "t",
            "--set-default",
        ])
        assert result.exit_code == 0, result.output
        from signoz_cli.config import load_config
        assert load_config()["default"] == "b"

    def test_profile_list_masks_tokens(self, runner):
        runner.invoke(cli, ["configure", "-p", "prod",
                            "--endpoint", "https://prod:443", "--token", "supersecretkey123"])
        result = runner.invoke(cli, ["profile", "list"])
        assert result.exit_code == 0, result.output
        assert "prod" in result.output
        assert "supersecretkey123" not in result.output

    def test_profile_use_and_show(self, runner):
        runner.invoke(cli, ["configure", "-p", "a", "--endpoint", "http://a:1", "--token", "tok-a-long"])
        runner.invoke(cli, ["configure", "-p", "b", "--endpoint", "http://b:2", "--token", "tok-b-long"])

        result = runner.invoke(cli, ["profile", "use", "b"])
        assert result.exit_code == 0
        assert "'b'" in result.output

        result = runner.invoke(cli, ["profile", "show", "a"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["endpoint"] == "http://a:1"
        assert "tok-a-long" not in data["token"]
        assert data["default"] is False

    def test_profile_use_unknown(self, runner):
        result = runner.invoke(cli, ["profile", "use", "nope"])
        assert result.exit_code != 0
        assert "Unknown profile" in result.output

    def test_profile_remove(self, runner):
        runner.invoke(cli, ["configure", "-p", "a", "--endpoint", "http://a:1", "--token", "t"])
        runner.invoke(cli, ["configure", "-p", "b", "--endpoint", "http://b:2", "--token", "t"])

        result = runner.invoke(cli, ["profile", "remove", "a", "--yes"])
        assert result.exit_code == 0

        from signoz_cli.config import load_config
        cfg = load_config()
        assert "a" not in cfg["profiles"]

    def test_profile_remove_default_reassigns(self, runner):
        runner.invoke(cli, ["configure", "-p", "a", "--endpoint", "http://a:1", "--token", "t"])
        runner.invoke(cli, ["configure", "-p", "b", "--endpoint", "http://b:2", "--token", "t"])

        result = runner.invoke(cli, ["profile", "remove", "a", "--yes"])
        assert result.exit_code == 0

        from signoz_cli.config import load_config
        assert load_config()["default"] == "b"


def runner_invoke_cfg():  # pragma: no cover - helper kept out of test body flow
    raise NotImplementedError


class TestWhoami:
    def test_whoami_with_env(self, runner, monkeypatch):
        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://env:8080")
        monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "envtoken-1234567890")
        result = runner.invoke(cli, ["auth", "whoami"])
        assert result.exit_code == 0
        assert "http://env:8080" in result.output
        assert "envtoken-1234567890" not in result.output
        assert "<none>" in result.output

    def test_whoami_shows_selected_profile(self, runner):
        runner.invoke(cli, ["configure", "-p", "prod",
                            "--endpoint", "https://prod:443", "--token", "tok-prod-long"])
        result = runner.invoke(cli, ["auth", "whoami"])
        assert "https://prod:443" in result.output
        assert "Profile:  prod" in result.output

    def test_whoami_flag_overrides_profile_display(self, runner):
        runner.invoke(cli, ["configure", "-p", "prod",
                            "--endpoint", "https://prod:443", "--token", "tok-prod-long"])
        result = runner.invoke(cli, ["auth", "whoami", "--endpoint", "http://flag:1"])
        assert "http://flag:1" in result.output


class TestAuthVerifyProfile:
    def test_verify_uses_selected_leaf_profile(self, runner, monkeypatch):
        import signoz_cli.commands.common as cm
        # Two profiles with distinct endpoints; `auth verify -p b` must connect
        # to b's endpoint. Before the fix, the leaf --profile was dropped and
        # the default (a) endpoint was used instead.
        runner.invoke(cli, ["configure", "-p", "a",
                            "--endpoint", "http://a-endpoint:1", "--token", "tok-a-123456"])
        runner.invoke(cli, ["configure", "-p", "b",
                            "--endpoint", "http://b-endpoint:2", "--token", "tok-b-123456"])

        captured = {}

        class Recorder:
            def __init__(self, endpoint, token, **kwargs):
                captured["endpoint"] = endpoint
                captured["token"] = token

            def close(self):
                pass

            def verify(self):
                return {"name": "svc"}

            def get_server_version(self):
                return {"version": "v0.138.0"}

        monkeypatch.setattr(cm, "SigNozClient", Recorder)
        result = runner.invoke(cli, ["auth", "verify", "-p", "b"])
        assert result.exit_code == 0, result.output
        assert captured["endpoint"] == "http://b-endpoint:2"
        assert captured["token"] == "tok-b-123456"


class TestMaskToken:
    def test_short_token_not_reported_as_unset(self):
        from signoz_cli.main import _mask_token
        assert _mask_token(None) == "<not set>"
        assert _mask_token("") == "<not set>"
        # A real but short token must not read as "<not set>".
        assert _mask_token("shorttok") == "***"
        assert _mask_token("abcdefghijklmnop") == "abcdefgh...mnop"


class TestJsonFlag:
    def test_json_shorthand_outputs_parseable_json(self, runner):
        runner.invoke(cli, ["configure", "-p", "a", "--endpoint", "http://a:1", "--token", "tok"])
        result = runner.invoke(cli, ["profile", "list", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["data"][0]["name"] == "a"

    def test_format_flag_still_works_alone(self, runner):
        runner.invoke(cli, ["configure", "-p", "a", "--endpoint", "http://a:1", "--token", "tok"])
        result = runner.invoke(cli, ["profile", "list", "-f", "json"])
        assert json.loads(result.output)["data"][0]["endpoint"] == "http://a:1"

    def test_profile_show_always_json(self, runner):
        runner.invoke(cli, ["configure", "-p", "a", "--endpoint", "http://a:1", "--token", "tok"])
        result = runner.invoke(cli, ["profile", "show", "a"])
        assert json.loads(result.output)["endpoint"] == "http://a:1"


class TestCsvShorthand:
    def test_csv_flag_outputs_csv(self, runner):
        runner.invoke(cli, ["configure", "-p", "a", "--endpoint", "http://a:1", "--token", "tok"])
        result = runner.invoke(cli, ["profile", "list", "--csv"])
        assert result.exit_code == 0, result.output
        assert any("http://a:1" in ln for ln in result.output.splitlines())


class TestTimeoutInsecure:
    @staticmethod
    def _capture_client(monkeypatch, env, holder_name, holder_value):

        import signoz_cli.commands.common as cm

        for k, v in env.items():
            monkeypatch.setenv(k, v)
        holder = getattr(cm, holder_name)
        old = holder["value"]
        captured = {}
        real = cm.SigNozClient

        def recorder(endpoint, token, **kwargs):
            captured.update(kwargs)
            return real(endpoint, token,
                        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))

        try:
            holder["value"] = holder_value
            cm.SigNozClient = recorder
            cm._get_client(None, None)
            return captured
        finally:
            holder["value"] = old
            cm.SigNozClient = real

    def test_env_timeout_used(self, runner, monkeypatch):
        captured = self._capture_client(
            monkeypatch,
            {"SIGNOZ_ENDPOINT": "http://x:8080", "SIGNOZ_ACCESS_TOKEN": "tok",
             "SIGNOZ_TIMEOUT": "5"},
            "_TIMEOUT_HOLDER", None)
        assert captured.get("timeout") == 5.0

    def test_flag_timeout_wins_over_env(self, runner, monkeypatch):
        monkeypatch.setenv("SIGNOZ_TIMEOUT", "5")
        captured = self._capture_client(
            monkeypatch,
            {"SIGNOZ_ENDPOINT": "http://x:8080", "SIGNOZ_ACCESS_TOKEN": "tok"},
            "_TIMEOUT_HOLDER", 42.0)
        assert captured.get("timeout") == 42.0

    def test_insecure_env_disables_verify(self, runner, monkeypatch):
        captured = self._capture_client(
            monkeypatch,
            {"SIGNOZ_ENDPOINT": "http://x:8080", "SIGNOZ_ACCESS_TOKEN": "tok",
             "SIGNOZ_INSECURE": "1"},
            "_INSECURE_HOLDER", False)
        assert captured.get("verify") is False

    def test_verify_default_true(self, runner, monkeypatch):
        captured = self._capture_client(
            monkeypatch,
            {"SIGNOZ_ENDPOINT": "http://x:8080", "SIGNOZ_ACCESS_TOKEN": "tok"},
            "_INSECURE_HOLDER", False)
        assert captured.get("verify") is True


class TestFormatDefault:
    def test_env_format_defaults_output(self, runner, monkeypatch):
        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://x:8080")
        monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok")
        monkeypatch.setenv("SIGNOZ_FORMAT", "json")
        from signoz_cli.main import _resolve_format_callback
        ctx = click.Context(click.Command("test"))
        assert _resolve_format_callback(ctx, None, "table") == "json"

    def test_profile_format_default(self, runner):
        runner.invoke(cli, ["configure", "-p", "jsonp",
                            "--endpoint", "http://x:1", "--token", "tok-1234567",
                            "--set-default"])
        import signoz_cli.config as cfgmod
        data = cfgmod.load_config()
        data["profiles"]["jsonp"]["default_format"] = "json"
        cfgmod.save_config(data)
        import signoz_cli.commands.common as cm
        from signoz_cli.main import _resolve_format_callback
        cm._ROOT_PROFILE = "jsonp"
        ctx = click.Context(click.Command("test"))
        assert _resolve_format_callback(ctx, None, "table") == "json"
        cm._ROOT_PROFILE = None  # reset

    def test_default_is_table_when_unset(self, runner):
        from signoz_cli.main import _resolve_format_callback
        ctx = click.Context(click.Command("test"))
        assert _resolve_format_callback(ctx, None, "table") == "table"

    def test_explicit_f_flag_overrides_profile(self, runner):
        import signoz_cli.config as cfgmod
        data = cfgmod.load_config()
        data["profiles"] = data.get("profiles", {})
        data["profiles"]["fp"] = {"endpoint": "http://x:1", "token": "tok",
                                   "default_format": "json"}
        data["default"] = "fp"
        cfgmod.save_config(data)
        from signoz_cli.main import _resolve_format_callback
        ctx = click.Context(click.Command("test"))
        # explicit -f csv beats profile default json
        assert _resolve_format_callback(ctx, None, "csv") == "csv"

    def test_json_flag_beats_profile(self, runner, monkeypatch):
        import signoz_cli.config as cfgmod
        data = cfgmod.load_config()
        data["profiles"] = data.get("profiles", {})
        data["profiles"]["fp2"] = {"endpoint": "http://x:1", "token": "tok",
                                    "default_format": "json"}
        data["default"] = "fp2"
        cfgmod.save_config(data)
        from signoz_cli.main import _resolve_format_callback
        ctx = click.Context(click.Command("test"))
        ctx.params = {"fmt": "json"}  # --json eager callback set this
        assert _resolve_format_callback(ctx, None, "table") == "json"


class TestCompletion:
    def test_prints_eval_snippet(self, runner):
        for shell in ("bash", "zsh", "fish"):
            result = runner.invoke(cli, ["completion", shell])
            assert result.exit_code == 0
            assert f"_SIGNOZ_COMPLETE={shell}_source signoz" in result.output


class TestStatus:
    def test_status_with_fake_client(self, runner, monkeypatch):
        import signoz_cli.commands.common as cm

        class Fake:
            def __init__(self, endpoint, token, **kwargs):
                self.endpoint = endpoint

            def close(self):
                pass

            def verify(self):
                return {"data": {"name": "svc", "id": "acc1", "orgId": "org1"}}

            def get_server_version(self):
                return {"version": "v0.138.0"}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        runner.invoke(cli, ["configure", "-p", "s", "--endpoint", "http://s:1",
                            "--token", "tok-1234567"])
        result = runner.invoke(cli, ["status", "-p", "s", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["server_version"] == "v0.138.0"
        assert data["compatible"] is True
        assert data["account"] == "svc"
        assert data["org_id"] == "org1"


class TestTypoSuggestion:
    def test_typo_suggests_close_command(self, runner):
        result = runner.invoke(cli, ["logss"])
        assert result.exit_code == 2
        assert "Did you mean 'logs'" in result.output

    def test_far_typo_no_suggestion(self, runner):
        result = runner.invoke(cli, ["zzzz"])
        assert result.exit_code == 2
        assert "Did you mean" not in result.output


class TestNoInput:
    def test_delete_without_yes_and_no_input_fails(self, runner):
        runner.invoke(cli, ["configure", "-p", "d", "--endpoint", "http://d:1",
                            "--token", "tok-1234567"])
        # dashboards delete with --no-input and no --yes must not prompt
        result = runner.invoke(cli, ["dashboards", "delete", "x",
                                     "--no-input", "-p", "d"])
        assert result.exit_code == 2
        assert "--yes" in result.output or "no-input" in result.output


class TestDryRun:
    def test_delete_dry_run_does_not_call_client(self, runner, monkeypatch):
        import signoz_cli.commands.common as cm

        called = {"delete": False}

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def delete_dashboard_v2(self, dashboard_id):
                called["delete"] = True
                return {"status": "ok"}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        runner.invoke(cli, ["configure", "-p", "d", "--endpoint", "http://d:1",
                            "--token", "tok-1234567"])
        result = runner.invoke(cli, ["dashboards", "delete", "abc",
                                     "--dry-run", "--json", "-p", "d"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["action"] == "delete"
        assert called["delete"] is False


class TestStdin:
    def test_dashboards_create_from_stdin(self, runner, monkeypatch):
        import signoz_cli.commands.common as cm

        created = {}

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def create_dashboard_v2(self, dashboard):
                created["dash"] = dashboard
                return {"status": "success", "data": {"id": "d1"}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        runner.invoke(cli, ["configure", "-p", "d", "--endpoint", "http://d:1",
                            "--token", "tok-1234567"])
        payload = json.dumps({"name": "from-stdin", "schemaVersion": "v6", "spec": {}})
        result = runner.invoke(cli, ["dashboards", "create", "-", "-p", "d",
                                     "--json"], input=payload)
        assert result.exit_code == 0, result.output
        assert created["dash"]["name"] == "from-stdin"


class TestNoColor:
    def test_no_color_removes_ansi(self, runner, monkeypatch):
        import signoz_cli.commands.common as cm

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                return {"status": "success", "data": {"type": "raw", "data": {
                    "results": [{"queryName": "A", "rows": [{"data": {"body": "x"}}]}]}}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        runner.invoke(cli, ["configure", "-p", "d", "--endpoint", "http://d:1",
                            "--token", "tok-1234567"])
        result = runner.invoke(cli, ["logs", "search", "--no-color", "-p", "d"])
        assert result.exit_code == 0, result.output
        assert "\x1b[" not in result.output
