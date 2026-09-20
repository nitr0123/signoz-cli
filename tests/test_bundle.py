"""Tests for export/import/diff bundle commands and CRUD completions."""

import json as _json

import pytest
from click.testing import CliRunner

import signoz_cli.commands.common as cm
from signoz_cli.main import cli


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    cfg_dir = tmp_path / ".signoz"
    monkeypatch.setattr("signoz_cli.config.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("signoz_cli.config.CONFIG_FILE", cfg_dir / "config.json")
    monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
    monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
    monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)


class _BundleFake:
    """Fake client covering every bundle touchpoint; records mutations."""

    def __init__(self, *a, **k):
        self.endpoint = a[0] if a else ""
        self.calls = {"create": [], "update": [], "delete": []}
        self.fail_version = False
        self.dashboards = [
            {"id": f"d{i}", "name": f"Dash{i}"} for i in range(3)
        ]

    def close(self):
        pass

    def get_server_version(self):
        if self.fail_version:
            raise RuntimeError("no version")
        return {"version": "v0.138.0"}

    def list_dashboards_v2(self, limit=None, offset=None, query=None):
        # Paginate in slices of `limit` to exercise multi-page export.
        limit = limit or 100
        offset = offset or 0
        page = self.dashboards[offset:offset + limit]
        return {"status": "success",
                "data": {"dashboards": page, "total": len(self.dashboards)}}

    def get_dashboard_v2(self, dashboard_id):
        return {"status": "success",
                "data": {"id": dashboard_id, "name": "Dash",
                         "createdAt": "t"}}

    def create_dashboard_v2(self, doc):
        self.calls["create"].append(("dashboards", doc))
        return {"status": "success", "data": {"id": "d9"}}

    def update_dashboard_v2(self, dashboard_id, doc):
        self.calls["update"].append(("dashboards", dashboard_id, doc))
        return {"status": "success"}

    def list_rules(self):
        return {"status": "success",
                "data": {"rules": {"rules": [
                    {"id": "r1", "alert": "Rule"}]}}}

    def get_alert(self, rule_id):
        return {"status": "success",
                "data": {"id": rule_id, "alert": "Rule"}}

    def create_rule(self, doc):
        self.calls["create"].append(("alerts", doc))
        return {"status": "success", "data": {"id": "r9"}}

    def update_rule(self, rule_id, doc):
        self.calls["update"].append(("alerts", rule_id, doc))
        return {"status": "success"}

    def list_channels(self):
        return {"status": "success",
                "data": [{"id": "ch1", "name": "Ch"}]}

    def get_channel(self, channel_id):
        return {"status": "success",
                "data": {"id": channel_id, "name": "Ch"}}

    def create_channel(self, doc):
        self.calls["create"].append(("channels", doc))
        return {"status": "success", "data": {"id": "ch9"}}

    def update_channel(self, channel_id, doc):
        self.calls["update"].append(("channels", channel_id, doc))
        return {"status": "success"}

    def delete_channel(self, channel_id):
        self.calls["delete"].append(("channels", channel_id))
        return {"status": "success"}

    def list_saved_views(self, source_page="logs"):
        return {"status": "success", "data": None}

    def get_saved_view(self, view_id):
        return {"status": "success",
                "data": {"id": view_id, "name": "V"}}

    def create_saved_view(self, doc):
        self.calls["create"].append(("views", doc))
        return {"status": "success", "data": {"id": "v9"}}

    def delete_saved_view(self, view_id):
        self.calls["delete"].append(("views", view_id))
        return {"status": "success"}


@pytest.fixture()
def fake(monkeypatch):
    inst = _BundleFake()
    monkeypatch.setattr(cm, "SigNozClient", lambda *a, **k: inst)
    return inst


class TestStripVolatile:
    def test_list_branch(self):
        from signoz_cli.commands.bundle import _strip_volatile

        assert _strip_volatile([{"id": "x", "a": 1}, 2]) == [{"a": 1}, 2]

    def test_scalars_untouched(self):
        from signoz_cli.commands.bundle import _strip_volatile

        assert _strip_volatile("x") == "x"
        assert _strip_volatile(3) == 3


class TestFetchFull:
    def test_item_without_id_returned(self):
        from signoz_cli.commands.bundle import _fetch_full

        assert _fetch_full(object(), "views", {"name": "V"}) == {"name": "V"}

    def test_non_dict_data_passthrough(self, fake):
        from signoz_cli.commands.bundle import _fetch_full

        assert _fetch_full(fake, "views", {"id": "v1"}) == {
            "id": "v1", "name": "V"}


class TestListItems:
    def test_unknown_kind(self):
        import click as _click

        from signoz_cli.commands.bundle import _list_items

        with pytest.raises(_click.UsageError):
            _list_items(object(), "nope")

    def test_double_nested_rules(self, fake):
        from signoz_cli.commands.bundle import _list_items

        assert _list_items(fake, "alerts") == [{"id": "r1", "alert": "Rule"}]

    def test_non_list_channels_tolerated(self, monkeypatch):
        from signoz_cli.commands.bundle import _list_items

        class Odd:
            def list_channels(self):
                return {"status": "success", "data": {"x": 1}}

        assert _list_items(Odd(), "channels") == []


class TestExportMore:
    def test_include_subset(self, runner, fake, tmp_path):
        target = tmp_path / "b"
        result = runner.invoke(cli, ["export", str(target), "--yes",
                                     "--include", "views"])
        assert result.exit_code == 0, result.output
        assert (target / "views").is_dir()
        assert not (target / "dashboards").exists()

    def test_unknown_include_rejected(self, runner, fake, tmp_path):
        result = runner.invoke(
            cli, ["export", str(tmp_path / "b"), "--yes",
                  "--include", "dashboards,nope"])
        assert result.exit_code == 2, result.output
        assert "Unknown kind" in result.output

    def test_confirm_abort(self, runner, fake, tmp_path):
        target = tmp_path / "b"
        result = runner.invoke(cli, ["export", str(target)], input="n\n")
        assert result.exit_code != 0
        assert not target.exists()

    def test_version_probe_failure_tolerated(self, runner, fake, tmp_path):
        fake.fail_version = True
        target = tmp_path / "b"
        result = runner.invoke(cli, ["export", str(target), "--yes",
                                     "--include", "views"])
        assert result.exit_code == 0, result.output

    def test_fetch_failure_skips_item(self, runner, monkeypatch, tmp_path):
        import signoz_cli.commands.bundle as _bundle

        fake = _BundleFake()
        monkeypatch.setattr(cm, "SigNozClient", lambda *a, **k: fake)
        monkeypatch.setattr(
            _bundle, "_fetch_full", lambda client, kind, item: None)
        target = tmp_path / "b"
        result = runner.invoke(cli, ["export", str(target), "--yes",
                                     "--include", "dashboards"])
        assert result.exit_code == 0, result.output
        assert "exported" in result.output


class TestImportMore:
    def _bundle(self, tmp_path, files):
        for kind, docs in files.items():
            kind_dir = tmp_path / "b" / kind
            kind_dir.mkdir(parents=True)
            for i, doc in enumerate(docs):
                (kind_dir / f"f{i}.json").write_text(_json.dumps(doc))
        (tmp_path / "b" / "junk.txt").write_text("ignore me")
        return tmp_path / "b"

    def test_empty_dir_rejected(self, runner, fake, tmp_path):
        target = tmp_path / "b"
        target.mkdir()
        result = runner.invoke(cli, ["import", str(target), "--yes"])
        assert result.exit_code == 2, result.output
        assert "No bundle kinds" in result.output

    def test_non_json_files_skipped(self, runner, fake, tmp_path):
        target = self._bundle(tmp_path, {"views": [{"name": "V"}]})
        result = runner.invoke(cli, ["import", str(target), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "junk.txt" not in result.output

    def test_create_alerts_channels_views(self, runner, fake, tmp_path):
        target = self._bundle(tmp_path, {
            "alerts": [{"alert": "NewR"}],
            "channels": [{"name": "NewC"}],
            "views": [{"name": "NewV"}],
        })
        result = runner.invoke(cli, ["import", str(target), "--yes"])
        assert result.exit_code == 0, result.output
        assert ("alerts", {"alert": "NewR"}) in fake.calls["create"]
        assert ("channels", {"name": "NewC"}) in fake.calls["create"]
        assert ("views", {"name": "NewV"}) in fake.calls["create"]

    def test_overwrite_alerts_channels(self, runner, fake, tmp_path):
        target = self._bundle(tmp_path, {
            "alerts": [{"id": "r1", "alert": "Rule"}],
            "channels": [{"id": "ch1", "name": "Ch"}],
        })
        result = runner.invoke(cli, ["import", str(target), "--yes",
                                     "--on-conflict", "overwrite"])
        assert result.exit_code == 0, result.output
        assert ("alerts", "r1", {"alert": "Rule"}) in fake.calls["update"]
        assert ("channels", "ch1", {"name": "Ch"}) in fake.calls["update"]

    def test_overwrite_views_deletes_then_creates(self, runner, fake, tmp_path):
        fake.list_saved_views = lambda source_page="logs": {
            "status": "success", "data": [{"id": "v1", "name": "V"}]}
        target = self._bundle(tmp_path, {"views": [{"name": "V"}]})
        result = runner.invoke(cli, ["import", str(target), "--yes",
                                     "--on-conflict", "overwrite"])
        assert result.exit_code == 0, result.output
        assert ("views", "v1") in fake.calls["delete"]
        assert ("views", {"name": "V"}) in fake.calls["create"]

    def test_overwrite_dry_run(self, runner, fake, tmp_path):
        target = self._bundle(tmp_path, {
            "channels": [{"id": "ch1", "name": "Ch"}]})
        result = runner.invoke(cli, ["import", str(target),
                                     "--on-conflict", "overwrite",
                                     "--dry-run"])
        assert result.exit_code == 0, result.output
        assert fake.calls["update"] == []
        assert "overwrite" in result.output

    def test_confirm_abort(self, runner, fake, tmp_path):
        target = self._bundle(tmp_path, {"views": [{"name": "V"}]})
        result = runner.invoke(cli, ["import", str(target)], input="n\n")
        assert result.exit_code != 0
        assert fake.calls["create"] == []


class TestChannelsViewsCommands:
    def test_channels_list_body(self, runner, fake):
        import json as _json

        result = runner.invoke(cli, ["channels", "list", "--json"])
        assert result.exit_code == 0, result.output
        assert _json.loads(result.stdout)["data"][0]["name"] == "Ch"

    def test_channels_get_body(self, runner, fake):
        import json as _json

        result = runner.invoke(cli, ["channels", "get", "ch1", "--json"])
        assert result.exit_code == 0, result.output
        assert _json.loads(result.stdout)["data"]["id"] == "ch1"

    def test_channels_create_body(self, runner, fake, tmp_path):
        import json as _json

        pf = tmp_path / "c.json"
        pf.write_text(_json.dumps({"name": "C"}))
        result = runner.invoke(cli, ["channels", "create", str(pf), "--json"])
        assert result.exit_code == 0, result.output
        assert "ch9" in result.output

    def test_views_list_null_normalized(self, runner, fake):
        import json as _json

        result = runner.invoke(cli, ["views", "list", "--json"])
        assert result.exit_code == 0, result.output
        assert _json.loads(result.stdout) == {"data": []}

    def test_views_get_body(self, runner, fake):
        import json as _json

        result = runner.invoke(cli, ["views", "get", "v1", "--json"])
        assert result.exit_code == 0, result.output
        assert _json.loads(result.stdout)["data"]["id"] == "v1"


class TestExportPagination:
    def test_second_page_fetched(self, runner, fake, tmp_path):
        import json as _json

        fake.dashboards = [
            {"id": f"d{i}", "name": f"Dash{i}"} for i in range(150)
        ]
        fake.get_dashboard_v2 = lambda dashboard_id: {
            "status": "success",
            "data": {"id": dashboard_id, "name": "D"}}
        target = tmp_path / "b"
        result = runner.invoke(cli, ["export", str(target), "--yes",
                                     "--include", "dashboards"])
        assert result.exit_code == 0, result.output
        manifest = _json.loads((target / "manifest.json").read_text())
        assert manifest["counts"] == {"dashboards": 150}
        assert len(list((target / "dashboards").glob("*.json"))) == 150


class TestBundleRemaining:
    def test_fetch_channels_and_views(self, fake):
        from signoz_cli.commands.bundle import _fetch_full

        fake.list_channels = lambda: {"status": "success",
                                      "data": [{"id": "ch1", "name": "Ch"}]}
        fake.list_saved_views = lambda source_page="logs": {
            "status": "success", "data": [{"id": "v1", "name": "V"}]}
        from signoz_cli.commands.bundle import _list_items

        channels = _list_items(fake, "channels")
        views = _list_items(fake, "views")
        assert channels == [{"id": "ch1", "name": "Ch"}]
        assert views == [{"id": "v1", "name": "V"}]
        assert _fetch_full(fake, "channels", channels[0]) == {
            "id": "ch1", "name": "Ch"}
        assert _fetch_full(fake, "views", views[0]) == {
            "id": "v1", "name": "V"}

    def test_fetch_non_dict_falls_back_to_item(self, fake):
        from signoz_cli.commands.bundle import _fetch_full

        fake.get_channel = lambda channel_id: ["odd"]
        # A bare non-dict GET response is unexpected; keep the
        # list-summary item rather than exporting garbage.
        assert _fetch_full(fake, "channels", {"id": "ch1"}) == {"id": "ch1"}

    def test_import_skips_non_json_in_kind_dir(self, runner, fake, tmp_path):
        kind_dir = tmp_path / "b" / "views"
        kind_dir.mkdir(parents=True)
        (kind_dir / "notes.txt").write_text("not json")
        (kind_dir / "v.json").write_text('{"name": "V"}')
        result = runner.invoke(
            cli, ["import", str(tmp_path / "b"), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "notes.txt" not in result.output
        assert "V" in result.output


class TestDeleteConfirmPaths:
    DELETES = [
        (["dashboards", "delete", "d1"], "delete_dashboard_v2"),
        (["alerts", "delete", "r1"], "delete_rule"),
        (["channels", "delete", "ch1"], "delete_channel"),
        (["views", "delete", "v1"], "delete_saved_view"),
        (["service-accounts", "delete", "sa1"], "delete_service_account"),
    ]

    def _fake(self, monkeypatch, called):
        import signoz_cli.commands.common as cm

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def __getattr__(self, name):
                if name.startswith("__"):
                    raise AttributeError(name)

                def _recorder(*a, **k):
                    called.append((name, a))
                    return {"status": "success"}

                return _recorder

        monkeypatch.setattr(cm, "SigNozClient", Fake)

    @pytest.mark.parametrize(
        "argv,method", DELETES, ids=[d[1] for d in DELETES])
    def test_yes_skips_prompt_and_deletes(self, runner, monkeypatch,
                                          argv, method):
        called = []
        self._fake(monkeypatch, called)
        result = runner.invoke(cli, [*argv, "--yes", "--json"])
        assert result.exit_code == 0, result.output
        assert (method, (argv[2],)) in called

    @pytest.mark.parametrize(
        "argv,method", DELETES, ids=[d[1] for d in DELETES])
    def test_confirm_yes_deletes(self, runner, monkeypatch, argv, method):
        called = []
        self._fake(monkeypatch, called)
        result = runner.invoke(cli, argv, input="y\n")
        assert result.exit_code == 0, result.output
        assert (method, (argv[2],)) in called

    @pytest.mark.parametrize(
        "argv,method", DELETES, ids=[d[1] for d in DELETES])
    def test_confirm_no_aborts(self, runner, monkeypatch, argv, method):
        called = []
        self._fake(monkeypatch, called)
        result = runner.invoke(cli, argv, input="n\n")
        assert result.exit_code != 0
        assert called == []


class TestNewCommandErrors:
    @pytest.mark.parametrize("argv", [
        ["channels", "list"],
        ["channels", "get", "ch1"],
        ["views", "list"],
        ["views", "get", "v1"],
    ])
    def test_api_error_propagates(self, runner, monkeypatch, argv):
        import signoz_cli.commands.common as cm
        from signoz_cli.client import SigNozApiError

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def __getattr__(self, name):
                if name.startswith("__"):
                    raise AttributeError(name)

                def _boom(*a, **k):
                    raise SigNozApiError(500, "down")

                return _boom

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, argv)
        assert result.exit_code == 1
        assert "500" in result.output

    @pytest.mark.parametrize("argv,body", [
        (["channels", "create"], {"name": "c"}),
        (["channels", "update", "ch1"], {"name": "c"}),
        (["views", "create"], {"name": "v"}),
    ])
    def test_file_command_api_error_propagates(
            self, runner, monkeypatch, tmp_path, argv, body):
        import json as _json

        import signoz_cli.commands.common as cm
        from signoz_cli.client import SigNozApiError

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def __getattr__(self, name):
                if name.startswith("__"):
                    raise AttributeError(name)

                def _boom(*a, **k):
                    raise SigNozApiError(500, "down")

                return _boom

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        pf = tmp_path / "doc.json"
        pf.write_text(_json.dumps(body))
        result = runner.invoke(cli, [*argv, str(pf)])
        assert result.exit_code == 1
        assert "500" in result.output


class TestDowntimeCommands:
    def _fake(self, monkeypatch, calls):
        import signoz_cli.commands.common as cm

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def list_downtime_schedules(self):
                return {"status": "success", "data": [
                    {"id": "m1", "name": "Maint"}]}

            def get_downtime_schedule(self, schedule_id):
                calls.append(("get", schedule_id))
                return {"status": "success",
                        "data": {"id": schedule_id, "name": "Maint"}}

            def create_downtime_schedule(self, schedule):
                calls.append(("create", schedule))
                return {"status": "success", "data": {"id": "m9"}}

            def update_downtime_schedule(self, schedule_id, schedule):
                calls.append(("update", schedule_id, schedule))
                return {"status": "ok"}

            def delete_downtime_schedule(self, schedule_id):
                calls.append(("delete", schedule_id))
                return {"status": "ok"}

        monkeypatch.setattr(cm, "SigNozClient", Fake)

    def test_list_body(self, runner, monkeypatch):
        import json as _json

        self._fake(monkeypatch, [])
        result = runner.invoke(cli, ["alerts", "downtime", "list", "--json"])
        assert result.exit_code == 0, result.output
        assert _json.loads(result.stdout) == {
            "data": [{"id": "m1", "name": "Maint"}]}

    def test_list_null_normalized(self, runner, monkeypatch):
        import json as _json

        import signoz_cli.commands.common as cm

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def list_downtime_schedules(self):
                return {"status": "success", "data": None}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["alerts", "downtime", "list", "--json"])
        assert result.exit_code == 0, result.output
        assert _json.loads(result.stdout) == {"data": []}

    def test_create_posts_payload(self, runner, monkeypatch, tmp_path):
        import json as _json

        calls = []
        self._fake(monkeypatch, calls)
        pf = tmp_path / "m.json"
        pf.write_text(_json.dumps({"name": "M", "schedule": {}}))
        result = runner.invoke(
            cli, ["alerts", "downtime", "create", str(pf), "--json"])
        assert result.exit_code == 0, result.output
        assert calls == [("create", {"name": "M", "schedule": {}})]

    def test_update_puts_payload(self, runner, monkeypatch, tmp_path):
        import json as _json

        calls = []
        self._fake(monkeypatch, calls)
        pf = tmp_path / "m.json"
        pf.write_text(_json.dumps({"name": "M2"}))
        result = runner.invoke(
            cli, ["alerts", "downtime", "update", "m1", str(pf), "--json"])
        assert result.exit_code == 0, result.output
        assert calls == [("update", "m1", {"name": "M2"})]

    def test_delete_dry_run(self, runner, monkeypatch):
        import json as _json

        calls = []
        self._fake(monkeypatch, calls)
        result = runner.invoke(
            cli, ["alerts", "downtime", "delete", "m1", "--dry-run", "--json"])
        assert result.exit_code == 0, result.output
        assert _json.loads(result.stdout)["action"] == "delete"
        assert calls == []

    def test_delete_yes(self, runner, monkeypatch):
        calls = []
        self._fake(monkeypatch, calls)
        result = runner.invoke(
            cli, ["alerts", "downtime", "delete", "m1", "--yes"])
        assert result.exit_code == 0, result.output
        assert calls == [("delete", "m1")]

    def test_get_error_propagates(self, runner, monkeypatch):
        import signoz_cli.commands.common as cm
        from signoz_cli.client import SigNozApiError

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def get_downtime_schedule(self, schedule_id):
                raise SigNozApiError(404, "not found")

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = runner.invoke(cli, ["alerts", "downtime", "get", "nope"])
        assert result.exit_code == 1
        assert "404" in result.output


class TestHostileFiles:
    def test_read_json_size_cap(self, tmp_path, monkeypatch):
        import signoz_cli.commands.common as cm

        monkeypatch.setattr(cm, "_MAX_JSON_BYTES", 64)
        big = tmp_path / "big.json"
        big.write_text('{"pad": "' + "x" * 100 + '"}')
        import click
        import pytest

        with pytest.raises(click.UsageError, match="cap"):
            cm._read_json(str(big))
        small = tmp_path / "small.json"
        small.write_text('{"a": 1}')
        assert cm._read_json(str(small)) == {"a": 1}

    def test_read_stdin_size_cap(self, monkeypatch):
        import io as _io
        import sys as _sys

        import click
        import pytest

        import signoz_cli.commands.common as cm

        monkeypatch.setattr(cm, "_MAX_JSON_BYTES", 16)
        monkeypatch.setattr(_sys, "stdin", _io.StringIO("x" * 100))
        with pytest.raises(click.UsageError, match="[Ss]tdin"):
            cm._read_json("-")

    def test_import_skips_escaping_symlink(self, runner, fake, tmp_path):
        import json as _json
        import os as _os

        bundle = tmp_path / "bundle"
        kind_dir = bundle / "dashboards"
        kind_dir.mkdir(parents=True)
        (kind_dir / "good.json").write_text(_json.dumps(
            {"name": "Good", "spec": {}}))
        outside = tmp_path / "outside.json"
        outside.write_text(_json.dumps({"name": "EXFIL", "spec": {}}))
        _os.symlink(outside, kind_dir / "evil.json")
        result = runner.invoke(
            cli, ["import", str(bundle), "--yes"])
        assert result.exit_code == 0, result.output
        assert "not a regular file" in result.output
        created = [c for c in fake.calls["create"] if c[0] == "dashboards"]
        assert len(created) == 1
        assert created[0][1].get("name") == "Good"

    def test_export_dedups_colliding_names(self, runner, fake, tmp_path):
        fake.dashboards = [
            {"id": "abcdef01", "name": "Same"},
            {"id": "abcdef02", "name": "Same"},
        ]
        out = tmp_path / "out"
        result = runner.invoke(
            cli, ["export", str(out), "--include", "dashboards", "--yes"])
        assert result.exit_code == 0, result.output
        files = sorted((out / "dashboards").iterdir())
        assert len(files) == 2
        assert files[0].name != files[1].name
