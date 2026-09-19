"""update command: check for and install newer releases."""

from __future__ import annotations

import os
import sys

import click

from signoz_cli import __version__
from signoz_cli.commands.common import (
    UPDATE_REPO,
    _handle_error,
    _latest_remote_tag,
    _parse_version,
    cli,
)

DEFAULT_PYPI_INDEX = "https://git.example.com/api/packages/hamza/pypi/simple"


def _pipx_upgrade_command(meta_path: str, repo: str) -> list[str] | None:
    """Build a source-faithful pipx reinstall command, or None to fall back.

    Reads ``main_package.package_or_url`` from pipx metadata: git URLs are
    re-pinned at the new tag, bare names become pinned index installs.
    Anything unparseable returns None (caller falls back to git reinstall).
    """
    import json as _json

    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = _json.load(fh)
        spec = meta.get("main_package", {}).get("package_or_url", "")
    except (OSError, ValueError):
        return None
    if not isinstance(spec, str) or not spec:
        return None
    if "://" in spec:
        base = spec.split("@")[0]
        return ["pipx", "install", "--force", f"{base}@{{tag}}"]
    return ["pipx", "install", "--force", f"{spec}=={{tag}}"]


def _pypi_index_url() -> str | None:
    """Registry index URL, or None when the user hasn't configured one."""
    return os.getenv("SIGNOZ_PYPI_INDEX") or None


def _detect_install_method() -> tuple[str, list[str] | None, str]:
    """Detect how this copy was installed.

    Returns (method, upgrade_argv_or_None, instructions). ``upgrade_argv``
    is None when upgrading must be done by hand (source checkouts), or
    when credentials are missing (index installs without SIGNOZ_PYPI_INDEX).
    """
    import importlib.metadata
    from pathlib import Path

    repo = os.getenv("SIGNOZ_UPDATE_REPO", UPDATE_REPO)
    # pipx venvs carry their metadata at the venv root, including the
    # original install source: reinstall from the SAME source so an
    # index install is never silently switched to git+SSH (or vice versa).
    meta_path = os.path.join(sys.prefix, "pipx_metadata.json")
    if os.path.exists(meta_path):
        cmd = _pipx_upgrade_command(meta_path, repo)
        if cmd is not None:
            return ("pipx", cmd, " ".join(cmd).replace("{tag}", "<tag>"))
        cmd = ["pipx", "install", "--force", f"git+{repo}@{{tag}}"]
        return ("pipx", cmd, " ".join(cmd).replace("{tag}", "<tag>"))
    try:
        dist = importlib.metadata.distribution("signoz-cli")
        direct_url = dist.read_text("direct_url.json")
        have_dist = True
    except Exception:
        direct_url = None
        have_dist = False
    if direct_url:
        import json as _json

        try:
            info = _json.loads(direct_url)
        except ValueError:
            info = {}
        if isinstance(info, dict) and info.get("dir_info", {}).get("editable"):
            root = Path(__file__).resolve().parent.parent.parent
            if (root / ".git").is_dir():
                return (
                    "editable",
                    ["git", "-C", str(root), "pull"],
                    f"cd {root} && git pull",
                )
        url = info.get("url", "") if isinstance(info, dict) else ""
        if url.startswith("git+"):
            base = url.split("@")[0]
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
                   "--force-reinstall", f"{base}@{{tag}}"]
            return ("pip", cmd,
                    f"{sys.executable} -m pip install --upgrade "
                    f"--force-reinstall \"{base}@<tag>\"")
    elif have_dist:
        # Regular index install (no direct_url.json): upgrade from the
        # registry. Credentials travel via SIGNOZ_PYPI_INDEX.
        index = _pypi_index_url()
        if index is None:
            return (
                "pip",
                None,
                "set SIGNOZ_PYPI_INDEX to your registry index URL "
                f"(e.g. {DEFAULT_PYPI_INDEX} with credentials), then "
                "re-run with --apply",
            )
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
               "--index-url", index,
               "--extra-index-url", "https://pypi.org/simple",
               "signoz-cli=={tag}"]
        shown = " ".join(cmd).replace("{tag}", "<tag>")
        return ("pip", cmd, shown)
    return (
        "unknown",
        None,
        f"curl -fsSL {repo}/install.sh ... "
        "(see README Installation; manual upgrade required)",
    )


@cli.command("update")
@click.option("--apply", is_flag=True,
              help="Actually perform the upgrade (otherwise just report).")
@click.option("--check", "check_only", is_flag=True,
              help="Only check; same as the default report without hints.")
def update_cmd(apply: bool, check_only: bool) -> None:
    """Check for a newer release and show (or apply) the upgrade.

    \b
    Examples:
      signoz update
      signoz update --apply
    """
    try:
        repo = os.getenv("SIGNOZ_UPDATE_REPO", UPDATE_REPO)
        latest = _latest_remote_tag(repo)
        current = _parse_version(__version__)
        if latest is None:
            click.echo("Could not determine the latest release "
                       "(git/SSH unavailable?). Try again later.")
            return
        click.echo(f"Installed: v{__version__}")
        click.echo(f"Latest:    {latest}")
        if not current or _parse_version(latest) <= current:
            click.echo("Already up to date.")
            return
        method, upgrade_argv, instructions = _detect_install_method()
        resolved = instructions.replace("<tag>", latest)
        click.echo(f"Update available via {method}:")
        click.echo(f"  {resolved}")
        if not apply:
            click.echo("Re-run with --apply to perform the upgrade.")
            return
        if upgrade_argv is None:
            raise click.UsageError(
                "Automatic upgrade is not supported for this install "
                "method; follow the instructions above."
            )
        import subprocess

        cmd = [c.replace("{tag}", latest) for c in upgrade_argv]
        proc = subprocess.run(cmd, timeout=300)
        if proc.returncode != 0:
            raise click.UsageError(
                f"Upgrade command failed with exit {proc.returncode}."
            )
        click.echo(f"Upgraded to {latest}.")
    except Exception as e:
        _handle_error(e)
