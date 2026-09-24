from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

CATALOG_PROFILE = "sharedrive-catalog"


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


class _Artifact(_Metadata):
    name: str | None = None
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
                    f"Unsupported fields {sorted(legacy)}; use path, sources, targets, resources and catalogs"
                )
        return value


class Resource(_Artifact):
    """A materialized artifact and its provenance/publication locations."""

    path: str = Field(min_length=1)
    format: str | None = None


class CatalogReference(_Metadata):
    """Reference to another local descriptor; descriptor.load owns resolution."""

    name: str | None = None
    model_config = ConfigDict(extra="forbid")
    path: str = Field(alias="$ref")


class Catalog(_Artifact):
    """Nested groups of resources and catalogs.

    Data Package field names remain useful metadata conventions, but this is a
    Sharedrive descriptor, not a full implementation of the Data Package schema.
    Paths are never rewritten on load. Transfer roots are explicit and default
    to cwd; reference paths are always relative to the containing descriptor.
    """

    profile: str = Field(default=CATALOG_PROFILE, alias="$schema")
    resources: list[Resource] = Field(default_factory=list)
    catalogs: list[Catalog | CatalogReference] = Field(default_factory=list)

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


__all__ = [
    "ServiceType",
    "Catalog",
    "Resource",
    "Location",
    "CatalogReference",
    "CATALOG_PROFILE",
    "EntityTypeValue",
    "normalize_entity_type",
]
