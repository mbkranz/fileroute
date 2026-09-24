"""Local descriptor I/O, traversal, lookup and offline URL resolution.

Models contain metadata only. Loading optionally expands references; walking
and resolving metadata never read files or construct provider clients.
"""

from __future__ import annotations

import json
import tempfile
from io import StringIO
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import ScalarString
from fileroute.models import Catalog, CatalogReference, Location, Resource, ServiceType


def _yaml() -> YAML:
    parser = YAML(typ="rt")
    parser.preserve_quotes = True
    parser.indent(mapping=2, sequence=4, offset=2)
    return parser


def _container(value: object) -> object:
    if isinstance(value, dict):
        return CommentedMap()
    if isinstance(value, list):
        return CommentedSeq()
    return None


def _synchronize(authored: object, canonical: object) -> object:
    """Update matching YAML nodes in place so their comments and styles survive."""
    if isinstance(authored, dict) and isinstance(canonical, dict):
        for key in list(authored):
            if key not in canonical:
                del authored[key]
        for key, value in canonical.items():
            if key in authored:
                authored[key] = _synchronize(authored[key], value)
            else:
                authored[key] = _synchronize(_container(value), value)
        return authored
    if isinstance(authored, list) and isinstance(canonical, list):
        for index, value in enumerate(canonical):
            if index < len(authored):
                authored[index] = _synchronize(authored[index], value)
            else:
                authored.append(_synchronize(_container(value), value))
        del authored[len(canonical) :]
        return authored
    if authored == canonical and (
        type(authored) is type(canonical)
        or isinstance(authored, ScalarString)
        and isinstance(canonical, str)
    ):
        return authored
    if isinstance(authored, ScalarString) and isinstance(canonical, str):
        return type(authored)(canonical)
    return canonical


def load(path: Path | str, *, resolve_references: bool = False) -> Catalog:
    """Load YAML/JSON; optionally expand $ref relative to each containing file.

    References stay authored by default, making load/save safe for editing.
    Transfer callers request expansion once, before planning. All reference
    cycle and path checks live here, rather than in models and each transfer.
    """

    def read(target: Path, seen: frozenset[Path]) -> Catalog:
        target = target.resolve()
        if target in seen:
            raise ValueError(f"Cyclic catalog reference: {target}")
        content = target.read_text(encoding="utf-8")
        try:
            data = (
                json.loads(content)
                if target.suffix.lower() == ".json"
                else _yaml().load(content)
            )
        except (ValueError, YAMLError) as exc:
            raise ValueError(f"Invalid descriptor '{target}': {exc}") from exc
        catalog = Catalog.model_validate(data)
        if resolve_references:
            # walk() remains structural: expanding a reference does not cause
            # it to read that document again while visiting its descendants.
            for row in list(walk(catalog, include_self=True)):
                if isinstance(row.model, Catalog):
                    row.model.catalogs = [
                        read_reference(child, target.parent, seen | {target})
                        if isinstance(child, CatalogReference)
                        else child
                        for child in row.model.catalogs
                    ]
        return catalog

    def read_reference(
        reference: CatalogReference, folder: Path, seen: frozenset[Path]
    ) -> Catalog:
        child = read(local_path(reference.path, folder), seen)
        if reference.name is not None:
            child.name = reference.name
        return child

    return read(Path(path), frozenset())


def save(catalog: Catalog, path: Path | str) -> None:
    """Atomically save canonical metadata, including authored defaults/extensions.

    Existing YAML is edited in place where practical, preserving authored
    comments, styles and ordering. Unexpanded $ref entries remain references.
    """
    path = Path(path)
    catalog = catalog.model_copy(deep=True)
    # Appending to a default list does not update Pydantic's fields_set.
    for row in walk(catalog, include_self=True):
        for field in ("sources", "targets", "resources", "catalogs"):
            if getattr(row.model, field, None):
                row.model.model_fields_set.add(field)
    data = catalog.model_dump(mode="json", by_alias=True, exclude_unset=True)
    if path.suffix.lower() == ".json":
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    else:
        parser = _yaml()
        authored = (
            parser.load(path.read_text(encoding="utf-8"))
            if path.exists()
            else CommentedMap()
        )
        if not isinstance(authored, dict):
            raise ValueError(f"Invalid descriptor '{path}': expected a YAML mapping")
        output = StringIO()
        parser.dump(_synchronize(authored, data), output)
        content = output.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if path.exists():
            temporary.chmod(path.stat().st_mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class EntityPath:
    name_path: str
    model: Catalog | Resource | CatalogReference
    json_pointer: str

    @property
    def entity_type(self) -> str:
        return "resource" if isinstance(self.model, Resource) else "catalog"


def walk(catalog: Catalog, *, include_self: bool = False) -> Iterator[EntityPath]:
    """Walk typed metadata only; no file I/O. Reject duplicate named paths."""
    seen: set[str] = set()

    def descend(
        parent: Catalog, prefix: str = "", pointer: str = ""
    ) -> Iterator[EntityPath]:
        for collection in ("resources", "catalogs"):
            for index, child in enumerate(getattr(parent, collection)):
                name_path = ".".join(part for part in (prefix, child.name) if part)
                child_pointer = f"{pointer}/{collection}/{index}"
                if child.name:
                    if name_path.casefold() in seen:
                        raise ValueError(f"Duplicate entity path: {name_path}")
                    seen.add(name_path.casefold())
                yield EntityPath(name_path, child, child_pointer)
                if isinstance(child, Catalog):
                    yield from descend(child, name_path, child_pointer)

    if include_self:
        yield EntityPath("", catalog, "")
    yield from descend(catalog)


def find(
    catalog: Catalog, name: str, *, kind: type[Catalog] | type[Resource] | None = None
) -> Catalog | Resource | CatalogReference:
    """Find one entity by name/dot-path, optionally restricting its model kind."""
    matches = [
        row.model
        for row in walk(catalog)
        if (kind is None or isinstance(row.model, kind))
        and name.casefold()
        in {row.name_path.casefold(), (row.model.name or "").casefold()}
    ]
    if not matches:
        raise ValueError(f'Entity selector "{name}" was not found')
    if len(matches) > 1:
        raise ValueError(f'Entity selector "{name}" is ambiguous; use a dot-path')
    return matches[0]


def local_path(path: str, root: Path, *, reject_symlinks: bool = False) -> Path:
    """Resolve a local artifact inside root; never accept URLs or escapes."""
    if "://" in path:
        raise ValueError(f"Expected a local artifact path, got {path!r}")
    root = root.resolve()
    candidate = root / path
    if reject_symlinks and any(
        p.is_symlink() for p in (candidate, *candidate.parents) if p != root
    ):
        raise ValueError(f"Refusing symlink in path: {candidate}")
    if any(parent.exists() and not parent.is_dir() for parent in candidate.parents):
        raise ValueError(f"Path parent is not a directory: {candidate}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path is outside working directory {root}: {path}")
    return resolved


def _resolve_location(location: Location, *, required: bool) -> None:
    """Infer only recognized URLs; never follow redirects or authenticate."""
    url = urlsplit(location.path)
    host = (url.hostname or "").casefold()
    inferred = None
    if url.scheme == "s3" and host:
        inferred = ServiceType.S3
    elif url.scheme in {"https", "http"} and host:
        if host == "sharepoint.com" or host.endswith(".sharepoint.com"):
            inferred = ServiceType.SHAREPOINT
        elif host in {"drive.google.com", "docs.google.com"}:
            inferred = ServiceType.GOOGLE_DRIVE
    remote = bool(url.scheme in {"s3", "https", "http"} and host)
    if location.service_type is not None:
        if not remote:
            raise ValueError(f"Provider requires a remote URL: {location.path}")
        if inferred is not None and inferred != location.service_type:
            raise ValueError(
                f"serviceType {location.service_type} conflicts with URL {location.path}"
            )
    elif inferred is not None:
        location.service_type = inferred
    if required and location.service_type is None:
        raise ValueError(
            f"Cannot resolve target {location.path!r}; use a supported remote URL and set serviceType explicitly when needed"
        )


def resolve(
    catalog: Catalog, *, direction: Literal["pull", "push"] | None = None
) -> Catalog:
    """Return resolved metadata without mutating input, URLs, files, or references.

    Known source URLs gain serviceType; other provenance remains valid metadata.
    Every target must resolve to a provider. Load external references separately
    before resolving a transfer; descriptor write-back changes only its own file.
    """
    if direction not in {None, "pull", "push"}:
        raise ValueError("direction must be pull or push")
    result = catalog.model_copy(deep=True)
    for row in walk(result, include_self=True):
        if isinstance(row.model, CatalogReference):
            continue
        if direction != "push":
            for location in row.model.sources:
                _resolve_location(location, required=False)
        if direction != "pull":
            for location in row.model.targets or []:
                _resolve_location(location, required=True)
    return result


__all__ = ["load", "save", "walk", "find", "resolve", "EntityPath", "local_path"]
