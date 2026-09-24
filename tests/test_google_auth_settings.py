from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fileroute.auth.google import GoogleAuth
from fileroute.auth.settings import (
    GoogleAuthConfig,
    GoogleAuthMode,
)


def _clear_google_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GOOGLE_AUTH_MODE",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_SERVICE_ACCOUNT_CREDENTIALS",
        "GOOGLE_OAUTH_CREDENTIALS",
        "GOOGLE_OAUTH_TOKEN_PATH",
        "GOOGLE_SCOPES",
        "GOOGLE_OAUTH_USE_LOCAL_SERVER",
    ):
        monkeypatch.delenv(name, raising=False)


def test_google_auth_config_defaults_to_adc_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_google_auth_env(monkeypatch)
    config = GoogleAuthConfig(_env_file=None)

    assert config.auth_mode == GoogleAuthMode.ADC


def test_google_auth_config_normalizes_comma_separated_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_google_auth_env(monkeypatch)
    config = GoogleAuthConfig(scopes="scope-a, scope-b", _env_file=None)

    assert config.scopes == ["scope-a", "scope-b"]


def test_google_auth_config_requires_service_account_path(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_google_auth_env(monkeypatch)
    with pytest.raises(ValidationError):
        GoogleAuthConfig(auth_mode=GoogleAuthMode.SERVICE_ACCOUNT, _env_file=None)


def test_google_auth_config_requires_oauth_secrets_for_user_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_google_auth_env(monkeypatch)
    with pytest.raises(ValidationError):
        GoogleAuthConfig(auth_mode=GoogleAuthMode.USER_OAUTH, _env_file=None)


def test_google_auth_config_to_auth_returns_google_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """to_auth() should return a GoogleAuth when ADC call is mocked."""
    _clear_google_auth_env(monkeypatch)

    class DummyCreds:
        valid = True
        token = "token"
        requires_scopes = False

    monkeypatch.setattr(
        "fileroute.auth.google.google.auth.default",
        lambda scopes: (DummyCreds(), "project"),
    )

    config = GoogleAuthConfig(auth_mode=GoogleAuthMode.ADC, _env_file=None)
    auth = config.to_auth()

    assert isinstance(auth, GoogleAuth)


def test_google_auth_config_builds_user_oauth_auth_with_token_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """to_auth() for USER_OAUTH triggers the OAuth flow and returns GoogleAuth."""
    _clear_google_auth_env(monkeypatch)

    class DummyCreds:
        valid = True
        token = "token"

    class DummyFlow:
        def run_local_server(self, port=0):
            return DummyCreds()

    monkeypatch.setattr(
        "fileroute.auth.google.InstalledAppFlow.from_client_secrets_file",
        lambda *a, **kw: DummyFlow(),
    )

    # No oauth_token_path: token storage is skipped, flow just returns creds
    config = GoogleAuthConfig(
        auth_mode=GoogleAuthMode.USER_OAUTH,
        oauth_client_secrets=Path("oauth-client.json"),
        scopes=["scope-a"],
        use_local_server=True,
        _env_file=None,
    )

    auth = config.to_auth()

    assert isinstance(auth, GoogleAuth)
