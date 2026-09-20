"""Tests for profile-aware config handling."""

import pytest
from click.testing import CliRunner

from signoz_cli.config import (
    get_profile_credentials,
    get_profiles,
    load_config,
    save_config,
    select_profile_name,
)


@pytest.fixture()
def runner():
    return CliRunner()


def _isolate(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".signoz"
    monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", cfg_dir / "config.json")
    # reset the once-per-process warning guard between tests
    monkeypatch.setattr("signoz_cli.config._CONFIG_WARNED", False)
    return cfg_dir


class TestConfigRoundtrip:
    def test_save_and_load(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        save_config({"default": "prod", "profiles": {"prod": {"endpoint": "https://x", "token": "t"}}})
        loaded = load_config()
        assert loaded["default"] == "prod"
        assert loaded["profiles"]["prod"]["token"] == "t"
        # file must be user-only readable
        import stat
        mode = stat.S_IMODE((tmp_path / ".signoz" / "config.json").stat().st_mode)
        assert mode == 0o600

    def test_load_missing_file(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        assert load_config() == {}

    def test_load_invalid_json(self, tmp_path, monkeypatch, capsys):
        d = _isolate(tmp_path, monkeypatch)
        d.mkdir(parents=True)
        (d / "config.json").write_text("{not json")
        assert load_config() == {}
        # A corrupt-but-present config should warn rather than fail silently.
        err = capsys.readouterr().err
        assert "could not read config" in err

    def test_load_missing_file_is_silent(self, tmp_path, monkeypatch, capsys):
        _isolate(tmp_path, monkeypatch)
        assert load_config() == {}
        assert capsys.readouterr().err == ""

    def test_save_creates_dir(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        save_config({"a": 1})
        assert (tmp_path / ".signoz" / "config.json").exists()


class TestProfileHelpers:
    def _cfg(self):
        return {
            "default": "local",
            "profiles": {
                "local": {"endpoint": "http://localhost:8080", "token": "tok-local"},
                "prod": {"endpoint": "https://signoz.example.com", "token": "tok-prod"},
            },
            # legacy flat keys from old configs
            "endpoint": "http://old:8080",
            "token": "tok-old",
        }

    def test_get_profiles_empty(self):
        assert get_profiles({}) == {}
        assert get_profiles({"profiles": None}) == {}

    def test_select_priority(self):
        cfg = self._cfg()
        assert select_profile_name(cfg, "prod", "stage") == "prod"
        assert select_profile_name(cfg, None, "stage") == "stage"
        assert select_profile_name(cfg) == "local"
        assert select_profile_name({}) is None

    def test_profile_credentials(self):
        cfg = self._cfg()
        ep, tok = get_profile_credentials(cfg, "prod")
        assert (ep, tok) == ("https://signoz.example.com", "tok-prod")

    def test_unknown_profile_falls_back_to_legacy(self):
        cfg = self._cfg()
        ep, tok = get_profile_credentials(cfg, "nope")
        assert (ep, tok) == ("http://old:8080", "tok-old")

    def test_no_selection_uses_legacy_keys(self):
        cfg = {"endpoint": "http://old:8080", "token": "tok-old"}
        ep, tok = get_profile_credentials(cfg, None)
        assert (ep, tok) == ("http://old:8080", "tok-old")

    def test_saved_file_roundtrips_profiles(self, tmp_path, monkeypatch):
        _isolate(tmp_path, monkeypatch)
        original = self._cfg()
        save_config(original)
        assert load_config() == original


class TestMalformedProfiles:
    def test_non_dict_entries_dropped(self):
        from signoz_cli.config import get_profiles

        cfg = {"profiles": {"good": {"endpoint": "http://h:8080"},
                            "bad": "oops", "num": 42, "nil": None}}
        assert get_profiles(cfg) == {"good": {"endpoint": "http://h:8080"}}
        assert get_profiles({"profiles": "oops"}) == {}
        assert get_profiles({}) == {}

    def test_list_survives_bad_entry(self, runner, monkeypatch, tmp_path):
        import json as _json

        from signoz_cli.main import cli

        monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(_json.dumps({
            "default": "q",
            "profiles": {"p": "not-a-dict",
                         "q": {"endpoint": "http://h:8080", "token": "t"}}}))
        result = runner.invoke(cli, ["profile", "list"])
        assert result.exit_code == 0, result.output
        assert result.exception is None

    def test_use_of_bad_entry_is_clean_usage_error(
            self, runner, monkeypatch, tmp_path):
        import json as _json

        from signoz_cli.main import cli

        monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_FILE", tmp_path / "config.json")
        (tmp_path / "config.json").write_text(_json.dumps(
            {"profiles": {"p": "not-a-dict"}}))
        result = runner.invoke(cli, ["logs", "search", "-p", "p"])
        assert result.exit_code == 2
        assert "Unknown profile" in result.output
        assert result.exception is None or isinstance(
            result.exception, SystemExit)


class TestMalformedScalars:
    def _write(self, tmp_path, cfg):
        import json as _json

        (tmp_path / "config.json").write_text(_json.dumps(cfg))

    def _isolate_cfg(self, monkeypatch, tmp_path):
        monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.config.CONFIG_FILE", tmp_path / "config.json")
        monkeypatch.setattr(
            "signoz_cli.commands.common.CONFIG_DIR", tmp_path)
        monkeypatch.setattr(
            "signoz_cli.commands.common.CONFIG_FILE",
            tmp_path / "config.json")

    def test_non_string_endpoint_rejected(self, runner, monkeypatch, tmp_path):
        from signoz_cli.main import cli

        self._isolate_cfg(monkeypatch, tmp_path)
        self._write(tmp_path, {"default": "p", "profiles": {
            "p": {"endpoint": 123, "token": "t"}}})
        result = runner.invoke(cli, ["logs", "search"])
        assert result.exit_code == 2
        assert "Invalid endpoint" in result.output
        assert result.exception is None or isinstance(
            result.exception, SystemExit)

    def test_non_string_default_rejected(self, runner, monkeypatch, tmp_path):
        from signoz_cli.main import cli

        self._isolate_cfg(monkeypatch, tmp_path)
        self._write(tmp_path, {"default": ["x"], "profiles": {
            "p": {"endpoint": "http://h:1", "token": "t"}}})
        result = runner.invoke(cli, ["logs", "search"])
        assert result.exit_code == 2
        assert "Invalid default profile" in result.output

    def test_non_string_time_range_rejected(
            self, runner, monkeypatch, tmp_path):
        from signoz_cli.main import cli

        self._isolate_cfg(monkeypatch, tmp_path)
        self._write(tmp_path, {"default": "p", "profiles": {
            "p": {"endpoint": "http://h:1", "token": "t",
                  "default_time_range": 123}}})
        result = runner.invoke(cli, ["logs", "search"])
        assert result.exit_code == 2
        assert "Invalid default_time_range" in result.output

    def test_non_string_token_never_tracebacks(
            self, runner, monkeypatch, tmp_path):
        from signoz_cli.main import cli

        self._isolate_cfg(monkeypatch, tmp_path)
        self._write(tmp_path, {"default": "p", "profiles": {
            "p": {"endpoint": "http://h:1", "token": 99}}})
        result = runner.invoke(cli, ["auth", "whoami", "-p", "p"])
        assert result.exit_code in (0, 2)
        assert result.exception is None or isinstance(
            result.exception, SystemExit)
