"""End-to-end CLI tests for profiles using click's CliRunner (no server)."""

import json

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
        # Two profiles with distinct endpoints; `auth verify -p b` must connect
        # to b's endpoint. Before the fix, the leaf --profile was dropped and
        # the default (a) endpoint was used instead.
        runner.invoke(cli, ["configure", "-p", "a",
                            "--endpoint", "http://a-endpoint:1", "--token", "tok-a-123456"])
        runner.invoke(cli, ["configure", "-p", "b",
                            "--endpoint", "http://b-endpoint:2", "--token", "tok-b-123456"])

        import signoz_cli.main as m
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

        monkeypatch.setattr(m, "SigNozClient", Recorder)
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

        import signoz_cli.main as m

        for k, v in env.items():
            monkeypatch.setenv(k, v)
        holder = getattr(m, holder_name)
        old = holder["value"]
        captured = {}
        real = m.SigNozClient

        def recorder(endpoint, token, **kwargs):
            captured.update(kwargs)
            return real(endpoint, token,
                        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))

        try:
            holder["value"] = holder_value
            m.SigNozClient = recorder
            m._get_client(None, None)
            return captured
        finally:
            holder["value"] = old
            m.SigNozClient = real

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
