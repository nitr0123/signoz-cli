"""update command: check for and install newer releases."""

from __future__ import annotations

import os
import sys
from typing import Any

import click

from signoz_cli import __version__
from signoz_cli.commands.common import (
    UPDATE_REPO,
    _handle_error,
    _latest_remote_tag,
    _parse_version,
    _redact_userinfo,
    cli,
)


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
        main = meta.get("main_package", {})
        spec = main.get("package_or_url", "") if isinstance(main, dict) else ""
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(spec, str) or not spec:
        return None
    if "://" in spec:
        # rsplit: credentials (user:pass@) may themselves contain "@".
        base = spec.rsplit("@", 1)[0]
        return ["pipx", "install", "--force", f"{base}@{{tag}}"]
    return ["pipx", "install", "--force", f"{spec}=={{tag}}"]


def _describe_upgrade_argv(argv: list[str]) -> str:
    """Render an upgrade command for display with credentials redacted.

    The executed argv may carry registry credentials (pipx git URLs,
    index URLs); the human-readable description must never echo them
    (shoulder surfing, scrollback, tee'd logs).
    """
    return " ".join(_redact_userinfo(c) for c in argv)


def _pypi_index_url() -> str | None:
    """Registry index URL, or None when the user hasn't configured one."""
    return os.getenv("SIGNOZ_PYPI_INDEX") or None


def _detect_install_method() -> tuple[str, Any, str]:
    """Detect how this copy was installed.

    Returns (method, upgrade_or_None, instructions). ``upgrade`` is
    either an argv list, or an ``(env, argv)`` tuple when the upgrade
    needs extra environment (registry credentials, which must never
    travel via ps-visible argv). It is None when upgrading must be
    done by hand (source checkouts), or when credentials are missing
    (index installs without SIGNOZ_PYPI_INDEX).
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
            return ("pipx", cmd, _describe_upgrade_argv(cmd).replace("{tag}", "<tag>"))
        cmd = ["pipx", "install", "--force", f"git+{repo}@{{tag}}"]
        return ("pipx", cmd, _describe_upgrade_argv(cmd).replace("{tag}", "<tag>"))
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
            base = url.rsplit("@", 1)[0]
            cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
                   "--force-reinstall", f"{base}@{{tag}}"]
            # base may embed registry credentials (git+https://user:pass@);
            # the executed argv keeps them (pip needs them) but the
            # terminal description must not echo them.
            return ("pip", cmd, _describe_upgrade_argv(cmd).replace("{tag}", "<tag>"))
    elif have_dist:
        # Regular index install (no direct_url.json): upgrade from the
        # registry. Credentials travel via SIGNOZ_PYPI_INDEX.
        index = _pypi_index_url()
        if index is None:
            return (
                "pip",
                None,
                "set SIGNOZ_PYPI_INDEX to your registry index URL, then "
                "re-run with --apply (or reinstall from git: "
                f"pipx install --force git+{UPDATE_REPO}@<tag>)",
            )
        # NOTE: the index URL (which embeds registry credentials) is
        # passed via the environment, never via argv: argv is visible to
        # other users through `ps`, and the upgrade description is
        # echoed to the terminal. Upgrade specs carrying an env mapping
        # are (env, argv) tuples; plain argv lists need no extra env.
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
               "signoz-cli=={tag}"]
        env = {"PIP_INDEX_URL": index,
               "PIP_EXTRA_INDEX_URL": "https://pypi.org/simple"}
        shown = _describe_upgrade_argv(cmd).replace("{tag}", "<tag>")
        return ("pip", (env, cmd), shown)
    return (
        "unknown",
        None,
        "manual upgrade required (see README Installation)",
    )


@cli.command("update")
@click.option("--apply", is_flag=True,
              help="Actually perform the upgrade (otherwise just report).")
def update_cmd(apply: bool) -> None:
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
                       "(git/network unavailable?). Try again later.")
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

        extra_env: dict[str, str] = {}
        if (isinstance(upgrade_argv, tuple)
                and len(upgrade_argv) == 2
                and isinstance(upgrade_argv[0], dict)):
            # (env, argv) spec: credentials via environment, not argv.
            extra_env, upgrade_argv = upgrade_argv
        cmd = [c.replace("{tag}", latest) for c in upgrade_argv]
        run_env = dict(os.environ)
        run_env.update(extra_env)
        try:
            proc = subprocess.run(cmd, timeout=300, env=run_env)
        except OSError as ex:
            raise click.UsageError(
                f"Cannot run the upgrade tool ({cmd[0] if cmd else '?'}): "
                f"{ex}. Reinstall it or upgrade manually."
            ) from None
        if proc.returncode != 0:
            raise click.UsageError(
                f"Upgrade command failed with exit {proc.returncode}."
            )
        click.echo(f"Upgraded to {latest}.")
    except Exception as e:
        _handle_error(e)
