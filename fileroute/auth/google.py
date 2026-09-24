from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import google.auth
from google.auth.credentials import Credentials
from google.oauth2.credentials import Credentials as UserCredentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from fileroute.exceptions import GoogleAuthError,GoogleRefreshError
from warnings import warn

DEFAULT_DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive",)
DEFAULT_DRIVE_READONLY_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly",
)


def normalize_google_scopes(
    scopes: Sequence[str] | str | None,
    *,
    default: Sequence[str] = DEFAULT_DRIVE_SCOPES,
) -> list[str]:
    if scopes is None:
        return list(default)
    if isinstance(scopes, str):
        return [scopes]
    normalized = [scope for scope in scopes if scope]
    return normalized or list(default)


class GoogleAuth:
    """Google credential holder with named constructors for each auth mode.

    The primary entry points are the ``from_*`` class methods. The
    ``__init__`` constructor accepts a raw :class:`~google.auth.credentials.Credentials`
    object and acts as a low-level escape hatch (e.g. for tests).

    Usage::

        # Application Default Credentials (default for most deployments)
        auth = GoogleAuth.from_adc()

        # Service account JSON key file
        auth = GoogleAuth.from_service_account("path/to/key.json")

        # Interactive OAuth (installed-app flow)
        auth = GoogleAuth.from_user_oauth("path/to/client_secrets.json")

        # Read mode + scopes from environment variables / .env file
        auth = GoogleAuth.from_settings()
    """

    def __init__(self, credentials: Credentials) -> None:
        """Low-level escape hatch: supply raw credentials directly."""
        self._creds = credentials

    def refresh(self) -> None:
        """Refresh the access token"""
        self._creds.refresh(Request())

    @classmethod
    def from_adc(cls, scopes: Sequence[str] | str | None = None) -> "GoogleAuth":
        """Build from Application Default Credentials (``gcloud auth application-default login``)."""
        normalized_scopes = normalize_google_scopes(scopes)
        try:
            creds, _project = google.auth.default(scopes=normalized_scopes)
            if getattr(creds, "requires_scopes", False) and hasattr(creds, "with_scopes"):
                creds = creds.with_scopes(normalized_scopes)
            return cls(creds)
        except Exception as exc:
            raise GoogleAuthError(
                "Failed to load Application Default Credentials: "
                f"{exc}. To use fileroute user OAuth instead, set "
                "GOOGLE_AUTH_MODE=user_oauth with GOOGLE_OAUTH_CREDENTIALS "
                "and run 'fileroute auth login gdrive'."
            ) from exc

    @classmethod
    def from_service_account(
        cls,
        credentials_path: str | Path,
        scopes: Sequence[str] | str | None = None,
    ) -> "GoogleAuth":
        """Build from a service account JSON key file."""
        normalized_scopes = normalize_google_scopes(scopes)
        try:
            creds = service_account.Credentials.from_service_account_file(
                str(credentials_path),
                scopes=normalized_scopes,
            )
            return cls(creds)
        except Exception as exc:
            raise GoogleAuthError(
                f"Failed to load service account credentials from {credentials_path}: {exc}"
            ) from exc


    @classmethod
    def from_user_oauth(
        cls,
        scopes: Sequence[str] | str,
        client_secrets_path: str | Path = None,
        token_path: str | Path = None,
        token_store: Any = None,
    ) -> "GoogleAuth":
        """Build via the OAuth installed-app flow, with token persistence."""
    
        def _interactive_login_from_client_secrets_file() -> UserCredentials:
            flow = InstalledAppFlow.from_client_secrets_file(
                                str(client_secrets_path),
                                scopes=normalized_scopes,
                            )
            creds = flow.run_local_server(port=0)
            return creds

        normalized_scopes = normalize_google_scopes(scopes)
        if token_store is not None:
            creds = token_store.load()
            if creds is not None and getattr(creds, "valid", False):
                return cls(creds)
            if creds is not None and getattr(creds, "refresh_token", None):
                creds.refresh(Request())
            elif client_secrets_path:
                creds = _interactive_login_from_client_secrets_file()
            else:
                raise GoogleAuthError(
                    "client_secrets_path is required when stored OAuth credentials are missing."
                )
            token_store.save(creds)
            return cls(creds)

        if Path(token_path).exists():
            creds = UserCredentials.from_authorized_user_file(token_path, scopes=scopes)
            try:
                creds.refresh(Request())
                
            except GoogleRefreshError as exc:
                warn(f"Failed to refresh stored credentials: {exc}. Proceeding to interactive login.")
                creds = _interactive_login_from_client_secrets_file()
        elif client_secrets_path:
            warn(f"Token file does not exist at {token_path}. Credentials will not be saved for future use.")
            creds = _interactive_login_from_client_secrets_file()
        else:
            raise GoogleAuthError("At least one of token_path or client_secrets_path must be provided for user OAuth.")
        
        if hasattr(creds, "to_json"):
            Path(token_path).parent.mkdir(parents=True, exist_ok=True)
            Path(token_path).write_text(creds.to_json())
        return cls(creds)

    @classmethod
    def from_settings(cls, config: object | None = None) -> "GoogleAuth":
        """Build from environment variables or a :class:`~fileroute.auth.settings.GoogleAuthConfig`.

        When *config* is ``None`` the settings are read from the environment
        (and any ``.env`` file in the working directory).
        """
        from fileroute.auth.settings import GoogleAuthConfig

        resolved: GoogleAuthConfig = config if config is not None else GoogleAuthConfig()  # type: ignore[assignment]
        return resolved.to_auth()

    @property
    def credentials(self) -> Credentials:
        """The underlying :class:`~google.auth.credentials.Credentials` object."""
        return self._creds

    def ensure_valid(self) -> None:
        """Refresh credentials when the current token is not valid."""
        if not self._creds.valid:
            self.refresh()

__all__ = [
    "GoogleAuth"
]
