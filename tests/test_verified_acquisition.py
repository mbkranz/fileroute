from pathlib import Path

import pytest

from fileroute.acquisition import (
    VerifiedContent,
    acquire_verified,
    checked_move,
    observe_drive,
    plan_incremental_pull,
    ensure_reserved_binary,
)
from fileroute.clients.googledrive import FOLDER_MIME, GDriveApiFile
from fileroute.discovery import (
    DiscoveryObservation,
    match_source_path,
    _changed_observations,
)
from fileroute.models import Location
from fileroute.exceptions import GoogleDriveError


class Drive:
    def __init__(self):
        self.files = {
            key: GDriveApiFile(
                id=key,
                name=name,
                parents=parents,
                mimeType=mime,
                trashed=False,
                version="1",
            )
            for key, name, parents, mime in [
                ("root", "Root", [], FOLDER_MIME),
                ("folder", "Ready", ["root"], FOLDER_MIME),
                ("doc", "Proposal", ["folder"], "application/vnd.google-apps.document"),
            ]
        }
        self.race = False
        self.writes = 0

    def get_file(self, key):
        if key not in self.files:
            raise GoogleDriveError("Unavailable", status_code=404)
        return self.files[key].model_copy(deep=True)

    def export_file(self, key, *, mime_type, output_path):
        Path(output_path).write_bytes(b"export")
        if self.race:
            self.files["folder"].name = "Review"

    def create_file(self, name, parent, source, mime_type, metadata):
        self.writes += 1
        self.files[metadata["id"]] = GDriveApiFile(
            id=metadata["id"],
            name=name,
            parents=[parent],
            mimeType=mime_type,
            version="1",
            trashed=False,
            appProperties=metadata["appProperties"],
        )
        raise GoogleDriveError("Lost response")


def test_export_survives_extensionless_name_and_rejects_ancestor_change(tmp_path):
    drive = Drive()
    source = observe_drive(drive, "doc", root_id="root")
    assert source.relative_path == "Ready/Proposal"
    template = Location(
        path="root", resourcePathTemplate="{stage}/{filename}", matchPathSegments=True
    )
    assert match_source_path(template, source.relative_path) == {
        "stage": "Ready",
        "filename": "Proposal",
    }
    assert match_source_path(template, "Ready/archive/Proposal") is None
    path = tmp_path / "capture.docx"
    receipt = acquire_verified(drive, source, path, export_mime="application/example")
    assert receipt.raw.read_bytes() == b"export"
    drive.race = True
    path.write_bytes(b"previous")
    with pytest.raises(ValueError, match="changed"):
        acquire_verified(drive, source, path, export_mime="application/example")
    assert path.read_bytes() == b"previous"
    assert _changed_observations(
        {"relativePath": "Ready/Proposal", "parents": ["folder"]},
        {"relativePath": "Review/Proposal", "parents": ["folder"]},
    ) == ["moved"]


def test_reuse_requires_matching_verified_content_and_export():
    row = DiscoveryObservation(
        resource="file",
        scope="review",
        service_type="GoogleDrive",
        service_id="doc",
        statuses=["moved"],
        metadata={"version": "1", "mimeType": "native"},
    )
    prior = VerifiedContent(
        service_type="GoogleDrive",
        service_id="doc",
        version="1",
        mime_type="native",
        export_mime="docx",
        sha256="a" * 64,
    )
    assert plan_incremental_pull([row])[0].action == "acquire"
    assert (
        plan_incremental_pull([row], verified=(prior,), export_mime="docx")[0].action
        == "reuse"
    )
    assert (
        plan_incremental_pull([row], verified=(prior,), export_mime="pdf")[0].action
        == "acquire"
    )
    row.metadata.pop("version")
    assert (
        plan_incremental_pull([row], verified=(prior,), export_mime="docx")[0].action
        == "acquire"
    )


def test_reserved_upload_lost_response_and_unsupported_and_stale_move(tmp_path):
    drive = Drive()
    args = dict(
        parent="folder",
        name="copy.docx",
        source=tmp_path,
        mime_type="application/binary",
        properties={"pr": "1"},
    )
    source = ensure_reserved_binary(drive, "reserved", **args)
    assert (
        ensure_reserved_binary(drive, "reserved", **args) == source
        and drive.writes == 1
    )
    assert checked_move(drive, source, "done").outcome == "unsupported"
    drive.get_file_with_etag = lambda key: (drive.get_file(key), "etag")

    def stale(*args):
        raise GoogleDriveError("changed", status_code=412)

    drive.move_if_match = stale
    assert (
        checked_move(drive, source, "done", conditional_supported=True).outcome
        == "stale"
    )


def test_segment_inventory_skips_other_roots_and_compares_the_live_path(tmp_path):
    from fileroute.discovery import discover_report
    from fileroute.acquisition import discovered_drive_file
    from fileroute.clients.googledrive import GDriveItem

    drive = Drive()
    drive.files["process"] = GDriveApiFile(
        id="process",
        name="process",
        parents=["root"],
        mimeType=FOLDER_MIME,
        trashed=False,
    )
    drive.files["cache"] = GDriveApiFile(
        id="cache", name="cache", parents=["root"], mimeType=FOLDER_MIME, trashed=False
    )
    drive.files["folder"].parents = ["process"]
    visited = []

    def children(key, **kwargs):
        visited.append(key)
        return [file for file in drive.files.values() if file.parents == [key]]

    drive.list_children = children
    drive.get_from_location = lambda location: GDriveItem._from_api_response(
        drive.get_file(location.service_id), drive
    )
    descriptor = tmp_path / "catalog.yaml"
    descriptor.write_text(
        "path: stage\ntargets: []\nsources:\n- path: https://drive.google.com/drive/folders/root\n  resourcePathTemplate: 'process/{stage}/{filename}'\n  matchPathSegments: true\n"
    )
    report = discover_report(descriptor, root=tmp_path, client=drive)
    assert report.complete and "cache" not in visited
    (row,) = report.observations
    assert row.path_properties["stage"] == "Ready"
    assert discovered_drive_file(row) == observe_drive(drive, "doc", root_id="root")
    drive.files["other"] = drive.files["folder"].model_copy(update={"id": "other"})
    ambiguous = discover_report(
        descriptor, root=tmp_path, previous=report.inventory, client=drive
    )
    assert not ambiguous.complete and "Ambiguous" in ambiguous.errors[0]
