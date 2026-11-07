"""Match remote file metadata and build concrete input catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import re
from typing import Iterator
from urllib.parse import parse_qsl, urlsplit

from botocore.exceptions import BotoCoreError, ClientError
from parse import Parser

from fileroute.clients.googledrive import (
    FOLDER_MIME,
    GDriveItem,
    GoogleDriveClient,
    GoogleMimeTypes,
)
from fileroute.clients.s3 import S3Item, _parse_s3_source_url
from fileroute.clients.sharepoint import SharepointItem
from fileroute.descriptor import find, load, local_path, walk
from fileroute.exceptions import GoogleRefreshError, RemoteApiError
from fileroute.item import FileMatch, ServiceItem, _registered_name
from fileroute.models import Catalog, Location, Resource, ServiceType
from fileroute.resolution import (
    _compile_template,
    _normalized_path,
    lookup_item,
    parse_location,
)
from fileroute.transfer import _location_clients


def _compile_source_template(template: str) -> Parser:
    normalized = _normalized_path(template)
    if (
        not normalized.strip()
        or normalized.startswith("/")
        or PureWindowsPath(normalized).drive
        or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", normalized)
        or "://" in normalized
        or ".." in normalized.split("/")
    ):
        raise ValueError(f"Expected a relative resourcePathTemplate, got {template!r}")
    return _compile_template(normalized)


def _iter_file_matches(root: ServiceItem, parser: Parser) -> Iterator[FileMatch]:
    if not root.is_directory:
        raise NotADirectoryError(
            f"Discovery root is not a folder or prefix: {root.path!r}"
        )
    if isinstance(root, SharepointItem) and root._api_payload.get("remoteItem"):
        raise ValueError(
            "Declare the SharePoint shortcut's target folder as the discovery root"
        )
    if isinstance(root, GDriveItem):
        if root._trashed:
            raise ValueError(f"Google Drive root {root.id!r} is trashed")
        if root.kind == "drive":
            # Drive names and driveId paths can resolve to a synthesized root.
            metadata = root.client.get_file(root.id)
            if (
                metadata.id != root.id
                or metadata.mimeType != FOLDER_MIME
                or metadata.trashed
                or (metadata.driveId and metadata.driveId != root._drive_id)
            ):
                raise ValueError(f"Invalid Google Drive root metadata for {root.id!r}")

    seen = {root.id}
    pending = [(root, "")]
    while pending:
        item, relative = pending.pop()
        if item.is_directory:
            children = sorted(item.children, key=lambda child: (child.name, child.id))
            for child in reversed(children):
                if (
                    not child.id
                    or child.id in seen
                    or child.service_type != root.service_type
                    or (child.parent_id is not None and child.parent_id != item.id)
                ):
                    raise ValueError(
                        f"Invalid or repeated child {child.id!r} below {item.id!r}"
                    )
                seen.add(child.id)
                if isinstance(child, GDriveItem) and (
                    child.mime_type == GoogleMimeTypes.SHORT.value or child._trashed
                ):
                    continue
                if isinstance(child, SharepointItem) and child._api_payload.get(
                    "remoteItem"
                ):
                    continue

                name = child.name
                if (
                    not name
                    or name in {".", ".."}
                    or "/" in name
                    or "\\" in name
                    or "\0" in name
                    or PureWindowsPath(name).drive
                ):
                    raise ValueError(
                        f"Unrepresentable remote name {name!r} for {child.id!r}"
                    )
                child_path = f"{relative}/{name}" if relative else name
                if isinstance(root, S3Item):
                    prefix = f"{root.key.rstrip('/')}/" if root.key else ""
                    suffix = "/" if child.is_directory else ""
                    if (
                        not isinstance(child, S3Item)
                        or child.bucket != root.bucket
                        or child.key != f"{prefix}{child_path}{suffix}"
                    ):
                        raise ValueError(
                            f"Invalid S3 key {child.path!r} below {root.path!r}"
                        )
                if isinstance(item, SharepointItem) and isinstance(
                    child, SharepointItem
                ):
                    if item._drive_id and child._drive_id != item._drive_id:
                        raise ValueError(
                            f"Invalid SharePoint drive for child {child.id!r}"
                        )
                pending.append((child, child_path))
        else:
            match = parser.parse(relative)
            if match is not None:
                yield FileMatch(item, relative, match.named)


@dataclass(frozen=True)
class _DiscoverySource:
    name: str
    local: Path
    location: Location
    service: ServiceType
    parser: Parser


def _prepare_discovery(
    descriptor: Path, root: Path, selector: str | None, *, output: Path | None = None
) -> list[_DiscoverySource]:
    document = load(descriptor, resolve_references=True)
    rows = list(walk(document, include_self=True))
    if output is not None:
        resolved_output = output.resolve()
        origins = {row.origin_descriptor for row in rows} - {None}
        if any(
            resolved_output == origin or (output.exists() and output.samefile(origin))
            for origin in origins
        ):
            raise ValueError(f"Discovery output aliases an input descriptor: {output}")

    selected = find(document, selector) if selector is not None else document
    if not isinstance(selected, Catalog):
        raise ValueError("Discovery must select a catalog")
    pointer = next(row.json_pointer for row in rows if row.model is selected)

    sources = []
    targets_by_pointer: dict[str, list[Location]] = {}
    for row in rows:
        entity = row.model
        if not isinstance(entity, (Catalog, Resource)):
            continue
        parent_pointer = row.json_pointer.rsplit("/", 2)[0]
        targets = entity.targets
        if targets is None:
            targets = targets_by_pointer.get(parent_pointer, [])
        targets_by_pointer[row.json_pointer] = targets
        if row.json_pointer != pointer and not row.json_pointer.startswith(
            pointer + "/"
        ):
            continue
        label = row.name_path or "root"
        if any(target.resource_path_template is not None for target in targets):
            raise ValueError(
                f"{label}: resourcePathTemplate belongs on a Catalog source"
            )
        templates = [
            source
            for source in entity.sources
            if source.resource_path_template is not None
        ]
        if not templates:
            continue
        if not isinstance(entity, Catalog) or len(entity.sources) != 1:
            raise ValueError(
                f"{label}: discovery requires a Catalog with exactly one source"
            )
        if targets:
            raise ValueError(
                f"{label}: discovery catalogs are input-only. Set targets: []"
            )
        if entity.path is None:
            raise ValueError(f"{label}: set path for the local discovery destination")
        local = local_path(entity.path, root, reject_symlinks=True)
        if local.exists() and not local.is_dir():
            raise NotADirectoryError(
                f"{label}: discovery destination is not a directory: {local}"
            )

        source = templates[0].model_copy()
        try:
            assert source.resource_path_template is not None
            parser = _compile_source_template(source.resource_path_template)
            if source.entity_type not in (None, "Directory"):
                raise ValueError("A discovery source must have Directory entityType")
            url = urlsplit(source.path)
            parse_location(source)
            service = source.service_type
            if service is None:
                raise ValueError(
                    "Set serviceType and its namespace or use a supported URL"
                )
            if service is ServiceType.GOOGLE_DRIVE and any(
                key.casefold() == "resourcekey"
                for key, _ in parse_qsl(url.query, keep_blank_values=True)
            ):
                raise ValueError(
                    "Google Drive discovery requires direct access without a resource key"
                )
            if service is ServiceType.S3:
                raw_key = (
                    _parse_s3_source_url(source.path, allow_empty_key=True)[1]
                    if url.scheme
                    else source.path
                )
                if raw_key != source.remote_path or (
                    url.scheme
                    and (url.query or url.fragment or url.path.startswith("//"))
                ):
                    raise ValueError(
                        "S3 discovery root must identify an exact bucket prefix"
                    )
        except ValueError as exc:
            raise ValueError(f"{label} ({source.path}): {exc}") from exc
        sources.append(_DiscoverySource(row.name_path, local, source, service, parser))
    if not sources:
        raise ValueError(
            "Discovery needs at least one Catalog source with resourcePathTemplate"
        )
    return sources


def _file_source(match: FileMatch, source: Location, profile: str | None) -> Location:
    item = match.item
    path = item.source_url
    context = {}
    if isinstance(item, GDriveItem):
        # Some native files expose a UI link outside the recognized ID forms.
        try:
            link_id = (
                GoogleDriveClient._extract_id_from_url(path)
                if GoogleDriveClient.recognizes_url(path)
                else None
            )
        except ValueError:
            link_id = None
        if link_id != item.id:
            path = f"https://drive.google.com/file/d/{item.id}/view"
        if item._drive_id:
            context["driveId"] = item._drive_id
    elif isinstance(item, SharepointItem):
        drive_id = item._drive_id or source.drive_id
        if not drive_id:
            raise ValueError(f"Missing driveId for SharePoint file {item.id!r}")
        context["driveId"] = drive_id
        if not path:
            path = item.path
            context.update({
                key: value
                for key, value in (("site", source.site), ("siteId", source.site_id))
                if value is not None
            })
    elif isinstance(item, S3Item):
        # A scoped key keeps characters that would otherwise become URL delimiters.
        url = urlsplit(path)
        if url.query or url.fragment or "?" in item.key or "#" in item.key:
            path = item.key
            context["bucket"] = item.bucket
    if profile is not None:
        context["authProfile"] = profile
    return parse_location(
        Location(
            path=path,
            serviceId=item.id,
            serviceType=item.service_type,
            entityType="File",
            **context,
        )
    )


def _collect_discovery(
    sources: list[_DiscoverySource], root: Path, profile: str | None
) -> Catalog:
    result = Catalog(targets=[])
    destinations: dict[Path, str] = {}
    keys = (
        (source.service, source.location.auth_profile or profile) for source in sources
    )
    with _location_clients(keys) as clients:
        for source in sources:
            location = source.location
            effective_profile = location.auth_profile or profile
            label = source.name or "root"
            try:
                item = lookup_item(
                    clients[(source.service, effective_profile)], location
                )
                if item.service_type != source.service:
                    raise ValueError("Resolved root has the wrong provider")
                if (
                    isinstance(item, GDriveItem)
                    and location.drive_id
                    and (item._drive_id != location.drive_id)
                ):
                    raise ValueError(
                        "Google Drive root conflicts with the declared driveId"
                    )
                matches = sorted(
                    _iter_file_matches(item, source.parser),
                    key=lambda match: (match.relative_path, match.item.id),
                )
                if not matches:
                    raise ValueError("No files matched resourcePathTemplate")
                resources: dict[str, Resource] = {}
                names: dict[str, FileMatch] = {}
                for match in matches:
                    local = local_path(
                        match.relative_path, source.local, reject_symlinks=True
                    )
                    if local.exists() and not local.is_file():
                        raise ValueError(
                            f"Discovery destination is not a file: {local}"
                        )
                    if local in destinations:
                        raise ValueError(
                            f"Duplicate destination {local} for IDs "
                            f"{destinations[local]!r} and {match.item.id!r}"
                        )
                    key = _registered_name(match.relative_path)
                    previous = names.get(key.casefold())
                    if previous is not None:
                        raise ValueError(
                            f"Registered name {key!r} collides for {previous.relative_path!r} "
                            f"({previous.item.id}) and {match.relative_path!r} ({match.item.id})"
                        )
                    names[key.casefold()] = match
                    destinations[local] = match.item.id
                    resources[key] = Resource(
                        path=local.relative_to(root).as_posix(),
                        pathProperties=match.path_properties,
                        sources=[_file_source(match, location, effective_profile)],
                    )
            except (
                OSError,
                ValueError,
                RemoteApiError,
                GoogleRefreshError,
                BotoCoreError,
                ClientError,
            ) as exc:
                raise ValueError(f"{label} ({location.path}): {exc}") from exc

            collection = result
            if source.name:
                for part in source.name.split("."):
                    if part not in collection.catalogs:
                        collection.catalogs[part] = Catalog(targets=[])
                        collection.model_fields_set.add("catalogs")
                    child = collection.catalogs[part]
                    assert isinstance(child, Catalog)
                    collection = child
            collection.path = source.local.relative_to(root).as_posix()
            collection.resources = resources

    for path, item_id in destinations.items():
        for parent in path.parents:
            if parent in destinations:
                raise ValueError(
                    f"Discovery destinations {path} ({item_id}) and "
                    f"{parent} ({destinations[parent]}) overlap"
                )
    return Catalog.model_validate(result.model_dump(by_alias=True, exclude_unset=True))


def discover_resources(
    descriptor: Path,
    *,
    root: Path | None = None,
    selector: str | None = None,
    profile: str | None = None,
) -> Catalog:
    """Discover a catalog's declared remote sources and return concrete resources.

    The working root defaults to the current directory. A source's authProfile
    overrides profile. Every selected source must yield a match and all clients
    opened by this call are closed. Use descriptor.save to persist the result.
    """
    root = (root or Path.cwd()).resolve()
    return _collect_discovery(
        _prepare_discovery(descriptor, root, selector), root, profile
    )


__all__ = ["discover_resources"]
