from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

import typer

from fileroute.commands.toolkit import (
    DESCRIPTOR_DEFAULT_HELP,
    OutputFormat,
    echo_json,
    examples_epilog,
    parse_field_args,
    prepare_descriptor_path,
)
from fileroute.exceptions import GoogleApiError, GraphApiError
from fileroute.commands.config import active_descriptor, set_active_descriptor
from fileroute.descriptor import load, save, walk, find, resolve
from fileroute.models import Catalog, Resource, normalize_entity_type, Location


def _add_resource_to_descriptor(
    descriptor: Path | str,
    *,
    name: str,
    create_if_missing: bool = False,
    catalog: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    descriptor_path = Path(descriptor)
    entity_name = name.strip()
    if not entity_name:
        raise ValueError("name must be a non-empty string")

    if descriptor_path.exists():
        document = load(str(descriptor_path))
    elif create_if_missing:
        document = Catalog()
    else:
        raise FileNotFoundError(f"Descriptor '{descriptor_path}' does not exist.")

    normalized_name = entity_name.lower()
    top_level_entries = [*document.resources, *document.catalogs]
    if any(
        str(getattr(entry, "name", "") or "").strip().lower() == normalized_name
        for entry in top_level_entries
    ):
        raise ValueError(f"Entity '{entity_name}' already exists in the descriptor")

    if "source" in kwargs:
        kwargs.setdefault("sources", [{"path": kwargs.pop("source")}])
    if "target" in kwargs:
        kwargs.setdefault("targets", [{"path": kwargs.pop("target")}])
    kwargs["name"] = entity_name
    if catalog:
        entry = Catalog.model_validate(kwargs)
        document.catalogs = [*document.catalogs, entry]
    else:
        entry = Resource.model_validate(kwargs)
        document.resources = [*document.resources, entry]

    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    save(document, descriptor_path)
    return entry.model_dump(mode="json", by_alias=True, exclude_unset=True)


def register_descriptor_commands(app: typer.Typer, clone_app: typer.Typer) -> None:

    @app.command(
        "push",
        help="Publish artifact paths to targets; create or replace, never delete.",
    )
    def push_command(
        descriptor: Optional[Path] = typer.Argument(None, help=DESCRIPTOR_DEFAULT_HELP),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="List files without authenticating or writing."
        ),
    ) -> None:
        """Publish artifact paths to targets; create or replace files."""
        from fileroute.transfer import plan_push, push

        try:
            files = plan_push(prepare_descriptor_path(descriptor))
            for file in files:
                target = file.destination
                typer.echo(
                    f"{'Would upload' if dry_run else 'Uploading'} {file.local} -> {target}"
                )
            if not dry_run:
                push(files)
        except (OSError, ValueError, NotImplementedError, GraphApiError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    @app.command("pull")
    def pull_command(
        descriptor: Optional[Path] = typer.Argument(None, help=DESCRIPTOR_DEFAULT_HELP),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Plan without authenticating or writing."
        ),
    ) -> None:
        """Materialize a single remote source into each artifact's path."""
        from fileroute.transfer import plan_pull, pull

        try:
            entries = plan_pull(prepare_descriptor_path(descriptor))
            for entry in entries:
                typer.echo(
                    f"{'Would download' if dry_run else 'Downloading'} {entry.remote} -> {entry.local}"
                )
            if not dry_run:
                pull(entries)
        except (
            OSError,
            ValueError,
            NotImplementedError,
            GoogleApiError,
            GraphApiError,
        ) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    @app.command("resolve")
    def resolve_command(
        descriptor: Optional[Path] = typer.Argument(None, help=DESCRIPTOR_DEFAULT_HELP),
        write: bool = typer.Option(
            False, "--write", help="Save resolved metadata back to this descriptor."
        ),
        online: bool = typer.Option(
            False, "--online", help="Verify and enrich remote locations using provider credentials."
        ),
    ) -> None:
        """Resolve locators offline by default; --online verifies IDs and types."""
        try:
            path = prepare_descriptor_path(descriptor)
            # Editing one document never rewrites or expands referenced files.
            catalog = resolve(load(path), online=online)
            if write:
                save(catalog, path)
                typer.echo(f"Resolved descriptor: {path}")
            else:
                echo_json(
                    catalog.model_dump(mode="json", by_alias=True, exclude_unset=True)
                )
        except (OSError, ValueError, GoogleApiError, GraphApiError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    @app.command(
        "migrate",
        help="Convert a legacy descriptor to path/sources/targets in a new file.",
        epilog=examples_epilog(
            "fileroute migrate old.yaml new.yaml --direction pull",
            "fileroute migrate old.yaml new.yaml --direction push",
        ),
    )
    def migrate_command(
        descriptor: Path = typer.Argument(..., help="Legacy descriptor to read."),
        output: Path = typer.Argument(..., help="New canonical descriptor to write."),
        direction: str = typer.Option(
            "pull",
            "--direction",
            help="Interpret legacy remote URLs as pull sources or push targets.",
        ),
    ) -> None:
        """Convert a legacy descriptor without overwriting its input."""
        if direction not in {"pull", "push"}:
            raise typer.BadParameter("--direction must be pull or push")
        if output.exists() or output.resolve() == descriptor.resolve():
            raise typer.BadParameter(
                "Choose a new output file; migrate does not overwrite files"
            )
        try:
            from fileroute.migration import migrate_descriptor

            save(migrate_descriptor(descriptor, direction=direction), output)
        except (OSError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"Migrated {descriptor} -> {output}")

    @clone_app.command(
        "descriptor",
        epilog=examples_epilog(
            "fileroute clone descriptor resources/descriptor-copy.yaml --descriptor resources/descriptor.yaml",
            "fileroute clone descriptor resources/descriptor-copy.json --descriptor resources/descriptor.yaml --dry-run",
        ),
    )
    def clone_descriptor(
        target_path: Path = typer.Argument(
            ..., help="Target descriptor path for the clone."
        ),
        descriptor: Optional[Path] = typer.Option(
            None, "--descriptor", help=DESCRIPTOR_DEFAULT_HELP
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show what would be cloned without writing files."
        ),
        force: bool = typer.Option(
            False, "--force", help="Overwrite an existing target descriptor."
        ),
    ) -> None:
        """Clone one descriptor file to a new local path."""
        source_descriptor = prepare_descriptor_path(descriptor)
        if source_descriptor.resolve() == target_path.resolve():
            raise typer.BadParameter("Source and target descriptor paths must differ.")
        if target_path.exists() and not force:
            raise typer.BadParameter(
                f"Refusing to overwrite existing descriptor without --force: {target_path}"
            )

        if dry_run:
            typer.echo(f"Would clone descriptor: {source_descriptor} -> {target_path}")
            return

        # Validate the source, then copy its authored fields without the model's
        # exclude-defaults serialization dropping $schema or empty collections.
        load(str(source_descriptor))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_descriptor.suffix.lower() == target_path.suffix.lower():
            shutil.copyfile(source_descriptor, target_path)
        else:
            save(load(source_descriptor), target_path)
        typer.echo(f"Cloned descriptor: {source_descriptor} -> {target_path}")

    @app.command(
        "update",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
        epilog=examples_epilog(
            'fileroute update --title "Hello" --description "hello"',
            'fileroute update --name file1 --title "Hello" --description "hello"',
            'fileroute update --descriptor resources/descriptor.yaml --name file1 --title "Hello"',
        ),
    )
    def update_command(
        ctx: typer.Context,
        descriptor: Optional[Path] = typer.Option(
            None, "--descriptor", help=DESCRIPTOR_DEFAULT_HELP
        ),
        name: Optional[str] = typer.Option(
            None, "--name", help="Exact entity name or dot-path to update."
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show what would be updated without writing files."
        ),
    ) -> None:
        """Update descriptor-root or resource properties using flag-style field edits."""
        parsed = parse_field_args(list(ctx.args))
        if not parsed:
            raise typer.BadParameter("Provide one or more field values to update.")

        descriptor_path = prepare_descriptor_path(descriptor)

        document = load(descriptor_path)
        target_label = str(descriptor_path)
        target = document
        if name is not None:
            try:
                target = find(document, name)
            except ValueError as exc:
                raise typer.BadParameter(str(exc)) from exc
            target_label = f"{name} in {descriptor_path}"

        changed_properties: list[str] = []
        for property_name, raw_value in parsed.items():
            property_path = {
                "service-type": "service_type",
                "serviceType": "service_type",
                "entity-type": "entity_type",
                "entityType": "entity_type",
                "$schema": "profile",
            }.get(property_name, property_name)
            value = raw_value
            if property_path == "service_type":
                value = Location(path="metadata", service_type=value).service_type
            elif property_path == "entity_type":
                value = normalize_entity_type(value)
            field_target = target
            if (
                property_path == "service_type"
                and name is not None
                and getattr(target, "sources", None)
            ):
                sources = target.sources
                if len(sources) != 1:
                    raise typer.BadParameter(
                        "--service-type requires exactly one source for this entity."
                    )
                field_target = sources[0]
            if getattr(field_target, property_path, None) != value:
                setattr(field_target, property_path, value)
                changed_properties.append(
                    f"{property_path} -> {json.dumps(value, default=str)}"
                )

        if not changed_properties:
            typer.echo("No changes needed.")
            return

        document = Catalog.model_validate(
            document.model_dump(by_alias=True, exclude_unset=True, warnings=False)
        )
        if dry_run:
            for change in changed_properties:
                typer.echo(f"Would update {target_label}: {change}")
            return

        save(document, descriptor_path)
        for change in changed_properties:
            typer.echo(f"Updated {target_label}: {change}")

    @app.command(
        "activate",
        epilog=examples_epilog("fileroute activate resources/descriptor.yaml"),
    )
    def activate_command(
        descriptor: Path = typer.Argument(
            ..., help="Descriptor path to activate for later commands."
        ),
    ) -> None:
        """Activate a descriptor for later commands."""
        try:
            descriptor_path = set_active_descriptor(descriptor)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        typer.echo(f"Activated descriptor: {descriptor_path}")

    @app.command(
        "list",
        epilog=examples_epilog(
            "fileroute list",
            "fileroute list resources/descriptor.yaml",
            "fileroute list resources/descriptor.yaml --format json",
        ),
    )
    def list_command(
        descriptor: Optional[Path] = typer.Argument(
            None, exists=False, help=DESCRIPTOR_DEFAULT_HELP
        ),
        output_format: OutputFormat = typer.Option(
            OutputFormat.TEXT, "--format", help="Output format."
        ),
    ) -> None:
        """List local descriptor entities, paths, and source metadata."""
        descriptor_path = prepare_descriptor_path(descriptor)

        try:
            model = load(descriptor_path, resolve_references=True)
            references = list(walk(model))
        except (OSError, ValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

        if output_format == OutputFormat.JSON:
            echo_json({
                "descriptor": descriptor_path.as_posix(),
                "entities": [
                    reference.model.model_dump(
                        mode="json", by_alias=True, exclude_unset=True
                    )
                    for reference in references
                ],
            })
            return

        from rich.console import Console
        from rich.tree import Tree

        root = Tree(descriptor_path.name)
        nodes: dict[str, Any] = {}
        for reference in references:
            item = reference.model
            name = getattr(item, "name", None) or reference.name_path.split(".")[-1]
            parent_path = reference.name_path.rpartition(".")[0]
            parent_node = nodes.get(parent_path, root) if parent_path else root
            label = (
                f"{name} ({reference.entity_type}) "
                f"[dim]{reference.name_path} {reference.json_pointer}[/dim]"
            )
            node = parent_node.add(label)
            nodes[reference.name_path] = node
            details = []
            resource_path = getattr(item, "path", None)
            entity_type = getattr(item, "entity_type", None)
            if resource_path:
                details.append(f"path={resource_path}")
            for field in ("sources", "targets"):
                for location in getattr(item, field, None) or []:
                    details.append(f"{field}={location.path}")
            if entity_type:
                details.append(f"entityType={entity_type}")
            if details:
                node.add("[dim]" + ", ".join(details) + "[/dim]")

        Console().print(root)

    @app.command(
        "add",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
        epilog=examples_epilog(
            "fileroute add my-resource --path downloads/file.csv --source https://drive.google.com/file/d/123...",
            "fileroute add my-folder --catalog --path docs/_output --target https://tenant.sharepoint.com/sites/docs/Published",
        ),
    )
    def add(
        ctx: typer.Context,
        name: str = typer.Argument(
            ..., help="Resource name to store in the descriptor."
        ),
        catalog: bool = typer.Option(
            False, "--catalog", help="Treat as a catalog of resources or a directory."
        ),
        descriptor: Optional[Path] = typer.Option(
            None, "--descriptor", help=DESCRIPTOR_DEFAULT_HELP
        ),
    ) -> None:
        """Add a standards-aligned resource or catalog entry to a descriptor."""
        descriptor_path = prepare_descriptor_path(
            descriptor,
            require_exists=descriptor is not None or (active_descriptor() is not None),
        )
        explicit_descriptor = descriptor is not None or (
            active_descriptor() is not None
        )

        parsed = parse_field_args(list(ctx.args))

        try:
            _add_resource_to_descriptor(
                descriptor=descriptor_path,
                name=name,
                catalog=catalog,
                create_if_missing=not explicit_descriptor,
                **parsed,
            )
        except (
            FileNotFoundError,
            NotImplementedError,
            ValueError,
            GoogleApiError,
            GraphApiError,
        ) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc


__all__ = ["register_descriptor_commands"]
