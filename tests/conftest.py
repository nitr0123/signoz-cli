"""Shared test fixtures and state reset."""

import signoz_cli.commands.common as _common
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


def pytest_runtest_setup(item) -> None:  # noqa: ANN001
    _reset_output_state()
