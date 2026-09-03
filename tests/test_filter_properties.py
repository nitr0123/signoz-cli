"""Property tests for filter escaping and filter builders.

``_q()`` is the single choke point between user-supplied values and the
SigNoz filter grammar (SQL-style quote doubling, live-verified on v0.138:
backslash escapes are rejected with HTTP 400). These properties pin the
escaping contract so a future "cleanup" cannot silently reintroduce filter
injection or 400s on apostrophes.
"""

from hypothesis import given
from hypothesis import strategies as st

from signoz_cli.commands.common import (
    _append_filter,
    _build_log_filter,
    _build_trace_filter,
    _q,
)

# Quote-heavy alphabet: hypothesis *will* generate injection attempts like
# "x' OR '1'='1" from this.
HOSTILE_TEXT = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
        blacklist_characters="\x00",
    ),
    max_size=30,
)


def _lone_quotes(s: str) -> int:
    """Count quotes that are not part of a doubled pair."""
    return s.replace("''", "").count("'")


@given(HOSTILE_TEXT)
def test_q_doubles_every_quote(value):
    assert _lone_quotes(_q(value)) == 0


@given(HOSTILE_TEXT)
def test_q_round_trips(value):
    assert _q(value).replace("''", "'") == value


@given(HOSTILE_TEXT)
def test_q_is_not_idempotent_by_design(value):
    # Double-escaping must be visible so callers never escape twice:
    # _q must be applied exactly once per value.
    if "'" in value:
        assert _q(_q(value)) != _q(value)
    else:
        assert _q(_q(value)) == _q(value) == value


@given(HOSTILE_TEXT)
def test_q_adds_no_backslashes(value):
    assert "\\" not in _q(value.replace("\\", ""))


def test_q_known_regression():
    assert _q("it's-broken") == "it''s-broken"
    assert _q("x' OR '1'='1") == "x'' OR ''1''=''1"
    assert _q("") == ""
    assert _q("plain") == "plain"


@given(
    service=HOSTILE_TEXT,
    operation=HOSTILE_TEXT,
    error_filter=st.sampled_from([None, "true", "false"]),
)
def test_trace_filter_values_always_safely_quoted(service, operation, error_filter):
    # Falsy values are skipped (empty string ≡ unset) -- pinned below.
    filt = _build_trace_filter(None, service, operation, error_filter)
    want = []
    if service:
        want.append(f"service.name = '{_q(service)}'")
    if operation:
        want.append(f"name = '{_q(operation)}'")
    if error_filter:
        want.append(f"hasError = {error_filter}")
    # Exact output: no room for unescaped quotes anywhere.
    assert filt == " AND ".join(want)


def test_empty_filter_value_is_skipped():
    assert _build_trace_filter(None, "", "", None) == ""
    assert _build_trace_filter(None, None, None, None) == ""
    assert _build_log_filter(None, "", "", "") == ""


@given(
    service=HOSTILE_TEXT,
    severity=HOSTILE_TEXT,
    search_text=HOSTILE_TEXT,
)
def test_log_filter_values_always_safely_quoted(service, severity, search_text):
    filt = _build_log_filter(None, service, severity, search_text)
    want = []
    if service:
        want.append(f"service.name = '{_q(service)}'")
    if severity:
        want.append(f"severity_text = '{_q(severity)}'")
    if search_text:
        want.append(f"body CONTAINS '{_q(search_text)}'")
    assert filt == " AND ".join(want)


@given(HOSTILE_TEXT)
def test_query_passthrough_is_verbatim(query):
    # --query/--filter are whole expressions the user writes on purpose.
    assert _build_trace_filter(query, "svc", "op", None).startswith(query)
    assert _build_log_filter(query, "svc", "sev", "txt").startswith(query)


@given(HOSTILE_TEXT, HOSTILE_TEXT)
def test_append_filter_properties(a, b):
    assert _append_filter("", "") == ""
    assert _append_filter("", b) == b
    assert _append_filter(a, "") == a
    if a and b:
        assert _append_filter(a, b) == f"{a} AND {b}"
