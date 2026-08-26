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


def load_config() -> dict[str, Any]:
    """Load config from ~/.signoz/config.json, or return empty dict."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        # A present-but-unreadable config silently resolving to "no config"
        # produces a confusing "Missing endpoint" downstream; surface why.
        print(f"Warning: could not read config {CONFIG_FILE}: {exc}", file=sys.stderr)
        return {}


def save_config(data: dict[str, Any]) -> None:
    """Save config to ~/.signoz/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n")
    CONFIG_FILE.chmod(0o600)


def get_profiles(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the profiles mapping from a loaded config (possibly empty)."""
    profiles = cfg.get("profiles")
    return profiles if isinstance(profiles, dict) else {}


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
