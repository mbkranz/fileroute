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
    name: str | None = None
    description: str | None = None
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
                    f"Unsupported fields {sorted(legacy)}; use named resources/catalogs and descriptor links"
                )
        return value


class Resource(_Artifact):
    """A concrete artifact or an unresolved parameterized artifact location."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    path: str | None = Field(default=None, min_length=1, pattern=r"\S")
    path_template: str | None = Field(
        default=None, alias="pathTemplate", min_length=1, pattern=r"\S"
    )
    format: str | None = None

    @model_validator(mode="after")
    def _check_locator(self) -> Resource:
        validate_name(self.name)
        if (self.path is None) == (self.path_template is None):
            raise ValueError("Resource requires exactly one of path or pathTemplate")
        if (
            self.path is not None
            and not self.path.strip()
            or self.path_template is not None
            and not self.path_template.strip()
        ):
            raise ValueError("Resource path or pathTemplate must be non-empty")
        return self

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        json_schema_extra={
            "not": {"anyOf": [{"required": [key]} for key in _ARTIFACT_LEGACY_FIELDS]},
            "oneOf": [
                {
                    "required": ["path"],
                    "properties": {
                        "path": {"type": "string"},
                        "pathTemplate": {"type": "null"},
                    },
                },
                {
                    "required": ["pathTemplate"],
                    "properties": {
                        "pathTemplate": {"type": "string"},
                        "path": {"type": "null"},
                    },
                },
            ],
        },
    )


class CatalogLink(BaseModel):
    """Link to a local catalog document, relative to its containing file."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    descriptor: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return validate_name(value)

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
    """Registered names are stable selectors; titles hold display text."""
    if not isinstance(name, str) or re.fullmatch(NAME_PATTERN, name) is None:
        raise ValueError(
            f"Invalid registered name {name!r}; use {NAME_PATTERN}; put display text in title"
        )
    return name


class Catalog(_Artifact):
    """Named resources and catalogs with parent-relative directory bases.

    Names are registered identities. Links are recognized before union validation
    so an extensible inline Catalog cannot absorb a malformed descriptor link.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "not": {
                "anyOf": [
                    {"required": [key]}
                    for key in (
                        *_ARTIFACT_LEGACY_FIELDS,
                        "$schema",
                        "pathTemplate",
                        "resourcePathTemplate",
                        "catalogType",
                        "path",
                    )
                ]
            }
        }
    )

    base_path: str | None = Field(
        default=None,
        alias="basePath",
        min_length=1,
        description="Local directory relative to the parent catalog base; omitted inherits that base.",
        examples=["build/resources"],
    )
    name: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_-]*$")
    profile: str = CATALOG_PROFILE
    resources: list[Resource] = Field(default_factory=list)
    catalogs: list[Catalog | CatalogLink] = Field(default_factory=list)
    # Runtime-only origins: expanded pointer -> (source file, source pointer, chain).
    _origins: dict[str, tuple[Path, str, tuple[Path, ...]]] = PrivateAttr(
        default_factory=dict
    )
    _registered_names: dict[str, str] = PrivateAttr(default_factory=dict)
    _expanded: bool = PrivateAttr(default=False)

    @classmethod
    def model_json_schema(cls, **kwargs: Any) -> dict[str, Any]:
        """Require child names in the exported schema while allowing unnamed roots."""
        schema = super().model_json_schema(**kwargs)
        children = schema["$defs"]["Catalog"]["properties"]["catalogs"]["items"]
        children["anyOf"] = [
            {
                "allOf": [
                    {"$ref": "#/$defs/Catalog"},
                    {"required": ["name"], "properties": {"name": {"type": "string"}}},
                ]
            },
            {"$ref": "#/$defs/CatalogLink"},
        ]
        return schema

    @model_validator(mode="before")
    @classmethod
    def _reject_schema_key(cls, value: Any) -> Any:
        if isinstance(value, dict) and "path" in value:
            raise ValueError(
                "Catalog.path has been replaced by parent-relative basePath"
            )
        if isinstance(value, dict) and "$schema" in value:
            raise ValueError(
                "Use 'profile' instead of '$schema' in catalog descriptors"
            )
        if isinstance(value, dict) and "pathTemplate" in value:
            raise ValueError("Place pathTemplate on a named resource")
        if isinstance(value, dict) and "resourcePathTemplate" in value:
            raise ValueError(
                "Place resourcePathTemplate on a named resource as pathTemplate"
            )
        if isinstance(value, dict) and "catalogType" in value:
            raise ValueError("catalogType is not supported")
        if isinstance(value, dict):
            for field in ("resources", "catalogs"):
                if field in value and not isinstance(value[field], list):
                    raise ValueError(f"{field} must be a named list")
            for child in value.get("catalogs", []):
                if (
                    isinstance(child, dict)
                    and "descriptor" in child
                    and set(child) != {"name", "descriptor"}
                ):
                    raise ValueError("Catalog links contain only name and descriptor")
        return value

    @field_validator("name")
    @classmethod
    def _validate_optional_name(cls, value: str | None) -> str | None:
        return validate_name(value) if value is not None else None

    @model_validator(mode="after")
    def _reject_name_collisions(self) -> Catalog:
        seen: set[str] = set()
        for child in [*self.resources, *self.catalogs]:
            if child.name is None:
                raise ValueError("Child catalog requires a name")
            folded = child.name.casefold()
            if folded in seen:
                raise ValueError(f"Duplicate registered name: {child.name}")
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
