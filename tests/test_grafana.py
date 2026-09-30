"""Tests for Grafana -> SigNoz v2 dashboard conversion."""

import json as _json

import pytest
from click.testing import CliRunner

from signoz_cli.grafana import convert_dashboard


@pytest.fixture()
def runner():
    return CliRunner()


def _grafana_doc() -> dict:
    return {
        "title": "Payments Overview",
        "description": "team dashboard",
        "tags": ["payments", "prod"],
        "templating": {"list": [
            {"name": "env", "label": "Env", "type": "custom",
             "current": {"value": "prod"}},
            {"name": "q", "type": "query",
             "current": {"value": "label_values(up)"}},
        ]},
        "panels": [
            {"id": 1, "title": "RPS", "type": "timeseries",
             "description": "requests",
             "targets": [{"refId": "A", "expr": "sum(rate(http[5m]))",
                          "legendFormat": "{{svc}}"}]},
            {"id": 2, "title": "Errors SQL", "type": "graph",
             "targets": [{"refId": "A",
                          "rawSql": "SELECT count() FROM logs"}]},
            {"id": 3, "title": "Note", "type": "text",
             "options": {"mode": "markdown", "content": "# hi"}},
            {"id": 4, "title": "Old", "type": "heatmap",
             "targets": [{"refId": "A", "expr": "x"}]},
            {"id": 5, "title": "Row", "type": "row",
             "panels": [{"id": 6, "title": "Nested", "type": "stat",
                         "targets": [{"refId": "A", "expr": "up"}]}]},
        ],
    }


class TestConvert:
    def test_shape(self):
        out, warnings = convert_dashboard(_grafana_doc())
        assert out["schemaVersion"] == "v6"
        assert out["name"] == "payments-overview"
        assert out["spec"]["display"]["name"] == "Payments Overview"
        assert out["tags"] == ["payments", "prod"]
        assert out["spec"]["layouts"] == []
        assert len(out["spec"]["panels"]) == 4  # heatmap skipped
        ts = out["spec"]["panels"]["panel-rps-0"]
        assert ts["spec"]["plugin"]["kind"] == "signoz/TimeSeriesPanel"
        q = ts["spec"]["queries"][0]
        assert q["kind"] == "time_series"
        assert q["spec"]["plugin"]["kind"] == "signoz/PromQLQuery"
        assert q["spec"]["plugin"]["spec"]["query"] == "sum(rate(http[5m]))"
        assert q["spec"]["plugin"]["spec"]["legend"] == "{{svc}}"

    def test_text_and_sql_and_stat(self):
        out, warnings = convert_dashboard(_grafana_doc())
        text = out["spec"]["panels"]["panel-note-2"]
        assert text["spec"]["plugin"]["kind"] == "signoz/TextPanel"
        assert text["spec"]["plugin"]["spec"]["text"] == "# hi"
        assert any("0.142+" in w for w in warnings)
        sql = out["spec"]["panels"]["panel-errors-sql-1"]
        assert sql["spec"]["plugin"]["kind"] == "signoz/TimeSeriesPanel"
        assert sql["spec"]["queries"][0]["spec"]["plugin"]["kind"] == \
            "signoz/ClickHouseSQL"
        stat = out["spec"]["panels"]["panel-nested-4"]
        assert stat["spec"]["plugin"]["kind"] == "signoz/NumberPanel"

    def test_variables(self):
        out, warnings = convert_dashboard(_grafana_doc())
        variables = out["spec"]["variables"]
        assert len(variables) == 1
        assert variables[0]["kind"] == "TextVariable"
        assert variables[0]["spec"]["value"] == "prod"
        assert any("needs manual" in w for w in warnings)

    def test_warnings_name_drops(self):
        _, warnings = convert_dashboard(_grafana_doc())
        blob = "\n".join(warnings)
        assert "heatmap" in blob
        assert "Row" not in blob  # rows flatten silently, no warning needed

    def test_non_object_rejected(self):
        import pytest

        with pytest.raises(ValueError, match="JSON object"):
            convert_dashboard([1, 2])


class TestConvertCommand:
    def test_convert_and_lint(self, runner, tmp_path):
        from signoz_cli.main import cli

        pf = tmp_path / "g.json"
        pf.write_text(_json.dumps(_grafana_doc()))
        out = tmp_path / "s.json"
        result = runner.invoke(
            cli, ["dashboards", "convert", str(pf), "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert "heatmap" in result.output  # warnings on stderr
        converted = _json.loads(out.read_text())
        assert converted["schemaVersion"] == "v6"
        result = runner.invoke(cli, ["dashboards", "lint", str(out)])
        assert result.exit_code == 0, result.output
