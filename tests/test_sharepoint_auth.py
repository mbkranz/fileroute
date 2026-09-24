from __future__ import annotations

import pytest

from fileroute.auth.microsoft import (
    DEFAULT_MICROSOFT_GRAPH_SCOPES,
    MicrosoftAuth,
    normalize_microsoft_scopes,
)
from fileroute.exceptions import GraphAuthError


def test_normalize_microsoft_scopes_supports_none_string_and_sequence() -> None:
    assert normalize_microsoft_scopes(None) == list(DEFAULT_MICROSOFT_GRAPH_SCOPES)
    assert normalize_microsoft_scopes("scope-a, scope-b") == ["scope-a", "scope-b"]
    assert normalize_microsoft_scopes(["scope-a", "scope-b"]) == ["scope-a", "scope-b"]


def test_microsoft_auth_init_stores_token() -> None:
    auth = MicrosoftAuth("my-token")
    assert auth.access_token == "my-token"


def test_microsoft_auth_from_delegated_uses_interactive_flow_when_no_cached_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummyApp:
        def get_accounts(self):
            return []

        def acquire_token_interactive(self, scopes):
            captured["scopes"] = scopes
            return {"access_token": "delegated-token"}

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.PublicClientApplication",
        lambda client_id, authority: DummyApp(),
    )

    auth = MicrosoftAuth.from_delegated(tenant_id="tenant", client_id="client", scopes=["scope-a"])

    assert auth.access_token == "delegated-token"
    assert captured["scopes"] == ["scope-a"]


def test_microsoft_auth_from_app_only_uses_confidential_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class DummyApp:
        def acquire_token_for_client(self, scopes):
            captured["scopes"] = scopes
            return {"access_token": "app-token"}

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.ConfidentialClientApplication",
        lambda **kwargs: DummyApp(),
    )

    auth = MicrosoftAuth.from_app_only(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        scopes=["scope-a"],
    )

    assert auth.access_token == "app-token"
    assert captured["scopes"] == ["scope-a"]


def test_microsoft_auth_from_app_only_requires_secret() -> None:
    with pytest.raises(GraphAuthError):
        MicrosoftAuth.from_app_only(
            tenant_id="tenant",
            client_id="client",
            client_secret="",
        )