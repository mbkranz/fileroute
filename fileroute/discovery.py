"""Match remote file metadata and build concrete input catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
import re
from typing import Any, Iterator, Literal
from urllib.parse import parse_qsl, urlsplit

from botocore.exceptions import BotoCoreError, ClientError
from parse import Parser
from pydantic import BaseModel, ConfigDict, Field

from fileroute.clients.base import BaseClient
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


def match_source_path(location: Location, relative_path: str) -> dict[str, Any] | None:
    """Apply the catalog source template to an exact ID's verified root-relative path."""
    template = location.resource_path_template
    if not template:
        raise ValueError("Expected a source resourcePathTemplate")
    template = _normalized_path(template)
    if location.match_path_segments and relative_path.count("/") != template.count("/"):
        return None
    match = _compile_source_template(template).parse(relative_path)
    return match.named if match is not None else None


def _iter_file_matches(
    root: ServiceItem, parser: Parser, segments: int | None = None
) -> Iterator[FileMatch]:
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
                depth = child_path.count("/") + 1
                if segments is None or (
                    depth < segments if child.is_directory else depth == segments
                ):
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

    def matches(self, root: ServiceItem) -> Iterator[FileMatch]:
        template = self.location.resource_path_template or ""
        segments = (
            _normalized_path(template).count("/") + 1
            if self.location.match_path_segments
            else None
        )
        return _iter_file_matches(root, self.parser, segments)


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
                    source.matches(item),
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


DiscoveryStatus = Literal[
    "new", "changed", "renamed", "moved", "missing_from_scope", "unchanged", "unknown"
]


class DiscoveryRequest(BaseModel):
    """Metadata inventory request; no downloads or descriptor writes are implied."""

    model_config = ConfigDict(extra="forbid")
    descriptor: Path
    root: Path | None = None
    selector: str | None = None
    profile: str | None = None


class DiscoveryScan(BaseModel):
    """One scope's observed files; incomplete inventory cannot establish absence."""

    model_config = ConfigDict(extra="forbid")
    scope: str
    resources: dict[str, Resource] = Field(default_factory=dict)
    complete: bool = True
    error: str | None = None


class DiscoveryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: str
    scope: str
    service_type: ServiceType | None = None
    service_id: str | None = None
    local_path: str | None = None
    observed: bool = True
    statuses: list[DiscoveryStatus]
    path_properties: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class DiscoveryInventory(BaseModel):
    """Positive observations from the last complete scan, keyed by provider identity on comparison."""

    model_config = ConfigDict(extra="forbid")
    roots: dict[str, str]
    files: list[DiscoveryObservation]


class ReconcileDiscoveryRequest(BaseModel):
    """Reconcile an expanded catalog against previously acquired metadata."""

    model_config = ConfigDict(extra="forbid")
    catalog: Catalog
    scans: list[DiscoveryScan]
    descriptor: Path
    root: Path


class DiscoveryResult(BaseModel):
    """Reviewable proposal; only callers explicitly requesting writes persist it.

    ``catalog`` retains expanded runtime origins. ``writes`` contains physical,
    unexpanded owner documents so a linked catalog is never flattened on save.
    """

    model_config = ConfigDict(extra="forbid")
    catalog: Catalog
    observations: list[DiscoveryObservation] = Field(default_factory=list)
    complete: bool = True
    errors: list[str] = Field(default_factory=list)
    writes: dict[Path, Catalog] = Field(default_factory=dict)
    inventory: DiscoveryInventory | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready report, with proposed owner filenames and catalog metadata."""
        return {
            "complete": self.complete,
            "errors": self.errors,
            "inventory": self.inventory.model_dump(mode="json")
            if self.inventory
            else None,
            "observations": [row.model_dump(mode="json") for row in self.observations],
            "writes": [str(path) for path in sorted(self.writes)],
            "catalog": self.catalog.model_dump(
                mode="json", by_alias=True, exclude_unset=True
            ),
        }


def _identity(source: Location) -> tuple[ServiceType, str] | None:
    resolved = parse_location(source.model_copy(deep=True))
    if resolved.service_type is None or not resolved.service_id:
        return None
    return resolved.service_type, resolved.service_id


def _observation_metadata(match: FileMatch, scope: str) -> dict[str, Any]:
    item = match.item
    metadata: dict[str, Any] = {
        "scope": scope,
        "relativePath": match.relative_path,
        "name": item.name,
    }
    if isinstance(item, GDriveItem):
        metadata.update(item.observed_metadata)
    return metadata


def _scan_source(
    source: _DiscoverySource, *, client: Any, root: Path, profile: str | None
) -> DiscoveryScan:
    scan = DiscoveryScan(scope=source.name)
    try:
        item = lookup_item(client, source.location)
        if item.service_type != source.service or (
            source.location.service_id and item.id != source.location.service_id
        ):
            raise ValueError(
                "Resolved root differs from the configured provider or identity"
            )
        if (
            isinstance(item, GDriveItem)
            and source.location.drive_id
            and item._drive_id != source.location.drive_id
        ):
            raise ValueError("Google Drive root conflicts with the declared driveId")
        for match in source.matches(item):
            local = local_path(match.relative_path, source.local, reject_symlinks=True)
            if local.exists() and not local.is_file():
                raise ValueError(f"Discovery destination is not a file: {local}")
            key = _registered_name(match.relative_path)
            if key.casefold() in {name.casefold() for name in scan.resources}:
                raise ValueError(
                    f"Registered name collision for {match.relative_path!r}"
                )
            # Carry source provider/profile metadata to new resources.
            data = source.location.model_dump(by_alias=True, exclude_unset=True)
            data.pop("resourcePathTemplate", None)
            data.pop("matchPathSegments", None)
            data.update(
                _file_source(match, source.location, profile).model_dump(
                    by_alias=True, exclude_unset=True
                )
            )
            data["discovery"] = _observation_metadata(match, source.name)
            scan.resources[key] = Resource(
                path=local.relative_to(root).as_posix(),
                pathProperties=match.path_properties,
                sources=[Location.model_validate(data)],
            )
    except (
        OSError,
        ValueError,
        RemoteApiError,
        GoogleRefreshError,
        BotoCoreError,
        ClientError,
    ) as exc:
        scan.complete = False
        scan.error = f"{source.name or 'root'} ({source.location.path}): {exc}"
    scan.resources = dict(sorted(scan.resources.items()))
    return scan


def _scan_sources(
    sources: list[_DiscoverySource], root: Path, profile: str | None
) -> list[DiscoveryScan]:
    keys = [
        (source.service, source.location.auth_profile or profile) for source in sources
    ]
    try:
        with _location_clients(keys) as clients:
            return [
                _scan_source(
                    source,
                    client=clients[
                        (source.service, source.location.auth_profile or profile)
                    ],
                    root=root,
                    profile=source.location.auth_profile or profile,
                )
                for source in sources
            ]
    except (
        OSError,
        ValueError,
        RemoteApiError,
        GoogleRefreshError,
        BotoCoreError,
        ClientError,
    ) as exc:
        return [
            DiscoveryScan(scope=source.name, complete=False, error=str(exc))
            for source in sources
        ]


def _changed_observations(
    before: dict[str, Any], after: dict[str, Any]
) -> list[DiscoveryStatus]:
    changes: list[DiscoveryStatus] = []
    if before.get("name") is not None and before.get("name") != after.get("name"):
        changes.append("renamed")
    old_parent = before.get(
        "parents", Path(str(before.get("relativePath", ""))).parent.as_posix()
    )
    new_parent = after.get(
        "parents", Path(str(after.get("relativePath", ""))).parent.as_posix()
    )
    if before and (
        old_parent != new_parent
        or before.get("scope") != after.get("scope")
        or Path(before.get("relativePath", "")).parent
        != Path(after.get("relativePath", "")).parent
    ):
        changes.append("moved")
    tokens = ("version", "modifiedTime", "md5Checksum", "sha256Checksum", "mimeType")
    if any(
        key in before and key in after and before[key] != after[key] for key in tokens
    ):
        changes.append("changed")
    if not before or any(key in before and key not in after for key in tokens):
        changes.append("unknown")
    return changes or ["unchanged"]


def _catalog_at(catalog: Catalog, scope: str) -> Catalog:
    selected = find(catalog, scope) if scope else catalog
    if not isinstance(selected, Catalog):
        raise ValueError(f"Discovery scope must select a catalog: {scope!r}")
    return selected


def _check_destinations(catalog: Catalog, root: Path) -> None:
    destinations: dict[Path, str] = {}
    for row in walk(catalog):
        if not isinstance(row.model, Resource):
            continue
        path = local_path(row.model.path, root, reject_symlinks=True)
        if path in destinations:
            raise ValueError(
                f"Duplicate discovery destination {path}: {destinations[path]} and {row.name_path}"
            )
        destinations[path] = row.name_path
    for path in destinations:
        if any(parent in destinations for parent in path.parents):
            raise ValueError(f"Overlapping discovery destination: {path}")


def reconcile_discovery(request: ReconcileDiscoveryRequest) -> DiscoveryResult:
    """Preserve registered identity and authored resource metadata while updating observations.

    Missing means absent from a successfully scanned scope, not deleted remotely.
    No descriptors or artifact bytes are written. This function compares metadata;
    it never claims a provider version is a downloaded-content checksum.
    """
    catalog = request.catalog.model_copy(deep=True)
    existing: dict[tuple[ServiceType, str], tuple[Catalog, str, str, Resource]] = {}
    existing_scopes: dict[tuple[ServiceType, str], str] = {}
    scopes = {scan.scope for scan in request.scans}
    for scope in sorted(scopes):
        owner = _catalog_at(catalog, scope)
        for row in walk(owner):
            if not isinstance(row.model, Resource):
                continue
            resource = row.model
            if len(resource.sources) != 1:
                # Manually authored non-transfer products remain outside inventory.
                continue
            identity = _identity(resource.sources[0])
            if identity is None:
                continue
            parent_scope = (
                row.name_path.rsplit(".", 1)[0] if "." in row.name_path else ""
            )
            parent = _catalog_at(owner, parent_scope)
            name = row.name_path.rsplit(".", 1)[-1]
            registered = ".".join(filter(None, (scope, row.name_path)))
            if identity in existing and existing[identity][2] != registered:
                raise ValueError(f"Duplicate remote identity {identity}: {registered}")
            existing[identity] = parent, name, registered, resource
            existing_scopes[identity] = scope

    result = DiscoveryResult(catalog=catalog)
    seen: set[tuple[ServiceType, str]] = set()
    scan_by_scope = {scan.scope: scan for scan in request.scans}
    for scan in request.scans:
        owner = _catalog_at(catalog, scan.scope)
        if not scan.complete:
            result.complete = False
            result.errors.append(
                scan.error or f"Incomplete discovery scope: {scan.scope or 'root'}"
            )
        for key, observed in scan.resources.items():
            if len(observed.sources) != 1:
                raise ValueError("Discovered resources require exactly one source")
            identity = _identity(observed.sources[0])
            if identity is None:
                raise ValueError(
                    f"Discovered source lacks stable provider/serviceId: {key}"
                )
            if identity in seen:
                raise ValueError(f"Remote identity observed more than once: {identity}")
            seen.add(identity)
            metadata = dict(
                (observed.sources[0].model_extra or {}).get("discovery", {})
            )
            if identity in existing:
                parent, name, registered, resource = existing[identity]
                prior = dict(
                    (resource.sources[0].model_extra or {}).get("discovery", {})
                )
                statuses = _changed_observations(prior, metadata)
                source_data = resource.sources[0].model_dump(
                    by_alias=True, exclude_unset=True
                )
                # Remote location changes retain authored resources and publication choices.
                fresh = parse_location(
                    observed.sources[0].model_copy(deep=True)
                ).model_dump(by_alias=True, exclude_unset=True)
                for field in (
                    "path",
                    "serviceType",
                    "serviceId",
                    "entityType",
                    "remotePath",
                    "drive",
                    "driveId",
                    "site",
                    "siteId",
                    "bucket",
                ):
                    if field in fresh:
                        source_data[field] = fresh[field]
                    else:
                        source_data.pop(field, None)
                source_data["discovery"] = metadata
                resource = resource.model_copy(
                    update={
                        "sources": [Location.model_validate(source_data)],
                        "path_properties": observed.path_properties,
                    }
                )
                parent.resources[name] = resource
            else:
                resource = observed.model_copy(deep=True)
                if key.casefold() in {
                    name.casefold() for name in (*owner.resources, *owner.catalogs)
                }:
                    raise ValueError(
                        f"Discovery registered name {key!r} already exists in {scan.scope or 'root'}"
                    )
                owner.resources[key] = resource
                owner.model_fields_set.add("resources")
                registered = ".".join(filter(None, (scan.scope, key)))
                statuses = ["new"]
            result.observations.append(
                DiscoveryObservation(
                    resource=registered,
                    scope=scan.scope,
                    service_type=identity[0],
                    service_id=identity[1],
                    local_path=resource.path,
                    statuses=statuses,
                    path_properties=observed.path_properties or {},
                    metadata=metadata,
                )
            )
    for identity, (_, _, registered, resource) in existing.items():
        if identity in seen:
            continue
        prior = dict((resource.sources[0].model_extra or {}).get("discovery", {}))
        scope = str(prior.get("scope", existing_scopes[identity]))
        scan = scan_by_scope.get(scope)
        if scan is None:
            continue
        result.observations.append(
            DiscoveryObservation(
                resource=registered,
                scope=scope,
                service_type=identity[0],
                service_id=identity[1],
                local_path=resource.path,
                observed=False,
                statuses=["missing_from_scope" if scan.complete else "unknown"],
                metadata=prior,
                error=scan.error,
            )
        )
    for scan in request.scans:
        if not scan.complete and not any(
            row.scope == scan.scope for row in result.observations
        ):
            result.observations.append(
                DiscoveryObservation(
                    resource=scan.scope or "root",
                    scope=scan.scope,
                    statuses=["unknown"],
                    observed=False,
                    error=scan.error,
                )
            )
    _check_destinations(catalog, request.root)
    return result


def _owned_writes(
    before: Catalog, after: Catalog, descriptor: Path
) -> dict[Path, Catalog]:
    """Update direct resource maps in physical owners, retaining links."""
    writes: dict[Path, Catalog] = {}
    previous = {row.json_pointer: row for row in walk(before, include_self=True)}
    for row in walk(after, include_self=True):
        if not isinstance(row.model, Catalog) or row.json_pointer not in previous:
            continue
        old = previous[row.json_pointer]
        assert isinstance(old.model, Catalog)
        dump = {
            key: value.model_dump(by_alias=True, exclude_unset=True)
            for key, value in row.model.resources.items()
        }
        old_dump = {
            key: value.model_dump(by_alias=True, exclude_unset=True)
            for key, value in old.model.resources.items()
        }
        if dump == old_dump:
            continue
        path = old.origin_descriptor or descriptor.resolve()
        if path not in writes:
            writes[path] = load(path)
        target = _catalog_at(writes[path], old.origin_pointer)
        target.resources = {
            key: value.model_copy(deep=True)
            for key, value in row.model.resources.items()
        }
        target.model_fields_set.add("resources")
    return writes


def discover_report(
    descriptor: Path,
    *,
    root: Path | None = None,
    selector: str | None = None,
    profile: str | None = None,
    previous: DiscoveryInventory | None = None,
    client: BaseClient | None = None,
) -> DiscoveryResult:
    """Acquire remote metadata and propose stable-identity catalog reconciliation.

    This is a report-only operation. Persist ``result.writes`` only after explicit
    caller authorization and only for a complete scan. Raw bytes are never pulled.
    ``discover_resources`` retains its original separate-output catalog behavior.
    """
    request = DiscoveryRequest(
        descriptor=descriptor, root=root, selector=selector, profile=profile
    )
    root = (request.root or Path.cwd()).resolve()
    before = load(request.descriptor, resolve_references=True)
    sources = _prepare_discovery(request.descriptor, root, request.selector)
    roots = {
        source.name: source.location.service_id or source.location.path
        for source in sources
    }
    if previous is not None and previous.roots != roots:
        raise ValueError("Discovery inventory belongs to different source roots")
    if client is None:
        scans = _scan_sources(sources, root, request.profile)
    else:
        if len(sources) != 1:
            raise ValueError("An explicit client requires exactly one discovery source")
        scans = [
            _scan_source(
                sources[0],
                client=client,
                root=root,
                profile=sources[0].location.auth_profile or profile,
            )
        ]
    result = reconcile_discovery(
        ReconcileDiscoveryRequest(
            catalog=before, scans=scans, descriptor=request.descriptor, root=root
        )
    )
    positives = [row for row in result.observations if row.observed and row.service_id]
    if previous is not None:
        prior = {(row.service_type, row.service_id): row for row in previous.files}
        if len(prior) != len(previous.files):
            raise ValueError(
                "Duplicate provider identity in previous discovery inventory"
            )
        for row in positives:
            old = prior.pop((row.service_type, row.service_id), None)
            row.statuses = (
                _changed_observations(old.metadata, row.metadata) if old else ["new"]
            )
        existing = {(row.service_type, row.service_id) for row in result.observations}
        by_scope = {scan.scope: scan for scan in scans}
        for identity, old in prior.items():
            if identity not in existing:
                scan = by_scope[old.scope]
                result.observations.append(
                    old.model_copy(
                        update={
                            "observed": False,
                            "statuses": [
                                "missing_from_scope" if scan.complete else "unknown"
                            ],
                            "error": scan.error,
                        }
                    )
                )
    if result.complete:
        result.inventory = DiscoveryInventory(roots=roots, files=positives)
    result.writes = _owned_writes(before, result.catalog, request.descriptor)
    return result


__all__ = [
    "discover_resources",
    "match_source_path",
    "DiscoveryInventory",
    "DiscoveryStatus",
    "DiscoveryRequest",
    "DiscoveryScan",
    "DiscoveryObservation",
    "ReconcileDiscoveryRequest",
    "DiscoveryResult",
    "reconcile_discovery",
    "discover_report",
]
