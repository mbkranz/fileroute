"""Manage and run endpoint sequences in their physical catalog document."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import typer
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from fileroute.commands.toolkit import (
    DESCRIPTOR_DEFAULT_HELP,
    echo_json,
    prepare_descriptor_path,
)
from fileroute.descriptor import find, load, save, select
from fileroute.models import Catalog, Location, Resource
from fileroute.tasks import (
    SequenceSpec,
    StepExecutionError,
    StepsRequest,
    default_markdown_sequence,
    execute_steps,
    resolve_output,
)


def _endpoint(
    descriptor: Path, selector: str
) -> tuple[Catalog, Location, Catalog | Resource, Path, str]:
    expanded = load(descriptor, resolve_references=True)
    selection = select(expanded, selector)
    if not isinstance(selection.model, Location):
        raise ValueError("Select an exact sources[index] or targets[index] endpoint")
    pointer = selection.origin_pointer
    owner_pointer, field, _ = pointer.rsplit("/", 2)
    if field not in {"sources", "targets"}:
        raise ValueError("Steps belong to a source or target endpoint")
    origin = selection.entry.origin_descriptor
    if origin is None:
        raise ValueError("Selected endpoint has no physical origin descriptor")
    document = load(origin)
    location = find(document, pointer, kind=Location)
    owner = find(document, owner_pointer) if owner_pointer else document
    if not isinstance(location, Location) or not isinstance(owner, (Catalog, Resource)):
        raise ValueError("Selected endpoint has no artifact owner")
    return (
        document,
        location,
        owner,
        origin,
        "after_pull" if field == "sources" else "before_push",
    )


def _read_sequence(path: Path) -> SequenceSpec:
    content = path.read_text(encoding="utf-8")
    try:
        value = (
            json.loads(content)
            if path.suffix.lower() == ".json"
            else YAML(typ="safe").load(content)
        )
    except (ValueError, YAMLError) as error:
        raise ValueError(f"Invalid sequence definition {path}: {error}") from error
    return SequenceSpec.model_validate(value)


def register_steps_commands(steps_app: typer.Typer) -> None:
    """Register metadata edits and explicit local hook execution; no provider calls."""

    @steps_app.command(
        "add", help="Add afterPull/beforePush steps to one source/target endpoint."
    )
    def add_command(
        descriptor: Path | None = typer.Argument(None, help=DESCRIPTOR_DEFAULT_HELP),
        select: str = typer.Option(
            ...,
            "--select",
            help="Exact source/target selector, including its list index.",
        ),
        file: Path | None = typer.Option(
            None,
            "--file",
            help="JSON/YAML native sequence definition, with steps/params aliases.",
        ),
        cmd: str | None = typer.Option(
            None,
            "--cmd",
            help="One native cmd step; input/output parameters use runtime paths.",
        ),
        auto: bool = typer.Option(
            False,
            "--auto",
            help="Add the default Markdown conversion to a .md resource source.",
        ),
        replace: bool = typer.Option(
            False,
            "--replace",
            help="Replace an existing sequence at the selected endpoint.",
        ),
    ) -> None:
        try:
            if sum((file is not None, cmd is not None, auto)) != 1:
                raise ValueError("Choose exactly one of --file, --cmd, or --auto")
            path = prepare_descriptor_path(descriptor)
            document, location, owner, origin, attribute = _endpoint(path, select)
            if getattr(location, attribute) is not None and not replace:
                raise ValueError(
                    "Endpoint already has steps; use --replace to replace them"
                )
            if file is not None:
                sequence = _read_sequence(file)
            else:
                if auto and (
                    attribute != "after_pull"
                    or not isinstance(owner, Resource)
                    or Path(owner.path).suffix.lower() != ".md"
                ):
                    raise ValueError(
                        "--auto requires a source of a Markdown (.md) resource"
                    )
                sequence = default_markdown_sequence()
                if cmd is not None:
                    value = sequence.model_dump()
                    value["steps"] = [{"cmd": cmd}]
                    sequence = SequenceSpec.model_validate(value)
            setattr(location, attribute, sequence)
            location.model_fields_set.add(attribute)
            # Validate the complete physical document before its atomic save.
            Catalog.model_validate(
                document.model_dump(by_alias=True, exclude_unset=True)
            )
            save(document, origin)
            echo_json({
                "descriptor": str(origin),
                "selector": select,
                "hook": "afterPull" if attribute == "after_pull" else "beforePush",
                "sequence": sequence.model_dump(),
            })
        except (OSError, ValueError) as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from error

    @steps_app.command(
        "run", help="Run one endpoint's sequence locally; do not download or upload."
    )
    def run_command(
        descriptor: Path | None = typer.Argument(None, help=DESCRIPTOR_DEFAULT_HELP),
        select: str = typer.Option(
            ...,
            "--select",
            help="Exact source/target selector, including its list index.",
        ),
        input: Path = typer.Option(
            ..., "--input", help="Already acquired local input file."
        ),
        output: Path | None = typer.Option(
            None,
            "--output",
            help="Default staging output; native params.output may select another staged filename.",
        ),
        root: Path | None = typer.Option(
            None,
            "--root",
            help="Transfer root and native Poe project directory; defaults to the current directory.",
        ),
        work_dir: Path | None = typer.Option(
            None,
            "--work-dir",
            help="Local staging directory; outputs must remain beneath it.",
        ),
        arg: list[str] | None = typer.Option(
            None,
            "--arg",
            help="Native outer argument token, repeated in order; use --arg=--option for option tokens.",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Resolve sequence/output without running commands or writing files.",
        ),
    ) -> None:
        try:
            path = prepare_descriptor_path(descriptor)
            _, location, owner, origin, attribute = _endpoint(path, select)
            sequence = getattr(location, attribute)
            if sequence is None:
                raise ValueError("The selected endpoint has no steps")
            transfer_root = (root or Path.cwd()).resolve()
            staging = work_dir or transfer_root / "build/fileroute/steps" / uuid4().hex
            if not staging.is_absolute():
                staging = transfer_root / staging
            candidate = output or staging / Path(owner.path or input.name).name
            request = StepsRequest(
                sequence=sequence,
                input=input,
                output=candidate,
                root=transfer_root,
                catalog_dir=origin.parent,
                work_dir=staging,
                arguments=arg or [],
                context={
                    "resource": owner.model_dump(
                        mode="json", by_alias=True, exclude_unset=True
                    ),
                    "location": location.model_dump(
                        mode="json", by_alias=True, exclude_unset=True
                    ),
                    "descriptor": str(origin),
                    "selector": select,
                    "phase": "afterPull" if attribute == "after_pull" else "beforePush",
                },
            )
            if dry_run:
                echo_json({"output": str(resolve_output(request)), "executed": False})
            else:
                result = execute_steps(request)
                echo_json(result.model_dump(mode="json"))
        except StepExecutionError as error:
            if error.result is not None:
                echo_json(error.result.model_dump(mode="json"))
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from error
        except (OSError, ValueError) as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(1) from error


__all__ = ["register_steps_commands"]
