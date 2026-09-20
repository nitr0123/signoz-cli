"""Tests for install/update detection (no network; subprocess mocked)."""

import io
import json as _json
import sys

import pytest
from click.testing import CliRunner

import signoz_cli.commands.common as cm
from signoz_cli.main import cli


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", tmp_path)
    monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
    monkeypatch.delenv("SIGNOZ_NO_UPDATE_CHECK", raising=False)


class _Completed:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _ls_remote_output(*tags):
    lines = [f"deadbeef{i:04d}\trefs/tags/{t}" for i, t in enumerate(tags)]
    lines += ["deadbeef9999\trefs/tags/v0.5.0^{}"]
    return "\n".join(lines) + "\n"


class TestLatestRemoteTag:
    def test_newest_tag_wins(self, monkeypatch):
        import subprocess as _sp

        monkeypatch.setattr(
            _sp, "run",
            lambda *a, **k: _Completed(0, _ls_remote_output("v0.5.8", "v0.5.14", "v0.5.9")),
        )
        assert cm._latest_remote_tag("ssh://x") == "v0.5.14"

    def test_ignores_non_version_tags(self, monkeypatch):
        import subprocess as _sp

        monkeypatch.setattr(
            _sp, "run",
            lambda *a, **k: _Completed(0, "abc\trefs/tags/nightly\n"),
        )
        assert cm._latest_remote_tag("ssh://x") is None

    def test_git_missing(self, monkeypatch):
        import subprocess as _sp

        def boom(*a, **k):
            raise FileNotFoundError("no git")

        monkeypatch.setattr(_sp, "run", boom)
        assert cm._latest_remote_tag("ssh://x") is None

    def test_nonzero_exit(self, monkeypatch):
        import subprocess as _sp

        monkeypatch.setattr(_sp, "run", lambda *a, **k: _Completed(128, ""))
        assert cm._latest_remote_tag("ssh://x") is None


class TestUpdateAvailable:
    def test_opt_out_env(self, monkeypatch):
        monkeypatch.setenv("SIGNOZ_NO_UPDATE_CHECK", "1")
        assert cm._update_available() is None

    def test_fresh_cache_newer_notifies_without_subprocess(self, monkeypatch, tmp_path):
        import subprocess as _sp
        import time as _t

        (tmp_path / ".update-check.json").write_text(_json.dumps({
            "checked_at": _t.time(), "latest": "v9.9.9"}))
        monkeypatch.setattr(cm, "__version__", "0.5.8")

        def boom(*a, **k):
            raise AssertionError("must use cache")

        monkeypatch.setattr(_sp, "run", boom)
        assert cm._update_available() == "v9.9.9"

    def test_fresh_cache_current_silent(self, monkeypatch, tmp_path):
        import time as _t

        (tmp_path / ".update-check.json").write_text(_json.dumps({
            "checked_at": _t.time(), "latest": "v0.5.8"}))
        monkeypatch.setattr(cm, "__version__", "0.5.8")
        assert cm._update_available() is None

    def test_stale_cache_triggers_lookup_and_rewrites(
            self, monkeypatch, tmp_path):
        import subprocess as _sp

        (tmp_path / ".update-check.json").write_text(_json.dumps({
            "checked_at": 0, "latest": "v0.5.8"}))
        monkeypatch.setattr(cm, "__version__", "0.5.8")
        monkeypatch.setattr(
            _sp, "run",
            lambda *a, **k: _Completed(0, _ls_remote_output("v0.5.9")),
        )
        assert cm._update_available() == "v0.5.9"
        cached = _json.loads((tmp_path / ".update-check.json").read_text())
        assert cached["latest"] == "v0.5.9"
        assert cached["checked_at"] > 0

    def test_lookup_failure_cached_as_none(self, monkeypatch, tmp_path):
        import subprocess as _sp

        monkeypatch.setattr(cm, "__version__", "0.5.8")
        monkeypatch.setattr(
            _sp, "run", lambda *a, **k: _Completed(128, ""))
        assert cm._update_available() is None
        cached = _json.loads((tmp_path / ".update-check.json").read_text())
        assert cached["latest"] is None


class TestNotify:
    def _tty(self, monkeypatch):
        out = io.StringIO()
        out.isatty = lambda: True  # type: ignore[method-assign]
        err = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        monkeypatch.setattr(sys, "stderr", err)
        return out, err

    def test_non_tty_silent(self, monkeypatch, capsys):
        monkeypatch.setattr(cm, "__version__", "0.0.1")
        cm.maybe_notify_update()
        assert capsys.readouterr().out == ""
        assert capsys.readouterr().err == ""

    def test_tty_newer_warns(self, monkeypatch):
        _, err = self._tty(monkeypatch)
        monkeypatch.setattr(cm, "__version__", "0.0.1")
        monkeypatch.setattr(cm, "_update_available", lambda: "v9.9.9")
        monkeypatch.setattr(sys, "argv", ["signoz", "status"])
        cm.maybe_notify_update()
        assert "v9.9.9" in err.getvalue()
        assert "signoz update" in err.getvalue()

    def test_skipped_for_update_command(self, monkeypatch):
        _, err = self._tty(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["signoz", "update"])
        cm.maybe_notify_update()
        assert err.getvalue() == ""

    def test_never_raises(self, monkeypatch, capsys):
        _, err = self._tty(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["signoz", "status"])

        def boom():
            raise RuntimeError("cache exploded")

        monkeypatch.setattr(cm, "_update_available", boom)
        cm.maybe_notify_update()  # must not raise
        assert err.getvalue() == ""


class TestUpdateCommand:
    def test_already_up_to_date(self, runner, monkeypatch):
        import signoz_cli.commands.update as _up

        monkeypatch.setattr(_up, "_latest_remote_tag", lambda repo: "v0.5.8")
        monkeypatch.setattr(_up, "__version__", "0.5.8")
        result = runner.invoke(cli, ["update"])
        assert result.exit_code == 0, result.output
        assert "up to date" in result.output

    def test_newer_shows_instructions(self, runner, monkeypatch):
        import signoz_cli.commands.update as _up

        monkeypatch.setattr(_up, "_latest_remote_tag", lambda repo: "v0.6.0")
        monkeypatch.setattr(_up, "__version__", "0.5.8")
        monkeypatch.setattr(
            _up, "_detect_install_method",
            lambda: ("pipx", ["pipx", "x"], "pipx install --force U@<tag>"),
        )
        result = runner.invoke(cli, ["update"])
        assert result.exit_code == 0, result.output
        assert "v0.6.0" in result.output
        assert "--apply" in result.output

    def test_apply_runs_upgrade(self, runner, monkeypatch):
        import subprocess as _sp

        import signoz_cli.commands.update as _up

        monkeypatch.setattr(_up, "_latest_remote_tag", lambda repo: "v0.6.0")
        monkeypatch.setattr(_up, "__version__", "0.5.8")
        seen = {}
        monkeypatch.setattr(
            _up, "_detect_install_method",
            lambda: ("pipx", ["pipx", "install", "--force", "U@{tag}"],
                     "do it"),
        )

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return _Completed(0, "")

        monkeypatch.setattr(_sp, "run", fake_run)
        result = runner.invoke(cli, ["update", "--apply"])
        assert result.exit_code == 0, result.output
        assert seen["cmd"] == ["pipx", "install", "--force", "U@v0.6.0"]
        assert "Upgraded to v0.6.0" in result.output

    def test_apply_failure_surfaces(self, runner, monkeypatch):
        import subprocess as _sp

        import signoz_cli.commands.update as _up

        monkeypatch.setattr(_up, "_latest_remote_tag", lambda repo: "v0.6.0")
        monkeypatch.setattr(_up, "__version__", "0.5.8")
        monkeypatch.setattr(
            _up, "_detect_install_method",
            lambda: ("pipx", ["false"], "do it"),
        )
        monkeypatch.setattr(
            _sp, "run", lambda *a, **k: _Completed(1, ""))
        result = runner.invoke(cli, ["update", "--apply"])
        assert result.exit_code == 2
        assert "failed with exit 1" in result.output

    def test_apply_unsupported_method(self, runner, monkeypatch):
        import signoz_cli.commands.update as _up

        monkeypatch.setattr(_up, "_latest_remote_tag", lambda repo: "v0.6.0")
        monkeypatch.setattr(_up, "__version__", "0.5.8")
        monkeypatch.setattr(
            _up, "_detect_install_method", lambda: ("unknown", None, "by hand"))
        result = runner.invoke(cli, ["update", "--apply"])
        assert result.exit_code == 2
        assert "not supported" in result.output

    def test_lookup_failure_message(self, runner, monkeypatch):
        import signoz_cli.commands.update as _up

        monkeypatch.setattr(_up, "_latest_remote_tag", lambda repo: None)
        result = runner.invoke(cli, ["update"])
        assert result.exit_code == 0, result.output
        assert "Could not determine" in result.output


class TestDetectInstallMethod:
    def test_pipx_venv(self, monkeypatch, tmp_path):
        import signoz_cli.commands.update as _up

        (tmp_path / "pipx_metadata.json").write_text("{}")
        monkeypatch.setattr(_up.sys, "prefix", str(tmp_path))
        method, argv, _ = _up._detect_install_method()
        assert method == "pipx"
        assert argv[0] == "pipx"

    def test_unknown_without_distribution(self, monkeypatch):
        import importlib.metadata as _md

        import signoz_cli.commands.update as _up

        def boom(name):
            raise _md.PackageNotFoundError()

        monkeypatch.setattr(_md, "distribution", boom)
        method, argv, _ = _up._detect_install_method()
        assert method == "unknown"
        assert argv is None


class TestIndexInstallMethod:
    def _no_direct_url(self, monkeypatch):
        import importlib.metadata as _md

        import signoz_cli.commands.update as _up

        class Dist:
            def read_text(self, name):
                return None

        monkeypatch.setattr(_md, "distribution", lambda name: Dist())
        return _up

    def test_index_without_env_gives_manual_instructions(
            self, monkeypatch, tmp_path):
        import signoz_cli.commands.update as _up

        monkeypatch.delenv("SIGNOZ_PYPI_INDEX", raising=False)
        monkeypatch.setattr(
            _up.sys, "prefix", str(tmp_path))  # no pipx metadata
        _up2 = self._no_direct_url(monkeypatch)
        method, argv, instructions = _up2._detect_install_method()
        assert method == "pip"
        assert argv is None
        assert "SIGNOZ_PYPI_INDEX" in instructions

    def test_index_with_env_builds_upgrade_command(self, monkeypatch):

        monkeypatch.setenv(
            "SIGNOZ_PYPI_INDEX", "https://u:t@host/api/packages/hamza/pypi/simple")
        _up2 = self._no_direct_url(monkeypatch)
        method, upgrade, instructions = _up2._detect_install_method()
        assert method == "pip"
        assert upgrade is not None
        env, argv = upgrade
        assert argv[-1] == "signoz-cli=={tag}"
        # Credentials must not travel via ps-visible argv ...
        assert "--index-url" not in argv
        assert not any("u:t@" in c for c in argv)
        # ... but via the environment passed to the subprocess.
        assert env["PIP_INDEX_URL"] == \
            "https://u:t@host/api/packages/hamza/pypi/simple"
        assert "<tag>" in instructions
        # ... and never echoed to the terminal either.
        assert "u:t@" not in instructions

    def test_index_apply_runs_pip(self, runner, monkeypatch):
        import subprocess as _sp

        import signoz_cli.commands.update as _up

        monkeypatch.setattr(_up, "_latest_remote_tag", lambda repo: "v0.6.0")
        monkeypatch.setattr(_up, "__version__", "0.5.8")
        monkeypatch.setenv(
            "SIGNOZ_PYPI_INDEX", "https://u:t@host/api/packages/hamza/pypi/simple")
        monkeypatch.setattr(
            _up.sys, "prefix", "/nonexistent-prefix-for-test")
        seen = {}

        class Dist:
            def read_text(self, name):
                return None

        import importlib.metadata as _md
        monkeypatch.setattr(_md, "distribution", lambda name: Dist())
        monkeypatch.setattr(
            _sp, "run",
            lambda cmd, **k: seen.update(cmd=cmd, kwargs=k) or _Completed(0, ""))
        result = runner.invoke(cli, ["update", "--apply"])
        assert result.exit_code == 0, result.output
        assert seen["cmd"][-1] == "signoz-cli==v0.6.0"
        # Index credentials travel via env, never via argv or terminal.
        assert not any("u:t@" in c for c in seen["cmd"])
        assert seen["kwargs"]["env"]["PIP_INDEX_URL"] == \
            "https://u:t@host/api/packages/hamza/pypi/simple"
        assert "u:t@" not in result.output
        assert "Upgraded to v0.6.0" in result.output


class TestPipxSourceFidelity:
    def test_git_source_repinned(self, tmp_path):
        import json as _json

        import signoz_cli.commands.update as _up

        (tmp_path / "pipx_metadata.json").write_text(_json.dumps({
            "main_package": {
                "package_or_url": "git+ssh://forgejo/hamza/signoz-cli.git@v0.5.1",
                "pip_args": [],
            }}))
        cmd = _up._pipx_upgrade_command(
            str(tmp_path / "pipx_metadata.json"), "ssh://x/y.git")
        assert cmd == ["pipx", "install", "--force",
                       "git+ssh://forgejo/hamza/signoz-cli.git@{tag}"]

    def test_index_source_stays_on_index(self, tmp_path):
        import json as _json

        import signoz_cli.commands.update as _up

        (tmp_path / "pipx_metadata.json").write_text(_json.dumps({
            "main_package": {"package_or_url": "signoz-cli",
                             "pip_args": []}}))
        cmd = _up._pipx_upgrade_command(
            str(tmp_path / "pipx_metadata.json"), "ssh://x/y.git")
        assert cmd == ["pipx", "install", "--force", "signoz-cli=={tag}"]
        assert "git+" not in " ".join(cmd)

    def test_unreadable_metadata_falls_back(self, tmp_path):
        import signoz_cli.commands.update as _up

        assert _up._pipx_upgrade_command(
            str(tmp_path / "missing.json"), "ssh://x/y.git") is None
        (tmp_path / "bad.json").write_text("not json{")
        assert _up._pipx_upgrade_command(
            str(tmp_path / "bad.json"), "ssh://x/y.git") is None
        (tmp_path / "empty.json").write_text("{}")
        assert _up._pipx_upgrade_command(
            str(tmp_path / "empty.json"), "ssh://x/y.git") is None

    def test_detect_uses_metadata_source(self, monkeypatch, tmp_path):
        import json as _json

        import signoz_cli.commands.update as _up

        (tmp_path / "pipx_metadata.json").write_text(_json.dumps({
            "main_package": {"package_or_url": "signoz-cli"}}))
        monkeypatch.setattr(_up.sys, "prefix", str(tmp_path))
        method, argv, _ = _up._detect_install_method()
        assert method == "pipx"
        assert argv == ["pipx", "install", "--force", "signoz-cli=={tag}"]


class TestPipxMetadataRobustness:
    def test_credentials_url_preserved(self, tmp_path):
        import json as _json

        import signoz_cli.commands.update as _up

        (tmp_path / "pipx_metadata.json").write_text(_json.dumps({
            "main_package": {"package_or_url":
                             "git+https://user:s3cret@host/x/y.git@v0.5.1"}}))
        cmd = _up._pipx_upgrade_command(
            str(tmp_path / "pipx_metadata.json"), "ssh://x/y.git")
        assert cmd == ["pipx", "install", "--force",
                       "git+https://user:s3cret@host/x/y.git@{tag}"]

    def test_non_dict_main_package_falls_back(self, tmp_path):
        import json as _json

        import signoz_cli.commands.update as _up

        (tmp_path / "meta.json").write_text(_json.dumps(
            {"main_package": ["not", "a", "dict"]}))
        assert _up._pipx_upgrade_command(
            str(tmp_path / "meta.json"), "ssh://x/y.git") is None

    def test_non_dict_top_level_falls_back(self, tmp_path):
        import signoz_cli.commands.update as _up

        (tmp_path / "meta.json").write_text("[1, 2]")
        assert _up._pipx_upgrade_command(
            str(tmp_path / "meta.json"), "ssh://x/y.git") is None


class TestGitUrlCredentialRedaction:
    def _git_direct_url(self, monkeypatch, url):
        import importlib.metadata as _md
        import json as _json

        import signoz_cli.commands.update as _up

        class Dist:
            def read_text(self, name):
                if name == "direct_url.json":
                    return _json.dumps({"url": url})
                return None

        monkeypatch.setattr(_md, "distribution", lambda name: Dist())
        monkeypatch.setattr(
            _up.sys, "prefix", "/nonexistent-prefix-for-test")
        return _up

    def test_git_instructions_redact_credentials(self, monkeypatch):
        _up = self._git_direct_url(
            monkeypatch, "git+https://user:s3cret@host/x/y.git@v0.5.1")
        method, argv, instructions = _up._detect_install_method()
        assert method == "pip"
        assert "s3cret" not in instructions
        assert "<redacted>" in instructions
        # Execution keeps working credentials; only display is redacted.
        assert any("user:s3cret@" in c for c in argv)

    def test_tag_gate_rejects_hostile_refs(self, monkeypatch):
        import subprocess as _sp

        import signoz_cli.commands.common as cm

        class R:
            returncode = 0
            stdout = ("abc\trefs/tags/v1.2.3;evil\n"
                      "def\trefs/tags/v0.6.0\n"
                      "ghi\trefs/tags/v9.9.9.9.9\n")

        monkeypatch.setattr(_sp, "run", lambda *a, **k: R())
        assert cm._latest_remote_tag("ssh://x/y.git") == "v0.6.0"
