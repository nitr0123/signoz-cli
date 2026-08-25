"""Tests for SigNozClient using httpx.MockTransport (no live server needed)."""

import json

import httpx
import pytest

from signoz_cli.client import SigNozApiError, SigNozClient, _extract_error

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

    def test_non_json_success_returns_raw_dict(self):
        def handler(request):
            return httpx.Response(200, text="<html>fallback</html>")
        assert make_client(handler).list_alerts() == {"raw": "<html>fallback</html>"}


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
