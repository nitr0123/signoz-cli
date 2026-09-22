"""Config file support for SigNoz CLI (~/.signoz/config.json).

Supports named connection profiles for working with multiple SigNoz
instances::

    {
      "default": "prod",
      "profiles": {
        "prod":  {"endpoint": "https://signoz.example.com", "token": "..."},
        "local": {"endpoint": "http://localhost:8080", "token": "..."}
      }
    }

Legacy flat configs ({"endpoint": ..., "token": ...}) are still readable and
act as an implicit default profile.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.getenv("SIGNOZ_CONFIG_DIR", str(Path.home() / ".signoz")))
CONFIG_FILE = CONFIG_DIR / "config.json"

_CONFIG_WARNED = False


def load_config() -> dict[str, Any]:
    """Load config from ~/.signoz/config.json, or return empty dict.

    A present-but-unreadable config warns once per process (it would otherwise
    resurface downstream as a confusing "Missing endpoint"), while a missing
    file stays silent — that is the normal first-run case.
    """
    global _CONFIG_WARNED
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        if not _CONFIG_WARNED:
            print(f"Warning: could not read config {CONFIG_FILE}: {exc}", file=sys.stderr)
            _CONFIG_WARNED = True
        return {}


def _sweep_stale_tmps(max_age_s: float = 3600.0) -> None:
    """Remove abandoned save temp files from crashed runs.

    Only files matching our pid-suffixed pattern and older than
    ``max_age_s`` are touched, so a concurrently-writing process
    (whose file is seconds old) is never disturbed. Best-effort.
    """
    import time as _time

    try:
        now = _time.time()
        for path in CONFIG_DIR.glob(".config.json.*.tmp"):
            try:
                if now - path.stat().st_mtime > max_age_s:
                    path.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _ensure_config_dir() -> None:
    """Create the config dir owner-only (0700), tightening existing dirs.

    The directory holds credentials, history and saved queries; a
    world- or group-accessible dir leaks filenames and eases local
    tampering (e.g. planting symlinks for the writers below).
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if CONFIG_DIR.stat().st_mode & 0o077:
            CONFIG_DIR.chmod(0o700)
    except OSError:
        pass


def _write_private(path: Path, text: str) -> None:
    """Write *text* to *path*, creating the file with mode 0600 from birth.

    Plain ``Path.write_text`` creates the file under the process umask
    (typically 0644) and a later ``chmod`` only narrows it afterwards --
    leaving a window where another local user can read secrets (tokens)
    from the new file. ``os.open`` with an explicit mode never grants
    more than 0600 regardless of umask.

    Symlinks are refused outright (``O_NOFOLLOW`` plus an explicit
    check): a symlink planted at a secret path must never redirect the
    write onto some other file the user owns.
    """
    if os.path.islink(path):
        raise OSError(f"refusing to write through symlink: {path}")
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def save_config(data: dict[str, Any]) -> None:
    """Save config to ~/.signoz/config.json (atomically, mode 0600).

    Writes to a temp file in the same directory then renames, so a
    crash mid-write can never leave a truncated config behind.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_config_dir()
    _sweep_stale_tmps()
    tmp_path = CONFIG_DIR / f".config.json.{os.getpid()}.tmp"
    try:
        _write_private(tmp_path, json.dumps(data, indent=2) + "\n")
        tmp_path.chmod(0o600)
        os.replace(tmp_path, CONFIG_FILE)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    CONFIG_FILE.chmod(0o600)


def get_profiles(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the profiles mapping from a loaded config (possibly empty).

    Entries that are not objects (hand-edited or corrupt configs, e.g.
    ``"profiles": {"p": "oops"}``) are dropped: every consumer calls
    ``.get`` on the values, so a stray string would otherwise surface as
    an unhandled AttributeError traceback instead of a clean error.
    """
    profiles = cfg.get("profiles")
    if not isinstance(profiles, dict):
        return {}
    return {k: v for k, v in profiles.items() if isinstance(v, dict)}


def select_profile_name(
    cfg: dict[str, Any], requested: str | None = None, env_var_value: str | None = None
) -> str | None:
    """Pick a profile name: requested > env var > config default."""
    return requested or env_var_value or cfg.get("default")


def get_profile_credentials(
    cfg: dict[str, Any], profile_name: str | None
) -> tuple[str | None, str | None]:
    """Return (endpoint, token) stored under a profile, else legacy flat keys."""
    prof = get_profiles(cfg).get(profile_name or "", {})
    endpoint = prof.get("endpoint") or cfg.get("endpoint")
    token = prof.get("token") or cfg.get("token")
    return endpoint, token


def get_profile_option(
    cfg: dict[str, Any], profile_name: str | None, key: str, default: Any = None
) -> Any:
    """Return a per-profile option (e.g. timeout, insecure, default_time_range, default_format)."""
    prof = get_profiles(cfg).get(profile_name or "", {})
    return prof.get(key, default)


def set_profile_option(
    data: dict[str, Any], profile_name: str, key: str, value: Any
) -> None:
    """Set a per-profile option value."""
    profiles = data.setdefault("profiles", {})
    profiles.setdefault(profile_name, {})[key] = value
