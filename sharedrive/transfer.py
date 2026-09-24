"""Plan resolved metadata offline, then execute through provider clients.

Plans are concrete in-memory transfer entries. They are not persisted lockfiles:
remote permissions and directory contents are checked during execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from sharedrive.models import Catalog, Resource, Location, ServiceType
from sharedrive.clients import get_provider
from sharedrive.descriptor import load, walk, resolve, local_path


@dataclass(frozen=True)
class PullEntry:
    local: Path
    remote: str
    service_type: ServiceType
    directory: bool = False


def plan_pull(descriptor: Path, *, root: Path | None = None) -> tuple[PullEntry, ...]:
    """Plan remote sources to local paths, offline.

    Multiple sources can describe a transformation; Sharedrive cannot reproduce
    that transformation and refuses to choose one. Local provenance is likewise
    not a download instruction. Targets are never used for retrieval.
    """
    root = (root or Path.cwd()).resolve()
    document = resolve(load(descriptor, resolve_references=True), direction="pull")
    entries = []
    for row in walk(document, include_self=True):
        entity = row.model
        if isinstance(entity, Catalog) and (entity.resources or entity.catalogs):
            continue
        if not isinstance(entity, (Resource, Catalog)) or not entity.sources:
            continue
        if len(entity.sources) != 1:
            raise ValueError(
                f"{entity.name}: pull requires exactly one source; build derived artifacts separately"
            )
        if entity.path is None:
            raise ValueError(
                f"{entity.name}: set path for the local artifact before pulling"
            )
        source = entity.sources[0]
        service = source.service_type
        if service is None:
            raise ValueError(
                f"Set serviceType for remote source {source.path!r}; local provenance cannot be pulled"
            )
        provider = get_provider(service)
        if not provider.capabilities.supports_download:
            raise ValueError(f"Pull is not implemented for {service}")
        local = local_path(entity.path, root, reject_symlinks=True)
        directory = isinstance(entity, Catalog)
        for previous in entries:
            if (
                local == previous.local
                or (directory and previous.local.is_relative_to(local))
                or (previous.directory and local.is_relative_to(previous.local))
            ):
                raise ValueError(
                    f"Overlapping pull destinations: {previous.local} and {local}"
                )
        if local.exists() and local.is_dir() != directory:
            raise ValueError(
                f"Pull destination has the wrong file/directory type: {local}"
            )
        entries.append(PullEntry(local, source.path, service, directory))
    if not entries:
        raise ValueError("Pull needs at least one artifact with a remote source")
    return tuple(entries)


def pull(entries: tuple[PullEntry, ...]) -> None:
    """Resolve remote items, check their paths, then download planned files."""
    clients = {}
    files = []
    destinations = set()
    for entry in entries:
        if entry.service_type not in clients:
            clients[entry.service_type] = get_provider(
                entry.service_type
            ).build_default()
        item = clients[entry.service_type].get_from_weburl(entry.remote)
        if item.is_directory != entry.directory:
            raise ValueError(
                f"Source has the wrong file/directory type: {entry.remote}"
            )
        if entry.directory:
            for child in item.iter_files():
                # Provider paths include the remote root; strip exactly that prefix.
                relative = Path(child.path).relative_to(Path(item.path))
                target = local_path(str(relative), entry.local, reject_symlinks=True)
                files.append((child, target))
        else:
            files.append((
                item,
                local_path(str(entry.local), entry.local.parent, reject_symlinks=True),
            ))
    for _, target in files:
        if target in destinations or (target.exists() and not target.is_file()):
            raise ValueError(f"Conflicting pull destination: {target}")
        destinations.add(target)
    for target in destinations:
        if any(parent in destinations for parent in target.parents):
            raise ValueError(f"File and directory destinations overlap: {target}")
    for item, target in files:
        target.parent.mkdir(parents=True, exist_ok=True)
        item.download(target)


@dataclass(frozen=True)
class PushEntry:
    local: Path
    remote: str
    relative: Path
    service_type: ServiceType
    direct_file: bool = False

    @property
    def destination(self) -> str:
        return (
            self.remote
            if self.direct_file
            else f"{self.remote.rstrip('/')}/{quote(self.relative.as_posix(), safe='/')}"
        )


def plan_push(descriptor: Path, *, root: Path | None = None) -> tuple[PushEntry, ...]:
    """Publish path to targets, never sources; validate everything before auth.

    Paths are relative to root (cwd by default). Catalog targets are folders;
    children inherit them using paths relative to the declaring catalog's path,
    or root when absent. Explicit child targets replace inherited ones; [] opts
    out. A catalog with children publishes only those children. A leaf catalog
    publishes its directory tree. Explicit resource targets are file URLs unless
    entityType is Directory/Container.
    """
    root = (root or Path.cwd()).resolve()
    document = resolve(load(descriptor, resolve_references=True), direction="push")
    entries: list[PushEntry] = []
    destinations: dict[str, Path] = {}

    def add(local: Path, target: Location, relative: Path | None) -> None:
        service = target.service_type
        provider = get_provider(service)
        if not provider.capabilities.supports_upload:
            raise ValueError(f"Upload is not implemented for {service}")
        url = urlparse(target.path)
        if url.query or url.fragment:
            raise ValueError(
                "Upload needs a direct folder/file URL without a query or fragment"
            )
        if (
            "your-tenant" in target.path.casefold()
            or "your-site" in target.path.casefold()
        ):
            raise ValueError(
                "Replace the SharePoint placeholder target before uploading"
            )
        if not local.is_file():
            raise ValueError(f"Upload input must be a file: {local}")
        if service is ServiceType.SHAREPOINT and local.stat().st_size > 250_000_000:
            raise ValueError(
                f"File exceeds Microsoft Graph's 250 MB PUT limit: {local}"
            )
        direct = relative is None
        if direct:
            name = unquote(url.path.rsplit("/", 1)[-1])
            if not name or name in {".", ".."} or "/" in name or "\\" in name:
                raise ValueError(f"Invalid remote file path: {target.path}")
            relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe relative upload path: {relative}")
        entry = PushEntry(local, target.path, relative, service, direct)
        # Decoding catches authored aliases for the same remote file.
        key = (
            unquote(entry.destination).casefold()
            if service is ServiceType.SHAREPOINT
            else entry.destination
        )
        if key in destinations:
            if destinations[key] != local:
                raise ValueError(f"Multiple local files target {entry.destination}")
            return
        destinations[key] = local
        entries.append(entry)

    def visit(
        entity: Catalog | Resource, inherited: list[Location], anchor: Path
    ) -> None:
        explicit = entity.targets is not None
        targets = entity.targets if explicit else inherited
        local = (
            local_path(entity.path, root, reject_symlinks=True)
            if entity.path is not None
            else None
        )
        if isinstance(entity, Catalog):
            if any(target.entity_type == "File" for target in targets or []):
                raise ValueError("Catalog targets must be folders, not files")
            if explicit:
                anchor = local or root
            children = [*entity.resources, *entity.catalogs]
            if children:
                for child in children:
                    visit(child, targets or [], anchor)
                return
            if not targets:
                return
            if local is None or not local.is_dir():
                raise ValueError(
                    f"Upload directory missing or not a directory: {entity.path}"
                )
            for file in sorted(local.rglob("*")):
                safe = local_path(str(file), root, reject_symlinks=True)
                if safe.is_file():
                    for target in targets:
                        add(safe, target, safe.relative_to(anchor))
        elif targets:
            if local is None or not local.exists():
                raise ValueError(f"Upload input missing: {entity.path}")
            for target in targets:
                relative = (
                    (
                        Path(local.name)
                        if target.entity_type in {"Directory", "Container"}
                        else None
                    )
                    if explicit
                    else local.relative_to(anchor)
                )
                add(local, target, relative)

    visit(document, [], root)
    if not entries:
        raise ValueError("Upload needs at least one local file with targets")
    return tuple(entries)


def push(files: tuple[PushEntry, ...]) -> None:
    """Transfer a prepared plan; remote files outside it are never deleted."""
    if any(
        "your-tenant" in file.remote.casefold() or "your-site" in file.remote.casefold()
        for file in files
    ):
        raise ValueError("Replace the SharePoint placeholder target before uploading")
    clients = {}
    for file in files:
        if file.service_type not in clients:
            clients[file.service_type] = get_provider(file.service_type).build_default()
        folder = file.remote.rsplit("/", 1)[0] if file.direct_file else file.remote
        clients[file.service_type].upload_to_folder(folder, file.relative, file.local)


__all__ = ["PullEntry", "PushEntry", "plan_pull", "plan_push", "pull", "push"]
