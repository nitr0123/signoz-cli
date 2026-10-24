"""users and roles commands (admin)."""

from __future__ import annotations

from typing import Any

import click

from signoz_cli.client import SigNozApiError
from signoz_cli.commands._crud import add_crud, data_of, items_of
from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    cli,
)
from signoz_cli.output import emit


def _user_row(u: dict[str, Any]) -> dict[str, Any]:
    roles = [
        (r.get("role") or {}).get("name", r.get("roleId", ""))
        for r in (u.get("userRoles") or []) if isinstance(r, dict)
    ]
    row = {
        "id": u.get("id", ""),
        "email": u.get("email", ""),
        "name": u.get("displayName", ""),
        "status": u.get("status", ""),
        "root": u.get("isRoot", False),
    }
    if roles:
        row["roles"] = ", ".join(str(r) for r in roles)
    return row


def _resolve_role(client: Any, wanted: str) -> dict[str, Any]:
    """Find a role by id or name ('admin' also matches 'signoz-admin')."""
    norm = wanted.lower().removeprefix("signoz-")
    for r in items_of(client.list_roles()):
        if not isinstance(r, dict):
            continue
        if r.get("id") == wanted or str(r.get("name", "")).lower().removeprefix("signoz-") == norm:
            return r
    raise click.UsageError(
        f"Unknown role '{wanted}'. List them with `signoz roles list`.")


@cli.group("users")
def users() -> None:
    """Manage users: invite, roles, password-reset links (requires admin)."""


@users.command("list")
@_common_options
def users_list(endpoint: str | None, token: str | None, fmt: str,
               profile: str | None) -> None:
    """List users in the organization."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_users()
        if fmt == "json":
            emit(raw, fmt)
            return
        emit({"data": [_user_row(u) for u in items_of(raw) if isinstance(u, dict)]}, fmt)
    except Exception as e:
        _handle_error(e)


@users.command("get")
@_common_options
@click.argument("user_id")
def users_get(endpoint: str | None, token: str | None, fmt: str,
              profile: str | None, user_id: str) -> None:
    """Get a user, including role assignments."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_user(user_id)
        emit(raw if fmt == "json" else _user_row(data_of(raw)), fmt)
    except Exception as e:
        _handle_error(e)


@users.command("me")
@_common_options
def users_me(endpoint: str | None, token: str | None, fmt: str,
             profile: str | None) -> None:
    """Show the user the credentials belong to (session/user tokens only;
    API keys belong to service accounts: see `auth whoami`)."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_me()
        emit(raw if fmt == "json" else _user_row(data_of(raw)), fmt)
    except Exception as e:
        _handle_error(e)


@users.command("invite")
@_common_options
@click.argument("email")
@click.option("--name", "display_name", default="", help="Display name.")
@click.option("--role", "roles", multiple=True,
              help="Role name or id to grant (repeatable; e.g. viewer, editor, admin).")
def users_invite(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, email: str, display_name: str,
                 roles: tuple[str, ...]) -> None:
    """Invite a user by email (they set a password from the invite link).

    SigNoz emails the invite when SMTP is configured; otherwise share the
    link from `users reset-link <id>`.
    """
    try:
        client = _get_client(endpoint, token, profile)
        role_ids = [_resolve_role(client, r)["id"] for r in roles]
        raw = client.invite_user(email, display_name, role_ids,
                                 frontend_base_url=client.endpoint)
        emit(raw if fmt == "json" else {"email": email, **data_of(raw)}, fmt)
    except Exception as e:
        _handle_error(e)


@users.command("update")
@_common_options
@click.argument("user_id")
@click.option("--name", "display_name", required=True, help="New display name.")
def users_update(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, user_id: str, display_name: str) -> None:
    """Change a user's display name."""
    try:
        client = _get_client(endpoint, token, profile)
        client.update_user(user_id, display_name)
        emit({"id": user_id, "name": display_name}, fmt)
    except Exception as e:
        _handle_error(e)


@users.command("delete")
@_common_options
@click.argument("user_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, help="Show what would happen; do nothing.")
def users_delete(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, user_id: str, yes: bool, dry_run: bool) -> None:
    """Delete a user (they lose access immediately).

    Needs a user's session token: the server records which user did the
    deleting, so service-account API keys are refused (SigNoz 0.147).
    """
    try:
        if dry_run:
            emit({"action": "delete", "resource": "user", "id": user_id}, fmt)
            return
        if not yes:
            _confirm(f"Delete user {user_id}?")
        client = _get_client(endpoint, token, profile)
        try:
            emit(client.delete_user(user_id), fmt)
        except SigNozApiError as ex:
            if ex.status_code == 404 and user_id not in ex.message:
                click.echo("Hint: the server looked up the caller, not the target; "
                           "deleting users needs a user session token, not a "
                           "service-account API key.", err=True)
            raise
    except Exception as e:
        _handle_error(e)


@users.command("roles")
@_common_options
@click.argument("user_id")
def users_roles(endpoint: str | None, token: str | None, fmt: str,
                profile: str | None, user_id: str) -> None:
    """List the roles granted to a user."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_user_roles(user_id)
        rows = [{"id": r.get("id", ""), "name": r.get("name", ""), "type": r.get("type", "")}
                for r in items_of(raw) if isinstance(r, dict)]
        emit(raw if fmt == "json" else {"data": rows}, fmt)
    except Exception as e:
        _handle_error(e)


@users.command("grant")
@_common_options
@click.argument("user_id")
@click.argument("role")
def users_grant(endpoint: str | None, token: str | None, fmt: str,
                profile: str | None, user_id: str, role: str) -> None:
    """Grant a role (name or id) to a user."""
    try:
        client = _get_client(endpoint, token, profile)
        r = _resolve_role(client, role)
        raw = client.assign_user_role(user_id, r["id"])
        emit(raw if fmt == "json" else {"user": user_id, "role": r.get("name", ""),
                                        "assignment": data_of(raw).get("id", "")}, fmt)
    except Exception as e:
        _handle_error(e)


@users.command("revoke")
@_common_options
@click.argument("user_id")
@click.argument("role")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def users_revoke(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, user_id: str, role: str, yes: bool) -> None:
    """Remove a role (name or id) from a user."""
    try:
        client = _get_client(endpoint, token, profile)
        r = _resolve_role(client, role)
        user = data_of(client.get_user(user_id))
        assignments = [a for a in (user.get("userRoles") or [])
                       if isinstance(a, dict) and a.get("roleId") == r["id"]]
        if not assignments:
            raise click.UsageError(f"User {user_id} does not have role '{r.get('name')}'.")
        if not yes:
            _confirm(f"Remove role '{r.get('name')}' from user {user_id}?")
        for a in assignments:
            client.delete_user_role(a["id"])
        emit({"user": user_id, "role": r.get("name", ""), "removed": True}, fmt)
    except Exception as e:
        _handle_error(e)


@users.command("reset-link")
@_common_options
@click.argument("user_id")
def users_reset_link(endpoint: str | None, token: str | None, fmt: str,
                     profile: str | None, user_id: str) -> None:
    """Print a password-reset (or invite) link for a user.

    The server reuses the current token until it expires and only then
    issues a new one. The link assumes the SigNoz UI is served at the
    configured endpoint.
    """
    try:
        client = _get_client(endpoint, token, profile)
        tok = data_of(client.create_reset_password_token(user_id))
        link = f"{client.endpoint}/password-reset?token={tok.get('token', '')}"
        emit({"user": user_id, "link": link, "expiresAt": tok.get("expiresAt", "")}, fmt)
    except Exception as e:
        _handle_error(e)


@cli.group("roles")
def roles() -> None:
    """Manage roles (requires admin; custom roles need an enterprise license)."""


add_crud(
    roles,
    resource="role",
    list_method="list_roles",
    get_method="get_role",
    create_method="create_role",
    update_method="update_role",
    delete_method="delete_role",
    columns=["id", "name", "type", "description"],
    create_example='{"name": "dashboards-only", "description": "read dashboards"}',
)


@roles.command("users")
@_common_options
@click.argument("role")
def roles_users(endpoint: str | None, token: str | None, fmt: str,
                profile: str | None, role: str) -> None:
    """List users who have a role (name or id)."""
    try:
        client = _get_client(endpoint, token, profile)
        r = _resolve_role(client, role)
        raw = client.list_role_users(r["id"])
        if fmt == "json":
            emit(raw, fmt)
            return
        emit({"data": [_user_row(u) for u in items_of(raw) if isinstance(u, dict)]}, fmt)
    except Exception as e:
        _handle_error(e)
