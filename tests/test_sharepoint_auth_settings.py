from __future__ import annotations

import pytest
from pydantic import ValidationError

from fileroute.auth.microsoft import MicrosoftAuth
from fileroute.auth.settings import (
    MicrosoftAuthConfig,
    MicrosoftAuthMode,
)


def _clear_sharepoint_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SHAREPOINT_AUTH_MODE",
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "SHAREPOINT_HOST_URL",
        "SHAREPOINT_SCOPES",
        "AZURE_HOST_URL",
        "AZURE_SCOPES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_sharepoint_auth_config_defaults_to_app_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_sharepoint_auth_env(monkeypatch)
    config = MicrosoftAuthConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        _env_file=None,
    )

    assert config.auth_mode == MicrosoftAuthMode.APP_ONLY


def test_sharepoint_auth_config_normalizes_scope_string(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_sharepoint_auth_env(monkeypatch)
    config = MicrosoftAuthConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        scopes="scope-a, scope-b",
        _env_file=None,
    )

    assert config.scopes == ["scope-a", "scope-b"]


def test_sharepoint_auth_config_requires_secret_for_app_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_sharepoint_auth_env(monkeypatch)
    with pytest.raises(ValidationError):
        MicrosoftAuthConfig(
            tenant_id="tenant",
            client_id="client",
            auth_mode=MicrosoftAuthMode.APP_ONLY,
            _env_file=None,
        )


def test_sharepoint_auth_config_to_auth_returns_microsoft_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_sharepoint_auth_env(monkeypatch)

    class DummyApp:
        def acquire_token_for_client(self, scopes):
            return {"access_token": "app-token"}

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.ConfidentialClientApplication",
        lambda **kwargs: DummyApp(),
    )

    config = MicrosoftAuthConfig(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        host_url="tenant.sharepoint.com",
        _env_file=None,
    )
    auth = config.to_auth()

    assert isinstance(auth, MicrosoftAuth)
    assert auth.access_token == "app-token"


def test_sharepoint_auth_config_to_auth_delegated(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_sharepoint_auth_env(monkeypatch)

    class DummyApp:
        def get_accounts(self):
            return []

        def acquire_token_interactive(self, scopes):
            return {"access_token": "delegated-token"}

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.PublicClientApplication",
        lambda client_id, authority: DummyApp(),
    )

    config = MicrosoftAuthConfig(
        tenant_id="tenant",
        client_id="client",
        auth_mode=MicrosoftAuthMode.DELEGATED,
        _env_file=None,
    )
    auth = config.to_auth()

    assert isinstance(auth, MicrosoftAuth)
    assert auth.access_token == "delegated-token"