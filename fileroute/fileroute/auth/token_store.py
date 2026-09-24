from __future__ import annotations

from pathlib import Path

from google.oauth2.credentials import Credentials as UserCredentials


class JsonTokenStore:
    """Persist Google authorized-user credentials as JSON."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> UserCredentials | None:
        if not self.path.exists():
            return None
        return UserCredentials.from_authorized_user_file(str(self.path))

    def save(self, creds: UserCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(creds.to_json(), encoding="utf-8")


__all__ = ["JsonTokenStore"]
