from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shlex
import sys
from typing import Any, Iterator, Optional

from pydantic import ValidationError
import typer

from fileroute.commands.toolkit import examples_epilog, load_env_file
from fileroute.models import Location, ServiceType


def _interactive_terminal() -> bool:
    return sys.stdin.isatty()


def _required_setting(value: str | None, option: str, label: str) -> str:
    if value is not None and value.strip():
        return value
    if not _interactive_terminal():
        raise typer.BadParameter(
            f"Provide {option}; interactive setup needs a terminal."
        )
    return typer.prompt(label)


@contextmanager
def _profile_errors() -> Iterator[None]:
    """Display profile errors without SDK payloads or Pydantic input values."""
    from fileroute.auth.profiles import ProfileError

    try:
        yield
    except ProfileError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, error['loc'])) or 'profile'}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        typer.echo(f"Invalid profile configuration: {details}", err=True)
        raise typer.Exit(code=1) from None
    except OSError:
        typer.echo(
            "Could not access profile configuration. Check the config path and file permissions.",
            err=True,
        )
        raise typer.Exit(code=1) from None


def _profile_login(
    name: str, provider: ServiceType, config: Path | None, *, conflicts: bool
) -> None:
    from fileroute.auth.profiles import authenticate_profile, get_profile

    if conflicts:
        raise typer.BadParameter(
            "Use --profile by itself; change saved settings with 'fileroute auth configure'."
        )
    with _profile_errors():
        profile = get_profile(name, config_path=config)
        if profile.provider != provider:
            raise typer.BadParameter(
                f"Profile '{name}' is for {profile.provider}, not {provider}."
            )
        authenticate_profile(name, config_path=config, interactive=True)
    typer.echo(f"Authentication ready for profile '{name}' ({provider}).")


def _run_microsoft_login(
    auth_mode: Optional[str],
    host_url: Optional[str],
    scope: Optional[list[str]],
    env_file: Optional[Path],
) -> None:
    from fileroute.auth.settings import MicrosoftAuthConfig, MicrosoftAuthMode

    load_env_file(env_file)

    config_kwargs: dict[str, Any] = {}
    if auth_mode is not None:
        config_kwargs["auth_mode"] = MicrosoftAuthMode(auth_mode)
    if host_url is not None:
        config_kwargs["host_url"] = host_url
    if scope is not None:
        config_kwargs["scopes"] = scope

    config = MicrosoftAuthConfig(**config_kwargs)
    config.to_auth()
    typer.echo(
        f"Microsoft login succeeded using {config.auth_mode.value} mode for {config.host_url}"
    )


def register_auth_commands(auth_app: typer.Typer, auth_login_app: typer.Typer) -> None:
    @auth_app.command("configure")
    def auth_configure(
        name: str = typer.Argument(
            ..., help="Name for this reusable authentication profile."
        ),
        provider: Optional[str] = typer.Option(
            None, "--provider", help="Provider: sharepoint, gdrive, or s3."
        ),
        mode: Optional[str] = typer.Option(
            None, "--mode", help="Authentication mode for the selected provider."
        ),
        tenant_id: Optional[str] = typer.Option(None, "--tenant-id"),
        client_id: Optional[str] = typer.Option(None, "--client-id"),
        host_url: Optional[str] = typer.Option(None, "--host-url"),
        client_secret_env: Optional[str] = typer.Option(
            None,
            "--client-secret-env",
            help="Environment variable name containing the secret; never the secret value.",
        ),
        credentials_file: Optional[Path] = typer.Option(
            None, "--credentials-file", help="Google service-account JSON file."
        ),
        oauth_client_secrets: Optional[Path] = typer.Option(
            None,
            "--oauth-client-secrets",
            help="Google installed-app OAuth client JSON file.",
        ),
        aws_profile: Optional[str] = typer.Option(
            None,
            "--aws-profile",
            help="Existing AWS profile name; credentials stay with AWS.",
        ),
        scope: Optional[list[str]] = typer.Option(
            None, "--scope", help="OAuth scope. Repeat for multiple scopes."
        ),
        replace: bool = typer.Option(
            False, "--replace", help="Replace an existing profile's settings."
        ),
        config: Optional[Path] = typer.Option(
            None, "--config", help="Use an explicit profile configuration file."
        ),
    ) -> None:
        """Save a named profile without logging in or storing raw secrets.

        Missing required settings are prompted for in a terminal. Complete flags
        work unattended. Existing profiles require --replace.
        """
        from fileroute.auth.profiles import AuthProfile, save_profile

        guided = provider is None
        provider = _required_setting(
            provider, "--provider", "Provider (sharepoint, gdrive, s3)"
        )
        providers = {
            "sharepoint": ServiceType.SHAREPOINT,
            "microsoft": ServiceType.SHAREPOINT,
            "gdrive": ServiceType.GOOGLE_DRIVE,
            "googledrive": ServiceType.GOOGLE_DRIVE,
            "s3": ServiceType.S3,
        }
        service = providers.get(provider.casefold())
        if service is None:
            raise typer.BadParameter("Choose --provider sharepoint, gdrive, or s3.")
        default_mode = {
            ServiceType.SHAREPOINT: "app_only",
            ServiceType.GOOGLE_DRIVE: "adc",
            ServiceType.S3: "default",
        }[service]
        supported_modes = {
            ServiceType.SHAREPOINT: ("app_only", "delegated"),
            ServiceType.GOOGLE_DRIVE: ("adc", "service_account", "user_oauth"),
            ServiceType.S3: ("default",),
        }[service]
        if mode is None:
            mode = (
                typer.prompt(
                    f"Authentication mode ({', '.join(supported_modes)})",
                    default=default_mode,
                )
                if guided
                else default_mode
            )
        if mode not in supported_modes:
            raise typer.BadParameter(
                f"Choose --mode from {', '.join(supported_modes)} for {provider}."
            )
        if service == ServiceType.SHAREPOINT:
            tenant_id = _required_setting(
                tenant_id, "--tenant-id", "Microsoft tenant ID"
            )
            client_id = _required_setting(
                client_id, "--client-id", "Microsoft application client ID"
            )
            host_url = _required_setting(
                host_url, "--host-url", "SharePoint host (tenant.sharepoint.com)"
            )
            if mode == "app_only":
                client_secret_env = _required_setting(
                    client_secret_env,
                    "--client-secret-env",
                    "Environment variable containing the client secret",
                )
        elif service == ServiceType.GOOGLE_DRIVE:
            if mode == "service_account":
                credentials_file = Path(
                    _required_setting(
                        str(credentials_file) if credentials_file else None,
                        "--credentials-file",
                        "Service-account JSON path",
                    )
                )
            elif mode == "user_oauth":
                oauth_client_secrets = Path(
                    _required_setting(
                        str(oauth_client_secrets) if oauth_client_secrets else None,
                        "--oauth-client-secrets",
                        "OAuth client JSON path",
                    )
                )
        with _profile_errors():
            # CLI file arguments refer to the invocation's working directory.
            # Hand-authored YAML may still use config-relative references.
            if credentials_file is not None:
                credentials_file = credentials_file.expanduser().absolute()
            if oauth_client_secrets is not None:
                oauth_client_secrets = oauth_client_secrets.expanduser().absolute()
            profile = AuthProfile(
                provider=service,
                mode=mode,
                tenant_id=tenant_id,
                client_id=client_id,
                host_url=host_url,
                client_secret_env=client_secret_env,
                credentials_file=credentials_file,
                oauth_client_secrets=oauth_client_secrets,
                aws_profile=aws_profile,
                scopes=scope,
                use_local_server=(
                    True
                    if service == ServiceType.GOOGLE_DRIVE and mode == "user_oauth"
                    else None
                ),
            )
            receipt = save_profile(name, profile, config_path=config, replace=replace)
        typer.echo(
            f"Saved profile '{name}' ({service}, {mode}) to {receipt.config_path}."
        )
        if service == ServiceType.S3:
            typer.echo(
                "Use AWS's native login or credential setup for the selected AWS profile."
            )
        else:
            command = "sharepoint" if service == ServiceType.SHAREPOINT else "gdrive"
            next_command = (
                f"fileroute auth login {command} --profile {shlex.quote(name)}"
            )
            if config is not None:
                next_command += f" --config {shlex.quote(str(config))}"
            typer.echo(f"Next: {next_command}")

    @auth_app.command("list")
    def auth_list(
        config: Optional[Path] = typer.Option(
            None, "--config", help="Profile configuration file."
        ),
    ) -> None:
        """List configured names, providers, and authentication modes."""
        from fileroute.auth.profiles import load_profiles

        with _profile_errors():
            profiles = load_profiles(config_path=config).profiles
        if not profiles:
            typer.echo(
                "No profiles configured. Run 'fileroute auth configure NAME --provider PROVIDER'."
            )
        for name, profile in sorted(profiles.items()):
            typer.echo(f"{name}\t{profile.provider}\t{profile.mode}")

    @auth_app.command("show")
    def auth_show(
        name: str = typer.Argument(...),
        config: Optional[Path] = typer.Option(
            None, "--config", help="Profile configuration file."
        ),
    ) -> None:
        """Show settings and secret references; never read credential contents."""
        from fileroute.auth.profiles import get_profile

        with _profile_errors():
            profile = get_profile(name, config_path=config)
        typer.echo(
            json.dumps(profile.model_dump(mode="json", exclude_none=True), indent=2)
        )

    @auth_app.command("remove")
    def auth_remove(
        name: str = typer.Argument(...),
        config: Optional[Path] = typer.Option(
            None, "--config", help="Profile configuration file."
        ),
    ) -> None:
        """Remove a profile and its Fileroute token cache; leave provider files intact."""
        from fileroute.auth.profiles import remove_profile

        with _profile_errors():
            remove_profile(name, config_path=config)
        typer.echo(f"Removed profile '{name}'.")

    @auth_app.command("check")
    def auth_check(
        profile: str = typer.Option(..., "--profile", help="Saved profile to check."),
        url: Optional[str] = typer.Option(
            None, "--url", help="Optionally read metadata at a provider URL."
        ),
        config: Optional[Path] = typer.Option(
            None, "--config", help="Profile configuration file."
        ),
    ) -> None:
        """Check credentials and optional read access without opening a browser or writing remotely."""
        from fileroute.auth.profiles import (
            authenticate_profile,
            build_profile_client,
            get_profile,
        )
        from fileroute.resolution import parse_location

        with _profile_errors():
            selected = get_profile(profile, config_path=config)
            if url is None:
                authenticate_profile(profile, config_path=config, interactive=False)
            else:
                try:
                    location = parse_location(Location(path=url), required=True)
                except ValueError:
                    raise typer.BadParameter(
                        "Provide a supported remote URL with --url."
                    ) from None
                if location.service_type != selected.provider:
                    raise typer.BadParameter(
                        "The --url provider does not match the selected profile."
                    )
                client = build_profile_client(
                    profile, selected.provider, config_path=config, interactive=False
                )
                try:
                    # Provider resolution verifies directories too; an S3
                    # directory locator alone does not contact the service.
                    client.resolve_location(location)
                except Exception:
                    typer.echo(
                        "Could not read location metadata. Check the URL and this account's read permissions.",
                        err=True,
                    )
                    raise typer.Exit(code=1) from None
        if url is None:
            typer.echo(
                f"Credentials are available for profile '{profile}'. Location access and write permission were not checked."
            )
        else:
            typer.echo(
                f"Read access checked for profile '{profile}'. Write permission was not checked."
            )

    @auth_login_app.command(
        "gdrive",
        epilog=examples_epilog(
            "fileroute auth login gdrive --oauth-client-secrets .google/oauth-credentials.json --oauth-token-path .google/oauth-token.json",
            "fileroute auth login gdrive --scope https://www.googleapis.com/auth/drive.readonly",
        ),
    )
    def auth_login_gdrive(
        oauth_client_secrets: Optional[Path] = typer.Option(
            None,
            "--oauth-client-secrets",
            help="Path to Google OAuth client secrets JSON.",
        ),
        oauth_token_path: Optional[Path] = typer.Option(
            None,
            "--oauth-token-path",
            help="Path to persist the authorized-user token JSON.",
        ),
        scope: Optional[list[str]] = typer.Option(
            None, "--scope", help="OAuth scope. Repeat for multiple scopes."
        ),
        no_local_server: bool = typer.Option(
            False,
            "--no-local-server",
            help="Legacy console-flow request; unsupported by current Google installed-app OAuth.",
        ),
        env_file: Optional[Path] = typer.Option(
            None,
            "--env-file",
            help="Path to .env file for credentials. Defaults to .env in the current directory.",
        ),
        profile: Optional[str] = typer.Option(
            None, "--profile", help="Use a saved Google Drive profile."
        ),
        config: Optional[Path] = typer.Option(
            None, "--config", help="Profile configuration file."
        ),
    ) -> None:
        """Run the Google installed-app OAuth flow and optionally persist a token."""
        from fileroute.auth.settings import GoogleAuthConfig, GoogleAuthMode

        if profile is not None:
            _profile_login(
                profile,
                ServiceType.GOOGLE_DRIVE,
                config,
                conflicts=any((
                    oauth_client_secrets is not None,
                    oauth_token_path is not None,
                    scope is not None,
                    no_local_server,
                    env_file is not None,
                )),
            )
            return
        if config is not None:
            raise typer.BadParameter("--config requires --profile.")
        load_env_file(env_file)

        config_kwargs: dict[str, Any] = {
            "auth_mode": GoogleAuthMode.USER_OAUTH,
            "use_local_server": not no_local_server,
        }
        if oauth_client_secrets is not None:
            config_kwargs["oauth_client_secrets"] = oauth_client_secrets
        if oauth_token_path is not None:
            config_kwargs["oauth_token_path"] = oauth_token_path
        if scope is not None:
            config_kwargs["scopes"] = scope

        config = GoogleAuthConfig(**config_kwargs)
        config.to_auth()

        if config.oauth_token_path is not None:
            typer.echo(
                f"Google Drive login succeeded. Token saved to {config.oauth_token_path}"
            )
        else:
            typer.echo(
                "Google Drive login succeeded. No token path was configured, so credentials are only available for this process."
            )

    @auth_login_app.command(
        "microsoft",
        epilog=examples_epilog(
            "fileroute auth login microsoft",
            "fileroute auth login microsoft --auth-mode delegated",
            "fileroute auth login microsoft --host-url norc.sharepoint.com",
        ),
    )
    def auth_login_microsoft(
        auth_mode: Optional[str] = typer.Option(
            None, "--auth-mode", help="Microsoft auth mode: app_only or delegated."
        ),
        host_url: Optional[str] = typer.Option(
            None,
            "--host-url",
            help="SharePoint host for validating Graph-backed access, for example norc.sharepoint.com.",
        ),
        scope: Optional[list[str]] = typer.Option(
            None, "--scope", help="Microsoft Graph scope. Repeat for multiple scopes."
        ),
        env_file: Optional[Path] = typer.Option(
            None,
            "--env-file",
            help="Path to .env file for credentials. Defaults to .env in the current directory.",
        ),
        profile: Optional[str] = typer.Option(
            None, "--profile", help="Use a saved SharePoint profile."
        ),
        config: Optional[Path] = typer.Option(
            None, "--config", help="Profile configuration file."
        ),
    ) -> None:
        """Validate Microsoft authentication used by SharePoint workflows."""
        if profile is not None:
            _profile_login(
                profile,
                ServiceType.SHAREPOINT,
                config,
                conflicts=any(
                    value is not None
                    for value in (auth_mode, host_url, scope, env_file)
                ),
            )
            return
        if config is not None:
            raise typer.BadParameter("--config requires --profile.")
        _run_microsoft_login(auth_mode, host_url, scope, env_file)

    @auth_login_app.command(
        "sharepoint",
        epilog=examples_epilog(
            "fileroute auth login sharepoint",
            "fileroute auth login sharepoint --auth-mode delegated",
            "fileroute auth login sharepoint --host-url norc.sharepoint.com",
        ),
    )
    def auth_login_sharepoint(
        auth_mode: Optional[str] = typer.Option(
            None,
            "--auth-mode",
            help="Microsoft auth mode for SharePoint: app_only or delegated.",
        ),
        host_url: Optional[str] = typer.Option(
            None, "--host-url", help="SharePoint host, for example norc.sharepoint.com."
        ),
        scope: Optional[list[str]] = typer.Option(
            None, "--scope", help="Microsoft Graph scope. Repeat for multiple scopes."
        ),
        env_file: Optional[Path] = typer.Option(
            None,
            "--env-file",
            help="Path to .env file for credentials. Defaults to .env in the current directory.",
        ),
        profile: Optional[str] = typer.Option(
            None, "--profile", help="Use a saved SharePoint profile."
        ),
        config: Optional[Path] = typer.Option(
            None, "--config", help="Profile configuration file."
        ),
    ) -> None:
        """Validate SharePoint authentication using the configured auth mode."""
        if profile is not None:
            _profile_login(
                profile,
                ServiceType.SHAREPOINT,
                config,
                conflicts=any(
                    value is not None
                    for value in (auth_mode, host_url, scope, env_file)
                ),
            )
            return
        if config is not None:
            raise typer.BadParameter("--config requires --profile.")
        _run_microsoft_login(auth_mode, host_url, scope, env_file)

    @auth_login_app.command("s3")
    def auth_login_s3(
        profile: str = typer.Option(
            ..., "--profile", help="Saved S3 profile to validate."
        ),
        config: Optional[Path] = typer.Option(
            None, "--config", help="Profile configuration file."
        ),
    ) -> None:
        """Use AWS's native credential setup; Fileroute does not collect AWS secrets."""
        _profile_login(profile, ServiceType.S3, config, conflicts=False)
        typer.echo(
            "For AWS SSO, run 'aws sso login --profile AWS_PROFILE' using the profile's aws_profile setting."
        )


__all__ = ["register_auth_commands"]
