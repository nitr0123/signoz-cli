"""Shared test fixtures and state reset."""

import pytest

import signoz_cli.commands.auth as _auth
import signoz_cli.commands.common as _common
import signoz_cli.config as _config
import signoz_cli.output as _out


def _reset_output_state() -> None:
    """Reset all module-level output holders between tests."""
    _out._OUTPUT_FILE = None
    _out._NO_COLOR = False
    _out._NO_PAGER = False
    _out._WIDE = False
    _out._NO_HEADERS = False
    _out._SORT_BY = None
    _out._SORT_REVERSE = False
    _out._CUSTOM_COLUMNS = None
    _out._JSONPATH = None
    _out._FIELD_SELECTOR = {}
    # Root -p profile: a test that sets this and fails mid-way must not
    # leak the profile into unrelated tests.
    _common._ROOT_PROFILE = None
    _common._TIMEOUT_HOLDER["value"] = None
    _common._INSECURE_HOLDER["value"] = False
    _common._NO_INPUT_HOLDER["value"] = False


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch):
    """Unit tests must never spawn git/network update checks."""
    monkeypatch.setenv("SIGNOZ_NO_UPDATE_CHECK", "1")


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """Unit tests must never depend on ambient credentials or ~/.signoz.

    Several suites failed in CI while passing locally because tool and
    command tests resolved credentials from the developer's real config
    and shell env. Every test now starts with an empty config dir and
    no credential env vars; tests that need auth pin their own (the
    per-file ``_env``/``_isolate`` fixtures keep working: monkeypatch
    stacking means explicit per-test values win). Uses its own directory
    name so per-test fixtures creating ``tmp_path / ".signoz"`` never
    collide with it.
    """
    cfg_dir = tmp_path / ".signoz-global-isolation"
    cfg_dir.mkdir()
    monkeypatch.setattr(_config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(_config, "CONFIG_FILE", cfg_dir / "config.json")
    monkeypatch.setattr(_common, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(_common, "CONFIG_FILE", cfg_dir / "config.json")
    monkeypatch.setattr(_auth, "CONFIG_FILE", cfg_dir / "config.json")
    for var in (
        "SIGNOZ_ENDPOINT",
        "SIGNOZ_ACCESS_TOKEN",
        "SIGNOZ_PROFILE",
        "SIGNOZ_FORMAT",
        "SIGNOZ_TIMEOUT",
        "SIGNOZ_INSECURE",
        "SIGNOZ_PYPI_INDEX",
        "SIGNOZ_UPDATE_REPO",
        "NO_PAGER",
        "PAGER",
    ):
        monkeypatch.delenv(var, raising=False)


def pytest_runtest_setup(item) -> None:  # noqa: ANN001
    _reset_output_state()
