from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any, Sequence

import google.auth
from google.auth.credentials import Credentials
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from fileroute.auth.token_store import JsonTokenStore
from fileroute.exceptions import GoogleAuthError

DEFAULT_DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive",)
DEFAULT_DRIVE_READONLY_SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)


def normalize_google_scopes(
    scopes: Sequence[str] | str | None, *, default: Sequence[str] = DEFAULT_DRIVE_SCOPES
) -> list[str]:
    if scopes is None:
        return list(default)
    if isinstance(scopes, str):
        return [scopes]
    normalized = [scope for scope in scopes if scope]
    return normalized or list(default)


def _has_scopes(creds: Any, scopes: Sequence[str]) -> bool:
    granted = getattr(creds, "granted_scopes", None)
    if granted is not None:
        return set(scopes).issubset(set(granted))
    checker = getattr(creds, "has_scopes", None)
    return not callable(checker) or bool(checker(scopes))


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

    def __init__(
        self,
        credentials: Credentials,
        *,
        token_store: Any = None,
        scopes: Sequence[str] | None = None,
    ) -> None:
        """Low-level escape hatch: supply raw credentials directly."""
        self._creds = credentials
        self._token_store = token_store
        self._scopes = list(scopes or ())

    def refresh(self) -> None:
        """Refresh the access token"""
        store = self._token_store
        lock = (
            store.locked()
            if store is not None and hasattr(store, "locked")
            else nullcontext()
        )
        with lock:
            if store is not None:
                cached = store.load()
                if cached is not None:
                    if not _has_scopes(cached, self._scopes):
                        raise GoogleAuthError(
                            "Cached Google OAuth credentials no longer grant the configured scopes; log in again."
                        )
                    if cached is not self._creds and getattr(cached, "valid", False):
                        self._creds = cached
                        return
                    self._creds = cached
            self._creds.refresh(Request())
            if not _has_scopes(self._creds, self._scopes):
                raise GoogleAuthError(
                    "Refreshed Google OAuth credentials do not grant the configured scopes; log in again."
                )
            if store is not None:
                store.save(self._creds)

    @classmethod
    def from_adc(cls, scopes: Sequence[str] | str | None = None) -> "GoogleAuth":
        """Build from Application Default Credentials (``gcloud auth application-default login``)."""
        normalized_scopes = normalize_google_scopes(scopes)
        try:
            creds, _project = google.auth.default(scopes=normalized_scopes)
            if getattr(creds, "requires_scopes", False) and hasattr(
                creds, "with_scopes"
            ):
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
        cls, credentials_path: str | Path, scopes: Sequence[str] | str | None = None
    ) -> "GoogleAuth":
        """Build from a service account JSON key file."""
        normalized_scopes = normalize_google_scopes(scopes)
        try:
            creds = service_account.Credentials.from_service_account_file(
                str(credentials_path), scopes=normalized_scopes
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
        client_secrets_path: str | Path | None = None,
        token_path: str | Path | None = None,
        token_store: Any = None,
        *,
        interactive: bool = True,
        use_local_server: bool = True,
    ) -> "GoogleAuth":
        """Load/refresh scoped user credentials, prompting only when permitted.

        ``interactive=True`` preserves the legacy direct-constructor behavior.
        Named profiles explicitly disable interaction during normal transfers.
        Refreshes are persisted privately; OAuth consent never holds a file lock.
        """
        normalized_scopes = normalize_google_scopes(scopes)
        store = token_store or (
            JsonTokenStore(token_path) if token_path is not None else None
        )
        lock = (
            store.locked()
            if store is not None and hasattr(store, "locked")
            else nullcontext()
        )
        needs_login = "No cached Google OAuth credentials are available."
        with lock:
            try:
                creds = store.load() if store is not None else None
            except (ValueError, OSError):
                raise GoogleAuthError(
                    "Cannot read Google OAuth token cache; remove it and log in again."
                ) from None
            if creds is not None:
                if not _has_scopes(creds, normalized_scopes):
                    needs_login = "Cached Google OAuth credentials do not grant the configured scopes."
                elif getattr(creds, "valid", False):
                    return cls(creds, token_store=store, scopes=normalized_scopes)
                elif getattr(creds, "refresh_token", None):
                    try:
                        creds.refresh(Request())
                    except RefreshError:
                        needs_login = (
                            "Cached Google OAuth credentials could not be refreshed."
                        )
                    else:
                        if not _has_scopes(creds, normalized_scopes):
                            raise GoogleAuthError(
                                "Refreshed Google OAuth credentials do not grant the configured scopes; log in again."
                            )
                        if store is not None:
                            store.save(creds)
                        return cls(creds, token_store=store, scopes=normalized_scopes)
        if not interactive:
            raise GoogleAuthError(
                f"{needs_login} Run 'fileroute auth login gdrive --profile NAME'."
            )
        if client_secrets_path is None:
            raise GoogleAuthError(
                "client_secrets_path is required for Google OAuth login."
            )
        if not use_local_server:
            raise GoogleAuthError(
                "Google installed-app OAuth requires a local callback; set use_local_server=true."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(client_secrets_path), scopes=normalized_scopes
        )
        creds = flow.run_local_server(port=0)
        if not _has_scopes(creds, normalized_scopes):
            raise GoogleAuthError(
                "Google OAuth consent did not grant the configured scopes."
            )
        if store is not None:
            lock = store.locked() if hasattr(store, "locked") else nullcontext()
            with lock:
                store.save(creds)
        return cls(creds, token_store=store, scopes=normalized_scopes)

    @classmethod
    def from_settings(cls, config: object | None = None) -> "GoogleAuth":
        """Build from environment variables or a :class:`~fileroute.auth.settings.GoogleAuthConfig`.

        When *config* is ``None`` the settings are read from the environment
        (and any ``.env`` file in the working directory).
        """
        from fileroute.auth.settings import GoogleAuthConfig

        resolved: GoogleAuthConfig = (
            config if config is not None else GoogleAuthConfig()
        )  # type: ignore[assignment]
        return resolved.to_auth()

    @property
    def credentials(self) -> Credentials:
        """The underlying :class:`~google.auth.credentials.Credentials` object."""
        return self._creds

    def ensure_valid(self) -> None:
        """Refresh credentials when the current token is not valid."""
        if not self._creds.valid:
            self.refresh()


__all__ = ["GoogleAuth"]
