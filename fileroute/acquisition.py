"""Metadata-driven pull decisions and verified, atomic acquisition of Drive bytes."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import file_digest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fileroute.clients.googledrive import FOLDER_MIME, GoogleDriveClient
from fileroute.discovery import DiscoveryObservation
from fileroute.exceptions import GoogleDriveError
from fileroute.models import ServiceType


class SourceObservation(BaseModel):
    """Exact item facts. relative_path is populated only with verified root ancestry."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    url: str
    mime_type: str
    name: str
    parent_ids: tuple[str, ...]
    version: str | None
    trashed: bool
    properties: dict[str, str]
    root_id: str | None = None
    relative_path: str | None = None
    checksum: str | None = None


def discovered_drive_file(row: DiscoveryObservation) -> SourceObservation:
    """Read a positive Drive listing observation without another API call."""
    data = row.metadata
    if (
        not row.observed
        or row.error
        or row.service_type != ServiceType.GOOGLE_DRIVE
        or not row.service_id
    ):
        raise ValueError("Expected a positive Google Drive file observation")
    if not all(
        data.get(key)
        for key in (
            "mimeType",
            "relativePath",
            "rootId",
            "sourceUrl",
            "name",
            "parents",
        )
    ):
        raise ValueError("Incomplete Drive discovery metadata")
    return SourceObservation(
        id=row.service_id,
        url=data["sourceUrl"],
        mime_type=data["mimeType"],
        name=data["name"],
        parent_ids=tuple(data["parents"]),
        version=data.get("version"),
        trashed=False,
        properties=data.get("appProperties", {}),
        root_id=data["rootId"],
        relative_path=data["relativePath"],
        checksum=data.get("sha256Checksum"),
    )


def observe_drive(
    client: GoogleDriveClient, file_id: str, *, root_id: str | None = None
) -> SourceObservation:
    """Inspect exact ID and, optionally, its current path below root. 404 remains ambiguous."""
    item = client.get_file(file_id)
    if item.id != file_id or not item.mimeType or item.trashed is None:
        raise ValueError(f"Incomplete Drive metadata for {file_id}")
    current = item
    seen = set()
    parts = []
    relative = None
    trashed = item.trashed
    while root_id and not trashed:
        if current.id in seen:
            raise ValueError("Repeated ancestor in Drive lineage")
        seen.add(current.id)
        if current.id == root_id:
            if current.mimeType != FOLDER_MIME:
                raise ValueError("Drive root must be a folder")
            relative = "/".join(reversed(parts))
            break
        if (
            not current.name
            or current.name in {".", ".."}
            or "/" in current.name
            or "\\" in current.name
        ):
            raise ValueError("Drive name cannot be represented in a template path")
        parts.append(current.name)
        if not current.parents:
            break
        if len(current.parents) != 1:
            raise ValueError("Ambiguous Drive lineage")
        parent = current.parents[0]
        current = client.get_file(parent)
        if (
            current.id != parent
            or current.mimeType != FOLDER_MIME
            or current.trashed is None
        ):
            raise ValueError("Incomplete Drive folder lineage")
        trashed = trashed or current.trashed
    return SourceObservation(
        id=file_id,
        url=item.webViewLink or f"https://drive.google.com/file/d/{file_id}/view",
        mime_type=item.mimeType,
        name=item.name or "",
        parent_ids=tuple(item.parents),
        version=item.version,
        trashed=trashed,
        properties=item.appProperties,
        checksum=item.sha256Checksum,
        root_id=root_id,
        relative_path=relative,
    )


class VerifiedReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: SourceObservation
    raw: Path
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    export_mime: str | None = None
    observed_at: datetime


def acquire_verified(
    client: GoogleDriveClient,
    expected: SourceObservation,
    destination: Path,
    *,
    export_mime: str | None = None,
) -> VerifiedReceipt:
    """Stage raw media or explicit native export and promote only after a second stable observation."""

    def inspect() -> SourceObservation:
        return observe_drive(client, expected.id, root_id=expected.root_id)

    if (
        expected.trashed
        or not expected.version
        or (expected.root_id and expected.relative_path is None)
    ):
        raise ValueError(
            "Verified acquisition requires a live, versioned source in scope"
        )
    if expected.mime_type == FOLDER_MIME or expected.mime_type.startswith(
        "application/vnd.google-apps."
    ) != bool(export_mime):
        raise ValueError(
            "Specify an export MIME type only for a native Workspace document"
        )
    if inspect() != expected:
        raise ValueError("Drive source changed before acquisition")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".fileroute-") as directory:
        staged = Path(directory) / "raw"
        if export_mime:
            client.export_file(
                expected.id, mime_type=export_mime, output_path=str(staged)
            )
        else:
            client.download_file(expected.id, output_path=str(staged))
        with staged.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
        if inspect() != expected or (
            not export_mime and expected.checksum and expected.checksum != digest
        ):
            raise ValueError(
                "Drive source changed during acquisition or checksum verification failed"
            )
        staged.replace(destination)
    return VerifiedReceipt(
        source=expected,
        raw=destination,
        sha256=digest,
        export_mime=export_mime,
        observed_at=datetime.now(timezone.utc),
    )


class VerifiedContent(BaseModel):
    """Identity of previously verified raw bytes in a caller-verified durable store."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    service_type: ServiceType
    service_id: str
    version: str
    mime_type: str
    export_mime: str | None = None
    raw: Path | None = None
    sha256: str = Field(pattern="^[0-9a-f]{64}$")


class PullDecision(BaseModel):
    observation: DiscoveryObservation
    action: Literal["acquire", "reuse", "unavailable"]


def plan_incremental_pull(
    observations: list[DiscoveryObservation],
    *,
    verified: tuple[VerifiedContent, ...] = (),
    export_mime: str | None = None,
) -> list[PullDecision]:
    """Plan selected observations. Callers supply receipts only after verifying their raw artifact or durable pointer."""
    receipts = {(row.service_type, row.service_id): row for row in verified}
    if len(receipts) != len(verified):
        raise ValueError("Duplicate incremental pull identity")
    decisions = []
    for row in observations:
        receipt = receipts.get((row.service_type, row.service_id))
        if (
            not row.observed
            or row.error
            or not row.service_id
            or "missing_from_scope" in row.statuses
        ):
            action = "unavailable"
        elif (
            receipt
            and receipt.version
            and receipt.version == row.metadata.get("version")
            and receipt.mime_type == row.metadata.get("mimeType")
            and receipt.export_mime == export_mime
            and (
                export_mime
                or not row.metadata.get("sha256Checksum")
                or receipt.sha256 == row.metadata["sha256Checksum"]
            )
        ):
            action = "reuse"
            if receipt.raw is not None:
                try:
                    with receipt.raw.open("rb") as stream:
                        if file_digest(stream, "sha256").hexdigest() != receipt.sha256:
                            action = "acquire"
                except OSError:
                    action = "acquire"
        else:
            action = "acquire"
        decisions.append(PullDecision(observation=row, action=action))
    return decisions


class DriveEffectUnconfirmed(RuntimeError):
    """The provider could not confirm the requested effect by exact identity."""


def ensure_reserved_binary(
    client: GoogleDriveClient,
    file_id: str,
    *,
    parent: str,
    name: str,
    source: Path,
    mime_type: str,
    properties: dict[str, str],
) -> SourceObservation:
    """Create at most one binary object, verifying ownership after a conflict or lost response."""
    if mime_type.startswith("application/vnd.google-apps."):
        raise ValueError("Reserved ID creation requires a binary MIME type")
    try:
        file = observe_drive(client, file_id)
    except GoogleDriveError as exc:
        if exc.status_code != 404:
            raise
        try:
            client.create_file(
                name,
                parent,
                source,
                mime_type=mime_type,
                metadata={"id": file_id, "appProperties": properties},
            )
        except GoogleDriveError as write_error:
            if (
                write_error.status_code not in (None, 409, 408, 429)
                and write_error.status_code < 500
            ):
                raise
        try:
            file = observe_drive(client, file_id)
        except (GoogleDriveError, ValueError) as read_error:
            raise DriveEffectUnconfirmed(
                "Could not verify the reserved Drive file"
            ) from read_error
    if file.trashed or file.mime_type != mime_type or file.properties != properties:
        raise ValueError("Reserved Drive file differs from its expected binding")
    return file


class MoveResult(BaseModel):
    outcome: Literal["completed", "stale", "unsupported", "unconfirmed"]


def checked_move(
    client: GoogleDriveClient,
    expected: SourceObservation,
    destination: str,
    *,
    conditional_supported: bool = False,
) -> MoveResult:
    """Move only with operator-verified conditional support, then read back the exact ID."""
    if not conditional_supported:
        return MoveResult(outcome="unsupported")
    current, etag = client.get_file_with_etag(expected.id)
    if not etag:
        return MoveResult(outcome="unsupported")
    if (
        not expected.version
        or expected.trashed
        or current.version != expected.version
        or len(expected.parent_ids) != 1
        or observe_drive(client, expected.id, root_id=expected.root_id) != expected
    ):
        return MoveResult(outcome="stale")
    try:
        client.move_if_match(expected.id, expected.parent_ids[0], destination, etag)
    except GoogleDriveError as exc:
        if exc.status_code == 412:
            return MoveResult(outcome="stale")
    try:
        actual = observe_drive(client, expected.id, root_id=expected.root_id)
    except (GoogleDriveError, ValueError):
        return MoveResult(outcome="unconfirmed")
    return MoveResult(
        outcome="completed"
        if actual.parent_ids == (destination,)
        and not actual.trashed
        and (not expected.root_id or actual.relative_path)
        else "unconfirmed"
    )


__all__ = [
    "SourceObservation",
    "observe_drive",
    "discovered_drive_file",
    "VerifiedReceipt",
    "acquire_verified",
    "VerifiedContent",
    "PullDecision",
    "plan_incremental_pull",
    "DriveEffectUnconfirmed",
    "ensure_reserved_binary",
    "MoveResult",
    "checked_move",
]
