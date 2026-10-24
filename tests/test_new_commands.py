"""CLI tests for the SigNoz 0.141-0.147 feature commands (client faked; no server)."""

import json

import pytest
from click.testing import CliRunner

import signoz_cli.commands.common as cm
from signoz_cli.client import SigNozApiError
from signoz_cli.main import cli


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
    monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")


@pytest.fixture()
def runner():
    return CliRunner()


def fake(monkeypatch, **responses):
    """Install a client whose methods record calls and return canned values.

    A value may be callable (called with the args) or an Exception (raised).
    """
    calls = []

    class Fake:
        endpoint = "http://sig.test:8080"
        max_retries = 3

        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def __getattr__(self, name):
            if name not in responses:
                raise AttributeError(name)

            def method(*args, **kwargs):
                calls.append((name, args, kwargs))
                value = responses[name]
                if isinstance(value, Exception):
                    raise value
                return value(*args, **kwargs) if callable(value) else value
            return method

    monkeypatch.setattr(cm, "SigNozClient", Fake)
    return calls


def names(calls):
    return [c[0] for c in calls]


class TestAlertsClone:
    def test_table_summarizes_copy(self, runner, monkeypatch):
        fake(monkeypatch, clone_rule={"status": "success", "data": {
            "id": "new", "alert": "cpu - Copy", "state": "inactive", "disabled": False}})
        r = runner.invoke(cli, ["alerts", "clone", "r1"])
        assert r.exit_code == 0, r.output
        assert "cpu - Copy" in r.output and "new" in r.output

    def test_legacy_rule_falls_back_to_create(self, runner, monkeypatch):
        calls = fake(
            monkeypatch,
            clone_rule=SigNozApiError(400, "invalid_input: rule threshold is nil"),
            get_alert=lambda rid: {"data": {"id": rid, "alert": "cpu", "state": "firing",
                                            "createdAt": "t", "condition": {"op": "above"}}},
            create_rule={"data": {"id": "new"}})
        r = runner.invoke(cli, ["alerts", "clone", "r1"])
        assert r.exit_code == 0, r.output
        created = next(c for c in calls if c[0] == "create_rule")[1][0]
        assert created == {"alert": "cpu - Copy", "condition": {"op": "above"}}
        assert "legacy-format" in r.output

    def test_other_400s_are_not_masked(self, runner, monkeypatch):
        calls = fake(monkeypatch, clone_rule=SigNozApiError(400, "bad id"))
        r = runner.invoke(cli, ["alerts", "clone", "r1"])
        assert r.exit_code == 1 and names(calls) == ["clone_rule"]

    def test_old_server_error(self, runner, monkeypatch):
        fake(monkeypatch, clone_rule=SigNozApiError(404, "page not found"))
        r = runner.invoke(cli, ["alerts", "clone", "r1"])
        assert r.exit_code == 1 and "HTTP 404" in r.output


class TestCrudHelper:
    def test_route_policy_list_columns(self, runner, monkeypatch):
        fake(monkeypatch, list_route_policies={"data": [
            {"id": "p1", "name": "db", "expression": "x", "channels": ["c"], "kind": "policy",
             "createdAt": "t"}]})
        r = runner.invoke(cli, ["alerts", "route-policies", "list", "--format", "csv"])
        assert r.exit_code == 0, r.output
        header = r.output.splitlines()[0]
        assert header == "id,name,expression,channels,kind"

    def test_rule_views_unwraps_views_key(self, runner, monkeypatch):
        fake(monkeypatch, list_rule_views={"data": {"views": [{"id": "v1", "name": "firing",
                                                               "data": {}}]}})
        r = runner.invoke(cli, ["alerts", "views", "list", "--json"])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["data"][0]["name"] == "firing"

    def test_create_reads_file(self, runner, monkeypatch, tmp_path):
        calls = fake(monkeypatch, create_dashboard_view={"data": {"id": "v1"}})
        f = tmp_path / "v.json"
        f.write_text('{"name": "recent", "data": {"version": "v1"}}')
        r = runner.invoke(cli, ["dashboards", "views", "create", str(f)])
        assert r.exit_code == 0, r.output
        assert calls[0][1] == ({"name": "recent", "data": {"version": "v1"}},)

    def test_delete_dry_run_makes_no_call(self, runner, monkeypatch):
        calls = fake(monkeypatch)
        r = runner.invoke(cli, ["roles", "delete", "r1", "--dry-run"])
        assert r.exit_code == 0 and calls == []

    def test_delete_no_input_refuses(self, runner, monkeypatch):
        calls = fake(monkeypatch, delete_rule_view={"status": "ok"})
        r = runner.invoke(cli, ["--no-input", "alerts", "views", "delete", "v1"])
        assert r.exit_code == 2 and calls == []


class TestDashboards:
    def test_share_creates_when_not_public(self, runner, monkeypatch):
        state = {"public": False}

        def get(_id):
            if not state["public"]:
                raise SigNozApiError(404, "not found")
            return {"data": {"publicPath": "/public/dashboard/abc",
                             "timeRangeEnabled": True, "defaultTimeRange": "30m"}}

        def create(*a):
            state["public"] = True
            return {"data": {"id": "x"}}

        calls = fake(monkeypatch, get_public_dashboard=get, create_public_dashboard=create)
        r = runner.invoke(cli, ["dashboards", "share", "d1", "--json"])
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out["url"] == "http://sig.test:8080/public/dashboard/abc"
        assert out["action"] == "shared"
        assert ("create_public_dashboard", ("d1", True, "30m"), {}) in calls

    def test_share_updates_when_public(self, runner, monkeypatch):
        calls = fake(monkeypatch,
                     get_public_dashboard={"data": {"publicPath": "/p/x"}},
                     update_public_dashboard={"status": "ok"})
        r = runner.invoke(cli, ["dashboards", "share", "d1", "--no-time-range-picker",
                                "--default-time-range", "6h", "--json"])
        assert r.exit_code == 0, r.output
        assert ("update_public_dashboard", ("d1", False, "6h"), {}) in calls
        assert json.loads(r.output)["action"] == "updated"

    def test_share_other_errors_propagate(self, runner, monkeypatch):
        calls = fake(monkeypatch, get_public_dashboard=SigNozApiError(403, "forbidden"))
        r = runner.invoke(cli, ["dashboards", "share", "d1"])
        assert r.exit_code == 1 and "HTTP 403" in r.output
        assert names(calls) == ["get_public_dashboard"]

    def test_lock_unlock(self, runner, monkeypatch):
        calls = fake(monkeypatch, lock_dashboard_v2={"status": "ok"},
                     unlock_dashboard_v2={"status": "ok"})
        assert runner.invoke(cli, ["dashboards", "lock", "d1"]).exit_code == 0
        assert runner.invoke(cli, ["dashboards", "unlock", "d1"]).exit_code == 0
        assert names(calls) == ["lock_dashboard_v2", "unlock_dashboard_v2"]


class TestTraces:
    def test_summary_table(self, runner, monkeypatch):
        fake(monkeypatch, get_trace_summary={"data": {
            "rootServiceName": "api", "rootServiceEntryPoint": "GET /", "rootSpanStatusCode": "Ok",
            "totalSpansCount": 7, "totalErrorSpansCount": 1, "hasMissingSpans": False,
            "startTimestampMillis": 1000, "endTimestampMillis": 1250,
            "ai": {"tokens": {"input": 10, "output": 20}, "totalCost": 0.5}}})
        r = runner.invoke(cli, ["traces", "summary", "t1", "--format", "csv"])
        assert r.exit_code == 0, r.output
        header, row = r.output.splitlines()[:2]
        cols = dict(zip(header.split(","), row.split(","), strict=True))
        assert cols["durationMs"] == "250" and cols["inputTokens"] == "10"

    def test_summary_table_view(self, runner, monkeypatch):
        fake(monkeypatch, get_trace_summary={"data": {
            "rootServiceName": "api", "rootServiceEntryPoint": "GET /",
            "ai": {"tokens": {"input": 10, "output": 20}, "totalCost": 0.5}}})
        r = runner.invoke(cli, ["traces", "summary", "t1"])
        assert r.exit_code == 0, r.output
        assert "inputTokens" in r.output and "GET /" in r.output

    def test_thread_rows_and_cursor_hint(self, runner, monkeypatch):
        calls = fake(monkeypatch, get_trace_thread={"data": {"nextCursor": "c2", "spans": [
            {"span_id": "s1", "name": "chat", "kind_string": "Client",
             "duration_nano": 2_500_000, "has_error": False,
             "resource": {"service.name": "bot"}}]}})
        r = runner.invoke(cli, ["traces", "thread", "t1", "--limit", "1", "--format", "csv"])
        assert r.exit_code == 0, r.output
        assert "s1,bot,chat,Client,2.5,False" in r.output
        assert "--after c2" in r.output
        assert calls[0][2]["limit"] == 1


class TestHealth:
    def _resp(self, healthy, status=200):
        return (status, {"data": {"healthy": healthy,
                                  "services": {"unhealthy": [] if healthy else ["sqlstore"]}}})

    def test_healthy_exit_0(self, runner, monkeypatch):
        fake(monkeypatch, get_health=lambda probe: self._resp(True))
        r = runner.invoke(cli, ["health"])
        assert r.exit_code == 0, r.output

    def test_unhealthy_exit_1(self, runner, monkeypatch):
        fake(monkeypatch, get_health=lambda probe: self._resp(False, 503))
        r = runner.invoke(cli, ["health", "--probe", "all", "--json"])
        assert r.exit_code == 1
        rows = json.loads(r.output)
        assert [x["probe"] for x in rows] == ["readyz", "livez", "healthz"]
        assert rows[0]["services"] == "unhealthy: sqlstore"

    def test_livez_bare_success_is_healthy(self, runner, monkeypatch):
        fake(monkeypatch, get_health=lambda probe: (200, {"status": "success"}))
        r = runner.invoke(cli, ["health", "--probe", "livez"])
        assert r.exit_code == 0, r.output

    def test_works_without_token(self, runner, monkeypatch):
        monkeypatch.delenv("SIGNOZ_ACCESS_TOKEN")
        fake(monkeypatch, get_health=lambda probe: self._resp(True))
        assert runner.invoke(cli, ["health"]).exit_code == 0


class TestServiceAccountKeys:
    def test_create_expiry_seconds(self, runner, monkeypatch):
        monkeypatch.setattr("time.time", lambda: 1_000_000)
        calls = fake(monkeypatch, create_service_account_key={"data": {"id": "k", "key": "s"}})
        r = runner.invoke(cli, ["service-accounts", "keys", "create", "sa", "ci",
                                "--expires-in", "1d"])
        assert r.exit_code == 0, r.output
        assert calls[0][1] == ("sa", "ci", 1_000_000 + 86_400)
        assert "cannot be shown again" in r.output

    def test_never_is_zero(self, runner, monkeypatch):
        calls = fake(monkeypatch, create_service_account_key={"data": {}})
        runner.invoke(cli, ["service-accounts", "keys", "create", "sa", "ci"])
        assert calls[0][1][2] == 0

    def test_bad_expiry(self, runner, monkeypatch):
        calls = fake(monkeypatch)
        r = runner.invoke(cli, ["service-accounts", "keys", "create", "sa", "ci",
                                "--expires-in", "soon"])
        assert r.exit_code == 2 and calls == []

    def test_update_keeps_current_expiry(self, runner, monkeypatch):
        calls = fake(monkeypatch,
                     list_service_account_keys={"data": [{"id": "k1", "expiresAt": 555}]},
                     update_service_account_key={"status": "ok"})
        r = runner.invoke(cli, ["service-accounts", "keys", "update", "sa", "k1", "--name", "n"])
        assert r.exit_code == 0, r.output
        assert calls[-1][1] == ("sa", "k1", "n", 555)

    def test_update_unknown_key(self, runner, monkeypatch):
        calls = fake(monkeypatch, list_service_account_keys={"data": []})
        r = runner.invoke(cli, ["service-accounts", "keys", "update", "sa", "k9", "--name", "n"])
        assert r.exit_code == 2 and names(calls) == ["list_service_account_keys"]

    def test_list_hides_nothing_secret(self, runner, monkeypatch):
        fake(monkeypatch, list_service_account_keys={"data": [
            {"id": "k1", "name": "ci", "expiresAt": 0, "lastObservedAt": "t"}]})
        r = runner.invoke(cli, ["service-accounts", "keys", "list", "sa", "--format", "csv"])
        assert "k1,ci,,never,t" in r.output


ROLES = {"data": [{"id": "r-admin", "name": "signoz-admin"},
                  {"id": "r-view", "name": "signoz-viewer"}]}


class TestUsers:
    def test_invite_resolves_role_names(self, runner, monkeypatch):
        calls = fake(monkeypatch, list_roles=ROLES, invite_user={"data": {"id": "u9"}})
        r = runner.invoke(cli, ["users", "invite", "a@b.c", "--role", "viewer"])
        assert r.exit_code == 0, r.output
        name, args, kwargs = calls[-1]
        assert name == "invite_user" and args[2] == ["r-view"]
        assert kwargs["frontend_base_url"] == "http://sig.test:8080"

    def test_unknown_role(self, runner, monkeypatch):
        fake(monkeypatch, list_roles=ROLES)
        r = runner.invoke(cli, ["users", "grant", "u1", "owner"])
        assert r.exit_code == 2 and "Unknown role" in r.output

    def test_revoke_deletes_the_assignment_id(self, runner, monkeypatch):
        calls = fake(monkeypatch, list_roles=ROLES, delete_user_role={"status": "ok"},
                     get_user={"data": {"id": "u1", "userRoles": [
                         {"id": "ur-1", "roleId": "r-view"}, {"id": "ur-2", "roleId": "x"}]}})
        r = runner.invoke(cli, ["users", "revoke", "u1", "viewer", "--yes"])
        assert r.exit_code == 0, r.output
        assert ("delete_user_role", ("ur-1",), {}) in calls

    def test_revoke_missing_role(self, runner, monkeypatch):
        fake(monkeypatch, list_roles=ROLES, get_user={"data": {"userRoles": []}})
        r = runner.invoke(cli, ["users", "revoke", "u1", "admin", "--yes"])
        assert r.exit_code == 2 and "does not have role" in r.output

    def test_reset_link(self, runner, monkeypatch):
        calls = fake(monkeypatch,
                     create_reset_password_token={"data": {"token": "T", "expiresAt": "e"}})
        r = runner.invoke(cli, ["users", "reset-link", "u1", "--json"])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)["link"] == "http://sig.test:8080/password-reset?token=T"
        assert names(calls) == ["create_reset_password_token"]

    def test_delete_with_api_key_hints(self, runner, monkeypatch):
        fake(monkeypatch, delete_user=SigNozApiError(
            404, "user_not_found: user with id sa-123 does not exist"))
        r = runner.invoke(cli, ["users", "delete", "u1", "--yes"])
        assert r.exit_code == 1 and "user session token" in r.output

    def test_delete_missing_target_no_hint(self, runner, monkeypatch):
        fake(monkeypatch, delete_user=SigNozApiError(
            404, "user_not_found: user with id u1 does not exist"))
        r = runner.invoke(cli, ["users", "delete", "u1", "--yes"])
        assert r.exit_code == 1 and "Hint" not in r.output

    def test_list_rows(self, runner, monkeypatch):
        fake(monkeypatch, list_users={"data": [{"id": "u1", "email": "a@b.c",
                                                "displayName": "A", "status": "active"}]})
        r = runner.invoke(cli, ["users", "list", "--format", "csv"])
        assert "u1,a@b.c,A,active,False" in r.output


class TestInfra:
    def test_pods_payload(self, runner, monkeypatch):
        calls = fake(monkeypatch, list_infra={"data": {"records": [
            {"podUID": "p1", "podCPU": 0.5, "meta": {"k8s.pod.name": "web"}}], "total": 3}})
        r = runner.invoke(cli, ["infra", "pods", "--start", "1000", "--end", "2000",
                                "--filter", "k8s.namespace.name = 'prod'",
                                "--status", "running", "--status", "pending",
                                "--order-by", "cpu", "--limit", "1", "--format", "csv"])
        assert r.exit_code == 0, r.output
        name, (kind, payload), _ = calls[0]
        assert kind == "pods"
        assert payload == {"start": 1000, "end": 2000, "limit": 1, "offset": 0,
                           "filter": {"expression": "k8s.namespace.name = 'prod'",
                                      "filterByPodStatus": ["running", "pending"]},
                           "orderBy": {"key": {"name": "cpu"}, "direction": "desc"}}
        assert "p1,0.5,web" in r.output
        assert "next page: --offset 1" in r.output

    def test_hosts_status_is_a_string(self, runner, monkeypatch):
        calls = fake(monkeypatch, list_infra={"data": {"records": []}})
        runner.invoke(cli, ["infra", "hosts", "--status", "inactive"])
        assert calls[0][1][1]["filter"] == {"filterByStatus": "inactive"}

    def test_containers_and_volumes_paths(self, runner, monkeypatch):
        calls = fake(monkeypatch, list_infra={"data": {"records": []}})
        runner.invoke(cli, ["infra", "containers"])
        runner.invoke(cli, ["infra", "volumes"])
        assert [c[1][0] for c in calls] == ["kube_containers", "pvcs"]

    def test_volumes_reject_status(self, runner, monkeypatch):
        calls = fake(monkeypatch, list_infra={})
        r = runner.invoke(cli, ["infra", "volumes", "--status", "x"])
        assert r.exit_code == 2 and calls == []

    def test_bad_order_direction(self, runner, monkeypatch):
        fake(monkeypatch, list_infra={})
        r = runner.invoke(cli, ["infra", "hosts", "--order-by", "cpu:up"])
        assert r.exit_code == 2

    def test_checks_missing_first(self, runner, monkeypatch):
        fake(monkeypatch, get_infra_checks={"data": {
            "type": "hosts", "ready": False,
            "presentDefaultEnabledMetrics": [{"metrics": ["system.cpu.time"],
                                              "associatedComponent": {"name": "hostmetrics"}}],
            "missingRequiredAttributes": [{"attributes": ["host.name"], "message": "add it",
                                           "associatedComponent": {"name": "resourcedetection"},
                                           "documentationLink": "http://docs"}]}})
        r = runner.invoke(cli, ["infra", "checks", "hosts", "--format", "csv"])
        assert r.exit_code == 0, r.output
        lines = r.output.splitlines()
        assert lines[1].startswith("missing,missingRequiredAttributes,resourcedetection,host.name")
        assert "ready: no" in r.output


class TestIngestionKeys:
    def test_list_masks_values(self, runner, monkeypatch):
        fake(monkeypatch, list_ingestion_keys={"data": {
            "keys": [{"id": "k1", "name": "prod", "value": "abcdefghijkl"}],
            "_pagination": {"page": 1, "pages": 2}}})
        r = runner.invoke(cli, ["ingestion-keys", "list"])
        assert r.exit_code == 0, r.output
        assert "abcdef..." in r.output and "abcdefghijkl" not in r.output
        assert "next: --page 2" in r.output

    def test_limit_needs_a_threshold(self, runner, monkeypatch):
        calls = fake(monkeypatch)
        r = runner.invoke(cli, ["ingestion-keys", "limits", "create", "k1", "--signal", "logs"])
        assert r.exit_code == 2 and calls == []

    def test_update_merges_current_values(self, runner, monkeypatch):
        calls = fake(monkeypatch, update_ingestion_key={"status": "ok"},
                     get_ingestion_key={"data": {"name": "old", "tags": ["prod"],
                                                 "expires_at": "2030-01-01T00:00:00Z"}})
        r = runner.invoke(cli, ["ingestion-keys", "update", "k1", "--name", "new"])
        assert r.exit_code == 0, r.output
        assert calls[-1][1] == ("k1", {"name": "new", "tags": ["prod"],
                                       "expires_at": "2030-01-01T00:00:00Z"})

    def test_limit_update_merges(self, runner, monkeypatch):
        calls = fake(monkeypatch, update_ingestion_limit={"status": "ok"},
                     get_ingestion_limit={"data": {"tags": ["t"], "config": {
                         "day": {"size": 100, "count": 5}, "second": {"count": 1}}}})
        r = runner.invoke(cli, ["ingestion-keys", "limits", "update", "l1", "--day-size", "200"])
        assert r.exit_code == 0, r.output
        assert calls[-1][1] == ("l1", {"tags": ["t"], "config": {
            "day": {"size": 200, "count": 5}, "second": {"count": 1}}})

    def test_limit_body(self, runner, monkeypatch):
        calls = fake(monkeypatch, create_ingestion_limit={"data": {"id": "l1"}})
        r = runner.invoke(cli, ["ingestion-keys", "limits", "create", "k1", "--signal", "logs",
                                "--day-size", "1000", "--second-count", "5"])
        assert r.exit_code == 0, r.output
        assert calls[0][1][0] == {"keyId": "k1", "signal": "logs", "config": {
            "day": {"size": 1000}, "second": {"count": 5}}}


class TestMetrics:
    def test_treemap_rows(self, runner, monkeypatch):
        calls = fake(monkeypatch, get_metrics_treemap={"data": {"timeseries": [
            {"metricName": "http_requests", "percentage": 41.234, "totalValue": 900}],
            "samples": []}})
        r = runner.invoke(cli, ["metrics", "treemap", "--format", "csv"])
        assert r.exit_code == 0, r.output
        assert "http_requests,41.23,900" in r.output
        assert calls[0][2]["mode"] == "timeseries"

    def test_inspect_rows(self, runner, monkeypatch):
        fake(monkeypatch, inspect_metric={"data": {"series": [{
            "labels": [{"key": {"name": "host"}, "value": "a"}],
            "values": [{"timestamp": 1, "value": 2}, {"timestamp": 3, "value": 4}]}]}})
        r = runner.invoke(cli, ["metrics", "inspect", "cpu", "--format", "csv"])
        assert "host=a,2,3,4" in r.output

    def test_reduction_preview_body(self, runner, monkeypatch):
        calls = fake(monkeypatch, preview_reduction_rule={"data": {
            "ingestedSeries": 100, "retainedSeries": 10, "reductionPercent": 90,
            "droppedLabels": ["pod"], "affectedAssets": [
                {"type": "dashboard", "name": "K8s", "impactedLabels": ["pod"]}]}})
        r = runner.invoke(cli, ["metrics", "reduction-rules", "preview", "cpu", "--match", "drop",
                                "--label", "pod", "--lookback", "1h"])
        assert r.exit_code == 0, r.output
        assert calls[0][1][0] == {"matchType": "drop", "labels": ["pod"],
                                  "metricName": "cpu", "lookbackMs": 3_600_000}
        assert "affects dashboard 'K8s'" in r.output


class TestPromotedPaths:
    def test_add_defaults_context_per_signal(self, runner, monkeypatch):
        calls = fake(monkeypatch, promote_paths={"status": "ok"})
        runner.invoke(cli, ["promoted-paths", "add", "user.id"])
        runner.invoke(cli, ["promoted-paths", "add", "http.route", "--signal", "traces"])
        assert calls[0][1][0] == [{"signal": "logs", "context": "body", "path": "user.id",
                                   "promote": True}]
        assert calls[1][1][0][0]["context"] == "attribute"

    def test_add_with_index(self, runner, monkeypatch):
        calls = fake(monkeypatch, promote_paths={"status": "ok"})
        r = runner.invoke(cli, ["promoted-paths", "add", "a", "--index", "bloom_filter(0.01)",
                                "--index-only"])
        assert r.exit_code == 0, r.output
        item = calls[0][1][0][0]
        assert item["promote"] is False
        assert item["indexes"] == [{"type": "bloom_filter(0.01)", "fieldDataType": "string",
                                    "granularity": 1}]

    def test_index_only_needs_index(self, runner, monkeypatch):
        calls = fake(monkeypatch)
        r = runner.invoke(cli, ["promoted-paths", "add", "a", "--index-only"])
        assert r.exit_code == 2 and calls == []


class TestQuickFilters:
    def test_set_accepts_wrapped_or_bare_list(self, runner, monkeypatch, tmp_path):
        calls = fake(monkeypatch, set_quick_filters={"status": "ok"})
        f = tmp_path / "qf.json"
        f.write_text('{"filters": [{"name": "service.name"}]}')
        r = runner.invoke(cli, ["quick-filters", "set", "logs", str(f), "--yes"])
        assert r.exit_code == 0, r.output
        assert calls[0][1] == ("logs", [{"name": "service.name"}])


class TestQuickFiltersRoundTrip:
    def test_set_accepts_list_json_output(self, runner, monkeypatch, tmp_path):
        calls = fake(monkeypatch, set_quick_filters={"status": "ok"})
        f = tmp_path / "qf.json"
        f.write_text('{"status": "success", "data": {"source": "logs", '
                     '"filters": [{"name": "host"}]}}')
        r = runner.invoke(cli, ["quick-filters", "set", "logs", str(f), "--yes"])
        assert r.exit_code == 0, r.output
        assert calls[0][1] == ("logs", [{"name": "host"}])


class TestCloud:
    def test_connect_aws_prints_artifact(self, runner, monkeypatch):
        calls = fake(monkeypatch, get_cloud_credentials={"data": {"sigNozApiUrl": "u", "sigNozApiKey": "k", "ingestionUrl": "i", "ingestionKey": "ik"}},
                     create_cloud_account={"data": {
            "id": "a1", "connectionArtifact": {"aws": {"connectionUrl": "https://cfn"}}}})
        r = runner.invoke(cli, ["cloud", "accounts", "connect", "aws", "--deployment-region",
                                "us-east-1", "--region", "us-east-1", "--region", "eu-west-1"])
        assert r.exit_code == 0, r.output
        assert calls[1][1] == ("aws", {"aws": {"regions": ["us-east-1", "eu-west-1"],
                                               "deploymentRegion": "us-east-1"}},
                               {"sigNozApiUrl": "u", "sigNozApiKey": "k", "ingestionUrl": "i", "ingestionKey": "ik"})
        assert "https://cfn" in r.output and "a1" in r.output
        assert "Finish the connection" in r.output

    def test_connect_requires_regions(self, runner, monkeypatch):
        calls = fake(monkeypatch)
        r = runner.invoke(cli, ["cloud", "accounts", "connect", "aws",
                                "--deployment-region", "x"])
        assert r.exit_code == 2 and calls == []

    def test_configure_keeps_existing_settings(self, runner, monkeypatch):
        calls = fake(monkeypatch,
                     get_cloud_service={"data": {"cloudIntegrationService": {"config": {"aws": {
                         "logs": {"enabled": False, "s3Buckets": {"us-east-1": ["b"]}}}}}}},
                     update_cloud_service={"status": "ok"})
        r = runner.invoke(cli, ["cloud", "services", "configure", "aws", "a1", "rds",
                                "--logs"])
        assert r.exit_code == 0, r.output
        sent = calls[-1][1][3]
        assert sent == {"aws": {"logs": {"enabled": True, "s3Buckets": {"us-east-1": ["b"]}},
                                "metrics": {"enabled": False}}}

    def test_credentials_masked(self, runner, monkeypatch):
        fake(monkeypatch, get_cloud_credentials={"data": {
            "sigNozApiKey": "supersecretkey", "ingestionKey": "ingestsecret",
            "sigNozApiUrl": "u", "ingestionUrl": "i"}})
        r = runner.invoke(cli, ["cloud", "credentials", "aws"])
        assert "supersecretkey" not in r.output and "supe..." in r.output


class TestExport:
    def test_logs_export_streams_all_columns(self, runner, monkeypatch, tmp_path):
        seen = {}

        def export(payload, out, fmt):
            seen["payload"], seen["fmt"] = payload, fmt
            out.write(b'{"body":"x"}\n')
            return 13

        fake(monkeypatch, export_raw_data=export)
        out = tmp_path / "logs.jsonl"
        r = runner.invoke(cli, ["logs", "export", "--service", "api", "--out", str(out),
                                "--limit", "500"])
        assert r.exit_code == 0, r.output
        assert out.read_bytes() == b'{"body":"x"}\n'
        assert not (tmp_path / "logs.jsonl.partial").exists()
        assert seen["fmt"] == "jsonl"
        spec = seen["payload"]["compositeQuery"]["queries"][0]["spec"]
        assert "selectFields" not in spec and "offset" not in spec
        assert spec["limit"] == 500 and spec["signal"] == "logs"
        assert "service.name = 'api'" in spec["filter"]["expression"]

    def test_failure_leaves_no_file(self, runner, monkeypatch, tmp_path):
        def export(payload, out, fmt):
            out.write(b"partial")
            raise SigNozApiError(500, "boom")

        fake(monkeypatch, export_raw_data=export)
        out = tmp_path / "t.csv"
        r = runner.invoke(cli, ["traces", "export", "--out", str(out)])
        assert r.exit_code == 1
        assert not out.exists() and not (tmp_path / "t.csv.partial").exists()

    def test_limit_cap(self, runner, monkeypatch, tmp_path):
        calls = fake(monkeypatch, export_raw_data=lambda *a: 0)
        r = runner.invoke(cli, ["logs", "export", "--out", str(tmp_path / "x.csv"),
                                "--limit", "60000"])
        assert r.exit_code == 2 and calls == []

    def test_traces_export_csv_default(self, runner, monkeypatch, tmp_path):
        seen = {}

        def export(payload, out, fmt):
            seen["fmt"] = fmt
            seen["spec"] = payload["compositeQuery"]["queries"][0]["spec"]
            return 0

        fake(monkeypatch, export_raw_data=export)
        r = runner.invoke(cli, ["traces", "export", "--error", "true",
                                "--out", str(tmp_path / "t.out")])
        assert r.exit_code == 0, r.output
        assert seen["fmt"] == "csv" and seen["spec"]["signal"] == "traces"
