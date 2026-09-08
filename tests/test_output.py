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
