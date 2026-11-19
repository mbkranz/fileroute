"""Plan resolved metadata offline, then execute through provider clients.

Plans are concrete in-memory transfer entries. They are not persisted lockfiles:
remote permissions and directory contents are checked during execution.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
import mimetypes
import os
import shutil
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Iterator, Literal
from pathlib import Path, PureWindowsPath
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from fileroute.models import Catalog, Resource, Location, ServiceType
from fileroute.clients import get_provider
from fileroute.clients.base import BaseClient
from fileroute.clients.googledrive import DEFAULT_EXPORTS
from fileroute.descriptor import find, load, walk, resolve, local_path
from fileroute.resolution import lookup_item, parse_location
from fileroute.conversion import (
    ConversionResult,
    DocxToMarkdownRequest,
    MarkdownToDocxRequest,
    docx_to_markdown,
    markdown_to_docx,
)

DocumentConversion = Literal["docx-to-md", "md-to-docx"]


def _document_format(path: str | Path, declared: str | None = None) -> str | None:
    """Recognize only the built-in document pair, without opening files or URLs."""
    value = declared or Path(unquote(urlparse(str(path)).path)).suffix
    return {"docx": "docx", "md": "md", "markdown": "md"}.get(value.lower().lstrip("."))


def _location_format(location: Location | None, path: str) -> str | None:
    if location is None:
        return _document_format(path)
    return _document_format(location.remote_path or "", location.format) or (
        _document_format(path) if location.format is None else None
    )


def _document_conversion(
    source: str | None, output: str | None, *, direction: Literal["pull", "push"]
) -> DocumentConversion | None:
    if (direction == "pull" and source == "md" and output == "docx") or (
        direction == "push" and source == "docx" and output == "md"
    ):
        raise ValueError(
            "Automatic document transfers use DOCX sources/targets and a Markdown local path. "
            "Remote Markdown review bundles require their retained files as well; "
            "use a Markdown local path for this round trip."
        )
    if source == "docx" and output == "md":
        return "docx-to-md"
    if source == "md" and output == "docx":
        return "md-to-docx"
    return None


def _convert(
    source: Path, output: Path, conversion: DocumentConversion
) -> ConversionResult:
    """Adapt the two document operations; this is not a configurable task runner."""
    if conversion == "docx-to-md":
        return docx_to_markdown(DocxToMarkdownRequest(source=source, output=output))
    return markdown_to_docx(MarkdownToDocxRequest(source=source, output=output))


@dataclass(frozen=True)
class PullReceipt:
    """Observed input and output of one pull; absent metadata stays unknown.

    ``raw_source`` is an original retained download. Receipts describe local execution, not a provider acceptance decision.
    A failed operation saves its receipt before propagating its exception.
    """

    resource: str | None
    outcome: Literal["complete", "failed"]
    stage: str
    source_url: str
    source_id: str | None
    source_version: str | None
    source_sha256: str | None
    raw_source: Path | None
    output: Path
    output_sha256: str | None = None
    error: str | None = None
    report: Path | None = None
    conversion: DocumentConversion | None = None
    unsupported: tuple[str, ...] = ()


@dataclass(frozen=True)
class PushReceipt:
    """One independently prepared publication and its truthful effect status."""

    resource: str | None
    outcome: Literal["prepared", "complete", "failed", "unknown"]
    stage: str
    input: Path
    input_sha256: str | None
    output: Path
    output_sha256: str | None
    destination: str
    error: str | None = None
    report: Path | None = None
    conversion: DocumentConversion | None = None


def _digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _report(receipt: PullReceipt | PushReceipt) -> None:
    if receipt.report is not None:
        receipt.report.parent.mkdir(parents=True, exist_ok=True)
        receipt.report.write_text(
            json.dumps(asdict(receipt), default=str, indent=2) + "\n", encoding="utf-8"
        )


def _run_directory(run_dir: Path | None, action: str) -> Path | None:
    if run_dir is None:
        return None
    result = run_dir.resolve() / f"{action}-{uuid4().hex}"
    result.mkdir(parents=True)
    return result


def _promote(source: Path, target: Path, root: Path) -> None:
    """Atomically replace one local artifact after a completed download."""
    target = local_path(str(target), root, reject_symlinks=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=target.parent, prefix=".fileroute-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as original:
                shutil.copyfileobj(original, stream)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _scope(
    descriptor: Path, selector: str | None, direction: str
) -> tuple[Catalog, Catalog | Resource | None]:
    """Expand links and identify one transfer scope before parsing locations."""
    document = load(descriptor, resolve_references=True)
    if selector is None:
        return resolve(document, direction=direction), None
    selected = find(document, selector)
    if isinstance(selected, Location):
        raise ValueError(
            "Transfers select a resource or catalog, not a location; select its owning resource or catalog"
        )
    if not isinstance(selected, (Catalog, Resource)):
        raise ValueError(f"{selector!r} must select a resource or catalog")
    return document, selected


@dataclass(frozen=True)
class PullEntry:
    local: Path
    remote: str
    service_type: ServiceType
    directory: bool = False
    location: Location | None = field(default=None, compare=False, repr=False)
    root: Path | None = None
    resource: str | None = None
    conversion: DocumentConversion | None = None


def plan_pull(
    descriptor: Path, *, root: Path | None = None, selector: str | None = None
) -> tuple[PullEntry, ...]:
    """Plan remote sources to local paths, offline.

    Multiple sources can describe a transformation; Fileroute cannot reproduce
    that transformation and refuses to choose one. Local provenance is likewise
    not a download instruction. Targets are never used for retrieval.
    """
    root = (root or Path.cwd()).resolve()
    document, selected = _scope(descriptor, selector, "pull")
    entries = []
    pointer = next(
        (
            row.json_pointer
            for row in walk(document, include_self=True)
            if row.model is selected
        ),
        None,
    )
    for row in walk(document, include_self=True):
        if (
            pointer is not None
            and row.json_pointer != pointer
            and not row.json_pointer.startswith(pointer + "/")
        ):
            continue
        entity = row.model
        if isinstance(entity, Catalog) and (entity.resources or entity.catalogs):
            continue
        if isinstance(entity, (Catalog, Resource)) and any(
            source.resource_path_template is not None for source in entity.sources
        ):
            raise ValueError(
                f"{row.name_path or 'root'}: run fileroute discover before pulling a templated source"
            )
        if not isinstance(entity, (Resource, Catalog)) or not entity.sources:
            continue
        if len(entity.sources) != 1:
            raise ValueError(
                f"{row.name_path or 'root'}: pull requires exactly one source; build derived artifacts separately"
            )
        if entity.path is None:
            raise ValueError(
                f"{row.name_path or 'root'}: set path for the local artifact before pulling"
            )
        source = entity.sources[0]
        if selected is not None:
            parse_location(source, required=False)
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
        entries.append(
            PullEntry(
                local,
                source.path,
                service,
                directory,
                source,
                root=root,
                resource=row.name_path or "root",
                conversion=(
                    _document_conversion(
                        _location_format(source, source.path),
                        _document_format(local),
                        direction="pull",
                    )
                    if not directory
                    else None
                ),
            )
        )
    if not entries:
        raise ValueError(
            f"Pull needs at least one artifact with a remote source{f' in {selector!r}' if selector else ''}"
        )
    return tuple(entries)


@contextmanager
def _transfer_clients(
    entries: tuple[PullEntry, ...] | tuple[PushEntry, ...], *, profile: str | None
) -> Iterator[dict[tuple[ServiceType, str | None], BaseClient]]:
    """Prepare each selected client before transfers and close it on scope exit."""
    keys = (
        (
            entry.service_type,
            (entry.location.auth_profile if entry.location is not None else None)
            or profile,
        )
        for entry in entries
    )
    with _location_clients(keys) as clients:
        yield clients


@contextmanager
def _location_clients(
    keys: Iterator[tuple[ServiceType, str | None]],
) -> Iterator[dict[tuple[ServiceType, str | None], BaseClient]]:
    """Open each distinct provider/profile pair and close it on scope exit."""
    with ExitStack() as stack:
        clients: dict[tuple[ServiceType, str | None], BaseClient] = {}
        for key in keys:
            service_type, selected = key
            if key not in clients:
                if selected is None:
                    clients[key] = get_provider(service_type).build_default()
                else:
                    from fileroute.auth.profiles import build_profile_client

                    clients[key] = build_profile_client(selected, service_type)
                stack.callback(clients[key].close)
        yield clients


def _source_name(item: Any, entry: PullEntry) -> str:
    name = str(getattr(item, "name", None) or entry.local.name)
    if (
        name in {".", ".."}
        or PureWindowsPath(name).drive
        or any(char in name for char in ("/", "\\", "\0"))
    ):
        raise ValueError(f"Unsafe downloaded filename: {name!r}")
    mime = getattr(item, "mime_type", None)
    # Google Workspace downloads are exported using the provider's default
    # export format; displayed extensions do not determine the exported bytes.
    exported_mime = DEFAULT_EXPORTS.get(mime)
    extension = (
        mimetypes.guess_extension(exported_mime)
        if exported_mime
        else (mimetypes.guess_extension(mime) if mime else None)
    )
    if exported_mime and extension and Path(name).suffix.lower() != extension:
        name += extension
    elif extension and not Path(name).suffix:
        name += extension
    return name


def _pull_conversion(
    item: Any, target: Path, entry: PullEntry
) -> DocumentConversion | None:
    if entry.directory:
        return None
    mime = getattr(item, "mime_type", None)
    exported = DEFAULT_EXPORTS.get(mime, mime)
    extension = mimetypes.guess_extension(exported) if exported else None
    source_format = (
        (_document_format("source" + extension) if extension else None)
        or _location_format(entry.location, entry.remote)
        or _document_format(_source_name(item, entry))
    )
    return _document_conversion(
        source_format, _document_format(target), direction="pull"
    )


def _source_id(item: Any, entry: PullEntry) -> str | None:
    try:
        identifier = item.id
    except (AttributeError, ValueError):
        identifier = None
    if identifier is None and not entry.directory and entry.location is not None:
        identifier = entry.location.service_id
    return str(identifier) if identifier is not None else None


def _source_version(item: Any) -> str | None:
    version = getattr(item, "version", None) or getattr(item, "_version", None)
    if version is None:
        payload = getattr(item, "_api_payload", {})
        if isinstance(payload, dict):
            version = payload.get("version") or payload.get("eTag")
    return str(version) if version is not None else None


def pull(
    entries: tuple[PullEntry, ...],
    *,
    profile: str | None = None,
    run_dir: Path | None = None,
) -> tuple[PullReceipt, ...]:
    """Download planned files after resolving paths and selected credentials.

    A location's ``authProfile`` overrides the run's ``profile`` default. An
    unnamed location/run retains the provider's legacy credential discovery.
    Profile selection is deferred until execution; planning remains offline.
    A DOCX source and Markdown local path select the built-in review conversion.
    Provider export metadata can resolve source formats hidden by opaque URLs.
    An explicit ``run_dir`` also retains raw downloads and receipts.
    """
    if not entries:
        return ()
    execution = _run_directory(run_dir, "pull")
    receipts = []
    with _transfer_clients(entries, profile=profile) as clients, ExitStack() as staging:
        files = []
        destinations = set()
        for entry in entries:
            selected = (
                entry.location.auth_profile if entry.location is not None else None
            ) or profile
            client = clients[(entry.service_type, selected)]
            item = (
                lookup_item(client, entry.location)
                if entry.location is not None and hasattr(client, "get_from_location")
                else client.get_from_weburl(entry.remote)
            )
            if item.is_directory != entry.directory:
                raise ValueError(
                    f"Source has the wrong file/directory type: {entry.remote}"
                )
            if entry.directory:
                for child in item.iter_files():
                    # Provider paths include the remote root; strip exactly that prefix.
                    relative = Path(child.path).relative_to(Path(item.path))
                    target = local_path(
                        str(relative), entry.local, reject_symlinks=True
                    )
                    files.append((child, target, entry, None))
            else:
                files.append((
                    item,
                    local_path(
                        str(entry.local), entry.local.parent, reject_symlinks=True
                    ),
                    entry,
                    _pull_conversion(item, entry.local, entry),
                ))
        for item, target, entry, conversion in files:
            if target in destinations or (target.exists() and not target.is_file()):
                raise ValueError(f"Conflicting pull destination: {target}")
            destinations.add(target)
        for target in destinations:
            if any(parent in destinations for parent in target.parents):
                raise ValueError(f"File and directory destinations overlap: {target}")
        for _, target, _, conversion in files:
            if conversion == "docx-to-md":
                bundle = target.parent / f"{target.stem}.assets"
                if any(
                    path == bundle or path.is_relative_to(bundle)
                    for path in destinations
                ):
                    raise ValueError(
                        f"Pull destination overlaps retained review assets: {bundle}"
                    )
        for index, (item, target, entry, conversion) in enumerate(files):
            directory = execution / f"{index:04d}" if execution is not None else None
            receipt = PullReceipt(
                resource=entry.resource,
                outcome="failed",
                stage="download",
                source_url=getattr(item, "source_url", None) or entry.remote,
                source_id=_source_id(item, entry),
                source_version=_source_version(item),
                source_sha256=None,
                raw_source=None,
                output=target,
                report=directory / "receipt.json" if directory else None,
                conversion=conversion,
            )
            try:
                if directory is None and conversion is None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    item.download(target)
                else:
                    work = directory or Path(
                        staging.enter_context(
                            TemporaryDirectory(prefix="fileroute-pull-")
                        )
                    )
                    raw = work / "raw" / _source_name(item, entry)
                    if conversion:
                        raw = raw.with_suffix(
                            ".docx" if conversion == "docx-to-md" else ".md"
                        )
                    raw.parent.mkdir(parents=True)
                    receipt = replace(receipt, raw_source=raw)
                    item.download(raw)
                    digest = _digest(raw)
                    receipt = replace(
                        receipt, raw_source=raw, source_sha256=digest, stage="promote"
                    )
                    if conversion:
                        receipt = replace(receipt, stage="convert")
                        converted = _convert(raw, target, conversion)
                        receipt = replace(
                            receipt,
                            raw_source=(
                                raw
                                if directory
                                else converted.assets / "original.docx"
                                if converted.assets
                                else None
                            ),
                            unsupported=tuple(converted.unsupported),
                        )
                    else:
                        _promote(raw, target, entry.root or target.parent)
                receipt = replace(
                    receipt,
                    outcome="complete",
                    stage="complete",
                    output_sha256=_digest(target) if target.is_file() else None,
                )
            except Exception as exc:
                _report(replace(receipt, error=str(exc)))
                raise
            _report(receipt)
            receipts.append(receipt)
    return tuple(receipts)


@dataclass(frozen=True)
class PushEntry:
    local: Path
    remote: str
    relative: Path
    service_type: ServiceType
    direct_file: bool = False
    location: Location | None = field(default=None, compare=False, repr=False)
    root: Path | None = None
    resource: str | None = None
    conversion: DocumentConversion | None = None

    @property
    def destination(self) -> str:
        if self.service_type is ServiceType.GOOGLE_DRIVE and not self.direct_file:
            # A Drive folder URL is an ID, not a hierarchical URL prefix.
            return f"{self.remote} :: {self.relative.as_posix()}"
        return (
            self.remote
            if self.direct_file
            else f"{self.remote.rstrip('/')}/{quote(self.relative.as_posix(), safe='/')}"
        )


def _destination_key(entry: PushEntry) -> str:
    """Remote identities stay independent of authentication and processing state."""
    if entry.service_type is ServiceType.GOOGLE_DRIVE:
        identity = entry.location.service_id if entry.location else None
        return (
            f"file:{identity or entry.remote}"
            if entry.direct_file
            else f"folder:{identity or entry.remote}:{entry.relative.as_posix()}"
        )
    return (
        unquote(entry.destination).casefold()
        if entry.service_type is ServiceType.SHAREPOINT
        else entry.destination
    )


def plan_push(
    descriptor: Path, *, root: Path | None = None, selector: str | None = None
) -> tuple[PushEntry, ...]:
    """Publish path to targets, never sources; validate everything before auth.

    Paths are relative to root (cwd by default). Catalog targets are folders;
    children inherit them using paths relative to the declaring catalog's path,
    or root when absent. Explicit child targets replace inherited ones; [] opts
    out. A catalog with children publishes only those children. A leaf catalog
    publishes its directory tree. Explicit resource targets are file URLs unless
    entityType is Directory/Container. Google Drive folder URLs also identify
    directory targets without an entityType hint.
    """
    root = (root or Path.cwd()).resolve()
    document, selected = _scope(descriptor, selector, "push")
    # Keep ancestors for inherited targets and path anchors, while skipping
    # sibling branches before their locations or local artifacts are checked.
    branch = None
    if selected is not None:
        pointer = next(
            row.json_pointer
            for row in walk(document, include_self=True)
            if row.model is selected
        )
        branch = {
            id(row.model)
            for row in walk(document, include_self=True)
            if pointer == row.json_pointer
            or pointer.startswith(row.json_pointer + "/")
            or row.json_pointer.startswith(pointer + "/")
        }
    entries: list[PushEntry] = []
    destinations: dict[str, Path] = {}

    def add(
        local: Path, target: Location, relative: Path | None, resource: str | None
    ) -> None:
        service = target.service_type
        provider = get_provider(service)
        if not provider.capabilities.supports_upload:
            raise ValueError(f"Upload is not implemented for {service}")
        url = urlparse(target.path)
        if (url.query or url.fragment) and not (
            service is ServiceType.GOOGLE_DRIVE and provider.recognizes_url(target.path)
        ):
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
        conversion = _document_conversion(
            _document_format(local),
            _location_format(target, target.path),
            direction="push",
        )
        if (
            service is ServiceType.SHAREPOINT
            and conversion is None
            and local.stat().st_size > 250_000_000
        ):
            raise ValueError(
                f"File exceeds Microsoft Graph's 250 MB PUT limit: {local}"
            )
        if (
            service is ServiceType.GOOGLE_DRIVE
            and target.entity_type == "File"
            and "/folders/" in url.path
        ):
            raise ValueError(
                f"Google Drive folder URL cannot be a file target: {target.path}"
            )
        direct = relative is None
        if direct:
            if service is ServiceType.GOOGLE_DRIVE:
                if not target.service_id:
                    raise ValueError(
                        f"Google Drive file target {target.path!r} needs an ID; "
                        "use a file URL or a folder target to create a file"
                    )
                # File URLs end in /view or /edit, neither is a filename.
                relative = Path(local.name)
            else:
                name = unquote(url.path.rsplit("/", 1)[-1])
                if not name or name in {".", ".."} or "/" in name or "\\" in name:
                    raise ValueError(f"Invalid remote file path: {target.path}")
                relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe relative upload path: {relative}")
        if not direct and conversion:
            relative = relative.with_suffix(
                ".docx" if conversion == "md-to-docx" else ".md"
            )
        entry = PushEntry(
            local,
            target.path,
            relative,
            service,
            direct,
            target,
            root,
            resource,
            conversion=conversion,
        )
        # Decoding catches authored aliases for the same remote file.
        key = _destination_key(entry)
        if key in destinations:
            if destinations[key] != entry.local:
                raise ValueError(f"Multiple local files target {entry.destination}")
            return
        destinations[key] = entry.local
        entries.append(entry)

    def visit(
        entity: Catalog | Resource, inherited: list[Location], anchor: Path
    ) -> None:
        if branch is not None and id(entity) not in branch:
            return
        explicit = entity.targets is not None
        targets = entity.targets if explicit else inherited
        if any(target.resource_path_template is not None for target in targets or []):
            raise ValueError(
                "resourcePathTemplate is supported only on discovery sources"
            )
        if selected is not None and explicit:
            for target in targets or []:
                parse_location(target, required=True)
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
            children = [*entity.resources.values(), *entity.catalogs.values()]
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
                        add(safe, target, safe.relative_to(anchor), None)
        elif targets:
            if local is None or not local.exists():
                raise ValueError(f"Upload input missing: {entity.path}")
            for target in targets:
                relative = (
                    (
                        Path(local.name)
                        if target.entity_type in {"Directory", "Container"}
                        or (
                            target.service_type is ServiceType.GOOGLE_DRIVE
                            and "/folders/" in urlparse(target.path).path
                        )
                        else None
                    )
                    if explicit
                    else local.relative_to(anchor)
                )
                resource = next(
                    row.name_path
                    for row in walk(document, include_self=True)
                    if row.model is entity
                )
                add(local, target, relative, resource)

    visit(document, [], root)
    if not entries:
        raise ValueError(
            f"Upload needs at least one local file with targets{f' in {selector!r}' if selector else ''}"
        )
    return tuple(entries)


def _push_conversion(file: PushEntry, client: BaseClient) -> DocumentConversion | None:
    if file.conversion:
        return file.conversion
    target_format = _location_format(file.location, file.remote)
    source_format = _document_format(file.local)
    if (
        target_format is None
        and source_format is not None
        and file.service_type is ServiceType.GOOGLE_DRIVE
        and file.direct_file
        and file.location is not None
    ):
        # Exact Drive URLs carry stable IDs, not names. Resolve the existing
        # target's metadata without changing the ID or treating /view as a name.
        target = client.get_from_location(file.location)
        target_format = _document_format(target.name)
    return _document_conversion(source_format, target_format, direction="push")


def push(
    files: tuple[PushEntry, ...],
    *,
    profile: str | None = None,
    run_dir: Path | None = None,
) -> tuple[PushReceipt, ...]:
    """Publish a plan using location profiles ahead of the run's default.

    All selected profiles are checked before uploading any bytes. Unnamed
    locations/runs retain legacy credentials. Files outside the plan are never
    deleted, and planning never loads authentication configuration.
    Markdown-to-DOCX targets are prepared from the review profile before any
    upload; source Markdown and retained assets stay unchanged. Converted outputs
    are temporary unless ``run_dir`` retains them. A provider call with a lost response is recorded
    as unknown, since a local exception cannot prove that publication failed.
    """
    if not files:
        return ()
    if any(
        "your-tenant" in file.remote.casefold() or "your-site" in file.remote.casefold()
        for file in files
    ):
        raise ValueError("Replace the SharePoint placeholder target before uploading")
    execution = _run_directory(run_dir, "push")
    with (
        _transfer_clients(files, profile=profile) as clients,
        TemporaryDirectory(prefix="fileroute-push-") as temporary,
    ):
        prepared: list[tuple[PushEntry, PushReceipt]] = []
        destinations: set[str] = set()
        for index, file in enumerate(files):
            directory = execution / f"{index:04d}" if execution else None
            receipt = PushReceipt(
                resource=file.resource,
                outcome="failed",
                stage="prepare",
                input=file.local,
                input_sha256=_digest(file.local),
                output=file.local,
                output_sha256=None,
                destination=file.destination,
                report=directory / "receipt.json" if directory else None,
            )
            try:
                if (
                    file.service_type is ServiceType.GOOGLE_DRIVE
                    and file.direct_file
                    and file.location is None
                ):
                    raise ValueError(
                        "Google Drive file upload requires a resolved target"
                    )
                output = file.local
                selected = (
                    file.location.auth_profile if file.location is not None else None
                ) or profile
                conversion = _push_conversion(
                    file, clients[(file.service_type, selected)]
                )
                if conversion:
                    receipt = replace(receipt, stage="convert", conversion=conversion)
                    work = directory or Path(temporary) / f"{index:04d}"
                    output = work / (
                        "output.docx" if conversion == "md-to-docx" else "output.md"
                    )
                    _convert(file.local, output, conversion)
                effective = replace(file, local=output, conversion=conversion)
                if (
                    effective.service_type is ServiceType.SHAREPOINT
                    and output.stat().st_size > 250_000_000
                ):
                    raise ValueError(
                        f"File exceeds Microsoft Graph's 250 MB PUT limit: {output}"
                    )
                key = _destination_key(effective)
                if key in destinations:
                    raise ValueError(
                        f"Multiple publications target {effective.destination}"
                    )
                destinations.add(key)
                receipt = replace(
                    receipt,
                    outcome="prepared",
                    stage="prepared",
                    output=output,
                    output_sha256=_digest(output),
                    destination=effective.destination,
                )
            except Exception as exc:
                if directory:
                    directory.mkdir(parents=True, exist_ok=True)
                _report(replace(receipt, error=str(exc)))
                raise
            if directory:
                directory.mkdir(parents=True, exist_ok=True)
            _report(receipt)
            prepared.append((effective, receipt))
        results = []
        for file, receipt in prepared:
            selected = (
                file.location.auth_profile if file.location is not None else None
            ) or profile
            client = clients[(file.service_type, selected)]
            receipt = replace(receipt, outcome="unknown", stage="upload")
            _report(receipt)
            try:
                if file.service_type is ServiceType.GOOGLE_DRIVE and file.direct_file:
                    if file.location is None:
                        raise ValueError(
                            "Google Drive file upload requires a resolved target"
                        )
                    client.upload_to_file(file.location, file.local)
                else:
                    folder = (
                        file.remote.rsplit("/", 1)[0]
                        if file.direct_file
                        else file.remote
                    )
                    if file.location is not None and hasattr(
                        client, "upload_to_location"
                    ):
                        location = file.location.model_copy(deep=True)
                        if file.direct_file:
                            location.path = folder
                            location.service_id = (
                                None  # Exact file ID is not a folder ID.
                            )
                            if location.remote_path is not None:
                                location.remote_path = location.remote_path.rpartition(
                                    "/"
                                )[0]
                        client.upload_to_location(location, file.relative, file.local)
                    else:
                        client.upload_to_folder(folder, file.relative, file.local)
            except Exception as exc:
                _report(replace(receipt, error=str(exc)))
                raise
            receipt = replace(receipt, outcome="complete", stage="complete")
            _report(receipt)
            results.append(receipt)
    return tuple(results)


__all__ = [
    "DocumentConversion",
    "PullEntry",
    "PullReceipt",
    "PushEntry",
    "PushReceipt",
    "plan_pull",
    "plan_push",
    "pull",
    "push",
]
