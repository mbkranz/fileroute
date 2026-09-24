"""Descriptor resolution: preserve authored URLs and resolve provider metadata."""

from urllib.parse import urlsplit

from sharedrive.models import Catalog, CatalogReference, Location, ServiceType


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
    else:
        location.service_type = inferred
    if required and location.service_type is None:
        raise ValueError(
            f"Cannot resolve target {location.path!r}; use a supported remote URL and set serviceType explicitly when needed"
        )


def resolve(catalog: Catalog) -> Catalog:
    """Return resolved metadata without mutating input, URLs, files, or references.

    Known source URLs gain serviceType; other provenance remains valid metadata.
    Every target must resolve to a provider. Load external references separately
    before resolving a transfer; descriptor write-back changes only its own file.
    """
    result = catalog.model_copy(deep=True)
    for row in result.iter_entity_paths(include_self=True, traverse_references=False):
        if isinstance(row.model, CatalogReference):
            continue
        for location in row.model.sources:
            _resolve_location(location, required=False)
        for location in row.model.targets or []:
            _resolve_location(location, required=True)
    return result


__all__ = ["resolve"]
