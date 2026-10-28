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
_ARTIFACT_LEGACY_FIELDS = (
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
)


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

    model_config = ConfigDict(
        json_schema_extra={
            "not": {"anyOf": [{"required": [key]} for key in ("descriptor", "$ref")]}
        }
    )

    path: str = Field(min_length=1)
    service_type: ServiceTypeField | None = Field(default=None, alias="serviceType")
    auth_profile: str | None = Field(
        default=None,
        alias="authProfile",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
        strict=True,
        description="Local authentication profile name; credentials stay outside the descriptor.",
    )
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
    def _reject_links(cls, value: Any) -> Any:
        if isinstance(value, dict) and {"descriptor", "$ref"} & value.keys():
            raise ValueError(
                "Locations cannot contain descriptor links; use a location path"
            )
        return value


class _Artifact(_Metadata):
    model_config = ConfigDict(
        json_schema_extra={
            "not": {"anyOf": [{"required": [key]} for key in _ARTIFACT_LEGACY_FIELDS]}
        }
    )

    title: str | None = None
    description: str | None = None
    path: str | None = None
    sources: list[Location] = Field(default_factory=list)
    # None inherits catalog targets; [] explicitly disables publication.
    targets: list[Location] | None = None
    entity_type: EntityTypeValue | None = Field(default=None, alias="entityType")

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            legacy = set(_ARTIFACT_LEGACY_FIELDS) & value.keys()
            if legacy:
                raise ValueError(
                    f"Unsupported fields {sorted(legacy)}; use keyed resources/catalogs and descriptor links; run fileroute migrate for old descriptors"
                )
        return value


class Resource(_Artifact):
    """A materialized artifact and its provenance/publication locations."""

    path: str = Field(min_length=1)
    format: str | None = None
    path_properties: dict[str, Any] = Field(
        default_factory=dict,
        alias="pathProperties",
        description="Named values extracted from the nearest catalog resourcePathTemplate during resolution.",
    )


class CatalogLink(BaseModel):
    """Link to a local catalog document, relative to its containing file."""

    model_config = ConfigDict(extra="forbid")
    descriptor: str = Field(min_length=1)

    @field_validator("descriptor")
    @classmethod
    def _validate_descriptor(cls, value: str) -> str:
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


def _catalog_schema(schema: dict[str, Any]) -> None:
    """Expose the input alias without replacing generated property schemas."""
    schema["not"] = {
        "anyOf": [{"required": [key]} for key in (*_ARTIFACT_LEGACY_FIELDS, "$schema")]
    }
    schema["properties"]["pathTemplate"] = {
        **schema["properties"]["resourcePathTemplate"],
        "description": "Input alias for resourcePathTemplate; saved using the canonical spelling.",
    }


class Catalog(_Artifact):
    """Keyed resources and catalogs; paths retain transfer-root semantics.

    Map keys are registered names. Links are recognized before union validation
    so an extensible inline Catalog cannot absorb a malformed descriptor link.
    """

    model_config = ConfigDict(json_schema_extra=_catalog_schema)

    resource_path_template: str | None = Field(
        default=None,
        alias="resourcePathTemplate",
        pattern=r"\S",
        description="Parse pattern for resource pathProperties, relative to this catalog path unless it is an absolute path or URI. Transfers do not expand it.",
        examples=["{surveyid}/{env}/v{version}/schema.json"],
    )
    profile: str = CATALOG_PROFILE
    resources: dict[str, Resource] = Field(
        default_factory=dict,
        json_schema_extra={"propertyNames": {"pattern": "^" + NAME_PATTERN + "$"}},
    )
    catalogs: dict[str, Catalog | CatalogLink] = Field(
        default_factory=dict,
        json_schema_extra={"propertyNames": {"pattern": "^" + NAME_PATTERN + "$"}},
    )
    # Runtime-only origins: expanded pointer -> (source file, source pointer, chain).
    _origins: dict[str, tuple[Path, str, tuple[Path, ...]]] = PrivateAttr(
        default_factory=dict
    )
    _expanded: bool = PrivateAttr(default=False)

    @model_validator(mode="before")
    @classmethod
    def _reject_schema_key(cls, value: Any) -> Any:
        if isinstance(value, dict) and "$schema" in value:
            raise ValueError(
                "Use 'profile' instead of '$schema' in catalog descriptors"
            )
        if isinstance(value, dict) and "pathTemplate" in value:
            value = dict(value)
            template = value.pop("pathTemplate")
            for spelling in ("resourcePathTemplate", "resource_path_template"):
                if spelling in value and value[spelling] != template:
                    raise ValueError(
                        f"pathTemplate and {spelling} must agree when both are provided"
                    )
            value.pop("resource_path_template", None)
            value["resourcePathTemplate"] = template
        return value

    @field_validator("resources", "catalogs", mode="before")
    @classmethod
    def _require_mapping(cls, children: Any) -> Any:
        if not isinstance(children, dict):
            raise ValueError(
                "resources/catalogs must be keyed mappings; run fileroute migrate INPUT OUTPUT_DIR"
            )
        for key in children:
            validate_name(key)
        return children

    @model_validator(mode="after")
    def _reject_name_collisions(self) -> Catalog:
        seen: set[str] = set()
        for key in [*self.resources, *self.catalogs]:
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
