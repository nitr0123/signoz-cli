"""Tests for encrypted bundles (Fernet + PBKDF2, passphrase via env/prompt)."""

import json as _json

import pytest
from click.testing import CliRunner

from signoz_cli.bundle_crypto import (
    decrypt_files,
    encrypt_files,
    read_passphrase,
)
from signoz_cli.main import cli


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
    monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
    monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)
    monkeypatch.delenv("SIGNOZ_BUNDLE_PASSPHRASE", raising=False)


class TestCryptoPrimitives:
    def test_round_trip(self):
        files = {"manifest.json": '{"a": 1}', "dashboards/d.json": "{}"}
        out = encrypt_files(files, "correct-horse-123")
        assert decrypt_files(out, "correct-horse-123") == files

    def test_wrong_passphrase_fails_clean(self):
        out = encrypt_files({"a.json": "{}"}, "correct-horse-123")
        with pytest.raises(ValueError, match="wrong passphrase|tampered"):
            decrypt_files(out, "wrong-passphrase-1")

    def test_tampered_token_fails_clean(self):
        out = encrypt_files({"a.json": "{}"}, "correct-horse-123")
        doc = _json.loads(out)
        token = doc["token"]
        doc["token"] = ("A" if token[0] != "A" else "B") + token[1:]
        with pytest.raises(ValueError, match="wrong passphrase|tampered"):
            decrypt_files(_json.dumps(doc), "correct-horse-123")

    def test_garbage_inputs_fail_clean(self):
        for bad in ["not json{", '{"format": "nope"}', "[]",
                    _json.dumps({"format": "signoz-bundle-enc/v1"})]:
            with pytest.raises(ValueError):
                decrypt_files(bad, "correct-horse-123")

    def test_salts_differ(self):
        a = encrypt_files({"x": "1"}, "correct-horse-123")
        b = encrypt_files({"x": "1"}, "correct-horse-123")
        assert _json.loads(a)["token"] != _json.loads(b)["token"]

    def test_passphrase_from_env(self, monkeypatch):
        monkeypatch.setenv("SIGNOZ_BUNDLE_PASSPHRASE", "env-passphrase-1")
        assert read_passphrase() == "env-passphrase-1"

    def test_short_passphrase_rejected(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda prompt="": "short")
        with pytest.raises(Exception, match="at least"):
            read_passphrase()

    def test_mismatched_confirm_rejected(self, monkeypatch):
        answers = iter(["long-enough-passphrase", "different-passphrase"])
        monkeypatch.setattr(
            "getpass.getpass", lambda prompt="": next(answers))
        with pytest.raises(Exception, match="do not match"):
            read_passphrase(confirm=True)


class TestEncryptedBundleFlow:
    def test_export_encrypt_then_import_decrypt(
            self, runner, monkeypatch, tmp_path):
        import signoz_cli.commands.common as _cm

        class Fake:
            endpoint = "http://sig.test:8080"

            def __init__(self, *a, **k):
                self.created = []

            def close(self):
                pass

            def get_server_version(self):
                return {"version": "v0.138.0"}

            def list_dashboards_v2(self, **k):
                return {"status": "ok",
                        "data": {"dashboards": [{"id": "d1", "name": "D"}]}}

            def get_dashboard_v2(self, i):
                return {"status": "ok",
                        "data": {"id": "d1", "name": "D"}}

            def list_rules(self):
                return {"status": "ok", "data": {"rules": []}}

            def list_channels(self):
                return {"status": "ok", "data": []}

            def list_saved_views(self, source_page="logs"):
                return {"status": "ok", "data": []}

            def create_dashboard_v2(self, doc):
                self.created.append(doc)
                return {"status": "ok", "data": {"id": "d1"}}

        monkeypatch.setattr(_cm, "SigNozClient", Fake)
        monkeypatch.setenv("SIGNOZ_BUNDLE_PASSPHRASE", "test-passphrase-12")
        bundle = tmp_path / "backup"
        result = runner.invoke(
            cli, ["export", str(bundle), "--yes", "--encrypt"])
        assert result.exit_code == 0, result.output
        enc = tmp_path / "backup.enc"
        assert enc.exists()
        assert not bundle.exists()  # only the .enc file is written
        # No plaintext leakage: JSON punctuation (quotes, spaces) can
        # never occur in base64 output, so this is deterministic.
        assert '"name": "D"' not in enc.read_text()

        created = []
        monkeypatch.setattr(
            _cm, "SigNozClient",
            lambda *a, **k: type("F2", (), {
                "__init__": lambda self, *a, **k: None,
                "close": lambda self: None,
                "list_dashboards_v2": lambda self, **k: {
                    "status": "ok", "data": {"dashboards": []}},
                "list_rules": lambda self: {
                    "status": "ok", "data": {"rules": []}},
                "list_channels": lambda self: {
                    "status": "ok", "data": []},
                "list_saved_views": lambda self, source_page="logs": {
                    "status": "ok", "data": []},
                "create_dashboard_v2": lambda self, doc: created.append(
                    doc) or {"status": "ok", "data": {"id": "d1"}},
            })())
        result = runner.invoke(
            cli, ["import", str(enc), "--decrypt", "--yes"])
        assert result.exit_code == 0, result.output
        assert created and created[0]["name"] == "D"

    def test_decrypt_wrong_passphrase_fails_clean(
            self, runner, monkeypatch, tmp_path):
        enc = tmp_path / "b.enc"
        enc.write_text(encrypt_files({"m": "{}"}, "right-passphrase-1"))
        monkeypatch.setenv("SIGNOZ_BUNDLE_PASSPHRASE", "wrong-passphrase-2")
        result = runner.invoke(cli, ["import", str(enc), "--decrypt",
                                     "--yes"])
        assert result.exit_code == 2
        assert "passphrase" in result.output or "decrypt" in result.output

    def test_decrypt_needs_a_file(self, runner, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        result = runner.invoke(cli, ["import", str(d), "--decrypt", "--yes"])
        assert result.exit_code == 2
        assert "--decrypt" in result.output

    def test_path_traversal_in_envelope_rejected(
            self, runner, monkeypatch, tmp_path):
        enc = tmp_path / "evil.enc"
        enc.write_text(encrypt_files(
            {"../escape.json": "{}"}, "test-passphrase-12"))
        monkeypatch.setenv("SIGNOZ_BUNDLE_PASSPHRASE", "test-passphrase-12")
        result = runner.invoke(cli, ["import", str(enc), "--decrypt",
                                     "--yes"])
        assert result.exit_code == 2
        assert "unsafe path" in result.output
        assert not (tmp_path / "escape.json").exists()

    def test_empty_export_round_trips(self, runner, monkeypatch, tmp_path):
        import signoz_cli.commands.common as _cm

        class Fake:
            endpoint = "http://sig.test:8080"

            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def get_server_version(self):
                return {"version": "v0.138.0"}

            def list_dashboards_v2(self, **k):
                return {"status": "ok", "data": {"dashboards": []}}

            def list_rules(self):
                return {"status": "ok", "data": {"rules": []}}

            def list_channels(self):
                return {"status": "ok", "data": []}

            def list_saved_views(self, source_page="logs"):
                return {"status": "ok", "data": []}

        monkeypatch.setattr(_cm, "SigNozClient", Fake)
        monkeypatch.setenv("SIGNOZ_BUNDLE_PASSPHRASE", "test-passphrase-12")
        bundle = tmp_path / "empty"
        result = runner.invoke(
            cli, ["export", str(bundle), "--yes", "--encrypt"])
        assert result.exit_code == 0, result.output
        result = runner.invoke(
            cli, ["import", str(tmp_path / "empty.enc"), "--decrypt",
                  "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "No bundle kinds" not in result.output
