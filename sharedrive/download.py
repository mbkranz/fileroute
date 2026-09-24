"""Materialize one remote source per artifact without interpreting provenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sharedrive.models import (
    Catalog,
    Resource,
    adapter_from_service_type,
    local_path,
    resolve_service_type,
)
from sharedrive.registry import get_client, get_provider


@dataclass(frozen=True)
class Download:
    local: Path
    remote: str
    service: str
    directory: bool = False


def plan_download(
    descriptor: Path, *, root: Path | None = None
) -> tuple[Download, ...]:
    """Plan remote sources to local paths, offline.

    Multiple sources can describe a transformation; Sharedrive cannot reproduce
    that transformation and refuses to choose one. Local provenance is likewise
    not a download instruction. Targets are never used for retrieval.
    """
    root = (root or Path.cwd()).resolve()
    document = Catalog.from_path(descriptor.resolve())
    entries = []
    for row in document.iter_entity_paths(include_self=True):
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
        service = resolve_service_type(source.path, source.serviceType)
        provider = get_provider(adapter_from_service_type(service) or "")
        if provider is None or not provider.capabilities.supports_download:
            raise ValueError(f"Download is not implemented for {service}")
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
        entries.append(Download(local, source.path, service, directory))
    if not entries:
        raise ValueError("Pull needs at least one artifact with a remote source")
    return tuple(entries)


def download(entries: tuple[Download, ...]) -> None:
    """Resolve remote items, check their paths, then download planned files."""
    clients = {}
    files = []
    destinations = set()
    for entry in entries:
        if entry.service not in clients:
            clients[entry.service] = get_client(
                adapter_from_service_type(entry.service) or ""
            )
        item = clients[entry.service].get_from_weburl(entry.remote)
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


__all__ = ["Download", "plan_download", "download"]
