"""Tests for credential resolution precedence (flags > env > profile > legacy)."""

import json

import pytest

from signoz_cli.main import _resolve_credentials


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

    def test_unknown_profile_falls_back_to_legacy(self, multi_config):
        ep, tok = _resolve_credentials(None, None, "does-not-exist")
        assert (ep, tok) == ("http://legacy:8080", "tok-legacy")

    def test_nothing_configured(self, tmp_path, monkeypatch):
        cfg_dir = tmp_path / ".signoz"
        monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", cfg_dir)
        monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", cfg_dir / "config.json")
        ep, tok = _resolve_credentials(None, None, None)
        assert ep is None and tok is None


class TestRootProfileFlag:
    def test_root_flag_beats_env_selection(self, multi_config, monkeypatch):
        import signoz_cli.main as m
        monkeypatch.setenv("SIGNOZ_PROFILE", "prod")
        monkeypatch.setattr(m, "_ROOT_PROFILE", "local")
        ep, tok = _resolve_credentials(None, None, None)
        assert (ep, tok) == ("http://local:8080", "tok-local")

    def test_leaf_flag_beats_root_flag(self, multi_config, monkeypatch):
        import signoz_cli.main as m
        monkeypatch.setattr(m, "_ROOT_PROFILE", "local")
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
