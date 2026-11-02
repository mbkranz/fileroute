from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
from typing import Any, Sequence

import msal

from fileroute.exceptions import GraphAuthError
from fileroute.auth.token_store import atomic_private_write, private_file_lock

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


def _load_cache(path: Path) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if path.exists():
        try:
            cache.deserialize(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise GraphAuthError(
                "Cannot read Microsoft token cache; remove it and log in again."
            ) from None
    return cache


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
        *,
        cache_path: Path | None = None,
    ) -> "MicrosoftAuth":
        """Build using client-credential (app-only) flow via MSAL."""
        if not client_secret:
            raise GraphAuthError(
                "client_secret is required for Microsoft app_only mode."
            )
        normalized_scopes = normalize_microsoft_scopes(scopes)
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        lock = (
            private_file_lock(cache_path) if cache_path is not None else nullcontext()
        )
        with lock:
            cache = _load_cache(cache_path) if cache_path is not None else None
            kwargs: dict[str, Any] = {
                "client_id": client_id,
                "client_credential": client_secret,
                "authority": authority,
            }
            if cache is not None:
                kwargs["token_cache"] = cache
            app = msal.ConfidentialClientApplication(**kwargs)
            # MSAL 1.23+ checks this cache before contacting the identity provider.
            result = app.acquire_token_for_client(scopes=normalized_scopes)
            if cache_path is not None and cache is not None and cache.has_state_changed:
                atomic_private_write(cache_path, cache.serialize())
        return cls(_extract_access_token(result))

    @classmethod
    def from_delegated(
        cls,
        tenant_id: str,
        client_id: str,
        scopes: Sequence[str] | str | None = None,
        *,
        cache_path: Path | None = None,
        interactive: bool = True,
    ) -> "MicrosoftAuth":
        """Acquire silently from a persistent MSAL cache before optional consent.

        The default preserves legacy interactive callers. Named profiles pass
        ``interactive=False`` for transfers. Cache reads and refresh writes are
        locked, but the browser login runs without holding a filesystem lock.
        See Microsoft's MSAL Python token-cache serialization guidance.
        """
        normalized_scopes = normalize_microsoft_scopes(scopes)
        authority = f"https://login.microsoftonline.com/{tenant_id}"
        lock = (
            private_file_lock(cache_path) if cache_path is not None else nullcontext()
        )
        with lock:
            cache = _load_cache(cache_path) if cache_path is not None else None
            kwargs: dict[str, Any] = {"authority": authority}
            if cache is not None:
                kwargs["token_cache"] = cache
            app = msal.PublicClientApplication(client_id, **kwargs)
            accounts = app.get_accounts()
            if len(accounts) > 1:
                raise GraphAuthError(
                    "Microsoft profile cache contains multiple accounts; clear it and log in again."
                )
            if accounts:
                result = app.acquire_token_silent(
                    normalized_scopes, account=accounts[0]
                )
                if (
                    cache_path is not None
                    and cache is not None
                    and cache.has_state_changed
                ):
                    atomic_private_write(cache_path, cache.serialize())
                if result and "access_token" in result:
                    return cls(_extract_access_token(result))
        if not interactive:
            raise GraphAuthError(
                "No usable cached Microsoft login. Run 'fileroute auth login sharepoint --profile NAME'."
            )
        result = app.acquire_token_interactive(scopes=normalized_scopes)
        token = _extract_access_token(result)
        if cache_path is not None and cache is not None:
            with private_file_lock(cache_path):
                # Another process may refresh while the browser is open. Merge
                # those entries before adding this consent result.
                latest = json.loads(_load_cache(cache_path).serialize())
                for category, entries in json.loads(cache.serialize()).items():
                    if isinstance(entries, dict):
                        latest.setdefault(category, {}).update(entries)
                    else:
                        latest[category] = entries
                atomic_private_write(cache_path, json.dumps(latest))
        return cls(token)

    @classmethod
    def from_settings(cls, config: object | None = None) -> "MicrosoftAuth":
        """Build from environment variables or a :class:`~fileroute.auth.settings.MicrosoftAuthConfig`.

        When *config* is ``None`` the settings are read from the environment
        (and any ``.env`` file in the working directory).
        """
        from fileroute.auth.settings import MicrosoftAuthConfig

        resolved: MicrosoftAuthConfig = (
            config if config is not None else MicrosoftAuthConfig()
        )  # type: ignore[assignment]
        return resolved.to_auth()

    # ------------------------------------------------------------------
    # Runtime helpers
    # ------------------------------------------------------------------

    @property
    def access_token(self) -> str:
        """The raw Bearer access token string."""
        return self._access_token


__all__ = ["MicrosoftAuth"]
