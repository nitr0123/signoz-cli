"""Routing tests for the SigNoz 0.141-0.147 client methods (MockTransport, no server).

Every expected (method, path, body) below was checked against the v0.147.0
OpenAPI spec (docs/api/openapi.yml in the SigNoz repo).
"""

import io
import json

import httpx
import pytest

from signoz_cli.client import SigNozApiError, SigNozClient

BASE = "http://signoz.test:8080"


class Recorder:
    def __init__(self, status=200, body=None, content=None, headers=None):
        self.requests = []
        self.status = status
        self.body = body if body is not None else {"status": "success", "data": {}}
        self.content = content
        self.headers = headers or {}

    def __call__(self, request):
        self.requests.append(request)
        if self.content is not None:
            return httpx.Response(self.status, content=self.content, headers=self.headers)
        if self.status == 204:
            return httpx.Response(204)
        return httpx.Response(self.status, json=self.body, headers=self.headers)


def client_for(handler):
    return SigNozClient(BASE, "tok", transport=httpx.MockTransport(handler), max_retries=0)


# (call, method, path, json body or None for "no body", query params or None)
ROUTES = [
    (lambda c: c.clone_rule("r1"), "POST", "/api/v2/rules/r1/clone", None, None),
    (lambda c: c.clone_dashboard_v2("d1"), "POST", "/api/v2/dashboards/d1/clone", None, None),
    (lambda c: c.lock_dashboard_v2("d1"), "PUT", "/api/v2/dashboards/d1/lock", None, None),
    (lambda c: c.unlock_dashboard_v2("d1"), "DELETE", "/api/v2/dashboards/d1/lock", None, None),
    (lambda c: c.get_public_dashboard("d1"), "GET", "/api/v1/dashboards/d1/public", None, None),
    (lambda c: c.create_public_dashboard("d1", True, "30m"), "POST",
     "/api/v1/dashboards/d1/public", {"timeRangeEnabled": True, "defaultTimeRange": "30m"}, None),
    (lambda c: c.update_public_dashboard("d1", False, "1h"), "PUT",
     "/api/v1/dashboards/d1/public", {"timeRangeEnabled": False, "defaultTimeRange": "1h"}, None),
    (lambda c: c.delete_public_dashboard("d1"), "DELETE", "/api/v1/dashboards/d1/public",
     None, None),
    (lambda c: c.get_trace_summary("t1"), "GET", "/api/v1/traces/t1/summary", None, None),
    (lambda c: c.get_trace_thread("t1", limit=5, after="c1", span_id="s1"), "GET",
     "/api/v1/traces/t1/thread", None, {"limit": "5", "after": "c1", "spanId": "s1"}),
    (lambda c: c.list_service_account_keys("sa"), "GET", "/api/v1/service_accounts/sa/keys",
     None, None),
    (lambda c: c.create_service_account_key("sa", "ci", 0), "POST",
     "/api/v1/service_accounts/sa/keys", {"name": "ci", "expiresAt": 0}, None),
    (lambda c: c.update_service_account_key("sa", "k1", "ci", 99), "PUT",
     "/api/v1/service_accounts/sa/keys/k1", {"name": "ci", "expiresAt": 99}, None),
    (lambda c: c.revoke_service_account_key("sa", "k1"), "DELETE",
     "/api/v1/service_accounts/sa/keys/k1", None, None),
    (lambda c: c.list_users(), "GET", "/api/v2/users", None, None),
    (lambda c: c.get_user("u1"), "GET", "/api/v2/users/u1", None, None),
    (lambda c: c.get_me(), "GET", "/api/v2/users/me", None, None),
    (lambda c: c.invite_user("a@b.c", "Ann", ["r1"], frontend_base_url="http://ui"), "POST",
     "/api/v2/users", {"email": "a@b.c", "displayName": "Ann", "userRoles": [{"id": "r1"}],
                       "frontendBaseUrl": "http://ui"}, None),
    (lambda c: c.update_user("u1", "Bo"), "PUT", "/api/v2/users/u1", {"displayName": "Bo"}, None),
    (lambda c: c.delete_user("u1"), "DELETE", "/api/v2/users/u1", None, None),
    (lambda c: c.get_user_roles("u1"), "GET", "/api/v2/users/u1/roles", None, None),
    (lambda c: c.assign_user_role("u1", "r1"), "POST", "/api/v2/user_roles",
     {"userId": "u1", "roleId": "r1"}, None),
    (lambda c: c.delete_user_role("ur1"), "DELETE", "/api/v2/user_roles/ur1", None, None),
    (lambda c: c.get_reset_password_token("u1"), "GET", "/api/v2/users/u1/reset_password_tokens",
     None, None),
    (lambda c: c.create_reset_password_token("u1"), "PUT",
     "/api/v2/users/u1/reset_password_tokens", None, None),
    (lambda c: c.get_role("r1"), "GET", "/api/v1/roles/r1", None, None),
    (lambda c: c.create_role({"name": "x"}), "POST", "/api/v1/roles", {"name": "x"}, None),
    (lambda c: c.update_role("r1", {"description": "d"}), "PUT", "/api/v1/roles/r1",
     {"description": "d"}, None),
    (lambda c: c.delete_role("r1"), "DELETE", "/api/v1/roles/r1", None, None),
    (lambda c: c.list_role_users("r1"), "GET", "/api/v2/roles/r1/users", None, None),
    (lambda c: c.list_infra("pods", {"start": 1, "end": 2, "limit": 5}), "POST",
     "/api/v2/infra_monitoring/pods", {"start": 1, "end": 2, "limit": 5}, None),
    (lambda c: c.get_infra_checks("hosts"), "GET", "/api/v2/infra_monitoring/checks", None,
     {"type": "hosts"}),
    (lambda c: c.list_ingestion_keys(), "GET", "/api/v2/gateway/ingestion_keys", None,
     {"page": "1", "per_page": "50"}),
    (lambda c: c.list_ingestion_keys(name="prod"), "GET", "/api/v2/gateway/ingestion_keys/search",
     None, {"page": "1", "per_page": "50", "name": "prod"}),
    (lambda c: c.get_ingestion_key("k1"), "GET", "/api/v2/gateway/ingestion_keys/k1", None, None),
    (lambda c: c.create_ingestion_key({"name": "n"}), "POST", "/api/v2/gateway/ingestion_keys",
     {"name": "n"}, None),
    (lambda c: c.update_ingestion_key("k1", {"name": "n"}), "PATCH",
     "/api/v2/gateway/ingestion_keys/k1", {"name": "n"}, None),
    (lambda c: c.delete_ingestion_key("k1"), "DELETE", "/api/v2/gateway/ingestion_keys/k1",
     None, None),
    (lambda c: c.list_ingestion_key_limits("k1"), "GET",
     "/api/v2/gateway/ingestion_keys/k1/limits", None, None),
    (lambda c: c.create_ingestion_limit({"keyId": "k1"}), "POST",
     "/api/v2/gateway/ingestion_limits", {"keyId": "k1"}, None),
    (lambda c: c.update_ingestion_limit("l1", {"config": {}}), "PATCH",
     "/api/v2/gateway/ingestion_limits/l1", {"config": {}}, None),
    (lambda c: c.delete_ingestion_limit("l1"), "DELETE", "/api/v2/gateway/ingestion_limits/l1",
     None, None),
    (lambda c: c.inspect_metric("m", 1, 2, "a = 'b'"), "POST", "/api/v2/metrics/inspect",
     {"metricName": "m", "start": 1, "end": 2, "filter": {"expression": "a = 'b'"}}, None),
    (lambda c: c.get_metrics_treemap(1, 2, mode="samples", limit=3), "POST",
     "/api/v2/metrics/treemap", {"start": 1, "end": 2, "mode": "samples", "limit": 3}, None),
    (lambda c: c.list_reduction_rules(metricName="m", search=None, limit=5), "GET",
     "/api/v2/metric_reduction_rules", None, {"metricName": "m", "limit": "5"}),
    (lambda c: c.get_reduction_rule("x"), "GET", "/api/v2/metric_reduction_rules/x", None, None),
    (lambda c: c.create_reduction_rule({"metricName": "m"}), "POST",
     "/api/v2/metric_reduction_rules", {"metricName": "m"}, None),
    (lambda c: c.update_reduction_rule("x", {"matchType": "drop"}), "PUT",
     "/api/v2/metric_reduction_rules/x", {"matchType": "drop"}, None),
    (lambda c: c.delete_reduction_rule("x"), "DELETE", "/api/v2/metric_reduction_rules/x",
     None, None),
    (lambda c: c.preview_reduction_rule({"metricName": "m"}), "POST",
     "/api/v2/metric_reduction_rules/preview", {"metricName": "m"}, None),
    (lambda c: c.get_reduction_stats(), "GET", "/api/v2/metric_reduction_rules/stats", None, None),
    (lambda c: c.list_promoted_paths(signal="logs", promoted=True, indexes=None), "GET",
     "/api/v1/promoted_paths", None, {"signal": "logs", "promoted": "true"}),
    (lambda c: c.promote_paths([{"path": "a"}]), "POST", "/api/v1/promoted_paths",
     [{"path": "a"}], None),
    (lambda c: c.index_materialized_paths("logs", dry_run=True), "POST",
     "/api/v1/promoted_paths/materialized", {"signal": "logs", "dryRun": True}, None),
    (lambda c: c.list_route_policies(), "GET", "/api/v1/route_policies", None, None),
    (lambda c: c.get_route_policy("p"), "GET", "/api/v1/route_policies/p", None, None),
    (lambda c: c.create_route_policy({"name": "p"}), "POST", "/api/v1/route_policies",
     {"name": "p"}, None),
    (lambda c: c.update_route_policy("p", {"name": "p"}), "PUT", "/api/v1/route_policies/p",
     {"name": "p"}, None),
    (lambda c: c.delete_route_policy("p"), "DELETE", "/api/v1/route_policies/p", None, None),
    (lambda c: c.list_rule_views(), "GET", "/api/v2/rule_views", None, None),
    (lambda c: c.create_rule_view({"name": "v"}), "POST", "/api/v2/rule_views", {"name": "v"},
     None),
    (lambda c: c.update_rule_view("v", {"name": "v"}), "PUT", "/api/v2/rule_views/v",
     {"name": "v"}, None),
    (lambda c: c.delete_rule_view("v"), "DELETE", "/api/v2/rule_views/v", None, None),
    (lambda c: c.list_dashboard_views(), "GET", "/api/v2/dashboard_views", None, None),
    (lambda c: c.create_dashboard_view({"name": "v"}), "POST", "/api/v2/dashboard_views",
     {"name": "v"}, None),
    (lambda c: c.update_dashboard_view("v", {"name": "v"}), "PUT", "/api/v2/dashboard_views/v",
     {"name": "v"}, None),
    (lambda c: c.delete_dashboard_view("v"), "DELETE", "/api/v2/dashboard_views/v", None, None),
    (lambda c: c.list_quick_filters(), "GET", "/api/v2/quick_filters", None, None),
    (lambda c: c.get_quick_filters("logs"), "GET", "/api/v2/quick_filters/logs", None, None),
    (lambda c: c.set_quick_filters("logs", [{"name": "a"}]), "PUT", "/api/v2/quick_filters/logs",
     {"filters": [{"name": "a"}]}, None),
    (lambda c: c.list_cloud_accounts("aws"), "GET", "/api/v1/cloud_integrations/aws/accounts",
     None, None),
    (lambda c: c.get_cloud_account("aws", "a1"), "GET",
     "/api/v1/cloud_integrations/aws/accounts/a1", None, None),
    (lambda c: c.create_cloud_account("aws", {"aws": {"regions": ["r"]}}, {"sigNozApiKey": "k"}),
     "POST", "/api/v1/cloud_integrations/aws/accounts",
     {"config": {"aws": {"regions": ["r"]}}, "credentials": {"sigNozApiKey": "k"}}, None),
    (lambda c: c.get_ingestion_limit("l1"), "GET", "/api/v2/gateway/ingestion_limits/l1",
     None, None),
    (lambda c: c.update_cloud_account("aws", "a1", {"aws": {}}), "PUT",
     "/api/v1/cloud_integrations/aws/accounts/a1", {"config": {"aws": {}}}, None),
    (lambda c: c.delete_cloud_account("aws", "a1"), "DELETE",
     "/api/v1/cloud_integrations/aws/accounts/a1", None, None),
    (lambda c: c.list_cloud_services("aws"), "GET", "/api/v1/cloud_integrations/aws/services",
     None, None),
    (lambda c: c.list_cloud_services("aws", "a1"), "GET",
     "/api/v1/cloud_integrations/aws/accounts/a1/services", None, None),
    (lambda c: c.get_cloud_service("aws", "rds"), "GET",
     "/api/v1/cloud_integrations/aws/services/rds", None, None),
    (lambda c: c.get_cloud_service("aws", "rds", "a1"), "GET",
     "/api/v1/cloud_integrations/aws/accounts/a1/services/rds", None, None),
    (lambda c: c.update_cloud_service("aws", "a1", "rds", {"aws": {}}), "PUT",
     "/api/v1/cloud_integrations/aws/accounts/a1/services/rds", {"config": {"aws": {}}}, None),
    (lambda c: c.get_cloud_credentials("aws"), "GET",
     "/api/v1/cloud_integrations/aws/credentials", None, None),
]


@pytest.mark.parametrize(("call", "method", "path", "body", "params"), ROUTES,
                         ids=[f"{r[1]} {r[2]}" for r in ROUTES])
def test_route(call, method, path, body, params):
    h = Recorder()
    call(client_for(h))
    req = h.requests[0]
    assert (req.method, req.url.path) == (method, path)
    if body is None:
        assert req.content in (b"", b"null")
    else:
        assert json.loads(req.content) == body
    assert dict(req.url.params) == (params or {})


@pytest.mark.parametrize("call", [
    lambda c: c.lock_dashboard_v2("d1"),
    lambda c: c.delete_user("u1"),
    lambda c: c.promote_paths([]),
])
def test_empty_204_is_ok(call):
    assert call(client_for(Recorder(status=204))) == {"status": "ok"}


def test_creates_are_not_retried_on_5xx():
    h = Recorder(status=502, body={"error": {"message": "bad gateway"}})
    c = SigNozClient(BASE, "tok", transport=httpx.MockTransport(h), max_retries=3)
    with pytest.raises(SigNozApiError):
        c.clone_rule("r1")
    assert len(h.requests) == 1


def test_redirect_is_an_error_not_empty_success():
    def handler(request):
        return httpx.Response(301, headers={"location": "/api/v2/rules/clone"})

    with pytest.raises(SigNozApiError, match="unexpected redirect"):
        client_for(handler).clone_rule("")


class TestHealth:
    def test_unhealthy_503_is_returned_not_raised(self):
        body = {"status": "error", "data": {"healthy": False,
                                            "services": {"unhealthy": ["sqlstore"]}}}
        status, got = client_for(Recorder(status=503, body=body)).get_health("readyz")
        assert status == 503 and got == body

    def test_probe_path(self):
        h = Recorder(body={"data": {"healthy": True}})
        client_for(h).get_health("livez")
        assert h.requests[0].url.path == "/api/v2/livez"

    def test_html_fallback_raises(self):
        h = Recorder(content=b"<html></html>", headers={"content-type": "text/html"})
        with pytest.raises(SigNozApiError, match="may not exist"):
            client_for(h).get_health("readyz")

    def test_unknown_probe(self):
        with pytest.raises(ValueError):
            client_for(Recorder()).get_health("bogus")


class TestExportRawData:
    def test_streams_body_to_file(self):
        h = Recorder(content=b"a,b\n1,2\n", headers={"content-type": "text/csv"})
        out = io.BytesIO()
        n = client_for(h).export_raw_data({"start": 1}, out, "csv")
        assert out.getvalue() == b"a,b\n1,2\n" and n == 8
        req = h.requests[0]
        assert (req.method, req.url.path) == ("POST", "/api/v1/export_raw_data")
        assert dict(req.url.params) == {"format": "csv"}
        assert json.loads(req.content) == {"start": 1}

    def test_error_status_raises_with_message(self):
        h = Recorder(status=400, body={"error": {"code": "invalid_input",
                                                 "message": "limit cannot be more than 50000"}})
        with pytest.raises(SigNozApiError, match="50000"):
            client_for(h).export_raw_data({}, io.BytesIO(), "jsonl")

    def test_html_fallback_raises(self):
        h = Recorder(content=b"<html>", headers={"content-type": "text/html; charset=utf-8"})
        out = io.BytesIO()
        with pytest.raises(SigNozApiError, match="may not exist"):
            client_for(h).export_raw_data({}, out, "csv")
        assert out.getvalue() == b""

    def test_bad_format(self):
        with pytest.raises(ValueError):
            client_for(Recorder()).export_raw_data({}, io.BytesIO(), "xml")


def test_error_details_are_shown():
    body = {"status": "error", "error": {
        "code": "invalid_input", "message": "alert rule is not valid",
        "errors": [{"message": "notificationSettings: field is required", "suggestions": []},
                   {"message": "evaluation: bad window"}]}}
    with pytest.raises(SigNozApiError) as ex:
        client_for(Recorder(status=400, body=body)).clone_rule("r")
    assert ex.value.message == ("invalid_input: alert rule is not valid: "
                                "notificationSettings: field is required; evaluation: bad window")


def test_export_redirect_is_an_error():
    h = Recorder(status=308, content=b"Moved", headers={"location": "https://x/",
                                                        "content-type": "text/plain"})
    out = io.BytesIO()
    with pytest.raises(SigNozApiError, match="redirect"):
        client_for(h).export_raw_data({}, out, "csv")
    assert out.getvalue() == b""
