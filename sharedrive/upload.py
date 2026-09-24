"""Offline publication planning followed by explicit provider writes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from sharedrive.models import Catalog, Resource, Location
from sharedrive.clients import get_provider
from sharedrive.descriptor import load, resolve, local_path
from sharedrive.models import ServiceType


@dataclass(frozen=True)
class UploadFile:
    local: Path
    remote: str
    relative: Path
    service: ServiceType
    direct_file: bool = False

    @property
    def destination(self) -> str:
        return (
            self.remote
            if self.direct_file
            else f"{self.remote.rstrip('/')}/{quote(self.relative.as_posix(), safe='/')}"
        )


def plan_upload(
    descriptor: Path, *, root: Path | None = None
) -> tuple[UploadFile, ...]:
    """Publish path to targets, never sources; validate everything before auth.

    Paths are relative to root (cwd by default). Catalog targets are folders;
    children inherit them using paths relative to the declaring catalog's path,
    or root when absent. Explicit child targets replace inherited ones; [] opts
    out. A catalog with children publishes only those children. A leaf catalog
    publishes its directory tree. Explicit resource targets are file URLs unless
    entityType is Directory/Container.
    """
    root = (root or Path.cwd()).resolve()
    document = resolve(load(descriptor, resolve_references=True))
    entries: list[UploadFile] = []
    destinations: dict[str, Path] = {}

    def add(local: Path, target: Location, relative: Path | None) -> None:
        service = target.service_type
        provider = get_provider(service)
        if provider is None or not provider.capabilities.supports_upload:
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
        if service == "SharePoint" and local.stat().st_size > 250_000_000:
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
        entry = UploadFile(local, target.path, relative, service, direct)
        # Decoding catches authored aliases for the same remote file.
        key = (
            unquote(entry.destination).casefold()
            if service == "SharePoint"
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


def upload(files: tuple[UploadFile, ...]) -> None:
    """Transfer a prepared plan; remote files outside it are never deleted."""
    if any(
        "your-tenant" in file.remote.casefold() or "your-site" in file.remote.casefold()
        for file in files
    ):
        raise ValueError("Replace the SharePoint placeholder target before uploading")
    clients = {}
    for file in files:
        if file.service not in clients:
            clients[file.service] = get_provider(file.service).build_default()
        folder = file.remote.rsplit("/", 1)[0] if file.direct_file else file.remote
        clients[file.service].upload_to_folder(folder, file.relative, file.local)


__all__ = ["UploadFile", "plan_upload", "upload"]
