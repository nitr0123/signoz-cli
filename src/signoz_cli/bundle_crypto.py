"""Encrypted bundles: passphrase-based encryption for export files.

Format (``signoz-bundle-enc/v1``) is a JSON envelope::

    {"format": "signoz-bundle-enc/v1", "salt": "<base64>",
     "iterations": 600000, "token": "<fernet string>"}

The Fernet token (AES-128-CBC + HMAC-SHA256, via ``cryptography``)
carries its own IV and integrity tag; the salt is public by design.
Keys are derived with PBKDF2-HMAC-SHA256. Passphrases come from an
interactive prompt or ``SIGNOZ_BUNDLE_PASSPHRASE`` — never from argv,
which would leak them to process listings and shell history.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

FORMAT = "signoz-bundle-enc/v1"
ITERATIONS = 600_000
MIN_PASSPHRASE_LEN = 8


def _fernet(passphrase: str, salt: bytes) -> Any:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=ITERATIONS,
    ).derive(passphrase.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def read_passphrase(*, confirm: bool = False,
                    env_var: str = "SIGNOZ_BUNDLE_PASSPHRASE") -> str:
    """Obtain the bundle passphrase (env var or no-echo prompt)."""
    import getpass

    passphrase = os.getenv(env_var, "")
    if passphrase:
        return passphrase
    passphrase = getpass.getpass("Bundle passphrase: ")
    if confirm:
        repeat = getpass.getpass("Confirm bundle passphrase: ")
        if repeat != passphrase:
            import click

            raise click.UsageError("Passphrases do not match.")
    if len(passphrase) < MIN_PASSPHRASE_LEN:
        import click

        raise click.UsageError(
            f"Passphrase must be at least {MIN_PASSPHRASE_LEN} characters.")
    return passphrase


def encrypt_files(files: dict[str, str], passphrase: str) -> str:
    """Encrypt a {relative-path: text-content} mapping; returns envelope JSON."""
    salt = os.urandom(16)
    token = _fernet(passphrase, salt).encrypt(
        json.dumps(files, sort_keys=True).encode("utf-8"))
    return json.dumps({
        "format": FORMAT,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iterations": ITERATIONS,
        "token": token.decode("ascii"),
    }) + "\n"


def decrypt_files(envelope_text: str, passphrase: str) -> dict[str, str]:
    """Decrypt an envelope; raises ValueError on any problem."""
    try:
        envelope = json.loads(envelope_text)
    except ValueError as ex:
        raise ValueError(f"not a bundle envelope: {ex}") from None
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
        have = envelope.get("format") if isinstance(envelope, dict) else "non-object"
        raise ValueError(
            f"not a signoz encrypted bundle (format={have!r})")
    try:
        salt = base64.b64decode(envelope["salt"])
        token = envelope["token"].encode("ascii")
    except (KeyError, ValueError, TypeError) as ex:
        raise ValueError(f"malformed bundle envelope: {ex}") from None
    try:
        plain = _fernet(passphrase, salt).decrypt(token)
    except Exception as ex:
        raise ValueError(
            "cannot decrypt bundle (wrong passphrase or tampered file)"
        ) from ex
    try:
        files = json.loads(plain.decode("utf-8"))
    except ValueError as ex:
        raise ValueError(f"decrypted payload is corrupt: {ex}") from None
    if not isinstance(files, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in files.items()):
        raise ValueError("decrypted payload is corrupt")
    return files
