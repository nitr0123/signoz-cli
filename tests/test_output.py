"""Tests for output formatting helpers."""

import json

from rich.console import Console

from signoz_cli.output import _dict_list_table, _flatten, _trunc, emit


class TestEmit:
    def test_json_output_is_parseable(self, capsys):
        emit({"a": 1, "nested": {"b": [1, 2]}}, "json")
        data = json.loads(capsys.readouterr().out)
        assert data["a"] == 1
        assert data["nested"]["b"] == [1, 2]

    def test_table_empty_list(self, capsys):
        emit([], "table")
        assert "no results" in capsys.readouterr().out

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
