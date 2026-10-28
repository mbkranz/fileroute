from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.credentials import Credentials as AwsCredentials
from google.oauth2.credentials import Credentials as GoogleCredentials
from pydantic import ValidationError

from fileroute.auth.google import GoogleAuth
from fileroute.auth.profiles import (
    AuthProfile,
    ProfileConfig,
    ProfileError,
    authenticate_profile,
    build_profile_client,
    get_profile,
    load_profiles,
    remove_profile,
    save_profile,
    _profile_cache_root,
)
from fileroute.models import ServiceType


def test_profile_validation_is_pure_and_defaults_provider_modes(monkeypatch):
    monkeypatch.setattr(
        Path, "is_file", lambda _path: pytest.fail("validation read a file")
    )
    monkeypatch.setattr(
        "fileroute.auth.profiles.boto3.Session",
        lambda **_kw: pytest.fail("validation built SDK"),
    )
    assert AuthProfile(provider="gdrive").mode == "adc"
    assert AuthProfile(provider="aws", aws_profile="team").mode == "default"
    profile = AuthProfile(
        provider="microsoft",
        tenant_id="tenant",
        client_id="client",
        host_url="https://team.sharepoint.com/",
        client_secret_env="TEAM_SECRET",
    )
    assert profile.mode == "app_only"
    assert profile.host_url == "team.sharepoint.com"
    assert AuthProfile(
        provider="google", mode="service_account", credentials_file="missing.json"
    ).credentials_file == Path("missing.json")


@pytest.mark.parametrize(
    "options",
    [
        {"provider": "s3", "client_secret": "raw-secret-value"},
        {"provider": "s3", "scopes": ["scope"]},
        {"provider": "gdrive", "mode": "delegated"},
        {"provider": "gdrive", "mode": "service_account"},
        {"provider": "gdrive", "mode": "user_oauth"},
        {"provider": "gdrive", "oauth_client_secrets": "oauth.json"},
        {
            "provider": "sharepoint",
            "mode": "delegated",
            "tenant_id": "tenant",
            "client_id": "client",
            "host_url": "team.sharepoint.com",
            "client_secret_env": "SECRET",
        },
        {
            "provider": "sharepoint",
            "tenant_id": "tenant",
            "client_id": "client",
            "host_url": "team.sharepoint.com",
            "client_secret_env": "raw-secret-value",
        },
    ],
)
def test_profiles_reject_unknown_secret_fields_and_irrelevant_options(options):
    with pytest.raises(ValidationError) as error:
        AuthProfile(**options)
    assert "raw-secret-value" not in str(error.value)


def test_profile_config_rejects_path_traversal_and_unknown_fields():
    with pytest.raises(ValidationError):
        ProfileConfig(profiles={"../other": AuthProfile(provider="s3")})
    with pytest.raises(ValidationError):
        ProfileConfig(raw_secret="secret")


def test_config_directory_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    first = save_profile("one", AuthProfile(provider="s3"))
    assert first.config_path == tmp_path / "xdg" / "fileroute" / "profiles.yaml"
    monkeypatch.setenv("FILEROUTE_CONFIG_DIR", str(tmp_path / "override"))
    second = save_profile("two", AuthProfile(provider="s3"))
    assert second.config_path == tmp_path / "override" / "profiles.yaml"
    explicit = tmp_path / "explicit.yaml"
    assert (
        save_profile(
            "three", AuthProfile(provider="s3"), config_path=explicit
        ).config_path
        == explicit
    )


def test_profiles_round_trip_are_private_and_replace_invalidates_tokens(tmp_path):
    path = tmp_path / "private" / "profiles.yaml"
    assert load_profiles(config_path=path).profiles == {}
    original = AuthProfile(provider="s3", aws_profile="team")
    saved = save_profile("team", original, config_path=path)
    assert saved.action == "saved"
    assert get_profile("team", config_path=path) == original
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    cache = _profile_cache_root(path, "team") / "old-generation" / "token.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("cached credential")
    with pytest.raises(ProfileError, match="already exists"):
        save_profile("team", AuthProfile(provider="s3"), config_path=path)
    assert cache.exists()
    save_profile("team", AuthProfile(provider="s3"), config_path=path, replace=True)
    assert not cache.exists()
    assert remove_profile("team", config_path=path).action == "removed"
    assert load_profiles(config_path=path).profiles == {}


def test_concurrent_config_updates_do_not_lose_profiles(tmp_path):
    path = tmp_path / "profiles.yaml"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda n: save_profile(
                    f"team{n}", AuthProfile(provider="s3"), config_path=path
                ),
                range(8),
            )
        )
    assert set(load_profiles(config_path=path).profiles) == {
        f"team{n}" for n in range(8)
    }


def test_config_errors_do_not_echo_pasted_secrets(tmp_path):
    path = tmp_path / "profiles.yaml"
    path.write_text(
        "profiles:\n  team:\n    provider: s3\n    secret: accidental-secret-value\n"
    )
    with pytest.raises(ProfileError) as error:
        load_profiles(config_path=path)
    assert "accidental-secret-value" not in str(error.value)


def test_provider_mismatch_fails_before_sdk_or_file_reads(tmp_path, monkeypatch):
    path = tmp_path / "profiles.yaml"
    save_profile(
        "google",
        AuthProfile(
            provider="gdrive", mode="service_account", credentials_file="missing.json"
        ),
        config_path=path,
    )
    monkeypatch.setattr(
        "fileroute.auth.profiles.GoogleAuth.from_service_account",
        lambda *_a, **_kw: pytest.fail("credential auth ran"),
    )
    with pytest.raises(ProfileError, match="location requires S3"):
        build_profile_client("google", ServiceType.S3, config_path=path)


def test_google_profile_uses_explicit_fields_and_private_cache(tmp_path, monkeypatch):
    path = tmp_path / "profiles.yaml"
    (tmp_path / "oauth.json").write_text("{}")
    save_profile(
        "google",
        AuthProfile(
            provider="gdrive",
            mode="user_oauth",
            oauth_client_secrets="oauth.json",
            scopes=["scope-a"],
        ),
        config_path=path,
    )
    captured = {}

    def fake_auth(**kwargs):
        captured.update(kwargs)
        return GoogleAuth(GoogleCredentials(token="token", scopes=kwargs["scopes"]))

    monkeypatch.setenv("GOOGLE_AUTH_MODE", "invalid-legacy-setting")
    monkeypatch.setattr("fileroute.auth.profiles.GoogleAuth.from_user_oauth", fake_auth)
    monkeypatch.setattr(
        "fileroute.clients.googledrive.GoogleDriveClient", lambda **kwargs: kwargs
    )
    build_profile_client("google", ServiceType.GOOGLE_DRIVE, config_path=path)
    assert captured["interactive"] is False
    assert captured["scopes"] == ["scope-a"]
    assert captured["client_secrets_path"] == tmp_path / "oauth.json"
    token = captured["token_path"]
    assert token.name == "google.json"
    assert token.parent.parent == _profile_cache_root(path, "google")


def test_missing_explicit_secret_reference_never_uses_legacy_secret(
    tmp_path, monkeypatch
):
    path = tmp_path / "profiles.yaml"
    save_profile(
        "team",
        AuthProfile(
            provider="sharepoint",
            tenant_id="tenant",
            client_id="client",
            host_url="team.sharepoint.com",
            client_secret_env="TEAM_SECRET",
        ),
        config_path=path,
    )
    monkeypatch.delenv("TEAM_SECRET", raising=False)
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "unrelated-legacy-secret")
    with pytest.raises(ProfileError, match="TEAM_SECRET"):
        build_profile_client("team", ServiceType.SHAREPOINT, config_path=path)


def test_sdk_authentication_failures_are_sanitized(tmp_path, monkeypatch):
    path = tmp_path / "profiles.yaml"
    save_profile("team", AuthProfile(provider="gdrive"), config_path=path)

    def fail(**_kwargs):
        raise RuntimeError("provider dumped access_token=very-secret")

    monkeypatch.setattr("fileroute.auth.profiles.GoogleAuth.from_adc", fail)
    with pytest.raises(ProfileError) as error:
        build_profile_client("team", ServiceType.GOOGLE_DRIVE, config_path=path)
    assert "very-secret" not in str(error.value)


def test_aws_profile_reuses_sdk_session_and_sts_identity(tmp_path, monkeypatch):
    path = tmp_path / "profiles.yaml"
    save_profile(
        "aws", AuthProfile(provider="s3", aws_profile="team-sso"), config_path=path
    )
    captured = []

    def session(**kwargs):
        assert kwargs == {"profile_name": "team-sso"}
        return SimpleNamespace(
            get_credentials=lambda: AwsCredentials("access-key", "secret-key"),
            client=lambda service: (
                captured.append(service)
                or SimpleNamespace(get_caller_identity=lambda: {"Arn": "identity"})
            ),
        )

    monkeypatch.setattr("fileroute.auth.profiles.boto3.Session", session)
    monkeypatch.setattr("fileroute.clients.s3.S3Client", lambda **_kw: object())
    assert authenticate_profile("aws", config_path=path).action == "authenticated"
    assert captured == ["s3", "sts"]


def test_profile_token_caches_are_isolated_by_config_and_generation(
    tmp_path, monkeypatch
):
    from fileroute.auth.token_store import atomic_private_write

    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    oauth_file = tmp_path / "oauth.json"
    oauth_file.write_text('{"installed": {"client_id": "first-client"}}')
    profile = AuthProfile(
        provider="gdrive",
        mode="user_oauth",
        oauth_client_secrets="oauth.json",
        scopes=["scope-a"],
    )
    save_profile("team", profile, config_path=first)
    save_profile("team", profile, config_path=second)
    captured = []

    def fake_auth(**kwargs):
        token = kwargs["token_path"]
        captured.append(token)
        atomic_private_write(token, "old cached identity")
        return GoogleAuth(GoogleCredentials(token="token", scopes=kwargs["scopes"]))

    monkeypatch.setattr("fileroute.auth.profiles.GoogleAuth.from_user_oauth", fake_auth)
    monkeypatch.setattr(
        "fileroute.clients.googledrive.GoogleDriveClient", lambda **_kw: object()
    )
    build_profile_client("team", ServiceType.GOOGLE_DRIVE, config_path=first)
    build_profile_client("team", ServiceType.GOOGLE_DRIVE, config_path=second)
    first_token, second_token = captured
    assert first_token != second_token
    save_profile("team", profile, config_path=first, replace=True)
    assert not first_token.exists()
    assert second_token.exists()
    build_profile_client("team", ServiceType.GOOGLE_DRIVE, config_path=first)
    before_edit = captured[-1]
    oauth_file.write_text('{"installed": {"client_id": "second-client"}}')
    build_profile_client("team", ServiceType.GOOGLE_DRIVE, config_path=first)
    assert captured[-1] != before_edit
    assert captured[-1] != second_token
    remove_profile("team", config_path=first)
    assert not captured[-1].exists()
    assert second_token.exists()


def test_hand_edited_profile_scopes_change_token_generation(tmp_path, monkeypatch):
    from fileroute.auth.token_store import atomic_private_write

    config = tmp_path / "profiles.yaml"
    (tmp_path / "oauth.json").write_text("{}")
    save_profile(
        "team",
        AuthProfile(
            provider="gdrive",
            mode="user_oauth",
            oauth_client_secrets="oauth.json",
            scopes=["scope-read"],
        ),
        config_path=config,
    )
    paths = []

    def fake_auth(**kwargs):
        paths.append(kwargs["token_path"])
        return GoogleAuth(GoogleCredentials(token="token", scopes=kwargs["scopes"]))

    monkeypatch.setattr("fileroute.auth.profiles.GoogleAuth.from_user_oauth", fake_auth)
    monkeypatch.setattr(
        "fileroute.clients.googledrive.GoogleDriveClient", lambda **_kw: object()
    )
    build_profile_client("team", ServiceType.GOOGLE_DRIVE, config_path=config)
    atomic_private_write(
        config, config.read_text().replace("scope-read", "scope-write")
    )
    build_profile_client("team", ServiceType.GOOGLE_DRIVE, config_path=config)
    assert paths[0] != paths[1]


def test_mutated_profile_scopes_are_revalidated_before_saving(tmp_path):
    path = tmp_path / "profiles.yaml"
    save_profile("good", AuthProfile(provider="s3"), config_path=path)
    before = path.read_text()
    profile = AuthProfile(provider="gdrive", scopes=["scope-a"])
    profile.scopes.append("")
    with pytest.raises(ValidationError):
        save_profile("bad", profile, config_path=path)
    assert path.read_text() == before
    assert set(load_profiles(config_path=path).profiles) == {"good"}


@pytest.mark.parametrize("mode", ["adc", "service_account"])
def test_google_profile_refresh_failure_prevents_client_construction(
    tmp_path, monkeypatch, mode
):
    config = tmp_path / "profiles.yaml"
    options = {"provider": "gdrive", "mode": mode}
    if mode == "service_account":
        (tmp_path / "service-account.json").write_text("{}")
        options["credentials_file"] = "service-account.json"
    save_profile("team", AuthProfile(**options), config_path=config)
    credentials = GoogleCredentials(token=None, scopes=["scope-a"])
    refreshes = []

    def fail_refresh(_request):
        refreshes.append(True)
        raise RuntimeError("SDK included access_token=secret-token-value")

    monkeypatch.setattr(credentials, "refresh", fail_refresh)
    constructor = "from_adc" if mode == "adc" else "from_service_account"
    monkeypatch.setattr(
        GoogleAuth, constructor, lambda *_a, **_kw: GoogleAuth(credentials)
    )
    monkeypatch.setattr(
        "fileroute.clients.googledrive.GoogleDriveClient",
        lambda **_kw: pytest.fail("client created before credential validation"),
    )
    with pytest.raises(ProfileError) as error:
        build_profile_client("team", ServiceType.GOOGLE_DRIVE, config_path=config)
    assert refreshes == [True]
    assert "secret-token-value" not in str(error.value)


@pytest.mark.parametrize(
    "credential_state", ["missing", "incomplete", "refresh_failure"]
)
def test_aws_profile_credentials_fail_before_client_creation(
    tmp_path, monkeypatch, credential_state
):
    config = tmp_path / "profiles.yaml"
    save_profile(
        "team", AuthProfile(provider="s3", aws_profile="team"), config_path=config
    )
    credentials = (
        None
        if credential_state == "missing"
        else AwsCredentials(
            "access-key", "" if credential_state == "incomplete" else "secret-key"
        )
    )
    if credential_state == "refresh_failure":

        def fail_refresh():
            raise RuntimeError("SDK included secret-key-value")

        monkeypatch.setattr(credentials, "get_frozen_credentials", fail_refresh)

    def session(**kwargs):
        assert kwargs == {"profile_name": "team"}
        return SimpleNamespace(
            get_credentials=lambda: credentials,
            client=lambda _service: pytest.fail(
                "SDK client created before credential validation"
            ),
        )

    monkeypatch.setattr("fileroute.auth.profiles.boto3.Session", session)
    with pytest.raises(ProfileError) as error:
        build_profile_client("team", ServiceType.S3, config_path=config)
    assert "secret-key-value" not in str(error.value)


def test_expired_aws_session_points_to_native_login_without_sdk_payload(
    tmp_path, monkeypatch
):
    path = tmp_path / "profiles.yaml"
    save_profile(
        "aws", AuthProfile(provider="s3", aws_profile="team-sso"), config_path=path
    )

    def fail(**_kwargs):
        raise RuntimeError("sensitive AWS token payload")

    monkeypatch.setattr("fileroute.auth.profiles.boto3.Session", fail)
    with pytest.raises(ProfileError) as error:
        authenticate_profile("aws", config_path=path)
    assert "aws sso login --profile team-sso" in str(error.value)
    assert "sensitive AWS token payload" not in str(error.value)
