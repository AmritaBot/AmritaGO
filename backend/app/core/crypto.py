"""Encryption at rest for provider credentials.

The Fernet key ships with nothing: ``AMRITA_GO_SECRET`` wins, then the OS
keyring, then ``secret.key`` beside the data (mode 0600, owner-only ACL on
Windows). The precedence is sticky — reading a different key later would make
stored credentials undecryptable. A corrupt file is reported, never replaced.
"""

from __future__ import annotations

import base64
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from amrita_sense.logging import logger
from cryptography.fernet import Fernet, InvalidToken

KEY_FILE = "secret.key"

#: Explicit key material (base64, 32 bytes). Intended for containers and CI.
ENV_SECRET = "AMRITA_GO_SECRET"

#: Truthy skips the OS keyring and always uses ``secret.key``.
ENV_NO_KEYRING = "AMRITA_GO_NO_KEYRING"

#: One keyring entry per data directory, so two installs do not share a key.
KEYRING_SERVICE = "amrita-go"


class SecretError(RuntimeError):
    """Raised when the secret store cannot be read or the key is wrong."""


@dataclass(frozen=True)
class KeyInfo:
    """Where the key in use comes from — surfaced at startup and in diagnostics."""

    source: str  # "env" | "keyring" | "file"
    detail: str


# File store


def _key_path(data_dir: Path) -> Path:
    return data_dir / KEY_FILE


def _validate(key: bytes, origin: str) -> bytes:
    """A Fernet key must decode to exactly 32 bytes; anything else is an error."""
    stripped = key.strip()
    try:
        raw = base64.urlsafe_b64decode(stripped)
    except Exception as exc:
        raise SecretError(
            f"{origin} does not contain a Fernet key; delete it to generate a new "
            "one (existing secrets will become unreadable)"
        ) from exc
    if len(raw) != 32:
        raise SecretError(
            f"{origin} does not contain a Fernet key (expected 32 bytes of key "
            "material); delete it to generate a new one (existing secrets will "
            "become unreadable)"
        )
    return stripped


def _restrict_permissions(path: Path) -> None:
    """Make the key readable by this user only.

    POSIX gets ``0600``. Windows ignores POSIX modes entirely, so it needs an
    ACL edit; both are best-effort but a failure is logged, because a key file
    every local account can read is a real (if quiet) problem.
    """
    if os.name == "nt":
        account = os.environ.get("USERNAME")
        if not account:
            logger.warning("Cannot determine the current user; leaving the key ACL as-is")
            return
        try:
            result = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:F"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning(f"Could not restrict access to {path}: {exc}")
            return
        if result.returncode != 0:
            logger.warning(
                f"Could not restrict access to {path} "
                f"(icacls exit {result.returncode}): {result.stdout.strip()}"
            )
        return

    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        logger.warning(f"Could not chmod 0600 {path}: {exc}")


def _read_key_file(path: Path) -> bytes:
    key = _validate(path.read_bytes(), str(path))
    # Fix up the mode of a file created before it was set.
    _restrict_permissions(path)
    return key


def _create_key_file(path: Path) -> bytes:
    """Create ``secret.key`` with the key material, once, atomically.

    The temporary file is created in the same directory so the publish step is
    atomic, and the restrictive mode is applied *before* anything is written.
    Two processes starting at once must agree on one key, and a half-written
    file must never be read back as if it were a key.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()

    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".secret-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(key)
        _restrict_permissions(tmp)
        try:
            # Exclusive publish: whoever wins, everyone reads the same key.
            os.link(tmp, path)
        except FileExistsError:
            return _read_key_file(path)
        except OSError:
            # No hard links here: check before replacing, so a concurrent creator wins.
            if path.exists():
                return _read_key_file(path)
            tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)

    _restrict_permissions(path)
    logger.info(f"Generated a new credential key at {path} (mode 0600)")
    return key


# OS keyring store


def _has_desktop_session() -> bool:
    """Whether a Linux session has a secret service to talk to.

    Headless (a server, a container, cron) has no D-Bus session bus and no
    display; asking keyring there either fails or, worse, blocks on a prompt.
    """
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS") or os.environ.get("XDG_RUNTIME_DIR"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


class _KeyringModule(Protocol):
    """The two calls this module makes against the ``keyring`` package."""

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...


def _keyring_unusable_reason() -> str | None:
    """Why the keyring cannot be used, or ``None`` when it can."""
    if os.environ.get(ENV_NO_KEYRING, "").strip().lower() in {"1", "true", "yes", "on"}:
        return f"{ENV_NO_KEYRING} is set"
    if os.name == "nt":
        # Windows keeps the key in an ACL-protected file.
        return "Windows uses the ACL-protected key file"
    if sys.platform.startswith("linux") and not _has_desktop_session():
        return "no desktop session (headless)"
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except Exception:
        return "the keyring package is not installed"
    try:
        backend = keyring.get_keyring()
    except Exception as exc:
        return f"could not load a keyring backend: {exc}"
    if isinstance(backend, FailKeyring) or getattr(backend, "priority", 0) <= 0:
        return "no secret service is available"
    return None


@dataclass
class _KeyringStore:
    module: _KeyringModule
    account: str

    def load(self) -> bytes | None:
        try:
            stored = self.module.get_password(KEYRING_SERVICE, self.account)
        except Exception as exc:
            logger.warning(f"Could not read the credential key from the keyring: {exc}")
            return None
        if not stored:
            return None
        try:
            return _validate(stored.encode(), "the OS keyring entry")
        except SecretError as exc:
            logger.error(str(exc))
            return None

    def save(self, key: bytes) -> bool:
        try:
            self.module.set_password(KEYRING_SERVICE, self.account, key.decode())
            return True
        except Exception as exc:
            # A locked keychain must not stop the app; the caller falls back.
            logger.warning(f"Could not store the credential key in the keyring: {exc}")
            return False


def _keyring_store(data_dir: Path) -> _KeyringStore | None:
    if _keyring_unusable_reason() is not None:
        return None
    import keyring

    return _KeyringStore(module=keyring, account=str(data_dir.resolve()))


# Public API


def _env_key() -> bytes | None:
    override = (os.environ.get(ENV_SECRET) or "").strip()
    if not override:
        return None
    return _validate(override.encode(), ENV_SECRET)


def load_key(data_dir: Path) -> bytes:
    """The Fernet key for this data directory, creating it on first use.

    Order is fixed (env -> keyring -> file) so the answer never changes between
    runs; see the module docstring for why that matters.
    """
    env = _env_key()
    if env is not None:
        return env

    path = _key_path(data_dir)
    if path.exists():
        return _read_key_file(path)

    store = _keyring_store(data_dir)
    if store is not None:
        stored = store.load()
        if stored is not None:
            return stored
        key = Fernet.generate_key()
        if store.save(key):
            logger.info(
                "Generated a new credential key and stored it in the OS keyring "
                f"(service {KEYRING_SERVICE!r})"
            )
            return key
        logger.warning("Falling back to secret.key: the keyring could not store the key")

    return _create_key_file(path)


def describe_key(data_dir: Path) -> KeyInfo:
    """Where the key comes from, without creating anything.

    Used by the startup log and by diagnostics: "which key is this process
    using" is the first question to ask when credentials fail to decrypt.
    """
    if _env_key() is not None:
        return KeyInfo("env", f"from {ENV_SECRET} (nothing written to disk)")

    path = _key_path(data_dir)
    if path.exists():
        return KeyInfo("file", str(path))

    reason = _keyring_unusable_reason()
    if reason is None:
        return KeyInfo("keyring", f"OS keyring, service {KEYRING_SERVICE!r}")
    return KeyInfo("file", f"will create {path} ({reason})")


def encrypt_blob(data_dir: Path, payload: bytes) -> bytes:
    return Fernet(load_key(data_dir)).encrypt(payload)


def decrypt_blob(data_dir: Path, token: bytes) -> bytes:
    try:
        return Fernet(load_key(data_dir)).decrypt(token)
    except InvalidToken as exc:
        raise SecretError(
            "cannot decrypt the stored credentials: the secret key does not match "
            "the data (the key was replaced, or the data came from another install)"
        ) from exc


__all__ = [
    "ENV_NO_KEYRING",
    "ENV_SECRET",
    "KEYRING_SERVICE",
    "KEY_FILE",
    "KeyInfo",
    "SecretError",
    "decrypt_blob",
    "describe_key",
    "encrypt_blob",
    "load_key",
]
