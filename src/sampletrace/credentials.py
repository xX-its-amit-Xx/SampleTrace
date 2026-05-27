"""Resolve and persist Benchling API credentials safely.

Precedence — first hit wins, no fallback when something is explicitly set:

  1. ``BENCHLING_API_KEY`` environment variable
  2. OS keyring entry (service ``sampletrace.benchling``, account = tenant URL)
  3. ``.env`` file at the current working directory (auto-loaded if python-dotenv
     is installed); the env var path above then re-applies
  4. ``api_key:`` value in the YAML config (with a WARNING — please don't)

The point of this module is that *no caller should ever read ``api_key``
directly from the YAML*. They go through ``resolve_api_key`` instead, which
gives the right answer for each environment (laptop, CI, container) and
emits the right warning if someone took a shortcut.

The key itself is **never logged**, never written into any output file, and
never echoed back when read via ``configure``. ``KeyResolution.source`` and
``KeyResolution.redacted`` are what callers display.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

# Stable service identifier for OS keyring entries. Don't rename this —
# users will lose access to the keys they've stored.
KEYRING_SERVICE = "sampletrace.benchling"

# Environment variable name. Documented; users will set this in CI.
ENV_VAR = "BENCHLING_API_KEY"


class KeySource(StrEnum):
    ENV = "env"
    KEYRING = "keyring"
    DOTENV = "dotenv"
    YAML = "yaml"
    MISSING = "missing"


@dataclass(frozen=True)
class KeyResolution:
    """The resolved key plus enough metadata to explain *where* it came from.

    Print ``source`` and ``redacted`` to your user; never print ``api_key``.
    """

    api_key: str | None
    source: KeySource
    redacted: str  # "sk_***abc" — last 4 chars only, for log lines

    @classmethod
    def missing(cls) -> KeyResolution:
        return cls(api_key=None, source=KeySource.MISSING, redacted="<none>")


def _redact(key: str) -> str:
    """Last 4 chars only, for diagnostic logging. Never log the full key."""
    if len(key) <= 4:
        return "***"
    return f"***{key[-4:]}"


def _load_dotenv_if_present(dotenv_path: Path | None = None) -> bool:
    """Best-effort .env load. Returns True if a file was found and loaded.

    python-dotenv is optional — if it isn't installed we simply skip this
    layer. Callers don't need to do anything different.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    path = dotenv_path or Path.cwd() / ".env"
    if not path.exists():
        return False
    # override=False so an explicit env var beats a .env entry.
    load_dotenv(path, override=False)
    logger.debug("loaded %s (env vars only override if previously unset)", path)
    return True


def _read_keyring(tenant_url: str) -> str | None:
    """Read the key for a given tenant from the OS keyring.

    Returns None if keyring isn't installed, isn't configured (no backend
    on this OS), or has no entry for this tenant.
    """
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        return None
    try:
        value = keyring.get_password(KEYRING_SERVICE, tenant_url)
    except KeyringError as e:
        logger.debug("keyring lookup failed: %s", e)
        return None
    return value


def resolve_api_key(
    *,
    tenant_url: str | None,
    yaml_value: str | None = None,
    load_dotenv: bool = True,
) -> KeyResolution:
    """Apply the documented precedence to find a usable API key.

    Args:
        tenant_url: Benchling tenant URL. Required for keyring lookup
            (the key is scoped per tenant so a single laptop can hold
            multiple). Without it, keyring is skipped.
        yaml_value: Value of ``benchling.api_key`` in the user's YAML, if any.
        load_dotenv: If True, attempt to load ``.env`` from CWD before
            reading the env var. Set to False in tests for determinism.

    Returns:
        KeyResolution — always populated, ``api_key`` may be None.
    """
    # 1. Env var (already set? short-circuit).
    env_value = os.environ.get(ENV_VAR)
    if env_value:
        return KeyResolution(env_value, KeySource.ENV, _redact(env_value))

    # 2. Keyring.
    if tenant_url:
        kv = _read_keyring(tenant_url)
        if kv:
            return KeyResolution(kv, KeySource.KEYRING, _redact(kv))

    # 3. .env file (may have populated env var; re-check).
    if load_dotenv:
        loaded = _load_dotenv_if_present()
        if loaded:
            env_value = os.environ.get(ENV_VAR)
            if env_value:
                return KeyResolution(env_value, KeySource.DOTENV, _redact(env_value))

    # 4. YAML (with warning).
    if yaml_value:
        logger.warning(
            "Reading Benchling API key from YAML config — this is the least "
            "safe option. Prefer `sampletrace configure` (OS keyring) or the "
            "%s environment variable. See docs/credentials.md.",
            ENV_VAR,
        )
        return KeyResolution(yaml_value, KeySource.YAML, _redact(yaml_value))

    return KeyResolution.missing()


def store_in_keyring(*, tenant_url: str, api_key: str) -> None:
    """Persist an API key to the OS keyring for a given tenant.

    Raises:
        RuntimeError: if keyring is not installed or has no usable backend
            on this OS (e.g. headless Linux without a secret service).
    """
    try:
        import keyring
        from keyring.errors import KeyringError, NoKeyringError
    except ImportError as e:
        raise RuntimeError(
            "keyring is not installed. Run: pip install 'sampletrace[benchling]' "
            "— or set the BENCHLING_API_KEY env var instead."
        ) from e
    try:
        keyring.set_password(KEYRING_SERVICE, tenant_url, api_key)
    except NoKeyringError as e:
        raise RuntimeError(
            "no OS keyring backend available. On headless Linux, install one "
            "(`pip install keyrings.alt` for a file backend, or run "
            "`secret-tool` from libsecret). On CI, prefer the BENCHLING_API_KEY "
            "env var with a CI secret."
        ) from e
    except KeyringError as e:
        raise RuntimeError(f"keyring write failed: {e}") from e


def delete_from_keyring(*, tenant_url: str) -> bool:
    """Remove the stored key for a tenant. Returns True if something was deleted."""
    try:
        import keyring
        from keyring.errors import KeyringError, PasswordDeleteError
    except ImportError:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE, tenant_url)
        return True
    except PasswordDeleteError:
        return False
    except KeyringError as e:
        logger.debug("keyring delete failed: %s", e)
        return False
