from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import typer

from fileroute.commands.toolkit import examples_epilog, load_env_file


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
            help="Use the console flow instead of a local callback server.",
        ),
        env_file: Optional[Path] = typer.Option(
            None,
            "--env-file",
            help="Path to .env file for credentials. Defaults to .env in the current directory.",
        ),
    ) -> None:
        """Run the Google installed-app OAuth flow and optionally persist a token."""
        from fileroute.auth.settings import GoogleAuthConfig, GoogleAuthMode

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
    ) -> None:
        """Validate Microsoft authentication used by SharePoint workflows."""
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
    ) -> None:
        """Validate SharePoint authentication using the configured auth mode."""
        _run_microsoft_login(auth_mode, host_url, scope, env_file)


__all__ = ["register_auth_commands"]
