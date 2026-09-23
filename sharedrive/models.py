from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Annotated, Any, Optional, TypeAlias,  Union,Literal
from urllib.parse import urlparse

import pydantic
from pydantic import AliasChoices, BeforeValidator, Field, GetCoreSchemaHandler,AnyUrl, TypeAdapter
from pydantic_core import core_schema

from dplib.helpers.path import assert_safe_path
from dplib.models import Package, Resource
from dplib.system import Model


CATALOG_PROFILE = "data-package-catalog"

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


def init() -> dict[str, Any]:
    return {"$schema": CATALOG_PROFILE, "resources": [], "packages": [], "catalogs": []}


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
    if "sharepoint.com" in host:
        return "SharePoint"
    if "drive.google.com" in host or "docs.google.com" in host:
        return "GoogleDrive"

    raise NotImplementedError(
        f"Could not infer serviceType from '{normalized}'. "
        "Pass service_type explicitly."
    )


def infer_entity_type(locator: str, *, service_type: str) -> str:
    """Infer whether a locator points at a file, directory, or container."""
    normalized = _require_non_empty(locator, "locator")
    canonical_service_type = normalize_service_type(service_type)
    parsed = urlparse(normalized)
    path = parsed.path or ""
    last_segment = path.rstrip("/").split("/")[-1] if path else ""

    if canonical_service_type == "GoogleDrive":
        return "Directory" if "/folders/" in normalized else "File"

    if canonical_service_type == "S3":
        object_path = parsed.path.lstrip("/")
        if not object_path:
            return "Container"
        return "Directory" if object_path.endswith("/") else "File"

    if canonical_service_type == "SharePoint":
        if normalized.rstrip("/") != normalized:
            return "Directory"
        return "File" if "." in last_segment else "Directory"

    return "File"


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


def resolve_entity_type(
    locator: str,
    *,
    service_type: str,
    entity_type: str | None = None,
) -> str:
    """Return the declared or inferred entity type."""
    if entity_type is None:
        return infer_entity_type(locator, service_type=service_type)

    return normalize_entity_type(entity_type) or "File"


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


def adapter_from_locator(locator: str) -> str:
    """Infer a registry adapter name from a remote locator."""
    return adapter_from_service_type(infer_service_type(locator)) or ""


ServiceId = str
ServiceTypeValue = Annotated[str, BeforeValidator(normalize_service_type)]
EntityTypeValue = Annotated[str, BeforeValidator(normalize_entity_type)]
def resolve_cache_path(cache: str|None, basepath: str|None):
    if cache and basepath:
        assert_safe_path(str(cache), basepath=basepath)
        resolved_cache = str(Path(basepath).joinpath(cache))
    else:
        resolved_cache = cache
    
    return resolved_cache
# ---------------------------------------------------------------------
GDriveKind = Annotated[Literal["drive","file"], BeforeValidator(lambda v: v.replace("drive#", ""))]
GDriveParents = Annotated[list[str], Field(default_factory=list)]
class GDriveApiFile(pydantic.BaseModel,validate_assignment=True):
    
    model_config = pydantic.ConfigDict()
    
    kind: Annotated[GDriveKind, Literal["file"]] = "file"
    id: Optional[str] = None
    name: Optional[str] = None
    mimeType: Optional[str] = None
    parents: GDriveParents
    webViewLink: Optional[str] = None
    driveId: Optional[str] = None
    
    
class GDriveApiDrive(pydantic.BaseModel,validate_assignment=True):
    
    kind: Annotated[GDriveKind, Literal["drive"]] = "drive"
    id: Optional[str] = None
    name: Optional[str] = None

GDriveApiItem = Annotated[Union[GDriveApiFile, GDriveApiDrive],Field(discriminator="kind")]
    
    
    
# Resource / package models
# ---------------------------------------------------------------------


class DriveRemoteResource(Resource):
    """Data Package resource with shared-drive adapter metadata.

    `path` remains the canonical Data Package data locator. `_cache` follows
    the Data Package caching recipe as the local materialized copy location.
    """
    path: AnyUrl = Field(validation_alias=AliasChoices("path", "url"))
    serviceType: Optional[ServiceTypeValue] = None
    serviceId: Optional[str] = None
    entityType: Optional[EntityTypeValue] = None
    cache: Optional[str] = Field(default=None, alias="_cache", validation_alias=AliasChoices("_cache", "cache"))
   
class DriveRemotePackage(Package):
    """Data Package package with shared-drive adapter metadata."""

    accessUrl: AnyUrl = Field(alias="accessURL", validation_alias=AliasChoices("accessURL", "accessUrl", "url"))
    cache: Optional[str] = Field(default=None, alias="_cache", validation_alias=AliasChoices("_cache", "cache"))
    serviceType: Optional[ServiceTypeValue] = None
    serviceId: Optional[str] = None
    entityType: Optional[EntityTypeValue] = None
    
    
  
# ---------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------



class CatalogSelector:
    """Normalized selector for catalog/package/resource lookup.

    Accepts:
    - None
    - a string
    - a comma-separated string
    - an iterable of strings

    None, empty input, or "all" means select everything.
    Otherwise, tokens are comma-split, stripped, and matched case-insensitively
    against an entity's name or dot-path.
    """

    __slots__ = ("tokens", "_lower")

    def __init__(self, raw: str | Iterable[str] | None = None) -> None:
        values = [] if raw is None else ([raw] if isinstance(raw, str) else list(raw))

        if not all(isinstance(value, str) for value in values):
            invalid = sorted(
                {
                    type(value).__name__
                    for value in values
                    if not isinstance(value, str)
                }
            )
            raise TypeError(
                "selector values must be strings, an iterable of strings, or None; "
                f"received invalid value types: {', '.join(invalid)}"
            )

        tokens = frozenset(
            part.strip()
            for value in values
            for part in value.split(",")
            if part.strip()
        )

        self.tokens: frozenset[str] | None = (
            None
            if not tokens or "all" in {token.lower() for token in tokens}
            else tokens
        )
        self._lower: frozenset[str] | None = (
            None
            if self.tokens is None
            else frozenset(token.lower() for token in self.tokens)
        )

    def __bool__(self) -> bool:
        return self.tokens is not None

    def __repr__(self) -> str:
        if not self:
            return f"{type(self).__name__}()"
        return f"{type(self).__name__}({sorted(self.tokens)!r})"

    def matches(self, model: Model, *, path: str | None = None) -> bool:
        """Return True if this selector matches a model name or dot-path."""
        if self._lower is None:
            return True

        candidates = {
            value.lower()
            for value in (path, getattr(model, "name", None))
            if isinstance(value, str) and value
        }
        return bool(candidates & self._lower)

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            lambda value: value if isinstance(value, cls) else cls(value),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda selector: (
                    sorted(selector.tokens)
                    if selector.tokens is not None
                    else None
                )
            ),
        )

# ---------------------------------------------------------------------
# Catalog reference and catalog models
# ---------------------------------------------------------------------

class DriveRemoteCatalog(Model):
    
    name: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    cache: Optional[str] = Field(default=None, alias="_cache", validation_alias=AliasChoices("_cache", "cache"))
    accessUrl: Optional[AnyUrl] = Field(default=None, alias="accessURL", validation_alias=AliasChoices("accessURL", "accessUrl", "url"))
    serviceType: Optional[ServiceTypeValue] = None
    serviceId: Optional[str] = None
    entityType: Optional[EntityTypeValue] = None
    resources: list[DriveRemoteResource] = pydantic.Field(default_factory=list)
    packages: list[DriveRemotePackage] = pydantic.Field(default_factory=list)
    catalogs: list[DriveRemoteCatalog] = pydantic.Field(default_factory=list)

    
DriveResourceChild: TypeAlias = Union["DriveRemoteResource", "Resource"]
DrivePackageChild: TypeAlias = Union["DriveRemotePackage", "Package"]
DriveCatalogChild: TypeAlias = Union["DriveRemoteCatalog", "DriveCatalogReference", "DriveCatalog"]


class DriveReference(Model):
    """ Base class for any named references to other metadata"""
    name: Optional[str] = None
    path: str
    basepath: Optional[str] = pydantic.Field(default=None, exclude=True)
    conformsTo: type[Model] # NOTE: https://www.w3.org/TR/vocab-dcat-3/#Property:record_conforms_to
    def with_basepath(self, basepath: str | None) -> "DriveReference":
        """Return a copy with inherited basepath, without rewriting path."""
        if basepath is None or self.basepath is not None:
            return self

        assert_safe_path(self.path, basepath=basepath)
        return self.model_copy(update={"basepath": basepath})

    def load(self) -> Model:
        """Load this reference as a DriveCatalog."""
        if self.basepath is not None:
            assert_safe_path(self.path, basepath=self.basepath)

        catalog = self.conformsTo.from_path(self.path, basepath=self.basepath)

        if catalog.name is None and self.name is not None:
            catalog.name = self.name
        else:
            raise ValueError(
                f"Loaded catalog from '{self.path}' must have a name, or the reference must have a name"
            )
            
        return catalog
    
class DriveCatalogReference(DriveReference):
    """Unresolved reference to an external DriveCatalog document.

    `path` stays exactly as authored. `basepath` is inherited from the parent
    catalog and used only for resolution/loading.
    """
    conformsTo: type[DriveCatalogChild] = pydantic.Field(default_factory=lambda: DriveCatalog)

       
class DriveCatalog(Model):

    """A registry, library, or folder containing independent data entities."""

    profile: str = pydantic.Field(default=CATALOG_PROFILE, alias="$schema")
    basepath: Optional[str] = pydantic.Field(default=None, exclude=True)
    name: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None

    resources: list[DriveResourceChild] = pydantic.Field(default_factory=list)
    packages: list[DrivePackageChild] = pydantic.Field(default_factory=list)
    catalogs: list[DriveCatalogChild] = pydantic.Field(
        default_factory=list
    )
    def model_post_init(self, _) -> None:
        
        if self.basepath is None:
            return
        
        for resource in self.resources:
            resource.basepath = self.basepath

        for package in self.packages:
            if package.basepath:
                assert_safe_path(package.basepath, basepath=self.basepath)
                package.basepath = self.basepath + "/" + package.basepath
            if isinstance(package, DriveRemotePackage):
                package.cache = resolve_cache_path(package.cache, package.basepath)
            package.model_post_init(None)

        normalized_catalogs = []

        for catalog in self.catalogs:
            if isinstance(catalog, DriveCatalogReference):
                catalog = catalog.with_basepath(self.basepath).load()
            
            if isinstance(catalog, DriveRemoteCatalog):
                catalog.cache = resolve_cache_path(catalog.cache, self.basepath)
            elif isinstance(catalog, DriveCatalog):
                catalog.basepath = self.basepath
            else:
                raise TypeError(
                    f"Expected catalogs to be DriveCatalog, DriveRemoteCatalog, or DriveCatalogReference, "
                    f"got {type(catalog).__name__}"
                )
                
            normalized_catalogs.append(catalog)
            catalog.model_post_init(None)

        self.catalogs = normalized_catalogs

    # ------------------------------------------------------------------
    # Loading / traversal
    # ------------------------------------------------------------------
    def assert_valid_entity_paths(self):
        pass

    def _walk(
        self,
        root: Model,
        prefix: str | None = None,
        *,
        traverse_references: bool = True,
    ) -> Iterator[tuple[str, Model]]:
        """Yield (dot_path, model) for descendants of root.

        DriveCatalogReference objects are yielded as selectable entities.

        When traverse_references=True, references are also loaded and walked
        lazily, without replacing the reference in the parent catalog.
        """
        for collection in ("resources", "packages", "catalogs"):
            for child in getattr(root, collection, []) or []:
                name = getattr(child, "name", None)
                path = f"{prefix}.{name}" if prefix and name else name or prefix

                if path:
                    yield path, child

                if isinstance(child, DriveCatalogReference):
                    if not traverse_references:
                        continue

                    loaded = child.load()

                    # Reference name wins as the traversal prefix. If the
                    # reference is unnamed, fall back to the loaded catalog name.
                    loaded_prefix = path or loaded.name

                    # If the reference was unnamed and the loaded catalog has a
                    # name, expose the loaded catalog itself as selectable.
                    if path is None and loaded.name:
                        yield loaded.name, loaded

                    yield from self._walk(
                        loaded,
                        loaded_prefix,
                        traverse_references=traverse_references,
                    )
                    continue

                yield from self._walk(
                    child,
                    path,
                    traverse_references=traverse_references,
                )

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------


    def _find_in_walk(
        self,
        selector: CatalogSelector,
        expected_class: type[Model],
        rows: Iterable[tuple[str, Model]],
    ) -> Model | None:
        for path, model in rows:
            if selector.matches(model, path=path):
                if isinstance(model,DriveReference):
                    return model.load()
                else:
                    return model

        return None

    def _find(self, name: str, expected_class: type[Model]) -> Model:
        selector = CatalogSelector(name)

        entity_iter = self._walk(self, traverse_references=True)
        found = self._find_in_walk(
            selector,
            expected_class,
            entity_iter,
        )
        if found is not None:
            return found
        else:
            raise ValueError(
                f"{expected_class.__name__} with name '{name}' not found"
            )

    # ------------------------------------------------------------------
    # Public lookup API
    # ------------------------------------------------------------------

    def get_package(self, name: str) -> DrivePackageChild:
        """Get a package by name or dot-path, traversing catalog references lazily."""
        return self._find(name, DrivePackageChild)

    def get_resource(self, name: str) -> DriveResourceChild:
        """Get a resource by name or dot-path, traversing catalog references lazily."""
        return self._find(name, DriveResourceChild)

    def get_catalog(self, name: str) -> DriveCatalog | DriveCatalogReference | DriveRemoteCatalog:
        """Get a catalog by name or dot-path.

        Matching DriveCatalogReference objects are loaded and returned.
        """
        catalog_classes = TypeAdapter(DriveCatalog | DriveCatalogReference | DriveRemoteCatalog)
        catalog = self._find(name, catalog_classes)
        return catalog
    # ------------------------------------------------------------------
    # Dereferencing
    # ------------------------------------------------------------------

    def dereference(self) -> "DriveCatalog":
        for resource in self.resources:
            resource.dereference()

        for package in self.packages:
            package.dereference()

        resolved_catalogs: list[DriveCatalog] = []

        for catalog in self.catalogs:
            if isinstance(catalog, DriveCatalogReference):
                resolved = catalog.load()
            elif isinstance(catalog, DriveCatalog):
                resolved = catalog
            else:
                raise TypeError(
                    f"Expected catalogs to be DriveCatalog or DriveCatalogReference, "
                    f"got {type(catalog).__name__}"
                )
            resolved.dereference()
            resolved_catalogs.append(resolved)

        self.catalogs = resolved_catalogs
        
        return self
    @classmethod
    def from_path_dereferenced(cls, path: str) -> "DriveCatalog":
        return cls.from_path(path).dereference()

DriveRemoteResource.model_rebuild()
DriveRemotePackage.model_rebuild()
DriveCatalogReference.model_rebuild()
DriveRemoteCatalog.model_rebuild()
DriveCatalog.model_rebuild()


__all__ = [
    "CATALOG_PROFILE",
    "ENTITY_TYPE_ALIASES",
    "SERVICE_TYPE_ALIASES",
    "SUPPORTED_SERVICE_TYPES",
    "CatalogSelector",
    "DriveCatalog",
    "DriveCatalogReference",
    "DriveRemotePackage",
    "DriveRemoteResource",
    "EntityTypeValue",
    "ServiceTypeValue",
    "adapter_from_locator",
    "adapter_from_service_type",
    "infer_entity_type",
    "infer_service_type",
    "normalize_entity_type",
    "normalize_service_type",
    "resolve_cache_path",
    "resolve_entity_type",
    "resolve_service_type",
]
