"""service accounts commands."""

from __future__ import annotations

import re
import time

import click

from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    cli,
)
from signoz_cli.output import emit
from signoz_cli.timeutil import parse_time_range


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


def _expiry_seconds(expires_in: str | None) -> int:
    """'90d' -> unix seconds 90 days from now; None/'never' -> 0 (no expiry)."""
    if not expires_in or expires_in == "never":
        return 0
    try:
        return int(time.time()) + parse_time_range(expires_in) // 1000
    except ValueError as ex:
        raise click.UsageError(f"{ex} Or 'never'.") from None


def _key_row(k: dict) -> dict:
    expires = k.get("expiresAt") or 0
    return {
        "id": k.get("id", ""),
        "name": k.get("name", ""),
        "createdAt": k.get("createdAt", ""),
        "expiresAt": (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires))
                      if expires else "never"),
        "lastUsed": k.get("lastObservedAt", ""),
    }


@service_accounts.group("keys")
def sa_keys() -> None:
    """Manage a service account's API keys (rotate without the UI)."""


@sa_keys.command("list")
@_common_options
@click.argument("account_id")
def sa_keys_list(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, account_id: str) -> None:
    """List a service account's API keys (values are never shown)."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_service_account_keys(account_id)
        if fmt == "json":
            emit(raw, fmt)
            return
        keys = raw.get("data") if isinstance(raw, dict) else raw
        emit({"data": [_key_row(k) for k in (keys or []) if isinstance(k, dict)]}, fmt)
    except Exception as e:
        _handle_error(e)


@sa_keys.command("create")
@_common_options
@click.argument("account_id")
@click.argument("name")
@click.option("--expires-in", default="never", show_default=True,
              help="Lifetime like 30d or 12h, or 'never'.")
def sa_keys_create(endpoint: str | None, token: str | None, fmt: str,
                   profile: str | None, account_id: str, name: str,
                   expires_in: str) -> None:
    """Create an API key. The key value is printed once; store it now."""
    try:
        expires_at = _expiry_seconds(expires_in)
        client = _get_client(endpoint, token, profile)
        raw = client.create_service_account_key(account_id, name, expires_at)
        emit(raw.get("data", raw) if isinstance(raw, dict) and fmt != "json" else raw, fmt)
        click.echo("Store the key now: it cannot be shown again.", err=True)
    except Exception as e:
        _handle_error(e)


@sa_keys.command("update")
@_common_options
@click.argument("account_id")
@click.argument("key_id")
@click.option("--name", required=True, help="Key name (the server requires it on update).")
@click.option("--expires-in", default=None,
              help="New lifetime from now, like 30d, or 'never'. Default: keep the "
                   "current expiry.")
def sa_keys_update(endpoint: str | None, token: str | None, fmt: str,
                   profile: str | None, account_id: str, key_id: str, name: str,
                   expires_in: str | None) -> None:
    """Rename a key or change its expiry."""
    try:
        client = _get_client(endpoint, token, profile)
        if expires_in is None:
            # The server replaces both fields: resend the current expiry so a
            # rename never silently turns an expiring key into a permanent one.
            raw = client.list_service_account_keys(account_id)
            keys = raw.get("data") if isinstance(raw, dict) else raw
            current = next((k for k in keys or [] if isinstance(k, dict)
                            and k.get("id") == key_id), None)
            if current is None:
                raise click.UsageError(f"No key {key_id} on service account {account_id}.")
            expires_at = int(current.get("expiresAt") or 0)
        else:
            expires_at = _expiry_seconds(expires_in)
        client.update_service_account_key(account_id, key_id, name, expires_at)
        emit(_key_row({"id": key_id, "name": name, "expiresAt": expires_at}), fmt)
    except Exception as e:
        _handle_error(e)


@sa_keys.command("revoke")
@_common_options
@click.argument("account_id")
@click.argument("key_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def sa_keys_revoke(endpoint: str | None, token: str | None, fmt: str,
                   profile: str | None, account_id: str, key_id: str,
                   yes: bool, dry_run: bool) -> None:
    """Revoke an API key (anything using it stops working immediately)."""
    try:
        if dry_run:
            emit({"action": "revoke", "resource": "service account key",
                  "id": key_id}, fmt)
            return
        if not yes:
            _confirm(f"Revoke key {key_id} of service account {account_id}?")
        client = _get_client(endpoint, token, profile)
        emit(client.revoke_service_account_key(account_id, key_id), fmt)
    except Exception as e:
        _handle_error(e)
