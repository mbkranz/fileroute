from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from sharedrive.commands.toolkit import DESCRIPTOR_DEFAULT_HELP, prepare_descriptor_path


def register_diagram_command(app: typer.Typer) -> None:
    @app.command(
        "diagram",
        help="Render descriptor sources, artifacts, and targets as an SVG workflow.",
    )
    def diagram_command(
        descriptor: Optional[Path] = typer.Argument(None, help=DESCRIPTOR_DEFAULT_HELP),
        output: Path = typer.Option(
            Path("sharedrive-diagram.svg"),
            "--output",
            "-o",
            help="SVG file to write.",
        ),
    ) -> None:
        """Render the resolved descriptor workflow without authenticating."""

        from sharedrive.diagram import load_graph, render_svg

        try:
            path = prepare_descriptor_path(descriptor)
            destination = render_svg(load_graph(path), output)
        except (OSError, ValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"Rendered descriptor diagram: {destination}")
