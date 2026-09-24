from __future__ import annotations

from pathlib import Path
from typing import Optional
from enum import StrEnum

import typer

from sharedrive.commands.toolkit import DESCRIPTOR_DEFAULT_HELP, prepare_descriptor_path


class Detail(StrEnum):
    summary = "summary"
    full = "full"


def register_diagram_command(app: typer.Typer) -> None:
    @app.command(
        "diagram",
        help="Render descriptor sources, artifacts, and targets as SVG, HTML, or Markdown.",
    )
    def diagram_command(
        descriptor: Optional[Path] = typer.Argument(None, help=DESCRIPTOR_DEFAULT_HELP),
        output: Path = typer.Option(
            Path("sharedrive-diagram.svg"),
            "--output",
            "-o",
            help="Output .svg, .html, or .md file (Markdown also writes a companion SVG).",
        ),
        detail: Detail = typer.Option(
            Detail.summary,
            help="Metadata exported in HTML/Markdown; SVG remains compact.",
        ),
    ) -> None:
        """Render the resolved descriptor workflow without authenticating."""

        from sharedrive.diagram import load_graph, render_svg
        from sharedrive.diagram_reports import render_html, render_markdown

        try:
            renderers = {
                ".svg": render_svg,
                ".html": render_html,
                ".htm": render_html,
                ".md": render_markdown,
                ".markdown": render_markdown,
            }
            renderer = renderers.get(output.suffix.lower())
            if renderer is None:
                raise ValueError(
                    "Output must end in .svg, .html, .htm, .md, or .markdown."
                )
            path = prepare_descriptor_path(descriptor)
            graph = load_graph(path)
            destination = (
                renderer(graph, output)
                if renderer is render_svg
                else renderer(graph, output, detail=detail.value)
            )
        except (OSError, ValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"Rendered descriptor diagram: {destination}")
