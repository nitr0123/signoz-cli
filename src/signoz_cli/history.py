"""Local query-history store: record, read, and secret-guarding helpers.

Lives here (instead of inside `commands/common.py`) because history is a
self-contained concern: a capped JSON log next to the config plus the
never-store-secrets guards. `common.py` re-exports these names so existing
imports keep working.
"""

from __future__ import annotations

import json
from typing import Any

from signoz_cli import config as _config

_QUERY_HISTORY_COMMANDS = {"logs", "traces", "metrics", "query", "promql", "sql"}
_QUERY_HISTORY_LIMIT = 100


def _has_url_userinfo(text: str) -> bool:
    """Whether *text* looks like a URL embedding credentials (user:pass@host).

    Covers both full URLs (``https://bob:secret@host``) and bare
    ``bob:secret@host:port`` forms. Used to keep secrets out of history
    and error output.
    """
    if "@" not in text or " " in text:
        return False
    try:
        from urllib.parse import urlsplit as _urlsplit

        if "://" in text:
            # Full URL: even a bare username is credential material
            # (https://bob@host performs basic auth as bob).
            parts = _urlsplit(text)
            return bool(parts.username or parts.password)
        # Scheme-less text: only user:PASS@host counts. A bare
        # user@host (e.g. an email inside a PromQL/SQL filter) is not
        # a secret and must not suppress history recording.
        parts = _urlsplit(f"//{text}")
        return bool(parts.password)
    except ValueError:
        return False


def _history_path():
    """History file lives next to the config.

    Resolved through the module attribute (never a by-value import) so
    runtime or test patching of ``config.CONFIG_DIR`` takes effect.
    """
    from pathlib import Path

    return Path(_config.CONFIG_DIR) / "history.json"


def _read_history() -> list[dict[str, Any]]:
    """Read recorded query invocations (newest last); never raises."""
    try:
        data = json.loads(_history_path().read_text())
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _record_history(args: list[str]) -> None:
    """Append a query invocation to the local history (best-effort).

    Only the read/query roots are recorded, and never --help runs.
    Invocations carrying `--token` are never recorded: persisting raw
    secrets to disk (and echoing them in `history`) would leak them.
    The same applies to endpoint URLs embedding userinfo credentials.
    Must never break the invoked command.
    """
    try:
        if not args or args[0] not in _QUERY_HISTORY_COMMANDS:
            return
        if "-h" in args or "--help" in args:
            return
        # Never persist raw secrets: --token in either space or equals
        # form disqualifies the whole invocation from recording.
        if any(a == "--token" or a.startswith("--token=") for a in args):
            return
        # Endpoint URLs with embedded credentials (https://user:pass@host)
        # are secrets too -- in any flag form.
        if any(_has_url_userinfo(a) for a in args):
            return
        import datetime

        entries = _read_history()
        entries.append({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "argv": list(args),
        })
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Private from birth (see config._write_private): history once
        # held raw --token values in older releases.
        from signoz_cli.config import _ensure_config_dir, _write_private

        _ensure_config_dir()
        _write_private(path, json.dumps(entries[-_QUERY_HISTORY_LIMIT:], indent=2))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except Exception:
        pass
