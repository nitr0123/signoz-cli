"""Tests for pagination helpers and client retry behaviour."""

import httpx
import pytest

from signoz_cli.client import SigNozApiError, SigNozClient
from signoz_cli.main import _logs_query_all, _traces_query_all


class FakeClient:
    """Scripted stand-in returning queued pages per call."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0

    def query_builder_v5(self, payload):
        self.calls += 1
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def _raw_page(rows, next_cursor=None):
    result = {"queryName": "A", "rows": [{"data": r} for r in rows]}
    if next_cursor is not None:
        result["nextCursor"] = next_cursor
    return {"status": "success", "data": {"type": "raw",
            "data": {"results": [result]}}}


class TestLogsPagination:
    def test_walks_offsets_until_short_page(self):
        # limit=2: full page, full page, short page -> stop
        c = FakeClient([
            _raw_page([{"id": i} for i in range(2)]),
            _raw_page([{"id": i} for i in range(2, 4)]),
            _raw_page([{"id": 4}]),
        ])
        rows = _logs_query_all(c, 0, 10, "", limit=2)
        assert len(rows) == 5
        assert c.calls == 3

    def test_stops_on_exact_multiple(self):
        c = FakeClient([_raw_page([{"id": 0}, {"id": 1}]), _raw_page([])])
        rows = _logs_query_all(c, 0, 10, "", limit=2)
        assert len(rows) == 2
        assert c.calls == 2

    def test_caps_pages(self):
        c = FakeClient([_raw_page([{"id": 0}, {"id": 1}])] * 500)
        rows = _logs_query_all(c, 0, 10, "", limit=2)
        assert len(rows) == 400  # _MAX_PAGES=200 x limit=2

    def test_error_passthrough(self):
        err = {"error": True}
        c = FakeClient([err])
        assert _logs_query_all(c, 0, 10, "", limit=2) == err


class TestTracesPagination:
    def test_follows_cursor(self):
        p1 = {
            "status": "success", "data": {"type": "raw", "data": {"results": [
                {"queryName": "A", "rows": [{"data": {"spanID": "s1"}}],
                 "nextCursor": "CUR"}]}}}
        p2 = {
            "status": "success", "data": {"type": "raw", "data": {"results": [
                {"queryName": "A", "rows": [{"data": {"spanID": "s2"}}]}]}}}
        c = FakeClient([p1, p2])
        rows = _traces_query_all(c, 0, 10, "", limit=1)
        assert [r["spanID"] for r in rows] == ["s1", "s2"]
        assert c.calls == 2

    def test_stops_without_cursor(self):
        c = FakeClient([_raw_page([{"spanID": "s1"}])])
        rows = _traces_query_all(c, 0, 10, "", limit=5)
        assert len(rows) == 1
        assert c.calls == 1


BASE = "http://signoz.test"


class TestRetries:
    def test_retries_on_500_then_succeeds(self):
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return httpx.Response(500, json={"message": "boom"})
            return httpx.Response(200, json={"ok": True})

        import signoz_cli.client as cl
        old_sleep = cl.time.sleep
        cl.time.sleep = lambda s: None
        try:
            c = SigNozClient(BASE, "t", transport=httpx.MockTransport(handler), max_retries=3)
            assert c.list_alerts() == {"ok": True}
            assert attempts["n"] == 3
        finally:
            cl.time.sleep = old_sleep

    def test_no_retry_on_404(self):
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            return httpx.Response(404, json={"message": "nope"})

        c = SigNozClient(BASE, "t", transport=httpx.MockTransport(handler), max_retries=3)
        with pytest.raises(Exception, match="HTTP 404"):
            c.list_alerts()
        assert attempts["n"] == 1

    def test_respects_retry_after_header(self):
        delays = []

        def handler(request):
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})

        import signoz_cli.client as cl
        old_sleep = cl.time.sleep
        cl.time.sleep = delays.append
        try:
            c = SigNozClient(BASE, "t", transport=httpx.MockTransport(handler), max_retries=1)
            with pytest.raises(SigNozApiError):
                c.list_alerts()
        finally:
            cl.time.sleep = old_sleep
        assert 7.0 in delays
