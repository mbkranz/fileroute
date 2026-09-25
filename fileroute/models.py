from __future__ import annotations

from enum import StrEnum
import re
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    PrivateAttr,
    model_validator,
)

CATALOG_PROFILE = "fileroute-catalog"


class ServiceType(StrEnum):
    GOOGLE_DRIVE = "GoogleDrive"
    SHAREPOINT = "SharePoint"
    S3 = "S3"


def _service_alias(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    key = value.strip().casefold().replace("_", "").replace("-", "").replace(" ", "")
    return {
        "google": ServiceType.GOOGLE_DRIVE,
        "drive": ServiceType.GOOGLE_DRIVE,
        "gdrive": ServiceType.GOOGLE_DRIVE,
        **{item.value.casefold(): item for item in ServiceType},
    }.get(key, value)


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


def normalize_entity_type(value: str | None) -> str | None:
    """Normalize OpenMetadata-style drive/storage entity names."""
    if value is None:
        return None

    normalized = _require_non_empty(value, "entityType")
    key = normalized.replace(" ", "").replace(".", "").lower()
    return ENTITY_TYPE_ALIASES.get(key, normalized[:1].upper() + normalized[1:])


ServiceTypeField = Annotated[ServiceType, BeforeValidator(_service_alias)]
EntityTypeValue = Annotated[str, BeforeValidator(normalize_entity_type)]


class _Metadata(BaseModel):
    """Owned metadata boundary; unknown fields survive load/save."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Location(_Metadata):
    """Upstream input or downstream destination, with optional provider metadata.

    ``path`` may be a local provenance file or remote URL. ``serviceType`` uses
    OpenMetadata's drive service vocabulary; it is resolved only for transfers.
    """

    path: str = Field(min_length=1)
    service_type: ServiceTypeField | None = Field(default=None, alias="serviceType")
    service_id: str | None = Field(default=None, alias="serviceId")
    entity_type: EntityTypeValue | None = Field(default=None, alias="entityType")
    remote_path: str | None = Field(default=None, alias="remotePath")
    site: str | None = None
    site_id: str | None = Field(default=None, alias="siteId")
    drive: str | None = None
    drive_id: str | None = Field(default=None, alias="driveId")
    bucket: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_links(cls, value: Any) -> Any:
        if isinstance(value, dict) and {"descriptor", "$ref"} & value.keys():
            raise ValueError(
                "Locations cannot contain descriptor links; use a location path"
            )
        return value


class _Artifact(_Metadata):
    title: str | None = None
    description: str | None = None
    path: str | None = None
    sources: list[Location] = Field(default_factory=list)
    # None inherits catalog targets; [] explicitly disables publication.
    targets: list[Location] | None = None
    entity_type: EntityTypeValue | None = Field(default=None, alias="entityType")

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            legacy = {
                "name",
                "$ref",
                "descriptor",
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
                    f"Unsupported fields {sorted(legacy)}; use keyed resources/catalogs and descriptor links; run fileroute migrate-format for old descriptors"
                )
        return value


class Resource(_Artifact):
    """A materialized artifact and its provenance/publication locations."""

    path: str = Field(min_length=1)
    format: str | None = None


class CatalogLink(BaseModel):
    """Link to a local catalog document, relative to its containing file."""

    model_config = ConfigDict(extra="forbid")
    descriptor: str = Field(min_length=1)

    @field_validator("descriptor")
    @classmethod
    def validate_descriptor(cls, value: str) -> str:
        if not value.strip() or "://" in value or "#" in value:
            raise ValueError(
                "descriptor must be a local file path without a URI fragment"
            )
        return value


NAME_PATTERN = r"[A-Za-z_][A-Za-z0-9_-]*"


def validate_name(name: str) -> str:
    """Registered names are stable map keys; titles hold display text."""
    if not isinstance(name, str) or re.fullmatch(NAME_PATTERN, name) is None:
        raise ValueError(
            f"Invalid registered name {name!r}; use {NAME_PATTERN}; put display text in title"
        )
    return name


class Catalog(_Artifact):
    """Keyed resources and catalogs; paths retain transfer-root semantics.

    Map keys are registered names. Links are recognized before union validation
    so an extensible inline Catalog cannot absorb a malformed descriptor link.
    """

    profile: str = Field(default=CATALOG_PROFILE, alias="$schema")
    resources: dict[str, Resource] = Field(default_factory=dict)
    catalogs: dict[str, Catalog | CatalogLink] = Field(default_factory=dict)
    # Runtime-only origins: expanded pointer -> (source file, source pointer, chain).
    _origins: dict[str, tuple[Path, str, tuple[Path, ...]]] = PrivateAttr(
        default_factory=dict
    )
    _expanded: bool = PrivateAttr(default=False)

    @field_validator("resources", "catalogs", mode="before")
    @classmethod
    def require_mapping(cls, children: Any) -> Any:
        if not isinstance(children, dict):
            raise ValueError(
                "resources/catalogs must be keyed mappings; run fileroute migrate-format INPUT OUTPUT_DIR"
            )
        for key in children:
            validate_name(key)
        return children

    @field_validator("catalogs", mode="before")
    @classmethod
    def identify_links(cls, children: Any) -> Any:
        if not isinstance(children, dict):
            return children
        return {
            key: (
                CatalogLink.model_validate(child)
                if "descriptor" in child
                else Catalog.model_validate(child)
            )
            if isinstance(child, dict)
            else child
            for key, child in children.items()
        }

    @model_validator(mode="after")
    def unique_names(self) -> Catalog:
        seen: set[str] = set()
        for key in [*self.resources, *self.catalogs]:
            validate_name(key)
            folded = key.casefold()
            if folded in seen:
                raise ValueError(f"Duplicate registered name: {key}")
            seen.add(folded)
        return self


__all__ = [
    "ServiceType",
    "Catalog",
    "Resource",
    "Location",
    "CatalogLink",
    "validate_name",
    "CATALOG_PROFILE",
    "EntityTypeValue",
    "normalize_entity_type",
]
