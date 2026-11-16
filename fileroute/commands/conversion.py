"""CLI composition for local conversion functions."""

from pathlib import Path
import json
import subprocess
from typing import Optional

import typer

from fileroute.conversion import ConversionRequest, convert_document, render_document


def register_conversion_commands(app: typer.Typer) -> None:
    """Register convert/render under the existing steps command group."""

    @app.command("convert")
    def convert_command(
        input: Path = typer.Option(..., "--input", help="Acquired original document."),
        output: Path = typer.Option(..., "--output", help="Staged Markdown result."),
        template: Optional[Path] = typer.Option(None, "--template"),
        mapping_file: Optional[Path] = typer.Option(None, "--mapping-file"),
        mappings_json: Optional[str] = typer.Option(None, "--mappings-json"),
        data_file: Optional[Path] = typer.Option(None, "--data-file"),
        timeout: float = typer.Option(120, "--timeout", min=0.1),
    ) -> None:
        """Convert with local MarkItDown; optional DOC normalization uses LibreOffice."""
        try:
            request = ConversionRequest(
                source=input,
                output=output,
                template=template,
                mapping_file=mapping_file,
                mappings=json.loads(mappings_json) if mappings_json else {},
                data=json.loads(data_file.read_text(encoding="utf-8"))
                if data_file
                else {},
                timeout_seconds=timeout,
            )
            typer.echo(convert_document(request).model_dump_json())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from error

    @app.command("render")
    def render_command(
        input: Path = typer.Option(..., "--input", help="Structured JSON/YAML input."),
        output: Path = typer.Option(..., "--output"),
        template: Path = typer.Option(..., "--template"),
        mapping_file: Optional[Path] = typer.Option(None, "--mapping-file"),
        mappings_json: Optional[str] = typer.Option(None, "--mappings-json"),
    ) -> None:
        """Render structured inputs with strict Jinja and optional JSON Pointers."""
        try:
            request = ConversionRequest(
                source=input,
                output=output,
                template=template,
                mapping_file=mapping_file,
                mappings=json.loads(mappings_json) if mappings_json else {},
            )
            typer.echo(render_document(request).model_dump_json())
        except (OSError, ValueError) as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from error


__all__ = ["register_conversion_commands"]
