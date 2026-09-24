from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Self, cast
from urllib.parse import urlparse

import pydantic
import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

CATALOG_PROFILE = "sharedrive-catalog"

SERVICE_TYPE_ALIASES = {
    "google": "GoogleDrive",
    "googledrive": "GoogleDrive",
    "google_drive": "GoogleDrive",
    "google-drive": "GoogleDrive",
    "drive": "GoogleDrive",
    "sharepoint": "SharePoint",
    "share_point": "SharePoint",
    "share-point": "SharePoint",
    "s3": "S3",
}
SUPPORTED_SERVICE_TYPES = {"GoogleDrive", "SharePoint", "S3"}

ENTITY_TYPE_ALIASES = {
    "file": "File",
    "object": "File",
    "blob": "File",
    "document": "File",
    "spreadsheet": "File",
    "directory": "Directory",
    "folder": "Directory",
    "container": "Container",
    "bucket": "Container",
}


# ---------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------


def _require_non_empty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string")
    return normalized


def normalize_service_type(value: str | None) -> str | None:
    """Normalize OpenMetadata-style drive/storage service names."""
    if value is None:
        return None

    normalized = _require_non_empty(value, "serviceType")
    key = normalized.replace(" ", "").replace(".", "").lower()
    return SERVICE_TYPE_ALIASES.get(key, normalized)


def normalize_entity_type(value: str | None) -> str | None:
    """Normalize OpenMetadata-style drive/storage entity names."""
    if value is None:
        return None

    normalized = _require_non_empty(value, "entityType")
    key = normalized.replace(" ", "").replace(".", "").lower()
    return ENTITY_TYPE_ALIASES.get(key, normalized[:1].upper() + normalized[1:])


def infer_service_type(locator: str) -> str:
    """Infer a supported serviceType from a remote locator."""
    normalized = _require_non_empty(locator, "locator")
    parsed = urlparse(normalized)
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()

    if scheme == "s3":
        return "S3"
    if host == "sharepoint.com" or host.endswith(".sharepoint.com"):
        return "SharePoint"
    if host in {"drive.google.com", "docs.google.com"}:
        return "GoogleDrive"

    raise NotImplementedError(
        f"Could not infer serviceType from '{normalized}'. "
        "Pass service_type explicitly."
    )


def resolve_service_type(locator: str, service_type: str | None = None) -> str:
    """Return a supported canonical serviceType, inferring it when omitted."""
    normalized = (
        infer_service_type(locator)
        if service_type is None
        else normalize_service_type(service_type)
    )

    if normalized not in SUPPORTED_SERVICE_TYPES:
        raise NotImplementedError(f"Service type '{normalized}' is not implemented.")

    return normalized


def adapter_from_service_type(service_type: str | None) -> str | None:
    """Map supported serviceType values to registry adapter names."""
    normalized = normalize_service_type(service_type)

    if normalized == "GoogleDrive":
        return "googledrive"
    if normalized == "SharePoint":
        return "sharepoint"
    if normalized == "S3":
        return "s3"

    return None


GDriveKind = Annotated[
    Literal["drive", "file"], BeforeValidator(lambda v: v.replace("drive#", ""))
]
GDriveParents = Annotated[list[str], Field(default_factory=list)]


class GDriveApiFile(pydantic.BaseModel, validate_assignment=True):
    model_config = pydantic.ConfigDict()

    kind: Annotated[GDriveKind, Literal["file"]] = "file"
    id: Optional[str] = None
    name: Optional[str] = None
    mimeType: Optional[str] = None
    parents: GDriveParents
    webViewLink: Optional[str] = None
    driveId: Optional[str] = None


class GDriveApiDrive(pydantic.BaseModel, validate_assignment=True):
    kind: Annotated[GDriveKind, Literal["drive"]] = "drive"
    id: Optional[str] = None
    name: Optional[str] = None


ServiceId = str
ServiceTypeValue = Annotated[str, BeforeValidator(normalize_service_type)]
EntityTypeValue = Annotated[str, BeforeValidator(normalize_entity_type)]


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


def read_descriptor(path: Path) -> dict[str, Any]:
    """Read JSON/YAML without discarding authored extension metadata."""
    content = path.read_text(encoding="utf-8")
    try:
        document = (
            json.loads(content)
            if path.suffix.lower() == ".json"
            else yaml.safe_load(content)
        )
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid descriptor '{path}': {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"Descriptor '{path}' must contain an object")
    return document


def write_descriptor(path: Path, document: dict[str, Any]) -> None:
    """Write JSON/YAML, preserving fields but not YAML comments/formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        if path.suffix.lower() == ".json"
        else yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    )
    path.write_text(content, encoding="utf-8")


class DescriptorModel(BaseModel):
    """Owned metadata boundary; unknown fields survive load/save."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )

    def to_path(self, path: str | Path) -> None:
        write_descriptor(Path(path), self.to_dict())

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Self:
        return cls.model_validate(document)


class Location(DescriptorModel):
    """Upstream input or downstream destination, with optional provider metadata.

    ``path`` may be a local provenance file or remote URL. ``serviceType`` uses
    OpenMetadata's drive service vocabulary; it is resolved only for transfers.
    """

    path: str = Field(min_length=1)
    serviceType: ServiceTypeValue | None = None
    serviceId: str | None = None
    entityType: EntityTypeValue | None = None


class Entity(DescriptorModel):
    name: str | None = None
    title: str | None = None
    description: str | None = None
    path: str | None = None
    sources: list[Location] = Field(default_factory=list)
    # None inherits catalog targets; [] explicitly disables publication.
    targets: list[Location] | None = None
    entityType: EntityTypeValue | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            legacy = {
                "_cache",
                "cache",
                "accessURL",
                "accessUrl",
                "packages",
                "serviceType",
                "serviceId",
                "syncTarget",
            } & value.keys()
            if legacy:
                raise ValueError(
                    f"Legacy fields {sorted(legacy)}: run sharedrive migrate first"
                )
        return value


class Resource(Entity):
    """A materialized artifact and its provenance/publication locations."""

    path: str = Field(min_length=1)
    format: str | None = None


class CatalogReference(DescriptorModel):
    """Lazy reference to another local descriptor; resolved beside its document."""

    name: str | None = None
    model_config = ConfigDict(extra="forbid")
    path: str = Field(alias="$ref")
    _basepath: Path = PrivateAttr(default_factory=Path.cwd)

    def load(self) -> Catalog:
        target = local_path(self.path, self._basepath)
        catalog = Catalog.from_path(target)
        if self.name is not None:
            catalog.name = self.name
        return catalog


@dataclass(frozen=True)
class EntityPath:
    name_path: str
    model: Entity | CatalogReference
    json_pointer: str
    entity_type: str


def walk_entities(
    root: Any, prefix: str = "", pointer: str = ""
) -> Iterator[EntityPath]:
    """One structural walker for models and authored mappings; no I/O."""
    for collection in ("resources", "catalogs"):
        children = (
            root.get(collection, [])
            if isinstance(root, dict)
            else getattr(root, collection, [])
        )
        for index, child in enumerate(children or []):
            name = child.get("name") if isinstance(child, dict) else child.name
            name_path = ".".join(part for part in (prefix, name) if part)
            child_pointer = f"{pointer}/{collection}/{index}"
            yield EntityPath(
                name_path,
                child,
                child_pointer,
                "resource" if collection == "resources" else "catalog",
            )
            yield from walk_entities(child, name_path, child_pointer)


class Catalog(Entity):
    """Nested groups of resources and catalogs.

    Data Package field names remain useful metadata conventions, but this is a
    Sharedrive descriptor, not a full implementation of the Data Package schema.
    Paths are never rewritten on load. Transfer roots are explicit and default
    to cwd; reference paths are always relative to the containing descriptor.
    """

    profile: str = Field(default=CATALOG_PROFILE, alias="$schema")
    resources: list[Resource] = Field(default_factory=list)
    catalogs: list[Catalog | CatalogReference] = Field(default_factory=list)
    _origin: Path | None = PrivateAttr(default=None)

    @field_validator("catalogs", mode="before")
    @classmethod
    def identify_references(cls, children: Any) -> Any:
        if not isinstance(children, list):
            return children
        return [
            (
                CatalogReference.model_validate(child)
                if "$ref" in child
                else Catalog.model_validate(child)
            )
            if isinstance(child, dict)
            else child
            for child in children
        ]

    @classmethod
    def from_path(cls, path: str | Path) -> Self:
        target = Path(path).resolve()
        model = cls.from_dict(read_descriptor(target))
        model._origin = target
        for row in walk_entities(model):
            if isinstance(row.model, CatalogReference):
                row.model._basepath = target.parent
        return model

    def iter_entity_paths(
        self,
        *,
        include_self: bool = False,
        traverse_references: bool = True,
        _seen: frozenset[Path] = frozenset(),
    ) -> Iterator[EntityPath]:
        if self._origin is not None:
            if self._origin in _seen:
                raise ValueError(f"Cyclic catalog reference: {self._origin}")
            _seen = _seen | {self._origin}
        if include_self:
            yield EntityPath(self.name or "", self, "", "catalog")
        for row in walk_entities(self):
            if isinstance(row.model, CatalogReference) and traverse_references:
                loaded = row.model.load()
                yield EntityPath(
                    row.name_path or loaded.name or "",
                    loaded,
                    row.json_pointer,
                    "catalog",
                )
                for nested in loaded.iter_entity_paths(_seen=_seen):
                    yield EntityPath(
                        ".".join(
                            p
                            for p in (row.name_path or loaded.name, nested.name_path)
                            if p
                        ),
                        nested.model,
                        row.json_pointer + nested.json_pointer,
                        nested.entity_type,
                    )
            else:
                yield row

    def assert_valid_entity_paths(self) -> None:
        seen: set[str] = set()
        for row in self.iter_entity_paths():
            if not row.name_path:
                continue
            key = row.name_path.casefold()
            if key in seen:
                raise ValueError(f"Duplicate entity path: {row.name_path}")
            seen.add(key)

    def _find(self, name: str, kind: type[Entity]) -> Entity:
        matches = [
            row.model
            for row in self.iter_entity_paths()
            if isinstance(row.model, kind)
            and name.casefold()
            in {row.name_path.casefold(), (row.model.name or "").casefold()}
        ]
        if not matches:
            raise ValueError(f"{kind.__name__} with name {name!r} not found")
        if len(matches) != 1:
            raise ValueError(f"Entity selector {name!r} is ambiguous; use a dot-path")
        return matches[0]

    def get_resource(self, name: str) -> Resource:
        return cast(Resource, self._find(name, Resource))

    def get_catalog(self, name: str) -> Catalog:
        return cast(Catalog, self._find(name, Catalog))

    def dereference(self, _seen: frozenset[Path] = frozenset()) -> Self:
        if self._origin in _seen:
            raise ValueError(f"Cyclic catalog reference: {self._origin}")
        seen = _seen | {self._origin} if self._origin else _seen
        self.catalogs = [
            (
                child.load() if isinstance(child, CatalogReference) else child
            ).dereference(seen)
            for child in self.catalogs
        ]
        return self


__all__ = [
    "Catalog",
    "Resource",
    "Location",
    "CatalogReference",
    "EntityPath",
    "walk_entities",
    "read_descriptor",
    "write_descriptor",
    "local_path",
    "CATALOG_PROFILE",
    "ServiceId",
    "ServiceTypeValue",
    "EntityTypeValue",
    "GDriveApiFile",
    "GDriveApiDrive",
    "normalize_service_type",
    "normalize_entity_type",
    "infer_service_type",
    "resolve_service_type",
    "adapter_from_service_type",
]
