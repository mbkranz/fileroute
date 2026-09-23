"""Plan local descriptor uploads before contacting a remote service.

Cache paths are relative to the command's working directory. This lets a
repository descriptor in ``config/`` refer to build output in ``docs/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from sharedrive.models import (
    DriveCatalog, DriveRemoteCatalog, DriveRemoteResource, adapter_from_service_type,
    resolve_service_type,
)
from sharedrive.registry import get_client, get_provider


@dataclass(frozen=True)
class UploadFile:
    local: Path
    remote: str
    relative: Path
    service: str
    direct_file: bool = False


def plan_upload(descriptor: Path, *, root: Path | None = None) -> tuple[UploadFile, ...]:
    """Expand file and directory caches, preserving paths under remote folders.

    No remote requests or authentication occur here. A missing or unsafe local
    input fails the whole plan before the first transfer.
    """
    root = (root or Path.cwd()).resolve()
    # Pass the repository root as basepath so cache paths are rooted at cwd.
    document = DriveCatalog.from_path(str(descriptor.resolve()), basepath=str(root))
    entries: list[UploadFile] = []

    def add(local_name: str | None, remote: object, service: str | None, directory: bool) -> None:
        if not local_name or not remote:
            raise ValueError("Upload entries require _cache and accessURL/path")
        service = resolve_service_type(str(remote), service)
        provider = get_provider(adapter_from_service_type(service) or "")
        if provider is None or not provider.capabilities.supports_upload:
            raise ValueError(f"Upload is not implemented for {service}")
        locator = urlparse(str(remote))
        if locator.query or locator.fragment:
            raise ValueError("Upload needs a direct folder/file URL without a query or fragment")
        local = root / local_name
        if local.is_symlink():
            raise ValueError(f"Upload cache is a symlink: {local}")
        local = local.resolve()
        if not local.is_relative_to(root):
            raise ValueError(f"Upload cache is outside working directory: {local}")
        if not local.exists():
            raise ValueError(f"Upload cache is missing: {local}")
        if directory:
            if not local.is_dir():
                raise ValueError(f"Upload cache must be a directory: {local}")
            files = sorted(local.rglob("*"))
            for file in files:
                if file.is_symlink():
                    raise ValueError(f"Refusing symlink in upload: {file}")
                if file.is_file():
                    entries.append(UploadFile(file, str(remote), file.relative_to(local), service))
        else:
            if not local.is_file():
                raise ValueError(f"Upload cache must be a file: {local}")
            remote_url = str(remote)
            name = unquote(urlparse(remote_url).path.rsplit("/", 1)[-1])
            if not name or name in {".", ".."} or "/" in name:
                raise ValueError(f"Invalid remote file path: {remote_url}")
            entries.append(UploadFile(local, remote_url, Path(name), service, True))

    for resource in document.resources:
        if not isinstance(resource, DriveRemoteResource):
            raise ValueError("Upload resources must have remote paths")
        add(resource.cache, resource.path, resource.serviceType, False)
    for catalog in document.catalogs:
        if not isinstance(catalog, DriveRemoteCatalog):
            raise ValueError("Upload catalogs must be remote directories")
        if catalog.entityType != "Directory":
            raise ValueError("Upload catalogs must have entityType Directory")
        add(catalog.cache, catalog.accessUrl, catalog.serviceType, True)
    if document.packages or not entries:
        raise ValueError("Upload needs at least one local file; packages are not supported")
    for entry in entries:
        if entry.service == "SharePoint" and entry.local.stat().st_size > 250_000_000:
            raise ValueError(f"File exceeds Microsoft Graph's 250 MB PUT limit: {entry.local}")
    return tuple(entries)


def upload(files: tuple[UploadFile, ...]) -> None:
    """Transfer a prepared plan; remote files outside it are never deleted."""
    if any("your-tenant" in file.remote.casefold() or "your-site" in file.remote.casefold() for file in files):
        raise ValueError("Replace the SharePoint placeholder accessURL before uploading")
    clients = {}
    for file in files:
        if file.service not in clients:
            clients[file.service] = get_client(adapter_from_service_type(file.service) or "")
        client = clients[file.service]
        folder = file.remote.rsplit("/", 1)[0] if file.direct_file else file.remote
        client.upload_to_folder(folder, file.relative, file.local)


__all__ = ["UploadFile", "plan_upload", "upload"]
