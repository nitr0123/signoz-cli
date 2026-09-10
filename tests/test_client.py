"""Tests for SigNozClient using httpx.MockTransport (no live server needed)."""

import json

import httpx
import pytest

from signoz_cli.client import (
    SigNozApiError,
    SigNozClient,
    _extract_error,
    _parse_retry_after,
)

BASE = "http://signoz.test:8080"


def make_client(handler, token="tok-123"):
    return SigNozClient(BASE, token, transport=httpx.MockTransport(handler))


class RecordingHandler:
    """Captures requests; returns a canned response."""

    def __init__(self, status=200, body=None):
        self.requests = []
        self.status = status
        self.body = body if body is not None else {"status": "success", "data": {}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


class TestAuthAndHeaders:
    def test_api_key_header_sent(self):
        h = RecordingHandler()
        make_client(h, token="sekrit").verify()
        assert h.requests[0].headers["SIGNOZ-API-KEY"] == "sekrit"

    def test_endpoint_is_trimmed_and_joined(self):
        client = SigNozClient("http://x:8080/", "t", transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={})
        ))
        assert client.endpoint == "http://x:8080"


class TestEndpointRouting:
    def test_verify_path(self):
        h = RecordingHandler()
        make_client(h).verify()
        r = h.requests[0]
        assert (r.method, r.url.path) == ("GET", "/api/v1/service_accounts/me")

    def test_query_builder_v5_post_body(self):
        h = RecordingHandler()
        payload = {"schemaVersion": "v1", "start": 1, "end": 2}
        make_client(h).query_builder_v5(payload)
        r = h.requests[0]
        assert (r.method, r.url.path) == ("POST", "/api/v5/query_range")
        assert json.loads(r.content)["start"] == 1

    def test_list_metric_keys_uses_v3_autocomplete(self):
        h = RecordingHandler()
        make_client(h).list_metric_keys()
        r = h.requests[0]
        assert r.url.path == "/api/v3/autocomplete/aggregate_attributes"
        assert r.url.params["dataSource"] == "metrics"

    def test_dashboards_v2_list_params(self):
        h = RecordingHandler()
        make_client(h).list_dashboards_v2(limit=10, offset=20,
                                           query="name contains x", sort="name", order="asc")
        params = h.requests[0].url.params
        assert params["limit"] == "10"
        assert params["offset"] == "20"
        assert params["query"] == "name contains x"
        assert (params["sort"], params["order"]) == ("name", "asc")

    def test_dashboard_crud_paths_and_methods(self):
        h = RecordingHandler()
        c = make_client(h)
        c.create_dashboard_v2({"spec": {}})
        c.get_dashboard_v2("d1")
        c.update_dashboard_v2("d1", {"spec": {}})
        c.patch_dashboard_v2("d1", [{"op": "add", "path": "/tags/-", "value": 1}])
        c.delete_dashboard_v2("d1")

        assert [(r.method, r.url.path) for r in h.requests] == [
            ("POST", "/api/v2/dashboards"),
            ("GET", "/api/v2/dashboards/d1"),
            ("PUT", "/api/v2/dashboards/d1"),
            ("PATCH", "/api/v2/dashboards/d1"),
            ("DELETE", "/api/v2/dashboards/d1"),
        ]
        # patch body is the JSON-Patch array
        assert json.loads(h.requests[3].content)[0]["op"] == "add"

    def test_delete_empty_response_becomes_ok(self):
        def handler(request):
            return httpx.Response(200, content=b"")
        assert make_client(handler).delete_dashboard_v2("d1") == {"status": "ok"}

    def test_alert_history_v2_params(self):
        h = RecordingHandler()
        make_client(h).get_alert_history_timeline(
            "rule-1", start=100, end=200, limit=25, order="desc",
            state="firing", filter_expression="service.name = 'x'", cursor="CUR",
        )
        r = h.requests[0]
        assert (r.method, r.url.path) == ("GET", "/api/v2/rules/rule-1/history/timeline")
        p = r.url.params
        assert p["start"] == "100" and p["end"] == "200"
        assert p["limit"] == "25" and p["order"] == "desc"
        assert p["state"] == "firing"
        assert p["filterExpression"] == "service.name = 'x'"
        assert p["cursor"] == "CUR"

    @pytest.mark.parametrize("suffix", ["stats", "overall_status", "top_contributors"])
    def test_history_subresources_get_only(self, suffix):
        h = RecordingHandler()
        c = make_client(h)
        getattr(c, f"get_alert_history_{suffix}")("r9", 1, 2)
        r = h.requests[0]
        assert (r.method, r.url.path) == ("GET", f"/api/v2/rules/r9/history/{suffix}")

    def test_services_post_nanoseconds_as_strings(self):
        h = RecordingHandler()
        make_client(h).list_services("1000", "2000")
        r = h.requests[0]
        assert (r.method, r.url.path) == ("POST", "/api/v1/services")
        assert json.loads(r.content) == {"start": "1000", "end": "2000"}

    def test_top_operations_with_tags(self):
        h = RecordingHandler()
        make_client(h).get_service_top_operations("svc", "1", "2",
                                                  tags=[{"key": "k", "value": "v"}])
        body = json.loads(h.requests[0].content)
        assert body["service"] == "svc"
        assert body["tags"] == [{"key": "k", "value": "v"}]


class TestPromqlSqlRequests:
    def test_promql_forwards_step_interval(self):
        h = RecordingHandler()
        make_client(h).query_promql("up", start=1, end=2, step=45)
        r = h.requests[0]
        assert (r.method, r.url.path) == ("POST", "/api/v5/query_range")
        spec = json.loads(r.content)["compositeQuery"]["queries"][0]["spec"]
        # Regression: the old inline payload accepted `step` but never sent it.
        # The wire field is `step` (0.138 rejects `stepInterval` on promql specs).
        assert spec["step"] == 45
        assert spec["query"] == "up"

    def test_sql_request_shape(self):
        h = RecordingHandler()
        make_client(h).query_sql("SELECT 1", start=1, end=2)
        body = json.loads(h.requests[0].content)
        assert body["requestType"] == "raw"
        q = body["compositeQuery"]["queries"][0]
        assert q["type"] == "clickhouse_sql"
        assert q["spec"]["query"] == "SELECT 1"


class TestErrorHandling:
    def test_error_raises_signoz_api_error(self):
        body = {"status": "error",
                "error": {"type": "forbidden", "code": "authz_forbidden",
                          "message": "not allowed"}}
        def handler(request):
            return httpx.Response(403, json=body)
        with pytest.raises(SigNozApiError) as exc:
            make_client(handler).list_alerts()
        assert exc.value.status_code == 403
        assert "authz_forbidden" in str(exc.value)

    def test_non_json_success_raises_api_error(self):
        def handler(request):
            return httpx.Response(200, text="<html>fallback</html>")
        with pytest.raises(SigNozApiError, match="non-JSON"):
            make_client(handler).list_alerts()


class TestNetworkRetry:
    def test_retries_on_transport_error_then_succeeds(self, monkeypatch):
        import signoz_cli.client as cl
        monkeypatch.setattr(cl.time, "sleep", lambda s: None)
        attempts = {"n": 0}

        def handler(request):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={"ok": True})

        c = SigNozClient(BASE, "t", transport=httpx.MockTransport(handler), max_retries=3)
        assert c.list_alerts() == {"ok": True}
        assert attempts["n"] == 3

    def test_gives_up_after_max_retries(self, monkeypatch):
        import signoz_cli.client as cl
        monkeypatch.setattr(cl.time, "sleep", lambda s: None)

        def handler(request):
            raise httpx.ConnectError("nope", request=request)

        c = SigNozClient(BASE, "t", transport=httpx.MockTransport(handler), max_retries=2)
        with pytest.raises(httpx.ConnectError):
            c.list_alerts()


class TestContextManager:
    def test_closes_on_exit(self):
        closed = {"v": False}
        c = SigNozClient(BASE, "t", transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={})))
        orig = c.close

        def _close():
            closed["v"] = True
            orig()

        c.close = _close  # type: ignore[method-assign]
        with c as ctx:
            assert ctx is c
        assert closed["v"] is True


class TestRetryAfterParsing:
    def test_integer_seconds(self):
        assert _parse_retry_after("7") == 7

    def test_blank_is_none(self):
        assert _parse_retry_after("") is None
        assert _parse_retry_after("   ") is None

    def test_garbage_is_none(self):
        assert _parse_retry_after("soon") is None

    def test_http_date_is_seconds_from_now(self):
        import email.utils
        import time as _t
        future = email.utils.formatdate(_t.time() + 30, usegmt=True)
        secs = _parse_retry_after(future)
        assert secs is not None and 20 <= secs <= 40


class TestExtractErrorMessage:
    def test_signoz_envelope(self):
        resp = httpx.Response(400, json={"error": {"code": "invalid_input",
                                                   "message": "bad stuff"}})
        assert _extract_error(resp) == "invalid_input: bad stuff"

    def test_flat_message(self):
        resp = httpx.Response(400, json={"message": "simple failure"})
        assert _extract_error(resp) == "simple failure"

    def test_html_falls_back_to_text(self):
        resp = httpx.Response(500, text="<html>boom</html>")
        assert "<html>" in _extract_error(resp)

    def test_truncates_long_messages(self):
        resp = httpx.Response(500, text="x" * 10_000)
        assert len(_extract_error(resp)) <= 503


class TestServiceAccountRoles:
    def test_failed_role_assignment_reported(self):
        def handler(request):
            if "/roles" in request.url.path:
                return httpx.Response(403, json={"error": {
                    "code": "authz_forbidden", "message": "not allowed"}})
            return httpx.Response(201, json={"data": {"id": "sa1"}})

        c = make_client(handler)
        _data, failed = c.create_service_account("new-sa", ["role-id-1"])
        assert failed and "role-id-1" in failed[0]

    def test_successful_role_assignment(self):
        def handler(request):
            return httpx.Response(200, json={"status": "success", "data": {}})

        c = make_client(handler)
        _data, failed = c.create_service_account("new-sa", ["role-id-1"])
        assert failed == []


class TestNonJsonResponses:
    def test_html_200_raises_api_error(self):
        # Unknown API paths serve the SPA fallback page with HTTP 200
        # (seen with saved-views on v0.136). Must fail loudly instead of
        # rendering HTML as table rows.
        def handler(request):
            return httpx.Response(
                200,
                content=b"<!doctype html><html></html>",
                headers={"content-type": "text/html"},
            )

        c = make_client(handler)
        with pytest.raises(SigNozApiError) as exc:
            c.list_saved_views()
        assert exc.value.status_code == 200
        assert "non-JSON" in exc.value.message

    def test_plain_text_200_raises_api_error(self):
        def handler(request):
            return httpx.Response(200, content=b"OK")

        c = make_client(handler)
        with pytest.raises(SigNozApiError):
            c.verify()

    def test_empty_body_still_ok(self):
        def handler(request):
            return httpx.Response(200, content=b"")

        c = make_client(handler)
        assert c.verify() == {}

    def test_html_error_body_extracts_text(self):
        def handler(request):
            return httpx.Response(502, content=b"<html>bad gateway</html>")

        c = make_client(handler)
        with pytest.raises(SigNozApiError) as exc:
            c.verify()
        assert exc.value.status_code == 502
        assert "bad gateway" in exc.value.message


class TestIdempotentRetry:
    """Mutations must not be retried on ambiguous failures (duplicates)."""

    def _counting(self, statuses, monkeypatch):
        import signoz_cli.client as cl

        monkeypatch.setattr(cl.time, "sleep", lambda s: None)
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(statuses[min(calls["n"] - 1, len(statuses) - 1)],
                                  json={"status": "success", "data": {}})

        return calls, handler

    def test_post_create_not_retried_on_500(self, monkeypatch):
        calls, handler = self._counting([500], monkeypatch)
        with pytest.raises(SigNozApiError):
            make_client(handler).create_dashboard_v2({"name": "x"})
        assert calls["n"] == 1

    def test_post_create_not_retried_on_transport_error(self, monkeypatch):
        import signoz_cli.client as cl

        monkeypatch.setattr(cl.time, "sleep", lambda s: None)
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            raise httpx.ConnectError("down")

        with pytest.raises(httpx.ConnectError):
            make_client(handler).create_rule({"name": "x"})
        assert calls["n"] == 1

    def test_post_create_retried_on_429(self, monkeypatch):
        calls, handler = self._counting([429, 200], monkeypatch)
        make_client(handler).create_channel({"name": "x"})
        assert calls["n"] == 2

    def test_patch_not_retried_on_500(self, monkeypatch):
        calls, handler = self._counting([500], monkeypatch)
        with pytest.raises(SigNozApiError):
            make_client(handler).patch_rule("r1", [{"op": "add"}])
        assert calls["n"] == 1

    def test_get_still_retried_on_500(self, monkeypatch):
        calls, handler = self._counting([500, 200], monkeypatch)
        make_client(handler).list_alerts()
        assert calls["n"] == 2

    def test_put_full_replace_still_retried(self, monkeypatch):
        calls, handler = self._counting([503, 200], monkeypatch)
        make_client(handler).update_channel("c1", {"name": "x"})
        assert calls["n"] == 2


class TestSavedViewsPaths:
    """Saved views live under /api/v1/explorer/views (v1/saved_views is gone)."""

    def test_list_uses_explorer_path(self):
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"status": "success", "data": []})

        make_client(handler).list_saved_views("traces")
        assert seen["path"] == "/api/v1/explorer/views"
        assert seen["params"].get("sourcePage") == "traces"

    def test_crud_paths(self):
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path))
            return httpx.Response(200, json={"status": "success", "data": {}})

        c = make_client(handler)
        c.get_saved_view("abc")
        c.create_saved_view({"name": "x"})
        c.delete_saved_view("abc")
        assert ("GET", "/api/v1/explorer/views/abc") in seen
        assert ("POST", "/api/v1/explorer/views") in seen
        assert ("DELETE", "/api/v1/explorer/views/abc") in seen
