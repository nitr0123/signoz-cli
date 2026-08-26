"""Tests for profile-aware config handling."""



from signoz_cli.config import (
    get_profile_credentials,
    get_profiles,
    load_config,
    save_config,
    select_profile_name,
)


def _isolate(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".signoz"
    monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", cfg_dir / "config.json")
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
