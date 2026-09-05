"""service accounts commands."""

from __future__ import annotations

import re

import click

from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    cli,
)
from signoz_cli.output import emit


@cli.group("service-accounts")
def service_accounts() -> None:
    """Manage service accounts and roles (requires admin)."""


@service_accounts.command("list")
@_common_options
def service_accounts_list(endpoint: str | None, token: str | None, fmt: str,
                          profile: str | None) -> None:
    """List service accounts."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_service_accounts()
        items = raw.get("data") if isinstance(raw, dict) else raw
        if isinstance(items, dict):
            items = items.get("data") or items.get("accounts") or []
        simplified = [
            {
                "id": a.get("id", ""),
                "name": a.get("name", ""),
                "status": a.get("status", ""),
                "email": a.get("email", ""),
            }
            for a in (items or [])
            if isinstance(a, dict)
        ]
        emit({"data": simplified}, fmt)
    except Exception as e:
        _handle_error(e)


@service_accounts.command("create")
@_common_options
@click.argument("name")
@click.option("--role", "role_name", default=None,
              help="Assign this managed role after creation (e.g. viewer).")
def service_accounts_create(endpoint: str | None, token: str | None, fmt: str,
                            profile: str | None, name: str,
                            role_name: str | None) -> None:
    """Create a service account, optionally assigning a managed role."""
    try:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,49}", name):
            raise click.UsageError(
                "Name must be lowercase letters/numbers/hyphens, max 50 chars."
            )
        client = _get_client(endpoint, token, profile)
        role_ids: list[str] = []
        if role_name:
            roles_raw = client.list_roles()
            roles = roles_raw.get("data") if isinstance(roles_raw, dict) else roles_raw
            wanted = role_name.lower().replace("signoz-", "")
            for r in roles or []:
                if r.get("name", "").lower().replace("signoz-", "") == wanted:
                    role_ids.append(r["id"])
                    break
            if not role_ids:
                raise click.UsageError(f"Unknown role '{role_name}'.")
        data, failed_roles = client.create_service_account(name, role_ids)
        if isinstance(data, dict) and fmt != "json":
            data = data.get("data", data)
            if isinstance(data, dict):
                data["assignedRoles"] = [role_name] if role_name and not failed_roles else []
                if failed_roles:
                    data["failedRoleAssignments"] = failed_roles
        emit(data, fmt)
        if failed_roles:
            click.echo(
                f"Warning: service account created but role assignment failed: "
                f"{'; '.join(failed_roles)}. Assign manually via Settings.",
                err=True,
            )
    except Exception as e:
        _handle_error(e)


@service_accounts.command("delete")
@_common_options
@click.argument("account_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def service_accounts_delete(endpoint: str | None, token: str | None, fmt: str,
                            profile: str | None, account_id: str, yes: bool, dry_run: bool) -> None:
    """Delete a service account (revokes all its keys)."""
    try:
        if dry_run:
            emit({"action": "delete", "resource": "service account", "id": account_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete service account {account_id}?")
        client = _get_client(endpoint, token, profile)
        emit(client.delete_service_account(account_id), fmt)
    except Exception as e:
        _handle_error(e)


@service_accounts.command("roles")
@_common_options
def service_accounts_roles(endpoint: str | None, token: str | None, fmt: str,
                           profile: str | None) -> None:
    """List available roles."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_roles()
        items = raw.get("data") if isinstance(raw, dict) else raw
        simplified = [
            {"id": r.get("id", ""), "name": r.get("name", ""),
             "type": r.get("type", ""), "description": r.get("description", "")}
            for r in (items or []) if isinstance(r, dict)
        ]
        emit({"data": simplified}, fmt)
    except Exception as e:
        _handle_error(e)
