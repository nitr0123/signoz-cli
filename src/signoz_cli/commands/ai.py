"""AI observability commands (SigNoz 0.140+).

Covers LLM span field discovery, per-model pricing rules, and models
without pricing. AI spans themselves are queried through the normal
trace/log query paths with ``gen_ai.*`` attributes.
"""

from __future__ import annotations

import click

from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    _read_json,
    cli,
)
from signoz_cli.output import emit


@cli.group("ai")
def ai() -> None:
    """Inspect AI/LLM observability data (fields work widely; pricing
    rules need a recent server)."""


@ai.command("fields")
@_common_options
@click.option("--search", default="", help="Free-text search over field keys.")
def ai_fields(endpoint: str | None, token: str | None, fmt: str,
              profile: str | None, search: str) -> None:
    """List AI span field keys (gen_ai.* attributes)."""
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.get_ai_fields_keys(search), fmt)
    except Exception as e:
        _handle_error(e)


@ai.command("field-values")
@_common_options
@click.argument("field_name")
@click.option("--search", default="", help="Free-text search over values.")
def ai_field_values(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, field_name: str,
                    search: str) -> None:
    """List values for an AI span field."""
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.get_ai_field_values(field_name, search), fmt)
    except Exception as e:
        _handle_error(e)


@ai.group("pricing-rules")
def pricing_rules() -> None:
    """Manage per-model LLM pricing rules."""


@pricing_rules.command("list")
@_common_options
def pricing_rules_list(endpoint: str | None, token: str | None, fmt: str,
                       profile: str | None) -> None:
    """List LLM pricing rules."""
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.list_llm_pricing_rules(), fmt)
    except Exception as e:
        _handle_error(e)


@pricing_rules.command("get")
@_common_options
@click.argument("rule_id")
def pricing_rules_get(endpoint: str | None, token: str | None, fmt: str,
                      profile: str | None, rule_id: str) -> None:
    """Get one LLM pricing rule by id."""
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.get_llm_pricing_rule(rule_id), fmt)
    except Exception as e:
        _handle_error(e)


@pricing_rules.command("create")
@_common_options
@click.argument("json_file", type=click.Path())
def pricing_rules_create(endpoint: str | None, token: str | None, fmt: str,
                         profile: str | None, json_file: str) -> None:
    """Create or update LLM pricing rules from a JSON file."""
    try:
        rule = _read_json(json_file)
        client = _get_client(endpoint, token, profile)
        emit(client.create_llm_pricing_rule(rule), fmt)
    except Exception as e:
        _handle_error(e)


@pricing_rules.command("delete")
@_common_options
@click.argument("rule_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def pricing_rules_delete(endpoint: str | None, token: str | None, fmt: str,
                         profile: str | None, rule_id: str,
                         yes: bool, dry_run: bool) -> None:
    """Delete an LLM pricing rule."""
    try:
        if dry_run:
            emit({"action": "delete", "resource": "llm pricing rule",
                  "id": rule_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete LLM pricing rule {rule_id}?")
        client = _get_client(endpoint, token, profile)
        emit(client.delete_llm_pricing_rule(rule_id), fmt)
    except Exception as e:
        _handle_error(e)


@ai.command("unmapped-models")
@_common_options
def ai_unmapped_models(endpoint: str | None, token: str | None, fmt: str,
                       profile: str | None) -> None:
    """List LLM models seen in spans that have no pricing rule."""
    try:
        client = _get_client(endpoint, token, profile)
        emit(client.list_unmapped_llm_models(), fmt)
    except Exception as e:
        _handle_error(e)
