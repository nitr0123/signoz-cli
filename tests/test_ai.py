"""Tests for the ai command group (SigNoz 0.140+: LLM fields, pricing)."""

import json as _json

import pytest
from click.testing import CliRunner

import signoz_cli.commands.common as cm
from signoz_cli.main import cli


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SIGNOZ_ENDPOINT", "http://sig.test:8080")
    monkeypatch.setenv("SIGNOZ_ACCESS_TOKEN", "tok-123456789")
    monkeypatch.delenv("SIGNOZ_PROFILE", raising=False)


class _AiFake:
    def __init__(self, *a, **k):
        self.calls = {"create": [], "delete": []}

    def close(self):
        pass

    def get_ai_fields_keys(self, search_text=""):
        assert search_text in ("", "gen_ai")
        return {"status": "success", "data": {"keys": ["gen_ai.model"]}}

    def get_ai_field_values(self, field_name, search_text=""):
        assert field_name == "gen_ai.model"
        return {"status": "success", "data": {"values": ["gpt-4o"]}}

    def list_llm_pricing_rules(self):
        return {"status": "success", "data": [{"id": "pr1"}]}

    def create_llm_pricing_rule(self, rule):
        self.calls["create"].append(rule)
        return {"status": "success", "data": {"id": "pr9"}}

    def get_llm_pricing_rule(self, rule_id):
        return {"status": "success", "data": {"id": rule_id}}

    def delete_llm_pricing_rule(self, rule_id):
        self.calls["delete"].append(rule_id)
        return {"status": "success"}

    def list_unmapped_llm_models(self):
        return {"status": "success", "data": [{"model": "mystery-1"}]}


@pytest.fixture()
def fake(monkeypatch):
    inst = _AiFake()
    monkeypatch.setattr(cm, "SigNozClient", lambda *a, **k: inst)
    return inst


class TestAiFields:
    def test_fields(self, runner, fake):
        result = runner.invoke(
            cli, ["ai", "fields", "--search", "gen_ai", "--json"])
        assert result.exit_code == 0, result.output
        assert "gen_ai.model" in result.output

    def test_field_values(self, runner, fake):
        result = runner.invoke(
            cli, ["ai", "field-values", "gen_ai.model", "--json"])
        assert result.exit_code == 0, result.output
        assert "gpt-4o" in result.output


class TestAiPricing:
    def test_list_and_get(self, runner, fake):
        result = runner.invoke(cli, ["ai", "pricing-rules", "list", "--json"])
        assert result.exit_code == 0, result.output
        assert "pr1" in result.output
        result = runner.invoke(
            cli, ["ai", "pricing-rules", "get", "pr1", "--json"])
        assert result.exit_code == 0, result.output
        assert _json.loads(result.stdout)["data"]["id"] == "pr1"

    def test_create(self, runner, fake, tmp_path):
        pf = tmp_path / "pr.json"
        pf.write_text(_json.dumps({"model": "m", "price": 1}))
        result = runner.invoke(
            cli, ["ai", "pricing-rules", "create", str(pf), "--json"])
        assert result.exit_code == 0, result.output
        assert fake.calls["create"] == [{"model": "m", "price": 1}]

    def test_delete_guards(self, runner, fake):
        result = runner.invoke(
            cli, ["ai", "pricing-rules", "delete", "pr1", "--dry-run",
                  "--json"])
        assert result.exit_code == 0, result.output
        assert fake.calls["delete"] == []
        result = runner.invoke(
            cli, ["ai", "pricing-rules", "delete", "pr1", "--yes", "--json"])
        assert result.exit_code == 0, result.output
        assert fake.calls["delete"] == ["pr1"]

    def test_unmapped_models(self, runner, fake):
        result = runner.invoke(cli, ["ai", "unmapped-models", "--json"])
        assert result.exit_code == 0, result.output
        assert "mystery-1" in result.output
