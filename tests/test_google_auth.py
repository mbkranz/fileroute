from __future__ import annotations

from pathlib import Path

import pytest
from google.oauth2.credentials import Credentials as UserCredentials

from fileroute.auth.google import (
    GoogleAuth,
    normalize_google_scopes,
)
from fileroute.auth.token_store import JsonTokenStore
from fileroute.exceptions import GoogleAuthError


class DummyCreds:
    def __init__(self, *, valid: bool = True, token: str | None = "token") -> None:
        self.valid = valid
        self.token = token
        self.expired = not valid
        self.refresh_count = 0

    def refresh(self, _request) -> None:
        self.refresh_count += 1
        self.valid = True
        self.expired = False
        self.token = "refreshed-token"


class DummyStore:
    def __init__(self, creds=None):
        self.creds = creds
        self.saved = []

    def load(self):
        return self.creds

    def save(self, creds) -> None:
        self.saved.append(creds)


def test_normalize_google_scopes_supports_none_string_and_sequence() -> None:
    assert normalize_google_scopes(None) == ["https://www.googleapis.com/auth/drive"]
    assert normalize_google_scopes("scope-a") == ["scope-a"]
    assert normalize_google_scopes(["scope-a", "scope-b"]) == [
        "scope-a",
        "scope-b",
    ]


def test_google_auth_init_stores_credentials() -> None:
    creds = DummyCreds(valid=True)
    auth = GoogleAuth(creds)
    assert auth.credentials is creds


def test_google_auth_from_adc_error_mentions_fileroute_user_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "fileroute.auth.google.google.auth.default",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("adc missing")),
    )

    with pytest.raises(GoogleAuthError, match="fileroute auth login gdrive"):
        GoogleAuth.from_adc()


def test_google_auth_from_service_account_raises_on_bad_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "fileroute.auth.google.service_account.Credentials.from_service_account_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("no file")),
    )
    with pytest.raises(GoogleAuthError, match="Failed to load service account"):
        GoogleAuth.from_service_account("nonexistent.json")


def test_json_token_store_round_trip(tmp_path: Path) -> None:
    token_path = tmp_path / "token.json"
    store = JsonTokenStore(token_path)
    creds = UserCredentials.from_authorized_user_info(
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
            "token": "access-token",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/drive.readonly"],
        }
    )

    store.save(creds)
    loaded = store.load()

    assert loaded is not None
    assert loaded.refresh_token == "refresh-token"
    assert loaded.client_id == "client-id"


def test_google_auth_from_user_oauth_returns_valid_stored_credentials() -> None:
    creds = DummyCreds(valid=True)
    store = DummyStore(creds)

    auth = GoogleAuth.from_user_oauth(
        client_secrets_path="oauth.json",
        scopes=["scope-a"],
        token_store=store,
    )

    assert auth.credentials is creds
    assert store.saved == []


def test_google_auth_from_user_oauth_refreshes_expired_credentials() -> None:
    creds = DummyCreds(valid=False, token=None)
    creds.refresh_token = "refresh-token"
    store = DummyStore(creds)

    auth = GoogleAuth.from_user_oauth(
        client_secrets_path="oauth.json",
        scopes=["scope-a"],
        token_store=store,
    )

    assert auth.credentials is creds
    assert creds.refresh_count == 1
    assert store.saved == [creds]


def test_google_auth_from_user_oauth_runs_flow_when_store_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued_creds = DummyCreds(valid=True)
    store = DummyStore()

    class DummyFlow:
        def run_local_server(self, port: int = 0):
            assert port == 0
            return issued_creds

    monkeypatch.setattr(
        "fileroute.auth.google.InstalledAppFlow.from_client_secrets_file",
        lambda path, scopes: DummyFlow(),
    )

    auth = GoogleAuth.from_user_oauth(
        client_secrets_path="oauth.json",
        scopes=["scope-a"],
        token_store=store,
    )

    assert auth.credentials is issued_creds
    assert store.saved == [issued_creds]


def test_google_auth_ensure_valid_refreshes_expired_token() -> None:
    creds = DummyCreds(valid=False, token=None)
    auth = GoogleAuth(creds)

    auth.ensure_valid()

    assert creds.refresh_count == 1


def test_google_auth_ensure_valid_skips_refresh_when_token_valid() -> None:
    creds = DummyCreds(valid=True, token="good-token")
    auth = GoogleAuth(creds)

    auth.ensure_valid()

    assert creds.refresh_count == 0
