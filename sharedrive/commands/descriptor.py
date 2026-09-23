from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional

import typer
import yaml
from dplib.error import Error

from sharedrive.commands.toolkit import (
    DESCRIPTOR_DEFAULT_HELP,
    OutputFormat,
    echo_json,
    examples_epilog,
    parse_set_args,
    prepare_descriptor_path,
)
from sharedrive.exceptions import GoogleApiError, GraphApiError
from sharedrive.helpers import has_saved_global_descriptor, set_active_descriptor
from sharedrive.models import (
    DriveCatalog,
    DriveRemoteResource,
    normalize_entity_type,
    normalize_service_type,
    resolve_entity_type,
    resolve_service_type,
)


def _read_descriptor(path: Path) -> dict[str, Any]:
    """Read the authored document without stripping defaults or unknown fields."""
    content = path.read_text(encoding="utf-8")
    document = (
        json.loads(content)
        if path.suffix.lower() == ".json"
        else yaml.safe_load(content)
    )
    if not isinstance(document, dict):
        raise ValueError(f"Descriptor '{path}' must contain an object.")
    return document


def _write_descriptor(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".json":
        content = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    else:
        content = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    path.write_text(content, encoding="utf-8")


def _find_descriptor_entity(
    document: dict[str, Any], name: str
) -> tuple[dict[str, Any], str]:
    """Find a unique named entity by its name or dot-path in this document."""
    matches: list[tuple[dict[str, Any], str]] = []

    def walk(parent: dict[str, Any], prefix: str = "") -> None:
        for collection in ("resources", "packages", "catalogs"):
            for child in parent.get(collection, []) or []:
                if not isinstance(child, dict):
                    continue
                child_name = child.get("name")
                path = f"{prefix}.{child_name}" if prefix else child_name
                if isinstance(path, str) and (
                    path.casefold() == name.casefold()
                    or (
                        isinstance(child_name, str)
                        and child_name.casefold() == name.casefold()
                    )
                ):
                    matches.append((child, path))
                walk(child, path if isinstance(path, str) else prefix)

    walk(document)
    if not matches:
        raise typer.BadParameter(f'Entity selector "{name}" was not found.')
    if len(matches) > 1:
        raise typer.BadParameter(
            f'Entity selector "{name}" is ambiguous; use a dot-path.'
        )
    return matches[0]


def _add_resource_to_descriptor(
    descriptor: Path | str,
    *,
    name: str,
    create_if_missing: bool = False,
    catalog: bool = False,
    package: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    descriptor_path = Path(descriptor)
    entity_name = name.strip()
    if not entity_name:
        raise ValueError("name must be a non-empty string")

    if descriptor_path.exists():
        document = DriveCatalog.from_path(str(descriptor_path))
    elif create_if_missing:
        document = DriveCatalog()
    else:
        raise FileNotFoundError(f"Descriptor '{descriptor_path}' does not exist.")

    normalized_name = entity_name.lower()
    top_level_entries = [*document.resources, *document.packages, *document.catalogs]
    if any(
        str(getattr(entry, "name", "") or "").strip().lower() == normalized_name
        for entry in top_level_entries
    ):
        raise ValueError(f"Entity '{entity_name}' already exists in the descriptor")

    # Map legacy aliases
    if "source" in kwargs:
        url = kwargs.pop("source")
        if catalog:
            kwargs.setdefault("accessURL", url)
        else:
            kwargs.setdefault("path", url)
            kwargs.setdefault("cache", url)
    if "access_url" in kwargs:
        kwargs.setdefault("accessURL", kwargs.pop("access_url"))

    url = kwargs.get("accessURL") if catalog else kwargs.get("path")
    if not url:
        raise ValueError(
            "A source URL must be provided via --path, --accessURL, or --source"
        )

    resolved_service_type = resolve_service_type(
        url, service_type=kwargs.get("serviceType")
    )

    kwargs["entityType"] = resolve_entity_type(
        url, service_type=resolved_service_type, entity_type=kwargs.get("entityType")
    )

    kwargs["serviceType"] = resolved_service_type
    kwargs["name"] = entity_name

    if catalog:
        if kwargs["entityType"] not in {"Directory", "Container"}:
            raise ValueError(
                "Catalog entries must use entityType Directory or Container."
            )
        entry = DriveCatalog.model_validate(kwargs)
        document.catalogs.append(entry)
    else:
        if kwargs["entityType"] != "File":
            raise ValueError(
                "Non-file drive entries should be added as catalogs with accessURL."
            )

        # Backward compatibility translation of cache mapped correctly in BaseModel
        if "_cache" not in kwargs and "cache" in kwargs:
            kwargs["_cache"] = kwargs.pop("cache")

        entry = DriveRemoteResource.model_validate(kwargs)
        document.resources.append(entry)

    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    document.to_path(str(descriptor_path))
    return entry.to_dict()


def register_descriptor_commands(app: typer.Typer, clone_app: typer.Typer) -> None:

    @app.command(
        "upload",
        epilog=examples_epilog(
            "sharedrive upload config/sharedrive.yaml --dry-run",
            "sharedrive upload config/sharedrive.yaml",
        ),
    )
    def upload_command(
        descriptor: Optional[Path] = typer.Argument(None, help=DESCRIPTOR_DEFAULT_HELP),
        dry_run: bool = typer.Option(False, "--dry-run", help="List files without authenticating or writing."),
    ) -> None:
        """Publish local descriptor caches; create or replace, never delete."""
        from sharedrive.upload import plan_upload, upload

        try:
            files = plan_upload(prepare_descriptor_path(descriptor))
            for file in files:
                target = file.remote if file.direct_file else f"{file.remote.rstrip('/')}/{file.relative.as_posix()}"
                typer.echo(f"{'Would upload' if dry_run else 'Uploading'} {file.local} -> {target}")
            if not dry_run:
                upload(files)
        except (Error, OSError, ValueError, GraphApiError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    @clone_app.command(
        "descriptor",
        epilog=examples_epilog(
            "sharedrive clone descriptor resources/descriptor-copy.yaml --descriptor resources/descriptor.yaml",
            "sharedrive clone descriptor resources/descriptor-copy.json --descriptor resources/descriptor.yaml --dry-run",
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
        DriveCatalog.from_path(str(source_descriptor))
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_descriptor.suffix.lower() == target_path.suffix.lower():
            shutil.copyfile(source_descriptor, target_path)
        else:
            _write_descriptor(target_path, _read_descriptor(source_descriptor))
        typer.echo(f"Cloned descriptor: {source_descriptor} -> {target_path}")

    @app.command(
        "update",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
        epilog=examples_epilog(
            'sharedrive update --title "Hello" --description "hello"',
            'sharedrive update --name file1 --title "Hello" --description "hello"',
            'sharedrive update --descriptor resources/descriptor.yaml --name file1 --title "Hello"',
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
        parsed = parse_set_args(list(ctx.args))
        if not parsed:
            raise typer.BadParameter("Provide one or more field values to update.")

        descriptor_path = prepare_descriptor_path(descriptor)

        document = _read_descriptor(descriptor_path)
        DriveCatalog.from_dict(document)
        target_label = str(descriptor_path)
        target: dict[str, Any] = document
        if name is not None:
            target, entity_path = _find_descriptor_entity(document, name)
            target_label = f"{entity_path} in {descriptor_path}"

        changed_properties: list[str] = []
        for property_name, raw_value in parsed.items():
            property_path = {
                "service-type": "serviceType",
                "entity-type": "entityType",
                "access-url": "accessURL",
            }.get(property_name, property_name)
            value = raw_value
            if property_path == "serviceType":
                value = normalize_service_type(value)
            elif property_path == "entityType":
                value = normalize_entity_type(value)
            field_target = target
            if (
                property_path == "serviceType"
                and name is not None
                and target.get("sources")
            ):
                sources = target["sources"]
                if len(sources) != 1 or not isinstance(sources[0], dict):
                    raise typer.BadParameter(
                        "--service-type requires exactly one source for this entity."
                    )
                field_target = sources[0]
            if field_target.get(property_path) != value:
                field_target[property_path] = value
                changed_properties.append(
                    f"{property_path} -> {json.dumps(value, default=str)}"
                )

        if not changed_properties:
            typer.echo("No changes needed.")
            return

        if dry_run:
            for change in changed_properties:
                typer.echo(f"Would update {target_label}: {change}")
            return

        DriveCatalog.from_dict(document)
        _write_descriptor(descriptor_path, document)
        for change in changed_properties:
            typer.echo(f"Updated {target_label}: {change}")

    @app.command(
        "checkout",
        epilog=examples_epilog(
            "sharedrive checkout resources/descriptor.yaml",
            "sharedrive checkout resources/descriptor.yaml research",
            "sharedrive checkout resources/descriptor.yaml research.archive",
        ),
    )
    def checkout_command(
        descriptor: Path = typer.Argument(
            ..., help="Descriptor path to activate for later commands."
        ),
        entity: Optional[str] = typer.Argument(
            None,
            help="Entity dot-path within the descriptor to set as the active scope for fetch/download commands.",
        ),
    ) -> None:
        """Activate a descriptor and optionally an entity within it for later commands."""
        try:
            descriptor_path = set_active_descriptor(descriptor, entity=entity)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        if entity and entity.strip():
            typer.echo(f"Checked out entity '{entity.strip()}' in {descriptor_path}")
        else:
            typer.echo(f"Checked out descriptor: {descriptor_path}")

    @app.command(
        "list",
        epilog=examples_epilog(
            "sharedrive list",
            "sharedrive list resources/descriptor.yaml",
            "sharedrive list resources/descriptor.yaml --format json",
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
            model = DriveCatalog.from_path(str(descriptor_path))
            model.assert_valid_entity_paths()
            references = model.iter_entity_paths(include_self=False)
        except (FileNotFoundError, ValueError, Error) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

        if output_format == OutputFormat.JSON:
            echo_json({
                "descriptor": descriptor_path.as_posix(),
                "entities": [reference.model.__dict__ for reference in references],
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
            cache = getattr(item, "cache", None)
            access_url = getattr(item, "accessURL", None)
            service_type = getattr(item, "serviceType", None)
            entity_type = getattr(item, "entityType", None)
            if resource_path:
                details.append(f"path={resource_path}")
            if cache:
                details.append(f"_cache={cache}")
            if access_url:
                details.append(f"accessURL={access_url}")
            if service_type:
                details.append(f"serviceType={service_type}")
            if entity_type:
                details.append(f"entityType={entity_type}")
            if details:
                node.add("[dim]" + ", ".join(details) + "[/dim]")

        Console().print(root)

    @app.command(
        "add",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
        epilog=examples_epilog(
            "sharedrive add my-resource --path https://drive.google.com/file/d/123... --cache downloads/file.csv",
            "sharedrive add my-folder --catalog --accessURL https://drive.google.com/drive/folders/abc...",
        ),
    )
    def add(
        ctx: typer.Context,
        name: str = typer.Argument(
            ..., help="Resource name to store in the descriptor."
        ),
        catalog: bool = typer.Option(
            False, "--catalog", help="Treat as a catalog with accessURL."
        ),
        descriptor: Optional[Path] = typer.Option(
            None, "--descriptor", help=DESCRIPTOR_DEFAULT_HELP
        ),
    ) -> None:
        """Add a standards-aligned resource or catalog entry to a descriptor."""
        descriptor_path = prepare_descriptor_path(
            descriptor,
            require_exists=descriptor is not None or has_saved_global_descriptor(),
        )
        explicit_descriptor = descriptor is not None or has_saved_global_descriptor()

        parsed = parse_set_args(list(ctx.args))

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
