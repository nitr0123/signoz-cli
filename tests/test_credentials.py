"""Tests for credential resolution precedence (flags > env > profile > legacy)."""

import json

import pytest
from click.testing import CliRunner

from signoz_cli.main import _resolve_credentials


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("SIGNOZ_ENDPOINT", "SIGNOZ_ACCESS_TOKEN", "SIGNOZ_PROFILE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def multi_config(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".signoz"
    monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", cfg_dir / "config.json")
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(json.dumps({
        "default": "local",
        "profiles": {
            "local": {"endpoint": "http://local:8080", "token": "tok-local"},
            "prod": {"endpoint": "https://prod:443", "token": "tok-prod"},
        },
        "endpoint": "http://legacy:8080",
        "token": "tok-legacy",
    }))
    return cfg_dir


class TestResolveCredentials:
    def test_flags_win_over_everything(self, multi_config):
        ep, tok = _resolve_credentials("http://flag:1", "tok-flag", "prod")
        assert (ep, tok) == ("http://flag:1", "tok-flag")

    def test_profile_selection_via_default(self, multi_config):
        ep, tok = _resolve_credentials(None, None, None)
        assert (ep, tok) == ("http://local:8080", "tok-local")

    def test_explicit_profile(self, multi_config):
        ep, tok = _resolve_credentials(None, None, "prod")
        assert (ep, tok) == ("https://prod:443", "tok-prod")

    def test_env_profile_var(self, multi_config, monkeypatch):
        monkeypatch.setenv("SIGNOZ_PROFILE", "prod")
        ep, tok = _resolve_credentials(None, None, None)
        assert (ep, tok) == ("https://prod:443", "tok-prod")

    def test_flag_beats_selected_profile(self, multi_config):
        ep, tok = _resolve_credentials("http://flag:1", None, "prod")
        assert ep == "http://flag:1"
        assert tok == "tok-prod"

    def test_env_beats_profile(self, multi_config, monkeypatch):
        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://env:2")
        monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-env")
        ep, tok = _resolve_credentials(None, None, "prod")
        assert (ep, tok) == ("http://env:2", "tok-env")

    def test_legacy_keys_when_no_profiles_apply(self, multi_config, monkeypatch):
        # no default set -> nothing selected -> legacy flat keys
        path = multi_config / "config.json"
        cfg = json.loads(path.read_text())
        del cfg["default"]
        path.write_text(json.dumps(cfg))
        ep, tok = _resolve_credentials(None, None, None)
        assert (ep, tok) == ("http://legacy:8080", "tok-legacy")

    def test_unknown_profile_is_rejected(self, multi_config):
        import click

        with pytest.raises(click.UsageError, match="does-not-exist"):
            _resolve_credentials(None, None, "does-not-exist")

    def test_nothing_configured(self, tmp_path, monkeypatch):
        cfg_dir = tmp_path / ".signoz"
        monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", cfg_dir)
        monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", cfg_dir / "config.json")
        ep, tok = _resolve_credentials(None, None, None)
        assert ep is None and tok is None


class TestRootProfileFlag:
    def test_root_flag_beats_env_selection(self, multi_config, monkeypatch):
        import signoz_cli.commands.common as cm
        monkeypatch.setenv("SIGNOZ_PROFILE", "prod")
        monkeypatch.setattr(cm, "_ROOT_PROFILE", "local")
        ep, tok = _resolve_credentials(None, None, None)
        assert (ep, tok) == ("http://local:8080", "tok-local")

    def test_leaf_flag_beats_root_flag(self, multi_config, monkeypatch):
        import signoz_cli.commands.common as cm
        monkeypatch.setattr(cm, "_ROOT_PROFILE", "local")
        ep, tok = _resolve_credentials(None, None, "prod")
        assert (ep, tok) == ("https://prod:443", "tok-prod")


class TestServerVersionCompat:
    def test_parse_version(self):
        from signoz_cli.main import _parse_version
        assert _parse_version("v0.138.0") == (0, 138, 0)
        assert _parse_version("v0.136.1") == (0, 136, 1)
        assert _parse_version("") == ()
        assert _parse_version("banana") == ()

    def test_supported_range(self):
        from signoz_cli.main import _is_supported_server
        assert _is_supported_server("v0.136.1") is True
        assert _is_supported_server("v0.138.0") is True
        assert _is_supported_server("v0.135.9") is False
        assert _is_supported_server("unknown") is False


class TestEndpointNormalization:
    def test_bare_host_port_gets_http_scheme(self, monkeypatch):
        import signoz_cli.commands.common as cm

        monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", __import__("pathlib").Path("/nonexistent-signoz-test"))
        monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)
        ep, tok = cm._resolve_credentials("localhost:8080", "tok", None)
        assert ep == "http://localhost:8080"
        assert tok == "tok"

    def test_explicit_scheme_untouched(self, monkeypatch):
        import signoz_cli.commands.common as cm

        monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)
        ep, _ = cm._resolve_credentials("https://h:443/x", "tok", None)
        assert ep == "https://h:443/x"


class TestEndpointUserinfo:
    def test_has_url_userinfo(self):
        from signoz_cli.commands.common import _has_url_userinfo as has

        assert has("https://bob:secret@localhost:8080") is True
        assert has("bob:secret@localhost:8080") is True
        assert has("https://localhost:8080") is False
        assert has("localhost:8080") is False
        # Bare user@host (email in a filter) is not a secret.
        assert has("bob@localhost") is False
        assert has("owner=bob@localhost") is False
        assert has("--limit") is False
        assert has("service.name = 'x'") is False

    def test_redact_userinfo(self):
        from signoz_cli.commands.common import _redact_userinfo as redact

        assert redact("https://bob:secret@localhost:8080") == \
            "https://<redacted>@localhost:8080"
        assert redact("https://localhost:8080") == "https://localhost:8080"
        assert redact("plain") == "plain"

    def test_resolve_rejects_userinfo_endpoint(self, monkeypatch):
        import click

        import signoz_cli.commands.common as cm

        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_DIR",
            __import__("pathlib").Path("/nonexistent-signoz-test"))
        for var in ("SIGNOZ_ENDPOINT", "SIGNOZ_ACCESS_TOKEN", "SIGNOZ_PROFILE"):
            monkeypatch.delenv(var, raising=False)
        with __import__("pytest").raises(click.UsageError, match="must not embed"):
            cm._resolve_credentials(
                "https://bob:secret@localhost:8080", "tok", None)

    def test_resolve_rejects_userinfo_from_env(self, monkeypatch):
        import click

        import signoz_cli.commands.common as cm

        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_DIR",
            __import__("pathlib").Path("/nonexistent-signoz-test"))
        monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)
        monkeypatch.setenv("SIGNOZ_ENDPOINT", "https://bob:secret@h:8080")
        with __import__("pytest").raises(click.UsageError, match="must not embed"):
            cm._resolve_credentials(None, "tok", None)

    def test_history_skips_userinfo_endpoint(self, tmp_path, monkeypatch):
        import signoz_cli.commands.common as cm

        monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
        cm._record_history(
            ["logs", "search", "--endpoint",
             "https://bob:secret@localhost:8080"])
        assert not (tmp_path / "history.json").exists()

    def test_configure_rejects_userinfo(self, runner, monkeypatch, tmp_path):
        from signoz_cli.main import cli

        monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_FILE", tmp_path / "config.json")
        result = runner.invoke(cli, [
            "configure", "-p", "evil",
            "--endpoint", "https://bob:secret@localhost:8080",
            "--token", "tok"])
        assert result.exit_code == 2
        assert "must not embed" in result.output
