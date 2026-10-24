"""cloud integration commands (AWS, Azure, GCP accounts and services)."""

from __future__ import annotations

from typing import Any

import click

from signoz_cli.commands._crud import data_of, items_of
from signoz_cli.commands.common import (
    _common_options,
    _confirm,
    _get_client,
    _handle_error,
    _read_json,
    cli,
)
from signoz_cli.output import emit

_PROVIDERS = ["aws", "azure", "gcp"]
_provider_arg = click.argument("provider", type=click.Choice(_PROVIDERS))


@cli.group("cloud")
def cloud() -> None:
    """Connect cloud accounts and choose which services send logs and metrics.

    Needs SigNoz Cloud or an enterprise license; unlicensed servers
    answer HTTP 451.
    """


@cloud.group("accounts")
def accounts() -> None:
    """Connected cloud accounts."""


def _account_row(a: dict[str, Any]) -> dict[str, Any]:
    report = a.get("agentReport")
    sync = report.get("syncState") if isinstance(report, dict) else None
    return {"id": a.get("id", ""), "provider": a.get("provider", ""),
            "providerAccountId": a.get("providerAccountId") or "(not connected yet)",
            "inSync": sync.get("inSync", "") if isinstance(sync, dict) else "",
            "config": a.get("config", ""), "removedAt": a.get("removedAt") or ""}


def _account_config(provider: str, deployment_region: str | None, regions: tuple[str, ...],
                    resource_groups: tuple[str, ...], project_ids: tuple[str, ...],
                    deployment_project: str | None, config_file: str | None,
                    creating: bool) -> dict[str, Any]:
    """Build {provider: {...}} from flags, or read it from --config."""
    if config_file:
        body = _read_json(config_file)
        if not isinstance(body, dict):
            raise click.UsageError("--config must hold a JSON object.")
        return body if provider in body else {provider: body}
    cfg: dict[str, Any] = {}
    if provider == "aws":
        if not regions:
            raise click.UsageError("AWS needs at least one --region to collect from.")
        cfg["regions"] = list(regions)
    elif provider == "azure":
        if not resource_groups:
            raise click.UsageError("Azure needs at least one --resource-group.")
        cfg["resourceGroups"] = list(resource_groups)
    else:
        if not project_ids or not deployment_project:
            raise click.UsageError("GCP needs --project-id and --deployment-project.")
        cfg["projectIds"] = list(project_ids)
        cfg["deploymentProjectId"] = deployment_project
    if provider == "gcp" or creating:
        if not deployment_region:
            raise click.UsageError("--deployment-region is required.")
        cfg["deploymentRegion"] = deployment_region
    return {provider: cfg}


def _account_options(f):
    f = click.option("--config", "config_file", type=click.Path(), default=None,
                     help="Read the provider config from a JSON file instead of flags.")(f)
    f = click.option("--deployment-project", default=None,
                     help="GCP: project that hosts the collection resources.")(f)
    f = click.option("--project-id", "project_ids", multiple=True,
                     help="GCP: project to collect from (repeatable).")(f)
    f = click.option("--resource-group", "resource_groups", multiple=True,
                     help="Azure: resource group to collect from (repeatable).")(f)
    f = click.option("--region", "regions", multiple=True,
                     help="AWS: region to collect from (repeatable).")(f)
    f = click.option("--deployment-region", default=None,
                     help="Region where the collection stack is deployed.")(f)
    return f


@accounts.command("list")
@_common_options
@_provider_arg
def accounts_list(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, provider: str) -> None:
    """List connected accounts for a provider."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_cloud_accounts(provider)
        if fmt == "json":
            emit(raw, fmt)
            return
        emit({"data": [_account_row(a) for a in items_of(raw, "accounts")
                       if isinstance(a, dict)]}, fmt)
    except Exception as e:
        _handle_error(e)


@accounts.command("get")
@_common_options
@_provider_arg
@click.argument("account_id")
def accounts_get(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, provider: str, account_id: str) -> None:
    """Get a connected account, including the collection agent's last report."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_cloud_account(provider, account_id)
        emit(raw if fmt == "json" else _account_row(data_of(raw)), fmt)
    except Exception as e:
        _handle_error(e)


@accounts.command("connect")
@_common_options
@_provider_arg
@_account_options
def accounts_connect(endpoint: str | None, token: str | None, fmt: str,
                     profile: str | None, provider: str, deployment_region: str | None,
                     regions: tuple[str, ...], resource_groups: tuple[str, ...],
                     project_ids: tuple[str, ...], deployment_project: str | None,
                     config_file: str | None) -> None:
    """Start connecting a cloud account; prints the link or command that
    finishes the setup on the provider side.

    \b
    Examples:
      signoz cloud accounts connect aws --deployment-region us-east-1 \\
          --region us-east-1 --region eu-west-1
      signoz cloud accounts connect azure --deployment-region eastus \\
          --resource-group prod-rg
    """
    try:
        config = _account_config(provider, deployment_region, regions, resource_groups,
                                 project_ids, deployment_project, config_file, creating=True)
        client = _get_client(endpoint, token, profile)
        # The server requires the SigNoz URLs/keys the provider-side stack
        # will use; it issues them itself via the credentials endpoint.
        credentials = data_of(client.get_cloud_credentials(provider))
        raw = client.create_cloud_account(provider, config, credentials)
        if fmt == "json":
            emit(raw, fmt)
            return
        data = data_of(raw)
        row: dict[str, Any] = {"id": data.get("id", "")}
        artifact = data.get("connectionArtifact") or {}
        for key, value in (artifact.get(provider) or {}).items():
            row[key] = value
        emit(row, fmt)
        click.echo("Finish the connection on the provider side with the link/command "
                   "above; `cloud accounts get` shows when the agent reports in.", err=True)
    except Exception as e:
        _handle_error(e)


@accounts.command("update")
@_common_options
@_provider_arg
@click.argument("account_id")
@_account_options
def accounts_update(endpoint: str | None, token: str | None, fmt: str,
                    profile: str | None, provider: str, account_id: str,
                    deployment_region: str | None, regions: tuple[str, ...],
                    resource_groups: tuple[str, ...], project_ids: tuple[str, ...],
                    deployment_project: str | None, config_file: str | None) -> None:
    """Change which regions / resource groups / projects an account collects from."""
    try:
        config = _account_config(provider, deployment_region, regions, resource_groups,
                                 project_ids, deployment_project, config_file, creating=False)
        client = _get_client(endpoint, token, profile)
        client.update_cloud_account(provider, account_id, config)
        emit({"id": account_id, "config": config}, fmt)
    except Exception as e:
        _handle_error(e)


@accounts.command("disconnect")
@_common_options
@_provider_arg
@click.argument("account_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def accounts_disconnect(endpoint: str | None, token: str | None, fmt: str,
                        profile: str | None, provider: str, account_id: str,
                        yes: bool) -> None:
    """Disconnect an account (stops collection; remove the provider-side stack yourself)."""
    try:
        if not yes:
            _confirm(f"Disconnect {provider} account {account_id}?")
        client = _get_client(endpoint, token, profile)
        emit(client.delete_cloud_account(provider, account_id), fmt)
    except Exception as e:
        _handle_error(e)


@cloud.group("services")
def services() -> None:
    """Cloud services SigNoz can collect from (EC2, RDS, Lambda, ...)."""


@services.command("list")
@_common_options
@_provider_arg
@click.option("--account", "account_id", default="",
              help="Show enabled state for this connected account.")
def services_list(endpoint: str | None, token: str | None, fmt: str,
                  profile: str | None, provider: str, account_id: str) -> None:
    """List supported services (and which are enabled for --account)."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.list_cloud_services(provider, account_id)
        if fmt == "json":
            emit(raw, fmt)
            return
        rows = [{"id": s.get("id", ""), "title": s.get("title", ""),
                 "enabled": s.get("enabled", False)}
                for s in items_of(raw, "services") if isinstance(s, dict)]
        emit({"data": rows}, fmt)
    except Exception as e:
        _handle_error(e)


@services.command("get")
@_common_options
@_provider_arg
@click.argument("service_id")
@click.option("--account", "account_id", default="",
              help="Include this account's config for the service.")
def services_get(endpoint: str | None, token: str | None, fmt: str,
                 profile: str | None, provider: str, service_id: str,
                 account_id: str) -> None:
    """Show a service: what it collects and its bundled dashboards."""
    try:
        client = _get_client(endpoint, token, profile)
        raw = client.get_cloud_service(provider, service_id, account_id)
        emit(raw if fmt in ("json", "yaml") else data_of(raw), fmt)
    except Exception as e:
        _handle_error(e)


@services.command("configure")
@_common_options
@_provider_arg
@click.argument("account_id")
@click.argument("service_id")
@click.option("--logs/--no-logs", default=None, help="Collect logs from the service.")
@click.option("--metrics/--no-metrics", default=None, help="Collect metrics from the service.")
@click.option("--config", "config_file", type=click.Path(), default=None,
              help="Full service config JSON (e.g. AWS S3 bucket log sources).")
def services_configure(endpoint: str | None, token: str | None, fmt: str,
                       profile: str | None, provider: str, account_id: str,
                       service_id: str, logs: bool | None, metrics: bool | None,
                       config_file: str | None) -> None:
    """Turn log and metric collection on or off for one service of an account.

    \b
    Example:
      signoz cloud services configure aws <account-id> rds --logs --metrics
    """
    try:
        if not config_file and logs is None and metrics is None:
            raise click.UsageError("Pass --logs/--no-logs and/or --metrics/--no-metrics.")
        client = _get_client(endpoint, token, profile)
        if config_file:
            body = _read_json(config_file)
            if not isinstance(body, dict):
                raise click.UsageError("--config must hold a JSON object.")
            config = body if provider in body else {provider: body}
        else:
            # Start from the current config so unrelated settings (e.g. AWS
            # S3 log buckets) survive a logs/metrics toggle.
            current = data_of(client.get_cloud_service(provider, service_id, account_id))
            service = current.get("cloudIntegrationService") if isinstance(current, dict) else None
            existing = ((service or {}).get("config") or {}).get(provider) or {}
            cfg = {k: dict(v) for k, v in existing.items() if isinstance(v, dict)}
            # GCP and Azure require both blocks; default the unset one to off.
            for key, value in (("logs", logs), ("metrics", metrics)):
                block = cfg.setdefault(key, {"enabled": False})
                if value is not None:
                    block["enabled"] = value
            config = {provider: cfg}
        client.update_cloud_service(provider, account_id, service_id, config)
        emit({"service": service_id, "account": account_id, "config": config}, fmt)
    except Exception as e:
        _handle_error(e)


@cloud.command("credentials")
@_common_options
@_provider_arg
@click.option("--show-secrets", is_flag=True, help="Print the keys unmasked.")
def cloud_credentials(endpoint: str | None, token: str | None, fmt: str,
                      profile: str | None, provider: str, show_secrets: bool) -> None:
    """Show the SigNoz URLs and keys the provider-side stack needs."""
    try:
        client = _get_client(endpoint, token, profile)
        data = dict(data_of(client.get_cloud_credentials(provider)) or {})
        if not show_secrets:
            for key in ("sigNozApiKey", "ingestionKey"):
                value = str(data.get(key) or "")
                if value:
                    data[key] = f"{value[:4]}... (--show-secrets to print)"
        emit(data, fmt)
    except Exception as e:
        _handle_error(e)
