from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
import os
from pathlib import Path
import tempfile
import time
from typing import Iterator

from google.oauth2.credentials import Credentials as UserCredentials


def _private_directory(path: Path) -> None:
    """Create missing directories privately, leaving existing parents alone."""
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)


def atomic_private_write(path: Path, content: str) -> None:
    """Atomically replace a UTF-8 file with owner-only permissions.

    Contents are private, not encrypted. Serialize read-modify-write operations
    with ``private_file_lock``; the temporary file stays on the same filesystem.
    """
    _private_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def private_file_lock(path: Path, *, timeout: float = 10.0) -> Iterator[None]:
    """Serialize local updates with an exclusive lock file and bounded waiting.

    A terminated process can leave a stale lock. Report its path rather than
    guessing whether another writer is alive. Never hold this lock across login.
    """
    _private_directory(path.parent)
    lock_path = path.with_name(f".{path.name}.lock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Authentication configuration is busy: {lock_path}. "
                    "Retry; if no fileroute process is running, remove the stale lock."
                ) from None
            time.sleep(0.05)
    try:
        os.close(descriptor)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


class JsonTokenStore:
    """Persist Google authorized-user credentials privately as JSON."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def locked(self) -> AbstractContextManager[None]:
        """Lock the complete load/refresh/save operation."""
        return private_file_lock(self.path)

    def load(self) -> UserCredentials | None:
        if not self.path.exists():
            return None
        return UserCredentials.from_authorized_user_file(str(self.path))

    def save(self, creds: UserCredentials) -> None:
        atomic_private_write(self.path, creds.to_json())


__all__ = ["JsonTokenStore", "atomic_private_write", "private_file_lock"]
