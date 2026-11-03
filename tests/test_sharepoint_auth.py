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
    assert auth.get_access_token() == "my-token"


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
        lambda client_id, authority, token_cache: DummyApp(),
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


def test_app_only_renewal_reuses_msal_app_and_reloads_private_cache(
    tmp_path, monkeypatch
):
    import json
    from fileroute.auth.token_store import atomic_private_write

    path = tmp_path / "cache.json"
    created, observed = [], []

    class App:
        def __init__(self, cache):
            self.cache = cache

        def acquire_token_for_client(self, scopes):
            assert path.with_name(f".{path.name}.lock").exists()
            observed.append(json.loads(self.cache.serialize()))
            self.cache.deserialize('{"AccessToken": {"renewed": {"secret": "new"}}}')
            self.cache.has_state_changed = True
            return {"access_token": f"token-{len(observed)}"}

    def build(**kwargs):
        app = App(kwargs["token_cache"])
        created.append(app)
        return app

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.ConfidentialClientApplication", build
    )
    auth = MicrosoftAuth.from_app_only("tenant", "client", "secret", cache_path=path)
    atomic_private_write(
        path, '{"AccessToken": {"other-process": {"secret": "latest"}}}'
    )
    assert auth.get_access_token() == "token-2"
    assert auth.access_token == "token-2"
    assert len(created) == 1
    assert "other-process" in observed[1]["AccessToken"]
    assert "renewed" in json.loads(path.read_text())["AccessToken"]
    assert not path.with_name(f".{path.name}.lock").exists()


def test_delegated_runtime_renewal_never_reopens_initial_login(monkeypatch):
    class App:
        logged_in = False
        silent_calls = 0
        browser_calls = 0

        def get_accounts(self):
            return [{"home_account_id": "user"}] if self.logged_in else []

        def acquire_token_interactive(self, scopes):
            self.browser_calls += 1
            self.logged_in = True
            return {"access_token": "initial"}

        def acquire_token_silent(self, scopes, account):
            self.silent_calls += 1
            return {"access_token": "renewed"} if self.silent_calls == 1 else None

    app = App()
    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.PublicClientApplication", lambda *_a, **_kw: app
    )
    auth = MicrosoftAuth.from_delegated("tenant", "client", interactive=True)
    assert auth.get_access_token() == "renewed"
    with pytest.raises(GraphAuthError, match="auth login sharepoint"):
        auth.get_access_token()
    assert app.browser_calls == 1
    assert auth.access_token == "renewed"


def test_app_only_runtime_failure_does_not_expose_provider_payload(monkeypatch):
    results = iter([
        {"access_token": "initial"},
        {"error_description": "accidentally included secret"},
    ])

    class App:
        def acquire_token_for_client(self, scopes):
            return next(results)

    monkeypatch.setattr(
        "fileroute.auth.microsoft.msal.ConfidentialClientApplication",
        lambda **_kw: App(),
    )
    auth = MicrosoftAuth.from_app_only("tenant", "client", "secret")
    with pytest.raises(GraphAuthError, match="Check credentials") as error:
        auth.get_access_token()
    assert "accidentally included secret" not in str(error.value)
