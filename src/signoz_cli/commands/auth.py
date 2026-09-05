"""configure / profile / auth commands."""

from __future__ import annotations

import os

import click

from signoz_cli.commands.common import (
    SUPPORTED_SIGNOZ_RANGE,
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    _is_supported_server,
    _mask_token,
    _resolve_credentials,
    _set_json_flag,
    cli,
)
from signoz_cli.config import (
    CONFIG_FILE,
    get_profiles,
    load_config,
    save_config,
    select_profile_name,
)
from signoz_cli.output import emit
from signoz_cli.timeutil import parse_time_range


@cli.command()
@click.option("--profile", "-p", "profile_name", default="default", show_default=True,
              help="Profile name to save credentials under.")
@click.option("--endpoint", prompt="SigNoz endpoint URL", help="SigNoz instance URL.")
@click.option(
    "--token", prompt="SigNoz API token", hide_input=True, help="SigNoz API key."
)
@click.option("--set-default/--no-set-default", default=None,
              help="Make this profile the default (default: yes for the first profile).")
@click.option("--default-time-range", default=None,
              help="Default time range for queries on this profile (e.g. 24h, 7d).")
@click.option("--default-format", default=None,
              type=click.Choice(["json", "table", "csv"]),
              help="Default output format for this profile (table/json/csv).")
def configure(
    profile_name: str,
    endpoint: str,
    token: str,
    set_default: bool | None,
    default_time_range: str | None,
    default_format: str | None,
) -> None:
    """Save SigNoz credentials as a named profile in ~/.signoz/config.json.

    Store one profile per SigNoz instance, e.g.:

      signoz configure --profile local --endpoint http://localhost:8080

      signoz configure --profile prod --endpoint https://signoz.example.com
    """
    cfg = load_config()
    profiles = get_profiles(cfg)
    profiles[profile_name] = {
        "endpoint": endpoint.rstrip("/"),
        "token": token,
    }
    if default_time_range:
        # Fail fast: a typo here would otherwise break every later query.
        try:
            parse_time_range(default_time_range)
        except ValueError:
            raise click.UsageError(
                f"Invalid --default-time-range '{default_time_range}'. "
                "Use formats like '30m', '24h', '7d'."
            ) from None
        profiles[profile_name]["default_time_range"] = default_time_range
    if default_format:
        profiles[profile_name]["default_format"] = default_format
    cfg["profiles"] = profiles
    if set_default is True or (
        set_default is None and cfg.get("default") is None
    ):
        cfg["default"] = profile_name
    save_config(cfg)
    click.echo(f"Profile '{profile_name}' saved to {CONFIG_FILE} "
               f"(endpoint: {profiles[profile_name]['endpoint']})")
    click.echo(f"Use it with: signoz -p {profile_name} <command>  "
               f"or export SIGNOZ_PROFILE={profile_name}")


@cli.group("profile")
def profile() -> None:
    """Manage named connection profiles (one per SigNoz instance)."""


@profile.command("list")
@click.option("-f", "--format", "fmt", type=click.Choice(["json", "table"]),
              default="table", show_default=True, help="Output format.")
@click.option("--json", is_flag=True, expose_value=False, is_eager=True,
              callback=_set_json_flag, help="Shorthand for -f json.")
@click.option("--csv", "csv_flag", is_flag=True, expose_value=False, is_eager=True,
              callback=lambda ctx, param, value: ctx.params.update({"fmt": "csv"}) if value else None,
              help="Shorthand for -f csv.")
def profile_list(fmt: str) -> None:
    """List configured profiles."""
    cfg = load_config()
    profiles = get_profiles(cfg)
    # legacy flat config acts as an implicit profile
    rows = []
    for name in sorted(profiles):
        p = profiles[name]
        rows.append({
            "name": name,
            "endpoint": p.get("endpoint", ""),
            "token": _mask_token(p.get("token")),
            "default": "*" if name == cfg.get("default") else "",
            "default_time_range": p.get("default_time_range", ""),
            "default_format": p.get("default_format", ""),
        })
    if not rows and cfg.get("endpoint"):
        rows.append({
            "name": "(legacy)",
            "endpoint": cfg["endpoint"],
            "token": _mask_token(cfg.get("token")),
            "default": "*",
            "default_time_range": "",
            "default_format": "",
        })
    emit({"data": rows}, fmt)


@profile.command("use")
@click.argument("name")
def profile_use(name: str) -> None:
    """Set the default profile used when none is given."""
    cfg = load_config()
    if name not in get_profiles(cfg):
        raise click.UsageError(f"Unknown profile '{name}'. See 'signoz profile list'.")
    cfg["default"] = name
    save_config(cfg)
    click.echo(f"Default profile set to '{name}'.")


@profile.command("show")
@click.argument("name")
def profile_show(name: str) -> None:
    """Show details of a single profile (token masked)."""
    cfg = load_config()
    prof = get_profiles(cfg).get(name)
    if prof is None:
        raise click.UsageError(f"Unknown profile '{name}'. See 'signoz profile list'.")
    emit({
        "name": name,
        "endpoint": prof.get("endpoint", ""),
        "token": _mask_token(prof.get("token")),
        "default": name == cfg.get("default"),
        "default_time_range": prof.get("default_time_range", ""),
        "default_format": prof.get("default_format", ""),
    }, "json")


@profile.command("remove")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def profile_remove(name: str, yes: bool) -> None:
    """Remove a profile from the config file."""
    cfg = load_config()
    profiles = get_profiles(cfg)
    if name not in profiles:
        raise click.UsageError(f"Unknown profile '{name}'. See 'signoz profile list'.")
    if not yes:
        _confirm(f"Remove profile '{name}'?")
    del profiles[name]
    cfg["profiles"] = profiles
    if cfg.get("default") == name:
        cfg["default"] = next(iter(profiles), None)
    save_config(cfg)
    click.echo(f"Profile '{name}' removed.")


@cli.group("auth")
def auth() -> None:
    """Authentication helpers."""


@auth.command("whoami")
@click.option("--endpoint", default=None, help="SigNoz instance URL.")
@click.option("--token", default=None, help="SigNoz API key.")
@click.option("-p", "--profile", "profile_name", default=None,
              help="Named connection profile to inspect.")
def auth_whoami(endpoint: str | None, token: str | None, profile_name: str | None) -> None:
    """Show effective configuration (endpoint, token masked).

    Resolution order: flags > env vars > selected profile > legacy config keys.
    """
    ep, tok = _resolve_credentials(endpoint, token, profile_name)
    cfg = load_config()
    selected = select_profile_name(cfg, profile_name, os.getenv("SIGNOZ_PROFILE"))
    click.echo(f"Endpoint: {ep or '<not set>'}")
    click.echo(f"Token:    {_mask_token(tok)}")
    click.echo(f"Profile:  {selected or '<none>'}")
    click.echo(f"Config:   {CONFIG_FILE}")


@auth.command("verify")
@_common_options
def auth_verify(endpoint: str | None, token: str | None, fmt: str,
    profile: str | None,) -> None:
    """Verify the API key against the live instance and report version compatibility."""
    try:
        client = _get_client(endpoint, token, profile)
        data = client.verify()
        try:
            server_version = (client.get_server_version() or {}).get("version", "")
        except Exception:
            server_version = ""
        result = {
            "service_account": data,
            "server_version": server_version or "<unknown>",
            "supported_range": SUPPORTED_SIGNOZ_RANGE,
            "compatible": _is_supported_server(server_version),
        }
        emit(result, fmt)
        if not result["compatible"]:
            click.echo(
                f"Warning: server version '{result['server_version']}' is outside "
                f"the tested range ({SUPPORTED_SIGNOZ_RANGE}). "
                "Commands may still work but are unverified.",
                err=True,
            )
    except Exception as e:
        _handle_error(e)


