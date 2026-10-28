from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.models import Location, ServiceType


runner = CliRunner()


def configure_s3(config: Path, name: str = "archive") -> None:
    result = runner.invoke(
        app,
        [
            "auth",
            "configure",
            name,
            "--provider",
            "s3",
            "--aws-profile",
            "research",
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output


def test_configure_show_list_and_remove_keep_secret_values_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth import profiles

    monkeypatch.setenv("TEST_SP_SECRET", "never-write-or-display-this-value")
    monkeypatch.setattr(
        profiles,
        "authenticate_profile",
        lambda *args, **kwargs: pytest.fail("configuration must not authenticate"),
    )
    config = tmp_path / "profiles.json"
    result = runner.invoke(
        app,
        [
            "auth",
            "configure",
            "work",
            "--provider",
            "sharepoint",
            "--tenant-id",
            "tenant",
            "--client-id",
            "app",
            "--host-url",
            "tenant.sharepoint.com",
            "--client-secret-env",
            "TEST_SP_SECRET",
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "never-write-or-display-this-value" not in config.read_text()
    shown = runner.invoke(app, ["auth", "show", "work", "--config", str(config)])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["client_secret_env"] == "TEST_SP_SECRET"
    assert "never-write-or-display-this-value" not in shown.output
    listed = runner.invoke(app, ["auth", "list", "--config", str(config)])
    assert listed.exit_code == 0
    assert "work\tSharePoint\tapp_only" in listed.output
    removed = runner.invoke(app, ["auth", "remove", "work", "--config", str(config)])
    assert removed.exit_code == 0, removed.output
    assert profiles.load_profiles(config_path=config).profiles == {}


def test_configure_requires_explicit_replacement(tmp_path: Path) -> None:
    config = tmp_path / "profiles.json"
    configure_s3(config)
    args = [
        "auth",
        "configure",
        "archive",
        "--provider",
        "s3",
        "--aws-profile",
        "other",
        "--config",
        str(config),
    ]
    duplicate = runner.invoke(app, args)
    assert duplicate.exit_code != 0
    assert "replace" in duplicate.output.lower()
    replaced = runner.invoke(app, [*args, "--replace"])
    assert replaced.exit_code == 0, replaced.output


def test_guided_configuration_prompts_and_saves_without_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth.profiles import get_profile

    config = tmp_path / "profiles.json"
    client_json = tmp_path / "desktop.json"
    client_json.write_text("{}")
    monkeypatch.setattr("fileroute.commands.auth._interactive_terminal", lambda: True)
    result = runner.invoke(
        app,
        ["auth", "configure", "personal", "--config", str(config)],
        input=f"gdrive\nuser_oauth\n{client_json}\n",
    )
    assert result.exit_code == 0, result.output
    profile = get_profile("personal", config_path=config)
    assert profile.provider == ServiceType.GOOGLE_DRIVE
    assert profile.mode == "user_oauth"
    assert "fileroute auth login gdrive --profile personal" in result.output


def test_missing_flags_fail_without_prompt_in_unattended_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("fileroute.commands.auth._interactive_terminal", lambda: False)
    config = tmp_path / "profiles.json"
    result = runner.invoke(
        app,
        [
            "auth",
            "configure",
            "work",
            "--provider",
            "sharepoint",
            "--config",
            str(config),
        ],
    )
    assert result.exit_code != 0
    assert "--tenant-id" in result.output
    assert not config.exists()


@pytest.mark.parametrize(
    ("provider", "extra"),
    [
        ("sharepoint", ["--auth-mode", "delegated"]),
        ("microsoft", ["--host-url", "tenant.sharepoint.com"]),
        ("gdrive", ["--oauth-token-path", "token.json"]),
        ("gdrive", ["--scope", "scope-a"]),
        ("gdrive", ["--no-local-server"]),
    ],
)
def test_profile_login_rejects_one_off_overrides_before_authentication(
    provider: str, extra: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth import profiles

    monkeypatch.setattr(
        profiles,
        "authenticate_profile",
        lambda *args, **kwargs: pytest.fail(
            "conflicting options must not authenticate"
        ),
    )
    result = runner.invoke(
        app,
        [
            "auth",
            "login",
            provider,
            "--profile",
            "work",
            "--config",
            str(tmp_path / "absent.json"),
            *extra,
        ],
    )
    assert result.exit_code != 0
    assert "Use --profile by itself" in result.output


def test_profile_login_enables_interactive_auth_only_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth import profiles

    config = tmp_path / "profiles.json"
    configure_s3(config)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        profiles, "authenticate_profile", lambda name, **kwargs: calls.append(kwargs)
    )
    result = runner.invoke(
        app, ["auth", "login", "s3", "--profile", "archive", "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert calls == [{"config_path": config, "interactive": True}]
    assert "aws sso login" in result.output


def test_check_rejects_wrong_provider_url_before_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth import profiles

    config = tmp_path / "profiles.json"
    configure_s3(config)
    monkeypatch.setattr(
        profiles,
        "build_profile_client",
        lambda *args, **kwargs: pytest.fail(
            "provider mismatch must fail before credentials"
        ),
    )
    result = runner.invoke(
        app,
        [
            "auth",
            "check",
            "--profile",
            "archive",
            "--url",
            "https://drive.google.com/file/d/FILE_ID/view",
            "--config",
            str(config),
        ],
    )
    assert result.exit_code != 0
    assert "does not match" in result.output


def test_check_is_noninteractive_and_does_not_claim_location_or_write_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth import profiles

    config = tmp_path / "profiles.json"
    configure_s3(config)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        profiles, "authenticate_profile", lambda name, **kwargs: calls.append(kwargs)
    )
    result = runner.invoke(
        app, ["auth", "check", "--profile", "archive", "--config", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert calls == [{"config_path": config, "interactive": False}]
    assert "Location access and write permission were not checked" in result.output


def test_location_check_reads_metadata_and_suppresses_provider_error_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth import profiles

    config = tmp_path / "profiles.json"
    configure_s3(config)
    calls: list[str] = []

    class Client:
        def resolve_location(self, location: Location) -> None:
            calls.append(location.path)

    def build(name: str, provider: ServiceType, **kwargs: object) -> Client:
        assert name == "archive"
        assert provider == ServiceType.S3
        assert kwargs["interactive"] is False
        return Client()

    monkeypatch.setattr(profiles, "build_profile_client", build)
    args = [
        "auth",
        "check",
        "--profile",
        "archive",
        "--url",
        "s3://bucket/key",
        "--config",
        str(config),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert calls == ["s3://bucket/key"]
    assert "Write permission was not checked" in result.output

    def fail(_self: object, _location: Location) -> None:
        raise RuntimeError("provider-returned-secret-token")

    monkeypatch.setattr(Client, "resolve_location", fail)
    failed = runner.invoke(app, args)
    assert failed.exit_code != 0
    assert "Check the URL" in failed.output
    assert "provider-returned-secret-token" not in failed.output


def test_profile_errors_produce_actionable_nonzero_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth import profiles

    config = tmp_path / "profiles.json"
    configure_s3(config)

    def fail(*args: object, **kwargs: object) -> None:
        raise profiles.ProfileError(
            "Run the provider login before checking this profile."
        )

    monkeypatch.setattr(profiles, "authenticate_profile", fail)
    result = runner.invoke(
        app, ["auth", "check", "--profile", "archive", "--config", str(config)]
    )
    assert result.exit_code != 0
    assert "Run the provider login" in result.output


def test_legacy_google_login_keeps_one_off_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth.settings import GoogleAuthMode

    captured: dict[str, object] = {}
    authenticated: list[bool] = []

    def settings(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            oauth_token_path=kwargs["oauth_token_path"],
            to_auth=lambda: authenticated.append(True),
        )

    monkeypatch.setattr("fileroute.auth.settings.GoogleAuthConfig", settings)
    client = tmp_path / "client.json"
    token = tmp_path / "token.json"
    result = runner.invoke(
        app,
        [
            "auth",
            "login",
            "gdrive",
            "--oauth-client-secrets",
            str(client),
            "--oauth-token-path",
            str(token),
            "--scope",
            "read-scope",
            "--no-local-server",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured == {
        "auth_mode": GoogleAuthMode.USER_OAUTH,
        "use_local_server": False,
        "oauth_client_secrets": client,
        "oauth_token_path": token,
        "scopes": ["read-scope"],
    }
    assert authenticated == [True]


@pytest.mark.parametrize("command", ["sharepoint", "microsoft"])
def test_legacy_microsoft_login_keeps_one_off_flags(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth.settings import MicrosoftAuthMode

    captured: dict[str, object] = {}
    loaded: list[Path | None] = []

    def settings(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(
            auth_mode=kwargs["auth_mode"],
            host_url=kwargs["host_url"],
            to_auth=lambda: None,
        )

    monkeypatch.setattr("fileroute.auth.settings.MicrosoftAuthConfig", settings)
    monkeypatch.setattr("fileroute.commands.auth.load_env_file", loaded.append)
    env = tmp_path / "custom.env"
    result = runner.invoke(
        app,
        [
            "auth",
            "login",
            command,
            "--auth-mode",
            "delegated",
            "--host-url",
            "tenant.sharepoint.com",
            "--scope",
            "scope-a",
            "--env-file",
            str(env),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured == {
        "auth_mode": MicrosoftAuthMode.DELEGATED,
        "host_url": "tenant.sharepoint.com",
        "scopes": ["scope-a"],
    }
    assert loaded == [env]


def test_configure_relative_credential_path_survives_working_directory_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth.profiles import get_profile

    project = tmp_path / "project"
    project.mkdir()
    private_config = tmp_path / "private" / "profiles.yaml"
    monkeypatch.chdir(project)
    result = runner.invoke(
        app,
        [
            "auth",
            "configure",
            "google",
            "--provider",
            "gdrive",
            "--mode",
            "service_account",
            "--credentials-file",
            "credentials.json",
            "--config",
            str(private_config),
        ],
    )
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(tmp_path)
    assert (
        get_profile("google", config_path=private_config).credentials_file
        == project / "credentials.json"
    )


@pytest.mark.parametrize("url", ["s3://bucket/", "s3://bucket/prefix/"])
def test_s3_location_check_rejects_denied_directory_metadata(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fileroute.auth import profiles
    from fileroute.clients.s3 import S3Client

    config = tmp_path / "profiles.yaml"
    configure_s3(config)
    calls = []

    def denied(**kwargs):
        calls.append(kwargs)
        raise PermissionError("sensitive SDK response")

    sdk = SimpleNamespace(head_bucket=denied, list_objects_v2=denied)
    monkeypatch.setattr(
        profiles, "build_profile_client", lambda *_a, **_kw: S3Client(client=sdk)
    )
    result = runner.invoke(
        app,
        [
            "auth",
            "check",
            "--profile",
            "archive",
            "--url",
            url,
            "--config",
            str(config),
        ],
    )
    assert result.exit_code != 0
    assert calls
    assert "Could not read location metadata" in result.output
    assert "sensitive SDK response" not in result.output
