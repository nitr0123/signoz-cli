"""Bundle export/import/apply for route policies, rule/dashboard views, quick filters."""

import json

import pytest
from click.testing import CliRunner

import signoz_cli.commands.common as cm
from signoz_cli.main import cli

NEW_KINDS = "route-policies,rule-views,dashboard-views,quick-filters"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
    monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")


class Store:
    """In-memory server holding one item of each new kind."""

    def __init__(self):
        self.calls = []
        self.policies = [{"id": "p1", "name": "db", "expression": "a", "channels": ["c"],
                          "createdAt": "t"}]
        self.rule_views = [{"id": "rv1", "name": "firing", "data": {"version": "v1"},
                            "orgId": "o"}]
        self.dash_views = [{"id": "dv1", "name": "recent", "data": {"version": "v1"}}]
        self.quick = [{"id": "q1", "source": "logs", "orgId": "o",
                       "filters": [{"name": "service.name"}]}]


@pytest.fixture()
def store(monkeypatch):
    st = Store()

    class Fake:
        endpoint = "http://sig.test:8080"

        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def get_server_version(self):
            return {"version": "v0.147.0"}

        def list_route_policies(self):
            return {"data": st.policies}

        def get_route_policy(self, pid):
            st.calls.append(("get_route_policy", pid))
            return {"data": next(p for p in st.policies if p["id"] == pid)}

        def list_rule_views(self):
            return {"data": {"views": st.rule_views}}

        def list_dashboard_views(self):
            return {"data": {"views": st.dash_views}}

        def list_quick_filters(self):
            return {"data": st.quick}

        def __getattr__(self, name):
            if name.startswith(("create_", "update_", "delete_", "set_")):
                def record(*args):
                    st.calls.append((name, *args))
                    return {"status": "ok"}
                return record
            raise AttributeError(name)

    monkeypatch.setattr(cm, "SigNozClient", Fake)
    return st


def test_export_writes_new_kinds(store, tmp_path):
    out = tmp_path / "b"
    r = CliRunner().invoke(cli, ["export", str(out), "--include", NEW_KINDS, "--yes", "--json"])
    assert r.exit_code == 0, r.output
    assert {row["kind"]: row["exported"] for row in json.loads(r.output)} == {
        "route-policies": 1, "rule-views": 1, "dashboard-views": 1, "quick-filters": 1}
    (qf,) = (out / "quick-filters").iterdir()
    assert qf.name.startswith("logs.")
    assert ("get_route_policy", "p1") in store.calls


def test_include_all_and_unknown_kind(store, tmp_path):
    r = CliRunner().invoke(cli, ["export", str(tmp_path / "x"), "--include", "bogus", "--yes"])
    assert r.exit_code == 2 and "route-policies" in r.output


def _bundle(tmp_path, kind, doc):
    d = tmp_path / "bundle" / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / "item.json").write_text(json.dumps(doc))
    return tmp_path / "bundle"


def test_import_creates_and_overwrites(store, tmp_path):
    b = _bundle(tmp_path, "route-policies", {"name": "new", "expression": "x", "channels": []})
    _bundle(tmp_path, "quick-filters", {"id": "zz", "source": "logs",
                                        "filters": [{"name": "host"}]})
    _bundle(tmp_path, "rule-views", {"id": "old", "name": "firing", "data": {"version": "v2"}})
    r = CliRunner().invoke(cli, ["import", str(b), "--on-conflict", "overwrite", "--yes"])
    assert r.exit_code == 0, r.output
    assert ("create_route_policy", {"name": "new", "expression": "x", "channels": []}) \
        in store.calls
    # Quick filters are keyed by source and replaced whole; ids are not sent.
    assert ("set_quick_filters", "logs", [{"name": "host"}]) in store.calls
    # The clash is updated in place by the server's id, not the bundle's.
    assert ("update_rule_view", "rv1", {"name": "firing", "data": {"version": "v2"}}) \
        in store.calls


def test_apply_prune_skips_quick_filters(store, tmp_path):
    b = _bundle(tmp_path, "quick-filters", {"source": "traces", "filters": []})
    _bundle(tmp_path, "dashboard-views", {"name": "other", "data": {"version": "v1"}})
    r = CliRunner().invoke(cli, ["apply", str(b), "--prune", "--yes", "--json"])
    assert r.exit_code == 0, r.output
    report = {(row["kind"], row["name"]): row for row in json.loads(r.output)}
    assert report[("quick-filters", "logs")]["detail"] == "cannot be deleted"
    assert report[("dashboard-views", "recent")]["action"] == "deleted"
    assert ("delete_dashboard_view", "dv1") in store.calls
    assert not any(c[0] == "delete_quick_filters" for c in store.calls)


def test_apply_unchanged_is_left_alone(store, tmp_path):
    b = _bundle(tmp_path, "route-policies", {"name": "db", "expression": "a", "channels": ["c"]})
    r = CliRunner().invoke(cli, ["apply", str(b), "--yes", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)[0]["action"] == "unchanged"
    assert not any(c[0].startswith(("create_", "update_")) for c in store.calls)


def test_exported_channel_is_unpacked_on_import(monkeypatch, tmp_path):
    """`channels get` returns the receiver as a JSON string; import must send the object."""
    sent = []

    class Fake:
        endpoint = "http://sig.test:8080"

        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

        def list_channels(self):
            return {"data": [{"id": "c1", "name": "existing"}]}

        def create_channel(self, body):
            sent.append(("create", body))
            return {"status": "ok"}

        def update_channel(self, cid, body):
            sent.append(("update", cid, body))
            return {"status": "ok"}

    monkeypatch.setattr(cm, "SigNozClient", Fake)
    receiver = {"name": "hook", "webhook_configs": [{"url": "http://x", "send_resolved": True}]}
    b = _bundle(tmp_path, "channels", {"id": "old", "name": "hook", "type": "webhook",
                                       "data": json.dumps(receiver)})
    (b / "channels" / "plain.json").write_text(json.dumps(
        {"name": "existing", "slack_configs": [{"channel": "#a"}]}))
    r = CliRunner().invoke(cli, ["import", str(b), "--on-conflict", "overwrite", "--yes"])
    assert r.exit_code == 0, r.output
    assert ("create", receiver) in sent
    # Hand-written receiver files pass through unchanged.
    assert ("update", "c1", {"name": "existing", "slack_configs": [{"channel": "#a"}]}) in sent


def test_include_all_skips_kinds_the_server_lacks(store, monkeypatch, tmp_path):
    from signoz_cli.client import SigNozApiError

    def missing(*a, **k):
        raise SigNozApiError(200, "unexpected non-JSON response; this API may not exist")

    for name in ("list_dashboards_v2", "list_rules", "list_channels", "list_saved_views",
                 "list_rule_views"):
        monkeypatch.setattr(cm.SigNozClient, name, missing, raising=False)
    r = CliRunner().invoke(cli, ["export", str(tmp_path / "b"), "--include", "all", "--yes",
                                 "--json"])
    assert r.exit_code == 0, r.output
    assert "Warning: skipping rule-views" in r.output
    rows = json.loads(r.output[r.output.index("["):])
    assert {row["kind"]: row["exported"] for row in rows}["route-policies"] == 1


def test_explicit_kind_still_fails_loudly(store, monkeypatch, tmp_path):
    from signoz_cli.client import SigNozApiError

    def missing(*a):
        raise SigNozApiError(404, "nope")

    monkeypatch.setattr(cm.SigNozClient, "list_rule_views", missing, raising=False)
    r = CliRunner().invoke(cli, ["export", str(tmp_path / "b"), "--include", "rule-views",
                                 "--yes"])
    assert r.exit_code == 1 and "nope" in r.output
