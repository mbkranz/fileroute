"""Provider-neutral location resolution and read-only item lookup.

The descriptor retains the authored ``path``. ``remotePath`` and provider
namespace fields are supplemental, so an offline pass remains reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from string import Formatter
import re

from parse import Parser, compile as compile_template

from fileroute.models import Catalog, Location, ServiceType
from fileroute.clients.base import BaseClient
from fileroute.item import ServiceItem


def _compile_template(template: str) -> Parser:
    """Validate format syntax before parse's permissive literal handling."""
    try:
        for _, _, _, conversion in Formatter().parse(template):
            if conversion is not None:
                raise ValueError("field conversions such as !r are unsupported")
        parser = compile_template(template, case_sensitive=True)
        # parse compiles the final regular expression lazily.
        parser.parse("")
        return parser
    except (ValueError, re.error, NotImplementedError) as exc:
        raise ValueError(f"Invalid resourcePathTemplate {template!r}: {exc}") from exc


def _normalized_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _template_path(path: str, template: str, base: str | None) -> str | None:
    """Keep URI/absolute patterns whole; scope relative ones by path components."""
    path = _normalized_path(path)
    if any(part == ".." for part in path.split("/")):
        return None
    if (
        "://" in template
        or template.startswith("/")
        or re.match(r"^[A-Za-z]:/", template)
    ):
        return path
    root = _normalized_path(base or "").rstrip("/")
    if not root or root == ".":
        return path
    if path == root:
        return ""
    prefix = root + "/"
    return path[len(prefix) :] if path.startswith(prefix) else None


def resolve_path_properties(catalog: Catalog) -> None:
    """Derive resource metadata in place without I/O or changing authored paths.

    The nearest declared template applies to descendants, relative to the path
    of the catalog that declares it. Absolute templates match the whole path.
    Discovery captures describe the remote file and take precedence. Local
    templates match the complete path and never generate transfer paths.
    """

    def descend(
        current: Catalog, inherited: tuple[str, str | None, Parser] | None = None
    ) -> None:
        context = inherited
        if current.resource_path_template is not None:
            template = _normalized_path(current.resource_path_template)
            context = (template, current.path, _compile_template(template))
        if context is not None:
            template, base, parser = context
            for resource in current.resources.values():
                if any(
                    "discovery" in (source.model_extra or {})
                    for source in resource.sources
                ):
                    continue
                candidate = _template_path(resource.path, template, base)
                match = parser.parse(candidate) if candidate is not None else None
                resource.path_properties = match.named if match else {}
        for child in current.catalogs.values():
            if isinstance(child, Catalog):
                descend(child, context)

    descend(catalog)


@dataclass(frozen=True)
class ResolvedLocation:
    service_type: ServiceType
    remote_path: str | None = None
    service_id: str | None = None
    entity_type: str | None = None
    context: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_location(cls, location: Location) -> "ResolvedLocation":
        if location.service_type is None:
            raise ValueError(f"Location has no serviceType: {location.path!r}")
        return cls(
            location.service_type,
            location.remote_path,
            location.service_id,
            location.entity_type,
            {
                key: value
                for key in ("site", "site_id", "drive", "drive_id", "bucket")
                if (value := getattr(location, key)) is not None
            },
        )

    def apply(self, location: Location) -> None:
        """Merge known fields, rejecting contradictory authored metadata."""
        values = {
            "service_type": self.service_type,
            "remote_path": self.remote_path,
            "service_id": self.service_id,
            "entity_type": self.entity_type,
            **self.context,
        }
        for field_name, value in values.items():
            if value is None:
                continue
            old = getattr(location, field_name)
            if old is not None and old != value:
                label = location.__class__.model_fields[field_name].alias or field_name
                raise ValueError(
                    f"{label} {old!r} conflicts with resolved value {value!r} for {location.path!r}"
                )
            setattr(location, field_name, value)


def _provider_class(service_type: ServiceType) -> type[BaseClient]:
    # Offline selection imports only classes: it does not build clients or auth.
    from fileroute.clients.googledrive import GoogleDriveClient
    from fileroute.clients.sharepoint import SharepointClient
    from fileroute.clients.s3 import S3Client

    return {
        ServiceType.GOOGLE_DRIVE: GoogleDriveClient,
        ServiceType.SHAREPOINT: SharepointClient,
        ServiceType.S3: S3Client,
    }[service_type]


def relative_path(path: str) -> str:
    """Normalize a remote path without changing the authored locator."""
    normalized = path.replace("\\", "/").strip("/")
    parts = normalized.split("/") if normalized else []
    if any(part in {".", ".."} for part in parts) or path.startswith(("/", "\\")):
        raise ValueError(f"Unsafe or absolute remote path: {path!r}")
    return "/".join(part for part in parts if part)


def parse_location(location: Location, *, required: bool = False) -> Location:
    """Parse a locator without constructing clients, authenticating, or I/O."""
    inferred = next(
        (
            service
            for service in ServiceType
            if _provider_class(service).recognizes_url(location.path)
        ),
        None,
    )
    if inferred is not None and location.service_type not in (None, inferred):
        raise ValueError(
            f"serviceType {location.service_type} conflicts with URL {location.path}"
        )
    if (
        location.binding is not None
        and (location.service_type or inferred) != ServiceType.GOOGLE_DRIVE
    ):
        raise ValueError(
            "binding requires a Google Drive file source; set serviceType to "
            "GoogleDrive or use a Google Drive file URL"
        )
    if location.service_type is None:
        if inferred is None:
            if required:
                raise ValueError(
                    f"Cannot resolve target {location.path!r}; set serviceType and "
                    "provider namespace fields, or use a supported remote URL"
                )
            return location
        location.service_type = inferred
    # Explicit providers may use nonstandard HTTP links, but local paths need
    # enough namespace context to avoid searching an entire remote account.
    result = _provider_class(location.service_type).parse_location(location)
    result.apply(location)
    return location


def lookup_item(client: BaseClient, location: Location) -> ServiceItem:
    """Use the provider's lookup for the location and its optional binding."""
    return client.get_from_location(location)


def resolve_online(location: Location, client: BaseClient) -> Location:
    """Verify and enrich via the selected provider without transfer side effects."""
    parsed = parse_location(location)
    client.resolve_location(parsed).apply(parsed)
    return parsed


__all__ = [
    "resolve_path_properties",
    "ResolvedLocation",
    "parse_location",
    "resolve_online",
    "lookup_item",
    "relative_path",
]
