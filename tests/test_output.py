"""Tests for output formatting helpers."""

import json

from rich.console import Console

from signoz_cli.output import (
    _apply_field_selector,
    _dict_list_table,
    _flatten,
    _jsonpath_extract,
    _sort_rows,
    _to_csv,
    _to_yaml,
    _trunc,
    emit,
    set_custom_columns,
    set_field_selector,
    set_sort_by,
)


class TestEmit:
    def test_json_output_is_parseable(self, capsys):
        emit({"a": 1, "nested": {"b": [1, 2]}}, "json")
        data = json.loads(capsys.readouterr().out)
        assert data["a"] == 1
        assert data["nested"]["b"] == [1, 2]

    def test_table_empty_list(self, capsys):
        emit([], "table")
        captured = capsys.readouterr()
        assert "no results" in captured.err
        assert captured.out == ""

    def test_table_key_value_for_dicts(self, capsys):
        emit({"status": "ok", "count": 3}, "table")
        text = capsys.readouterr().out
        assert "status" in text and "ok" in text
        assert "count" in text and "3" in text

    def test_table_renders_dict_rows_with_pruning(self, capsys):
        rows = [
            {"id": "abc", "name": "row1", "secret": "hide-me"},
            {"id": "def", "name": "row2", "secret": "hide-me-too"},
        ]
        emit(rows, "table", table_keys=["id", "name"])
        text = capsys.readouterr().out
        assert "row1" in text and "row2" in text
        assert "hide-me" not in text  # pruned column not rendered

    def test_json_ignores_pruning(self, capsys):
        rows = [{"id": "abc", "secret": "keep-in-json"}]
        emit(rows, "json", table_keys=["id"])
        data = json.loads(capsys.readouterr().out)
        assert data[0]["secret"] == "keep-in-json"

    def test_table_unwraps_data_envelope(self, capsys):
        emit({"data": [{"k": "v"}]}, "table")
        assert "v" in capsys.readouterr().out


class TestHelpers:
    def test_trunc(self):
        assert _trunc("short") == "short"
        long = "x" * 200
        cut = _trunc(long, max_len=50)
        assert len(cut) == 50 and cut.endswith("...")

    def test_flatten_flat_dict_untouched(self):
        row = {"a": 1, "b": "x"}
        assert _flatten(row) == row

    def test_flatten_merges_scalar_dicts(self):
        row = {"name": "svc", "tag": {"key": "env", "value": "prod"}}
        flat = _flatten(row)
        assert flat["name"] == "svc"
        assert flat["tag.key"] == "env"
        assert flat["tag.value"] == "prod"

    def test_flatten_keeps_nested_structures(self):
        row = {"tags": [{"k": 1}], "spec": {"deep": {"deeper": True}}}
        assert _flatten(row) == row

    def test_dict_list_table_collects_all_keys(self):
        buf_out = __import__("io").StringIO()
        console = Console(file=buf_out, width=200, force_terminal=False)
        rows = [{"a": 1}, {"a": 2, "b": 3}]
        _dict_list_table(console, rows)
        out = buf_out.getvalue()
        assert "a" in out and "b" in out

    def test_table_keys_mismatch_falls_back_to_full_row(self, capsys):
        # Regression: rows that share no key with table_keys rendered
        # as an empty (invisible) table instead of their content.
        emit([{"data": [], "total": 0}], "table", table_keys=["nope"])
        out = capsys.readouterr().out
        assert "total" in out

    def test_empty_envelope_renders_no_results(self, capsys):
        emit({"status": "success", "data": {"items": [], "total": 0}}, "table")
        captured = capsys.readouterr()
        assert "no results" in captured.err
        assert captured.out == ""

    def test_scalar_list_envelope_kept(self, capsys):
        # Non-empty scalar lists under envelope keys must not be dropped.
        emit({"data": ["a", "b"]}, "table")
        out = capsys.readouterr().out
        assert "a" in out and "b" in out


class TestSortBy:
    def test_sort_ascending(self):
        data = [{"n": 3}, {"n": 1}, {"n": 2}]
        set_sort_by("n")
        result = _sort_rows(data)
        assert [r["n"] for r in result] == [1, 2, 3]
        set_sort_by(None)

    def test_sort_descending(self):
        data = [{"n": 3}, {"n": 1}, {"n": 2}]
        set_sort_by("n", reverse=True)
        result = _sort_rows(data)
        assert [r["n"] for r in result] == [3, 2, 1]
        set_sort_by(None)

    def test_sort_by_string(self):
        data = [{"name": "c"}, {"name": "a"}, {"name": "b"}]
        set_sort_by("name")
        result = _sort_rows(data)
        assert [r["name"] for r in result] == ["a", "b", "c"]
        set_sort_by(None)

    def test_sort_missing_key(self):
        data = [{"n": 1}, {}, {"n": 2}]
        set_sort_by("n")
        result = _sort_rows(data)
        assert len(result) == 3
        set_sort_by(None)

    def test_sort_literal_dot_key(self):
        data = [{"service.name": "b"}, {"service.name": "a"}]
        set_sort_by("service.name")
        result = _sort_rows(data)
        assert [r["service.name"] for r in result] == ["a", "b"]
        set_sort_by(None)


class TestFieldSelector:
    def test_filter_eq(self):
        data = [{"s": "a", "v": 1}, {"s": "b", "v": 2}, {"s": "a", "v": 3}]
        set_field_selector("s=a")
        result = _apply_field_selector(data)
        assert len(result) == 2
        assert all(r["s"] == "a" for r in result)
        set_field_selector(None)

    def test_filter_no_match(self):
        data = [{"s": "a"}]
        set_field_selector("s=x")
        assert _apply_field_selector(data) == []
        set_field_selector(None)

    def test_filter_literal_dot_key(self):
        data = [{"service.name": "svc-a"}, {"service.name": "svc-b"}]
        set_field_selector("service.name=svc-a")
        result = _apply_field_selector(data)
        assert len(result) == 1
        set_field_selector(None)


class TestJsonpath:
    def test_root(self):
        data = [{"a": 1}, {"a": 2}]
        assert _jsonpath_extract(data, "$") == data

    def test_array_index(self):
        data = [{"a": 1}, {"a": 2}]
        assert _jsonpath_extract(data, "$[0]") == {"a": 1}
        assert _jsonpath_extract(data, "$[1]") == {"a": 2}

    def test_map_field(self):
        data = [{"name": "a"}, {"name": "b"}]
        assert _jsonpath_extract(data, "$[*].name") == ["a", "b"]

    def test_map_nested_field(self):
        data = [{"svc": {"name": "a"}}, {"svc": {"name": "b"}}]
        assert _jsonpath_extract(data, "$[*].svc.name") == ["a", "b"]

    def test_top_level_field(self):
        data = {"items": [{"x": 1}]}
        assert _jsonpath_extract(data, "$.items") == [{"x": 1}]

    def test_missing_field(self):
        data = [{"a": 1}]
        assert _jsonpath_extract(data, "$[*].b") == [None]

    def test_nested_wildcard_keeps_remainder(self):
        data = {"items": [{"name": "a"}, {"name": "b"}]}
        assert _jsonpath_extract(data, "$.items[*].name") == ["a", "b"]

    def test_nested_wildcard_deep_remainder(self):
        data = {"a": [{"b": {"c": 1}}, {"b": {"c": 2}}]}
        assert _jsonpath_extract(data, "$.a[*].b.c") == [1, 2]

    def test_nested_index(self):
        data = {"items": [{"name": "a"}, {"name": "b"}]}
        assert _jsonpath_extract(data, "$.items[0].name") == "a"
        assert _jsonpath_extract(data, "$.items[1].name") == "b"
        assert _jsonpath_extract(data, "$.items[5].name") is None

    def test_top_level_wildcard_with_remainder(self):
        data = [{"name": "a"}, {"name": "b"}]
        assert _jsonpath_extract(data, "$[*].name") == ["a", "b"]


class TestYaml:
    def test_yaml_list(self):
        out = _to_yaml([{"a": 1}, {"a": 2}])
        assert "- a: 1" in out
        assert "- a: 2" in out

    def test_yaml_dict(self):
        out = _to_yaml({"key": "value"})
        assert "key: value" in out


class TestCsv:
    def test_csv_rows(self):
        out = _to_csv([{"a": "1", "b": "2"}, {"a": "3", "b": "4"}])
        lines = out.strip().split("\n")
        assert lines[0] == "a,b"
        assert lines[1] == "1,2"

    def test_csv_empty(self):
        out = _to_csv([])
        assert out.strip() == ""


class TestCustomColumns:
    def test_custom_columns_renames_keys(self, capsys):
        data = [{"service.name": "svc-a", "name": "op1", "extra": "x"}]
        set_custom_columns(["svc:service.name", "op:name"])
        emit(data, "json")
        result = json.loads(capsys.readouterr().out)
        assert result == [{"svc": "svc-a", "op": "op1"}]
        set_custom_columns(None)

    def test_custom_columns_bare_field(self, capsys):
        data = [{"name": "a", "value": 1}]
        set_custom_columns(["name"])
        emit(data, "json")
        result = json.loads(capsys.readouterr().out)
        assert result == [{"name": "a"}]
        set_custom_columns(None)

    def test_custom_columns_table(self, capsys):
        data = [{"service.name": "svc-a", "name": "op1"}]
        set_custom_columns(["svc:service.name", "op:name"])
        emit(data, "table")
        out = capsys.readouterr().out
        assert "svc" in out and "op" in out
        assert "svc-a" in out
        set_custom_columns(None)

    def test_custom_columns_csv(self, capsys):
        data = [{"service.name": "svc-a", "name": "op1", "extra": "x"}]
        set_custom_columns(["svc:service.name", "op:name"])
        emit(data, "csv")
        out = capsys.readouterr().out
        lines = out.strip().split("\n")
        assert lines[0] == "svc,op"
        assert lines[1] == "svc-a,op1"
        set_custom_columns(None)


class TestWide:
    def test_wide_no_truncation(self, capsys):
        long_val = "x" * 200
        data = [{"name": long_val}]
        emit(data, "wide")
        out = capsys.readouterr().out
        assert long_val in out


class TestPagerSpawn:
    def test_quoted_pager_args_parsed(self, monkeypatch, capsys):
        import signoz_cli.output as _output

        seen = {}

        class FakeProc:
            def communicate(self, input=None):
                seen["input"] = input

        def fake_popen(args, **kwargs):
            seen["args"] = args
            return FakeProc()

        monkeypatch.setattr(_output.subprocess, "Popen", fake_popen)
        monkeypatch.setenv("PAGER", 'my-pager --opt "a  b"')
        _output._spawn_pager("hello")
        assert seen["args"][:3] == ["my-pager", "--opt", "a  b"]
        assert seen["input"] == b"hello"

    def test_malformed_pager_falls_back_to_stdout(self, monkeypatch, capsys):
        import signoz_cli.output as _output

        monkeypatch.setenv("PAGER", 'unbalanced "quote')
        _output._spawn_pager("hello")
        assert capsys.readouterr().out == "hello"

    def test_empty_pager_falls_back_to_stdout(self, monkeypatch, capsys):
        import signoz_cli.output as _output

        monkeypatch.setenv("PAGER", "   ")
        _output._spawn_pager("hello")
        assert capsys.readouterr().out == "hello"


class TestFlattenFields:
    def _flat(self):
        from signoz_cli.commands.common import (
            _flatten_field_keys,
            _flatten_field_values,
        )
        return _flatten_field_keys, _flatten_field_values

    def test_keys_attribute_keys_shape(self):
        keys, _ = self._flat()
        data = {"status": "success", "data": {"attributeKeys": [
            {"key": "a", "type": "tag", "dataType": "string"},
            {"key": "b", "type": "resource", "dataType": "int"},
            "junk",
        ]}}
        assert keys(data) == [
            {"key": "a", "type": "tag", "dataType": "string"},
            {"key": "b", "type": "resource", "dataType": "int"},
        ]

    def test_keys_logs_attributes_shape(self):
        keys, _ = self._flat()
        data = {"status": "success", "data": {"attributes": [
            {"key": "service.name", "type": "resource", "dataType": "string"},
        ]}}
        assert keys(data) == [
            {"key": "service.name", "type": "resource", "dataType": "string"},
        ]

    def test_keys_null_and_garbage(self):
        keys, _ = self._flat()
        assert keys({"status": "success", "data": None}) == []
        assert keys({"status": "success"}) == []
        assert keys("nope") == []
        assert keys(None) == []

    def test_values_traces_shape(self):
        _, values = self._flat()
        data = {"status": "success", "data": {"values": {
            "stringValues": ["a", "b"]}, "complete": True}}
        assert values(data) == [{"value": "a"}, {"value": "b"}]

    def test_values_empty_dict_shape(self):
        _, values = self._flat()
        assert values({"status": "success",
                       "data": {"values": {}, "complete": True}}) == []

    def test_values_logs_shape_with_nulls(self):
        _, values = self._flat()
        data = {"status": "success", "data": {
            "stringAttributeValues": ["x"],
            "numberAttributeValues": None,
            "boolAttributeValues": [True],
        }}
        assert values(data) == [{"value": "x"}, {"value": True}]

    def test_values_metrics_keys_shape(self):
        _, values = self._flat()
        data = {"status": "success", "data": {"keys": {
            "b": [{"name": "b"}], "a": [{"name": "a"}],
        }}}
        assert values(data) == [{"value": "a"}, {"value": "b"}]

    def test_values_garbage(self):
        _, values = self._flat()
        assert values(None) == []
        assert values({"data": {"keys": [1, 2]}}) == []


class TestEmptyContract:
    """Empty results stay machine-readable per format (report H1/H2)."""

    def test_empty_json_is_parseable_list(self, capsys):
        import json as _json

        emit([], "json")
        assert _json.loads(capsys.readouterr().out) == []

    def test_empty_yaml_is_parseable(self, capsys):
        import yaml as _yaml

        emit([], "yaml")
        out = capsys.readouterr().out
        assert _yaml.safe_load(out) in ([], None, "")

    def test_empty_table_and_wide_notice_on_stderr(self, capsys):
        for fmt in ("table", "wide"):
            emit([], fmt)
            captured = capsys.readouterr()
            assert "no results" in captured.err
            assert captured.out == ""

    def test_empty_custom_columns_notice_on_stderr(self, capsys):
        set_custom_columns(["a:b"])
        try:
            emit([], "table")
            captured = capsys.readouterr()
            assert "no results" in captured.err
        finally:
            set_custom_columns(None)

    def test_empty_csv_rejected(self):
        import click as _click
        import pytest as _pytest

        with _pytest.raises(_click.UsageError):
            emit([], "csv")


class TestYamlRoundTrip:
    def test_hostile_keys_and_values(self):
        import yaml as _yaml

        from signoz_cli.output import _to_yaml

        data = {
            "a:b": 1,
            "c d": "x: y",
            "e\nf": 1,
            "num": "0123",
            "ver": "1.0",
            "bool-ish": "yes",
            "quote": 'say "hi"\nbye',
            "lead": "  padded  ",
            "dash": "- item",
            "nested": {"k": [1, "a:b", None, True]},
        }
        assert _yaml.safe_load(_to_yaml(data)) == data

    @staticmethod
    def _json_values():
        from hypothesis import strategies as st

        hostile = st.text(
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "S", "Z"),
                blacklist_characters="\x00",
            ),
            max_size=25,
        )
        scalar = st.one_of(
            hostile, st.integers(), st.floats(allow_nan=False),
            st.booleans(), st.none(),
        )
        return st.recursive(
            scalar,
            lambda children: st.one_of(
                st.lists(children, max_size=4),
                st.dictionaries(hostile, children, max_size=4),
            ),
            max_leaves=12,
        )

    def test_property_round_trip(self):
        import yaml as _yaml
        from hypothesis import given, settings

        from signoz_cli.output import _to_yaml

        @given(self._json_values())
        @settings(max_examples=150, deadline=None)
        def check(data):
            # NaN never round-trips (excluded); infinities become strings
            # only if the emitter misquotes -- assert exact equality.
            assert _yaml.safe_load(_to_yaml(data)) == data

        check()


class TestTerminalInjection:
    EVIL = ("\x1b]0;PWNED\x07\x1b[2K\r[red]INJECTED[/red]"
            "\x1b]8;;http://evil\x07click\x1b]8;;\x07")

    def _table_text(self, rows):
        import io as _io

        from rich.console import Console

        from signoz_cli.output import _dict_list_table

        buf = _io.StringIO()
        console = Console(file=buf, force_terminal=True, width=250)
        _dict_list_table(console, rows)
        return buf.getvalue()

    def test_no_escape_sequences_reach_terminal(self):
        out = self._table_text([{"service": "svc", "body": self.EVIL}])
        assert "\x1b]0;" not in out
        assert "\x1b]8;;" not in out
        assert "\x07" not in out
        # Rich's own styling escapes are fine; payload must not add any.
        assert "PWNED" not in out or "\x1b]0" not in out

    def test_markup_link_neutralized_but_readable(self):
        out = self._table_text([{"body": "[link=http://evil]click[/link]"}])
        assert "]8;;" not in out
        assert "click" in out

    def test_control_chars_stripped(self):
        from signoz_cli.output import _safe_text

        assert _safe_text("a\x00b\x1fc\x7fd") == "abcd"
        assert _safe_text("[prod]") == "\\[prod]"
        assert _safe_text("plain") == "plain"

    def test_machine_formats_stay_byte_faithful(self, capsys):
        import json as _json

        rows = [{"body": self.EVIL}]
        emit(rows, "json")
        assert _json.loads(capsys.readouterr().out) == rows
        emit(rows, "csv")
        out = capsys.readouterr().out
        assert "PWNED" in out  # CSV keeps raw bytes for pipelines


class TestPlainOutputSanitization:
    EVIL = "\x1b]0;PWNED\x07\x1b[2K\rbody with [brackets] intact"

    def test_safe_plain_strips_controls_keeps_brackets(self):
        from signoz_cli.output import _safe_plain

        out = _safe_plain(self.EVIL)
        assert "\x1b" not in out
        assert "\x07" not in out
        assert "[brackets]" in out

    def test_extract_error_neutralized(self):
        import httpx

        from signoz_cli.client import _extract_error

        req = httpx.Request("GET", "http://x/api/v1/rules")
        resp = httpx.Response(400, json={"message": self.EVIL}, request=req)
        msg = _extract_error(resp)
        assert "\x1b" not in msg and "\x07" not in msg
        assert "[brackets]" in msg
        resp2 = httpx.Response(500, text="<html>" + self.EVIL + "</html>",
                               request=req)
        assert "\x1b" not in _extract_error(resp2)

    def test_warn_v5_neutralized(self, capsys):
        from signoz_cli.commands.common import _warn_v5

        _warn_v5({"data": {"meta": {"warning": {"warnings": [
            {"message": self.EVIL}]}}}})
        err = capsys.readouterr().err
        assert "\x1b" not in err
        assert "[brackets]" in err

    def test_error_path_end_to_end(self):
        from click.testing import CliRunner

        from signoz_cli.main import cli

        result = CliRunner().invoke(cli, ["alerts", "get", "no-such-rule",
                                          "--endpoint", "http://127.0.0.1:9",
                                          "--token", "tok-123456789"])
        assert result.exit_code == 1
        assert "\x1b" not in (result.output or "")



class TestCharts:
    def test_sparkline_exact_shape(self):
        from signoz_cli.output import _sparkline

        assert _sparkline([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], 8) == "▁▂▃▄▅▆▇█"
        assert _sparkline([5.0, 5.0, 5.0], 3) == "▄▄▄"
        assert _sparkline([], 4) == "    "
        assert _sparkline([1.0], 0) == ""

    def test_sparkline_downsamples(self):
        from signoz_cli.output import _sparkline

        out = _sparkline([float(i) for i in range(100)], 10)
        assert len(out) == 10
        assert out[0] == "▁" and out[-1] == "█"

    def test_render_chart_groups_and_stats(self):
        from signoz_cli.output import render_chart

        rows = [
            {"series": "a", "timestamp": 1, "value": 1.0},
            {"series": "a", "timestamp": 2, "value": 3.0},
            {"series": "b", "timestamp": 1, "value": 10.0},
            {"series": "b", "timestamp": 2, "value": 10.0},
            {"series": "evil\x1b[2K", "timestamp": 1, "value": 2.0},
        ]
        out = render_chart(rows, width=60)
        assert "last=3" in out and "min=1" in out and "max=3" in out
        assert "\x1b" not in out

    def test_render_chart_rejects_garbage(self):
        import pytest

        from signoz_cli.output import render_chart

        with pytest.raises(ValueError, match="plottable"):
            render_chart([{"foo": "bar"}])
        with pytest.raises(ValueError, match="plottable"):
            render_chart([])

    def test_chart_flag_end_to_end(self, monkeypatch):
        from click.testing import CliRunner

        import signoz_cli.commands.common as cm
        from signoz_cli.main import cli

        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
        monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
        monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_promql(self, *a, **k):
                return {"data": {"data": {"results": [{
                    "aggregations": [{"series": [{
                        "labels": [{"key": "s", "value": "x"}],
                        "values": [{"timestamp": 1, "value": 2.0},
                                   {"timestamp": 2, "value": 4.0}]}]}]}]}}}

        saved = cm.SigNozClient
        cm.SigNozClient = Fake
        try:
            result = CliRunner().invoke(cli, ["promql", "up", "--chart"])
            assert result.exit_code == 0, result.output
            assert "last=4" in result.output
            bad = CliRunner().invoke(cli, ["promql", "up", "--chart", "--json"])
            assert bad.exit_code == 2
            assert "--chart" in bad.output
        finally:
            cm.SigNozClient = saved


class TestChartNonFinite:
    def test_inf_nan_points_skipped(self):
        from signoz_cli.output import render_chart

        out = render_chart([
            {"series": "a", "timestamp": 1, "value": float("inf")},
            {"series": "a", "timestamp": 2, "value": 2.0},
            {"series": "b", "timestamp": 1, "value": float("nan")},
        ], width=40)
        assert "last=2" in out
        assert "inf" not in out and "nan" not in out

    def test_all_non_finite_rejected_cleanly(self):
        import pytest

        from signoz_cli.output import render_chart

        with pytest.raises(ValueError, match="plottable"):
            render_chart([{"series": "a", "timestamp": 1,
                           "value": float("inf")}])
class TestWaterfall:
    def _spans(self):
        return [
            {"span_id": "r", "parent_span_id": "", "name": "root",
             "time_unix": 1000, "duration_nano": 50_000_000,
             "level": 0, "has_error": False,
             "resource": {"service.name": "web"}},
            {"span_id": "c", "parent_span_id": "r", "name": "db\x1b[2K",
             "time_unix": 1010, "duration_nano": 20_000_000,
             "level": 1, "has_error": True,
             "resource": {"service.name": "db"}},
            {"span_id": "x", "name": "nodata"},
        ]

    def test_renders_bars_and_flags(self):
        from signoz_cli.output import render_waterfall

        out = render_waterfall(self._spans(), width=70)
        assert "web root" in out
        assert "█" in out
        assert "!" in out
        assert "\x1b" not in out
        assert "2 span(s)" in out

    def test_rejects_empty(self):
        import pytest

        from signoz_cli.output import render_waterfall

        with pytest.raises(ValueError, match="No spans"):
            render_waterfall([{"name": "nodata"}])
        with pytest.raises(ValueError, match="No spans"):
            render_waterfall([])

    def test_waterfall_command(self, monkeypatch):
        from click.testing import CliRunner

        import signoz_cli.commands.common as cm
        from signoz_cli.main import cli

        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
        monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
        monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)
        spans = self._spans()

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def get_trace_detail(self, trace_id, s, e):
                assert trace_id == "abc123"
                return {"data": {"spans": spans}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = CliRunner().invoke(cli, ["traces", "waterfall", "abc123"])
        assert result.exit_code == 0, result.output
        assert "█" in result.output
        bad = CliRunner().invoke(
            cli, ["traces", "waterfall", "abc123", "--json"])
        assert bad.exit_code == 2


class TestCompare:
    def test_join_aligns_and_pcts(self):
        from signoz_cli.commands.common import _join_compare

        cur = [
            {"series": "a", "timestamp": 2, "value": 150.0},
            {"series": "a", "timestamp": 1, "value": 100.0},
            {"series": "b", "timestamp": 1, "value": 10.0},
        ]
        prev = [
            {"series": "a", "timestamp": 1, "value": 100.0},
            {"series": "a", "timestamp": 2, "value": 100.0},
        ]
        out = _join_compare(cur, prev)
        assert out[0]["previous"] == 100.0
        assert out[0]["change_pct"] == 50.0
        assert out[1]["previous"] == 100.0
        assert out[1]["change_pct"] == 0.0
        assert out[2]["previous"] is None
        assert out[2]["change_pct"] is None

    def test_join_zero_previous(self):
        from signoz_cli.commands.common import _join_compare

        out = _join_compare(
            [{"series": "a", "timestamp": 1, "value": 5.0}],
            [{"series": "a", "timestamp": 1, "value": 0.0}])
        assert out[0]["previous"] == 0.0
        assert out[0]["change_pct"] is None

    def test_compare_flag_validation(self, monkeypatch):
        from click.testing import CliRunner

        import signoz_cli.commands.common as cm
        from signoz_cli.main import cli

        monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
        monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
        monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)

        class Fake:
            def __init__(self, *a, **k):
                pass

            def close(self):
                pass

            def query_builder_v5(self, payload):
                return {"status": "success",
                        "data": {"type": "scalar", "data": {"results": []}}}

        monkeypatch.setattr(cm, "SigNozClient", Fake)
        result = CliRunner().invoke(
            cli, ["metrics", "query", "m", "--compare", "bogus"])
        assert result.exit_code == 2
        assert "Invalid --compare" in result.output
