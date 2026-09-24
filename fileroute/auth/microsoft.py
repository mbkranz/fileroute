from __future__ import annotations

from typing import Sequence

import msal

from fileroute.exceptions import GraphAuthError

DEFAULT_MICROSOFT_GRAPH_SCOPES = ("https://graph.microsoft.com/.default",)


def normalize_microsoft_scopes(
    scopes: Sequence[str] | str | None,
    *,
    default: Sequence[str] = DEFAULT_MICROSOFT_GRAPH_SCOPES,
) -> list[str]:
    if scopes is None:
        return list(default)
    if isinstance(scopes, str):
        normalized = [scope.strip() for scope in scopes.split(",") if scope.strip()]
        return normalized or list(default)
    normalized = [scope for scope in scopes if scope]
    return normalized or list(default)


def _extract_access_token(result: dict[str, object]) -> str:
    token = result.get("access_token")
    if isinstance(token, str) and token:
        return token
    raise GraphAuthError(f"Token error: {result.get('error_description')}")


class MicrosoftAuth:
    """Microsoft access-token holder with named constructors for each auth mode.

    The primary entry points are the ``from_*`` class methods. The
    ``__init__`` constructor accepts a raw access token string and acts as a
    low-level escape hatch (e.g. for tests).

    Usage::

        # App-only (client credentials) – most common for server-side automation
        auth = MicrosoftAuth.from_app_only(tenant_id, client_id, client_secret)

        # Delegated (interactive browser login)
        auth = MicrosoftAuth.from_delegated(tenant_id, client_id)

        # Read mode + credentials from environment variables / .env file
        auth = MicrosoftAuth.from_settings()
    """

    def __init__(self, access_token: str) -> None:
        """Low-level escape hatch: supply a raw access token directly."""
        self._access_token = access_token

    # ------------------------------------------------------------------
    # Named constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_app_only(
        cls,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scopes: Sequence[str] | str | None = None,
    ) -> "MicrosoftAuth":
        """Build using client-credential (app-only) flow via MSAL."""
        if not client_secret:
            raise GraphAuthError(
                "client_secret is required for Microsoft app_only mode."
            )
        normalized_scopes = normalize_microsoft_scopes(scopes)
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        app = msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=authority,
        )
        result = app.acquire_token_for_client(scopes=normalized_scopes)
        return cls(_extract_access_token(result))

    @classmethod
    def from_delegated(
        cls,
        tenant_id: str,
        client_id: str,
        scopes: Sequence[str] | str | None = None,
    ) -> "MicrosoftAuth":
        """Build using interactive delegated (user) flow via MSAL."""
        normalized_scopes = normalize_microsoft_scopes(scopes)
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        app = msal.PublicClientApplication(client_id, authority=authority)
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(normalized_scopes, account=accounts[0])
            if result and "access_token" in result:
                return cls(_extract_access_token(result))
        result = app.acquire_token_interactive(scopes=normalized_scopes)
        return cls(_extract_access_token(result))

    @classmethod
    def from_settings(cls, config: object | None = None) -> "MicrosoftAuth":
        """Build from environment variables or a :class:`~fileroute.auth.settings.MicrosoftAuthConfig`.

        When *config* is ``None`` the settings are read from the environment
        (and any ``.env`` file in the working directory).
        """
        from fileroute.auth.settings import MicrosoftAuthConfig

        resolved: MicrosoftAuthConfig = config if config is not None else MicrosoftAuthConfig()  # type: ignore[assignment]
        return resolved.to_auth()

    # ------------------------------------------------------------------
    # Runtime helpers
    # ------------------------------------------------------------------

    @property
    def access_token(self) -> str:
        """The raw Bearer access token string."""
        return self._access_token


__all__ = [
    "MicrosoftAuth"
]