"""Tests for CSV output and -o/--output file routing."""

import csv
import io
import json

import pytest

from signoz_cli.output import emit, set_output_file


@pytest.fixture(autouse=True)
def _reset_output_file():
    yield
    set_output_file(None)


class TestCsv:
    def test_csv_rows(self, capsys):
        rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y,z"}]
        emit(rows, "csv")
        out = capsys.readouterr().out
        parsed = list(csv.DictReader(io.StringIO(out)))
        assert len(parsed) == 2
        assert parsed[0]["a"] == "1"
        assert parsed[1]["b"] == "y,z"

    def test_csv_union_of_keys(self, capsys):
        rows = [{"a": 1}, {"b": 2}]
        out = capsys.readouterr()
        emit(rows, "csv")
        out = capsys.readouterr().out
        reader = csv.DictReader(io.StringIO(out))
        assert set(reader.fieldnames or set()) == {"a", "b"}
        rows_parsed = list(reader)
        assert rows_parsed[0]["b"] == ""  # restval

    def test_csv_unwraps_data_envelope(self, capsys):
        emit({"data": [{"k": "v"}]}, "csv")
        out = capsys.readouterr().out
        assert next(iter(csv.DictReader(io.StringIO(out))))["k"] == "v"

    def test_csv_rejects_non_tabular(self):
        with pytest.raises(Exception, match="tabular"):
            emit({"status": "ok"}, "csv")

    def test_csv_empty_rows_error(self):
        with pytest.raises(Exception, match="No rows"):
            emit([], "csv")


class TestOutputFile:
    def test_json_to_file(self, tmp_path, capsys):
        target = tmp_path / "out.json"
        set_output_file(str(target))
        emit({"hello": [1, 2]}, "json")
        data = json.loads(target.read_text())
        assert data["hello"] == [1, 2]
        # stdout gets only the stderr-style notice (captured in err)
        captured = capsys.readouterr()
        assert "Output written" in captured.err
        assert captured.out == ""

    def test_table_to_file(self, tmp_path):
        target = tmp_path / "out.txt"
        set_output_file(str(target))
        emit([{"id": "abc"}], "table")
        content = target.read_text()
        assert "abc" in content

    def test_csv_to_file(self, tmp_path):
        target = tmp_path / "out.csv"
        set_output_file(str(target))
        emit([{"x": 1}], "csv")
        assert "x" in target.read_text()

    def test_no_results_to_file(self, tmp_path):
        target = tmp_path / "out.txt"
        set_output_file(str(target))
        emit([], "table")
        assert "no results" in target.read_text()
