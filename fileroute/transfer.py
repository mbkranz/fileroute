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
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any, Callable, Iterator, Literal
from pathlib import Path, PureWindowsPath
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from fileroute.models import Catalog, Resource, Location, ServiceType
from fileroute.clients import get_provider
from fileroute.clients.base import BaseClient
from fileroute.clients.googledrive import DEFAULT_EXPORTS
from fileroute.descriptor import find, load, walk, resolve, local_path
from fileroute.resolution import lookup_item, parse_location

if TYPE_CHECKING:
    from fileroute.tasks import SequenceSpec, StepsRequest, StepsResult

StepsRunner = Callable[["StepsRequest"], "StepsResult"]


@dataclass(frozen=True)
class PullReceipt:
    """Observed input and output of one pull; absent metadata stays unknown.

    ``raw_source`` is an original retained download, never the processing input
    copy. Receipts describe local execution, not a provider acceptance decision.
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


def _run_directory(
    entries: tuple[PullEntry, ...] | tuple[PushEntry, ...],
    run_dir: Path | None,
    action: str,
) -> Path | None:
    if run_dir is None and not any(entry.hook is not None for entry in entries):
        return None
    root = entries[0].root or Path.cwd()
    parent = (
        run_dir
        if run_dir is not None
        else local_path(".fileroute/runs", root, reject_symlinks=True)
    )
    result = parent.resolve() / f"{action}-{uuid4().hex}"
    result.mkdir(parents=True)
    return result


def _steps_request(
    entry: PullEntry | PushEntry,
    *,
    input: Path,
    output: Path,
    work_dir: Path,
    context: dict[str, Any] | None = None,
) -> StepsRequest:
    from fileroute.tasks import StepsRequest

    assert entry.hook is not None
    return StepsRequest(
        sequence=entry.hook,
        input=input,
        output=output,
        root=entry.root or Path.cwd(),
        catalog_dir=(
            entry.catalog_origin.parent if entry.catalog_origin else Path.cwd()
        ),
        work_dir=work_dir,
        context={"resource": entry.resource, **(context or {})},
        arguments=[],
    )


def _hook_name(entry: PullEntry | PushEntry) -> str:
    """Resolve only an output basename offline; native argument parsing does no I/O."""
    from fileroute.tasks import resolve_output

    work = (entry.root or Path.cwd()) / ".fileroute/processing-plan"
    request = _steps_request(
        entry,
        input=work / "input" / entry.local.name,
        output=work / "output" / entry.local.name,
        work_dir=work,
    )
    return resolve_output(request).name


def _processed_copy(
    entry: PullEntry | PushEntry,
    source: Path,
    directory: Path,
    *,
    runner: StepsRunner | None,
    context: dict[str, Any] | None = None,
) -> Path:
    """Give tasks disposable copies; independently validate the returned output."""
    from fileroute.tasks import execute_steps, resolve_output

    work = directory / "work"
    input = work / "input" / source.name
    input.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, input)
    request = _steps_request(
        entry,
        input=input,
        output=work / "output" / entry.local.name,
        work_dir=work,
        context=context,
    )
    candidate = local_path(
        str(resolve_output(request)), work.resolve(), reject_symlinks=True
    )
    candidate.parent.mkdir(parents=True, exist_ok=True)
    result = (runner or execute_steps)(request)
    if result.exit_code != 0:
        raise ValueError(f"Processing exited with status {result.exit_code}")
    output = local_path(str(result.output), work.resolve(), reject_symlinks=True)
    if not output.is_file():
        raise ValueError(f"Processing output is not a regular file: {output}")
    if output != candidate.resolve():
        raise ValueError(
            "Processing returned an output different from its declared output"
        )
    return output


def _promote(source: Path, target: Path, root: Path) -> None:
    """Replace one local artifact only after a successful, validated processing run."""
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
    catalog_origin: Path | None = None
    resource: str | None = None
    hook: SequenceSpec | None = None


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
        hook = getattr(source, "after_pull", None)
        if directory and hook is not None:
            raise ValueError(
                "afterPull requires a file resource; discover concrete resources before processing a directory"
            )
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
                catalog_origin=row.origin_descriptor,
                resource=row.name_path or "root",
                hook=hook,
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
    runner: StepsRunner | None = None,
) -> tuple[PullReceipt, ...]:
    """Download planned files after resolving paths and selected credentials.

    A location's ``authProfile`` overrides the run's ``profile`` default. An
    unnamed location/run retains the provider's legacy credential discovery.
    Profile selection is deferred until execution; planning remains offline.
    Source ``afterPull`` sequences receive working copies, and only successful
    outputs replace artifacts. Their original downloads and receipts are retained
    below ``run_dir`` (or ``.fileroute/runs``). An explicit run directory also
    records raw inputs for ordinary file pulls. Existing directory pulls cannot
    carry processing sequences; discover concrete resources first.
    """
    if not entries:
        return ()
    if any(entry.directory and entry.hook is not None for entry in entries):
        raise ValueError("afterPull requires concrete file resources, not a directory")
    execution = _run_directory(entries, run_dir, "pull")
    receipts = []
    with _transfer_clients(entries, profile=profile) as clients:
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
                    files.append((child, target, entry))
            else:
                files.append((
                    item,
                    local_path(
                        str(entry.local), entry.local.parent, reject_symlinks=True
                    ),
                    entry,
                ))
        for item, target, entry in files:
            if target in destinations or (target.exists() and not target.is_file()):
                raise ValueError(f"Conflicting pull destination: {target}")
            if (
                entry.hook is None
                and target.suffix.lower() == ".md"
                and Path(_source_name(item, entry)).suffix.lower()
                in {".doc", ".docx", ".pdf", ".pptx", ".xlsx", ".xls", ".zip", ".msg"}
            ):
                raise ValueError(
                    "Markdown output needs source afterPull conversion; use fileroute steps add --auto"
                )
            destinations.add(target)
        for target in destinations:
            if any(parent in destinations for parent in target.parents):
                raise ValueError(f"File and directory destinations overlap: {target}")
        for index, (item, target, entry) in enumerate(files):
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
            )
            try:
                if directory is None:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    item.download(target)
                else:
                    raw = directory / "raw" / _source_name(item, entry)
                    raw.parent.mkdir(parents=True)
                    receipt = replace(receipt, raw_source=raw)
                    item.download(raw)
                    digest = _digest(raw)
                    receipt = replace(
                        receipt, raw_source=raw, source_sha256=digest, stage="afterPull"
                    )
                    output = (
                        _processed_copy(
                            entry,
                            raw,
                            directory,
                            runner=runner,
                            context={
                                "source": {
                                    "url": receipt.source_url,
                                    "id": receipt.source_id,
                                    "version": receipt.source_version,
                                    "sha256": digest,
                                },
                                "resource_path": str(entry.local),
                            },
                        )
                        if entry.hook is not None
                        else raw
                    )
                    if _digest(raw) != digest:
                        raise ValueError(
                            "Processing changed the retained original download"
                        )
                    receipt = replace(receipt, stage="promote")
                    _promote(output, target, entry.root or target.parent)
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
    catalog_origin: Path | None = None
    resource: str | None = None
    hook: SequenceSpec | None = None

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


def _hook_identity(entry: PushEntry) -> tuple[Path, str | None, Path | None]:
    return (
        entry.local,
        json.dumps(entry.hook.model_dump(mode="json"), sort_keys=True)
        if entry.hook is not None
        else None,
        entry.catalog_origin if entry.hook is not None else None,
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
    destinations: dict[str, tuple[Path, str | None, Path | None]] = {}
    origins = {
        id(location): row.origin_descriptor
        for row in walk(document, include_self=True)
        if isinstance(row.model, (Catalog, Resource))
        for location in row.model.targets or []
    }

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
        hook = getattr(target, "before_push", None)
        if (
            hook is None
            and service is ServiceType.SHAREPOINT
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
        entry = PushEntry(
            local,
            target.path,
            relative,
            service,
            direct,
            target,
            root,
            origins.get(id(target)),
            resource,
            hook,
        )
        if hook is not None and not direct:
            entry = replace(entry, relative=relative.with_name(_hook_name(entry)))
        # Decoding catches authored aliases for the same remote file.
        key = _destination_key(entry)
        if key in destinations:
            if destinations[key] != _hook_identity(entry):
                raise ValueError(
                    f"Multiple local files or processing sequences target {entry.destination}"
                )
            return
        destinations[key] = _hook_identity(entry)
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
            if any(
                getattr(target, "before_push", None) is not None for target in targets
            ):
                raise ValueError(
                    "beforePush requires concrete file resources, not a leaf directory catalog"
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


def push(
    files: tuple[PushEntry, ...],
    *,
    profile: str | None = None,
    run_dir: Path | None = None,
    runner: StepsRunner | None = None,
) -> tuple[PushReceipt, ...]:
    """Publish a plan using location profiles ahead of the run's default.

    All selected profiles are checked before uploading any bytes. Unnamed
    locations/runs retain legacy credentials. Files outside the plan are never
    deleted, and planning never loads authentication configuration. Each target's
    ``beforePush`` receives an independent working copy. All transformed outputs
    are prepared and checked before the first upload; canonical inputs are never
    passed as task output paths. A provider call with a lost response is recorded
    as unknown, since a local exception cannot prove that publication failed.
    """
    if not files:
        return ()
    if any(
        "your-tenant" in file.remote.casefold() or "your-site" in file.remote.casefold()
        for file in files
    ):
        raise ValueError("Replace the SharePoint placeholder target before uploading")
    execution = _run_directory(files, run_dir, "push")
    with _transfer_clients(files, profile=profile) as clients:
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
                if file.hook is not None:
                    assert directory is not None
                    output = _processed_copy(file, file.local, directory, runner=runner)
                    if _digest(file.local) != receipt.input_sha256:
                        raise ValueError(
                            "Processing changed the canonical publication input"
                        )
                relative = file.relative
                if file.hook is not None and not file.direct_file:
                    relative = relative.with_name(output.name)
                    if relative != file.relative:
                        raise ValueError(
                            "Processing output filename differs from the planned destination"
                        )
                effective = replace(file, local=output, relative=relative)
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
    "PullEntry",
    "PullReceipt",
    "PushEntry",
    "PushReceipt",
    "StepsRunner",
    "plan_pull",
    "plan_push",
    "pull",
    "push",
]
