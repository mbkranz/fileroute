from __future__ import annotations

from dotenv import find_dotenv, load_dotenv
import typer

from sharedrive.commands import (
    register_auth_commands,
    register_descriptor_commands,
    register_diagram_command,
)

load_dotenv(find_dotenv(usecwd=True))

app = typer.Typer(
    name="sharedrive",
    help="Shared drive utilities for SharePoint, Google Drive, and S3.",
    rich_markup_mode="markdown",
)
clone_app = typer.Typer(
    help="Clone descriptor state for new local variants.", rich_markup_mode="markdown"
)
auth_app = typer.Typer(help="Authentication helpers.", rich_markup_mode="markdown")
auth_login_app = typer.Typer(
    help="Interactive login commands.", rich_markup_mode="markdown"
)
app.add_typer(auth_app, name="auth")
app.add_typer(clone_app, name="clone")
auth_app.add_typer(auth_login_app, name="login")

register_descriptor_commands(app, clone_app)
register_diagram_command(app)
register_auth_commands(auth_app, auth_login_app)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
