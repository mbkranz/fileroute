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
from fileroute.descriptor import (
    load,
    save,
    walk,
    find,
    resolve,
    resolve_selection,
    select as select_entry,
    _pointer_json_path,
)
from fileroute.models import (
    Catalog,
    CatalogLink,
    Resource,
    normalize_entity_type,
    Location,
)


def _add_resource_to_descriptor(
    descriptor: Path | str,
    *,
    name: str,
    create_if_missing: bool = False,
    catalog: bool = False,
    parent: str | None = None,
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

    parent_catalog = find(document, parent) if parent is not None else document
    if not isinstance(parent_catalog, Catalog):
        raise ValueError(f"Parent {parent!r} must select a catalog in this descriptor")
    normalized_name = entity_name.casefold()
    top_level_entries = [*parent_catalog.resources, *parent_catalog.catalogs]
    if any(key.casefold() == normalized_name for key in top_level_entries):
        raise ValueError(f"Entity '{entity_name}' already exists in the descriptor")

    if "source" in kwargs:
        kwargs.setdefault("sources", [{"path": kwargs.pop("source")}])
    if "target" in kwargs:
        kwargs.setdefault("targets", [{"path": kwargs.pop("target")}])
    from fileroute.models import validate_name

    validate_name(entity_name)
    if catalog:
        entry = Catalog.model_validate(kwargs)
        parent_catalog.catalogs[entity_name] = entry
    else:
        entry = Resource.model_validate(kwargs)
        parent_catalog.resources[entity_name] = entry

    # An unnamed parent can make an otherwise distinct name path ambiguous.
    list(walk(document))

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
        select: Optional[str] = typer.Option(
            None,
            "--select",
            help="Registered name or exact address. Use fileroute list for names and selectors.",
        ),
        write: bool = typer.Option(
            False, "--write", help="Save resolved metadata back to this descriptor."
        ),
        online: bool = typer.Option(
            False,
            "--online",
            help="Verify and enrich remote locations using provider credentials.",
        ),
    ) -> None:
        """Resolve locators offline by default; --online verifies IDs and types."""
        try:
            path = prepare_descriptor_path(descriptor)
            if select is not None:
                result = resolve_selection(path, select, online=online, write=write)
                echo_json(result.as_dict())
                return
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
        help="Convert named-list descriptors and $ref links to the keyed format.",
    )
    def migrate_command(
        descriptor: Path = typer.Argument(..., help="Legacy descriptor to read."),
        output: Path = typer.Argument(
            ..., help="New directory for the converted descriptor graph."
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Validate and report outputs without writing."
        ),
    ) -> None:
        """Convert the complete linked descriptor graph without overwriting inputs."""
        from fileroute.migration import migrate

        try:
            echo_json(migrate(descriptor, output, dry_run=dry_run))
        except (OSError, ValueError) as exc:
            raise typer.BadParameter(str(exc)) from exc

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
            "fileroute update --select '$.catalogs.docs.resources.guide' --title 'Hello'",
        ),
    )
    def update_command(
        ctx: typer.Context,
        descriptor: Optional[Path] = typer.Option(
            None, "--descriptor", help=DESCRIPTOR_DEFAULT_HELP
        ),
        name: Optional[str] = typer.Option(
            None,
            "--name",
            "--select",
            help="Entity or location by name, dot-path, JSON Pointer, or exact JSONPath (optional $). Defaults to the descriptor root. Use 'fileroute list' to see exact JSONPaths.",
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
                # list expands descriptor links for inspection, but updates only
                # the selected file. Explain this boundary for copied selectors.
                try:
                    find(load(descriptor_path, resolve_references=True), name)
                except (OSError, ValueError):
                    pass
                else:
                    raise typer.BadParameter(
                        "Selected entry is inside a descriptor link; edit its own descriptor file"
                    ) from exc
                raise typer.BadParameter(str(exc)) from exc
            target_label = f"{name} in {descriptor_path}"

        changed_properties: list[str] = []
        for property_name, raw_value in parsed.items():
            property_path = {
                "service-type": "service_type",
                "serviceType": "service_type",
                "entity-type": "entity_type",
                "entityType": "entity_type",
                "service-id": "service_id",
                "serviceId": "service_id",
                "remote-path": "remote_path",
                "remotePath": "remote_path",
                "site-id": "site_id",
                "siteId": "site_id",
                "drive-id": "drive_id",
                "driveId": "drive_id",
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
            if property_path in {
                "service_type",
                "service_id",
                "remote_path",
                "site",
                "site_id",
                "drive",
                "drive_id",
                "bucket",
            } and not isinstance(field_target, Location):
                raise typer.BadParameter(
                    f"--{property_name} requires a source or target location; "
                    "select it with --select '$.resources.guide.sources[0]'"
                )
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
            "fileroute list --select '$.catalogs.docs.resources.guide'",
        ),
    )
    def list_command(
        descriptor: Optional[Path] = typer.Argument(
            None, exists=False, help=DESCRIPTOR_DEFAULT_HELP
        ),
        output_format: OutputFormat = typer.Option(
            OutputFormat.TEXT, "--format", help="Output format."
        ),
        kind: str = typer.Option(
            "all",
            "--kind",
            help="Filter catalogs or resources: all, catalog, resource.",
        ),
        select: Optional[str] = typer.Option(
            None,
            "--select",
            help="Show one entity, location, or catalog subtree by name, JSON Pointer, or exact JSONPath (optional $). Use 'fileroute list' to see exact JSONPaths.",
        ),
    ) -> None:
        """List registered names, origins, exact selectors and local metadata."""
        descriptor_path = prepare_descriptor_path(descriptor)

        try:
            if kind not in {"all", "catalog", "resource"}:
                raise ValueError("--kind must be all, catalog, or resource")
            model = load(descriptor_path, resolve_references=True)
            references = list(walk(model))
            location_rows = [
                (f"{row.json_pointer}/{field_name}/{index}", location)
                for row in walk(model, include_self=True)
                if not isinstance(row.model, CatalogLink)
                for field_name in ("sources", "targets")
                for index, location in enumerate(getattr(row.model, field_name) or [])
            ]
            if select is not None and select != "$":
                selected = find(model, select)
                if isinstance(selected, Location):
                    references = []
                    location_rows = [
                        (pointer, location)
                        for pointer, location in location_rows
                        if location is selected
                    ]
                else:
                    pointer = next(
                        row.json_pointer for row in references if row.model is selected
                    )
                    references = [
                        row
                        for row in references
                        if row.json_pointer == pointer
                        or row.json_pointer.startswith(pointer + "/")
                    ]
                    location_rows = [
                        (path, location)
                        for path, location in location_rows
                        if path.startswith(pointer + "/")
                    ]
        except (OSError, ValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

        if kind != "all":
            references = [row for row in references if row.entity_type == kind]
        if output_format == OutputFormat.JSON:
            echo_json({
                "descriptor": descriptor_path.as_posix(),
                "entities": [
                    {
                        **reference.model.model_dump(
                            mode="json", by_alias=True, exclude_unset=True
                        ),
                        "name": reference.name,
                        "qualifiedName": reference.name_path,
                        "kind": reference.entity_type,
                        "originDescriptor": str(reference.origin_descriptor),
                        "originSelector": _pointer_json_path(reference.origin_pointer),
                        "referenceDescriptor": (
                            str(reference.origin_descriptor)
                            if not reference.origin_pointer
                            and len(reference.reference_chain) > 1
                            else None
                        ),
                        "serviceTypes": sorted({
                            location.service_type.value
                            for field in ("sources", "targets")
                            for location in (
                                getattr(reference.model, field, None) or []
                            )
                            if location.service_type is not None
                        }),
                    }
                    for reference in references
                ],
                "selectors": [
                    {
                        "jsonPath": row.json_path,
                        "jsonPointer": row.json_pointer,
                        "originDescriptor": str(row.origin_descriptor),
                        "originSelector": _pointer_json_path(row.origin_pointer),
                    }
                    for row in references
                ],
                "locations": [
                    {
                        "jsonPath": _pointer_json_path(pointer),
                        "jsonPointer": pointer,
                        "originDescriptor": str(
                            select_entry(model, pointer).entry.origin_descriptor
                        ),
                        "originSelector": _pointer_json_path(
                            select_entry(model, pointer).origin_pointer
                        ),
                        "location": location.model_dump(
                            mode="json", by_alias=True, exclude_unset=True
                        ),
                    }
                    for pointer, location in location_rows
                ],
            })
            return

        from rich.console import Console
        from rich.tree import Tree

        root = Tree(descriptor_path.name)
        if not references:
            for pointer, location in location_rows:
                root.add(f"{location.path} [dim]{_pointer_json_path(pointer)}[/dim]")
        nodes: dict[str, Any] = {}
        for reference in references:
            item = reference.model
            name = reference.name
            parent_path = reference.name_path.rpartition(".")[0]
            parent_node = nodes.get(parent_path, root) if parent_path else root
            label = (
                f"{name} ({reference.entity_type}) "
                f"[dim]{reference.name_path} {reference.json_path} "
                f"origin={reference.origin_descriptor}:{_pointer_json_path(reference.origin_pointer)}[/dim]"
            )
            node = parent_node.add(label)
            nodes[reference.name_path] = node
            details = []
            resource_path = getattr(item, "path", None) or getattr(
                item, "descriptor", None
            )
            entity_type = getattr(item, "entity_type", None)
            if resource_path:
                details.append(f"path={resource_path}")
            for field in ("sources", "targets"):
                for index, location in enumerate(getattr(item, field, None) or []):
                    pointer = f"{reference.json_pointer}/{field}/{index}"
                    details.append(
                        f"{field}={location.path} ({_pointer_json_path(pointer)})"
                    )
            services = sorted({
                location.service_type.value
                for field in ("sources", "targets")
                for location in (getattr(item, field, None) or [])
                if location.service_type is not None
            })
            if services:
                details.append("services=" + ",".join(services))
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
            "fileroute add my-folder --catalog --path docs/_output --parent '$.catalogs.docs'",
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
        parent: Optional[str] = typer.Option(
            None,
            "--parent",
            help="Catalog parent by name, dot-path, JSON Pointer, or exact JSONPath (optional $); defaults to root. Use 'fileroute list' to see exact JSONPaths.",
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
                parent=parent,
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
