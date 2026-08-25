"""Config file support for SigNoz CLI (~/.signoz/config.json)."""

from __future__ import annotations

import json
import os
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
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(data: dict[str, Any]) -> None:
    """Save config to ~/.signoz/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n")
    CONFIG_FILE.chmod(0o600)
