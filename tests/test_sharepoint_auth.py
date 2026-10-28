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

    auth = MicrosoftAuth.from_delegated(
        tenant_id="tenant", client_id="client", scopes=["scope-a"]
    )

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
            tenant_id="tenant", client_id="client", client_secret=""
        )


def test_delegated_missing_cache_noninteractive_never_opens_browser(
    tmp_path, monkeypatch
):
    class App:
        def get_accounts(self):
            return []

        def acquire_token_interactive(self, **_kwargs):
            pytest.fail("browser login started")

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.PublicClientApplication",
        lambda *_a, **_kw: App(),
    )
    with pytest.raises(GraphAuthError, match="auth login"):
        MicrosoftAuth.from_delegated(
            "tenant", "client", cache_path=tmp_path / "cache.json", interactive=False
        )


def test_delegated_cache_reloads_silent_refresh_and_private_persistence(
    tmp_path, monkeypatch
):
    import json
    import os
    from fileroute.auth.token_store import atomic_private_write

    path = tmp_path / "cache.json"
    atomic_private_write(
        path, '{"Account": {"account-key": {"home_account_id": "user"}}}'
    )

    class App:
        def __init__(self, cache):
            self.cache = cache

        def get_accounts(self):
            assert "account-key" in json.loads(self.cache.serialize())["Account"]
            return [{"home_account_id": "user"}]

        def acquire_token_silent(self, scopes, account):
            assert scopes == ["Files.Read"]
            assert account["home_account_id"] == "user"
            self.cache.deserialize(
                '{"Account": {"refreshed-key": {"home_account_id": "user"}}}'
            )
            self.cache.has_state_changed = True
            return {"access_token": "refreshed"}

        def acquire_token_interactive(self, **_kw):
            pytest.fail("browser login started")

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.PublicClientApplication",
        lambda *_a, **kw: App(kw["token_cache"]),
    )
    auth = MicrosoftAuth.from_delegated(
        "tenant", "client", scopes=["Files.Read"], cache_path=path, interactive=False
    )
    assert auth.access_token == "refreshed"
    assert "refreshed-key" in json.loads(path.read_text())["Account"]
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_delegated_login_releases_lock_before_browser_and_merges_new_cache(
    tmp_path, monkeypatch
):
    import json
    from fileroute.auth.token_store import atomic_private_write

    path = tmp_path / "cache.json"

    class App:
        def __init__(self, cache):
            self.cache = cache

        def get_accounts(self):
            return []

        def acquire_token_interactive(self, **_kw):
            assert not path.with_name(f".{path.name}.lock").exists()
            atomic_private_write(
                path, '{"AccessToken": {"concurrent": {"secret": "other"}}}'
            )
            self.cache.deserialize('{"AccessToken": {"login": {"secret": "new"}}}')
            self.cache.has_state_changed = True
            return {"access_token": "new"}

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.PublicClientApplication",
        lambda *_a, **kw: App(kw["token_cache"]),
    )
    assert (
        MicrosoftAuth.from_delegated(
            "tenant", "client", cache_path=path, interactive=True
        ).access_token
        == "new"
    )
    assert set(json.loads(path.read_text())["AccessToken"]) == {"concurrent", "login"}


def test_app_only_profile_uses_persistent_msal_cache(tmp_path, monkeypatch):
    import json
    from fileroute.auth.token_store import atomic_private_write

    path = tmp_path / "cache.json"
    atomic_private_write(path, '{"AccessToken": {"existing": {"secret": "cached"}}}')

    class App:
        def __init__(self, cache):
            self.cache = cache

        def acquire_token_for_client(self, scopes):
            assert "existing" in json.loads(self.cache.serialize())["AccessToken"]
            self.cache.deserialize('{"AccessToken": {"updated": {"secret": "new"}}}')
            self.cache.has_state_changed = True
            return {"access_token": "new"}

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.ConfidentialClientApplication",
        lambda **kw: App(kw["token_cache"]),
    )
    auth = MicrosoftAuth.from_app_only("tenant", "client", "secret", cache_path=path)
    assert auth.access_token == "new"
    assert "updated" in json.loads(path.read_text())["AccessToken"]
