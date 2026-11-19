"""Metadata reconciliation preserves authored names, paths and publication metadata."""

from contextlib import contextmanager
from pathlib import Path

import pytest

from fileroute.clients.googledrive import GDriveApiFile, GDriveItem, GoogleDriveClient
from fileroute.descriptor import load, save
from fileroute.discovery import (
    DiscoveryScan,
    ReconcileDiscoveryRequest,
    discover_report,
    reconcile_discovery,
)
from fileroute.models import Catalog, CatalogLink, Location, Resource, ServiceType
from fileroute.resolution import parse_location
from fileroute.transfer import PullEntry, pull


def observed(
    name="term.docx", *, item_id="file-1", scope="", version="1", parent="folder"
):
    return Resource(
        path=f"data/{name}",
        sources=[
            Location(
                path=f"https://drive.google.com/file/d/{item_id}/view",
                serviceType="GoogleDrive",
                serviceId=item_id,
                discovery={
                    "scope": scope,
                    "relativePath": name,
                    "name": Path(name).name,
                    "parents": [parent],
                    "version": version,
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                },
            )
        ],
    )


def reconcile(tmp_path, catalog, scans, **kwargs):
    return reconcile_discovery(
        ReconcileDiscoveryRequest(
            catalog=catalog,
            scans=scans,
            descriptor=tmp_path / "catalog.yaml",
            root=tmp_path,
            **kwargs,
        )
    )


def test_stable_identity_preserves_authored_fields_and_reports_simultaneous_changes(
    tmp_path,
):
    original = observed()
    original = original.model_copy(
        update={"path": "reviewed/term.md", "title": "Reviewed title"}
    )
    original.sources[0].model_extra["custom"] = {"retain": True}
    catalog = Catalog(resources={"stable_term": original})
    fresh = observed("other/renamed.docx", version="2", parent="new-folder")

    report = reconcile(
        tmp_path, catalog, [DiscoveryScan(scope="", resources={"new_name": fresh})]
    )

    assert list(report.catalog.resources) == ["stable_term"]
    resource = report.catalog.resources["stable_term"]
    assert resource.path == "reviewed/term.md" and resource.title == "Reviewed title"
    assert resource.sources[0].model_extra["custom"] == {"retain": True}
    assert report.observations[0].statuses == ["renamed", "moved", "changed"]
    assert (
        catalog.resources["stable_term"].sources[0].model_extra["discovery"]["version"]
        == "1"
    )


def test_complete_empty_scan_reports_missing_without_deletion(tmp_path):
    catalog = Catalog(resources={"term": observed()})
    report = reconcile(tmp_path, catalog, [DiscoveryScan(scope="")])
    assert report.complete and report.observations[0].statuses == ["missing_from_scope"]
    assert report.catalog == catalog


def test_incomplete_scan_never_reports_missing_and_preserves_positive_observations(
    tmp_path,
):
    catalog = Catalog(
        resources={
            "term": observed(),
            "absent": observed("other.docx", item_id="other"),
        }
    )
    report = reconcile(
        tmp_path,
        catalog,
        [
            DiscoveryScan(
                scope="",
                complete=False,
                error="incomplete listing",
                resources={"term": observed(version="2")},
            )
        ],
    )
    assert not report.complete and report.errors == ["incomplete listing"]
    statuses = {row.service_id: row.statuses for row in report.observations}
    assert statuses == {"file-1": ["changed"], "other": ["unknown"]}


def test_unchanged_metadata_and_same_name_new_identity(tmp_path):
    catalog = Catalog(resources={"term": observed()})
    report = reconcile(
        tmp_path,
        catalog,
        [DiscoveryScan(scope="", resources={"renamed_key": observed()})],
    )
    assert report.observations[0].statuses == ["unchanged"]
    with pytest.raises(ValueError, match="already exists"):
        reconcile(
            tmp_path,
            catalog,
            [DiscoveryScan(scope="", resources={"term": observed(item_id="new")})],
        )


def test_drive_optional_observations_are_retained_without_claiming_content_verification():
    api = GDriveApiFile.model_validate({
        "id": "file",
        "name": "term.docx",
        "mimeType": "application/octet-stream",
        "parents": ["folder"],
        "version": "12",
        "modifiedTime": "2026-11-16T12:00:00Z",
        "md5Checksum": "provider-md5",
        "sha256Checksum": "provider-sha256",
    })
    item = GDriveItem._from_api_response(
        api, GoogleDriveClient.__new__(GoogleDriveClient)
    )
    assert item.observed_metadata == {
        "name": "term.docx",
        "mimeType": "application/octet-stream",
        "parents": ["folder"],
        "version": "12",
        "modifiedTime": "2026-11-16T12:00:00Z",
        "md5Checksum": "provider-md5",
        "sha256Checksum": "provider-sha256",
    }
    assert {"version", "modifiedTime", "md5Checksum", "sha256Checksum"} <= set(
        GoogleDriveClient.file_fields
    )


def test_google_metadata_is_optional_and_retained_without_download():
    item = GDriveItem._from_api_response(
        GDriveApiFile(
            id="file",
            name="term.docx",
            mimeType="application/octet-stream",
            parents=["folder"],
            version="12",
            modifiedTime="2026-11-16T12:00:00Z",
            md5Checksum="a" * 32,
            sha256Checksum="b" * 64,
        ),
        GoogleDriveClient.__new__(GoogleDriveClient),
    )
    assert item.observed_metadata["version"] == "12"
    assert item.observed_metadata["parents"] == ["folder"]
    assert item.observed_metadata["sha256Checksum"] == "b" * 64
    sparse = GDriveItem(
        id="sparse", name="Native", mime_type="application/vnd.google-apps.document"
    )
    assert "version" not in sparse.observed_metadata


def test_report_proposes_linked_owner_write_without_flattening_or_saving(
    tmp_path, monkeypatch
):
    root = Catalog(catalogs={"terms": CatalogLink(descriptor="terms.yaml")})
    child = Catalog(
        path="data",
        targets=[],
        sources=[
            Location(
                path="https://drive.google.com/drive/folders/folder",
                serviceType="GoogleDrive",
                resourcePathTemplate="{name}.docx",
            )
        ],
    )
    root_path, child_path = tmp_path / "catalog.yaml", tmp_path / "terms.yaml"
    save(root, root_path)
    save(child, child_path)
    initial = (root_path.read_bytes(), child_path.read_bytes())
    fake = object()

    @contextmanager
    def clients(keys):
        assert list(keys) == [(ServiceType.GOOGLE_DRIVE, None)]
        yield {(ServiceType.GOOGLE_DRIVE, None): fake}

    monkeypatch.setattr("fileroute.discovery._location_clients", clients)
    monkeypatch.setattr(
        "fileroute.discovery.lookup_item", lambda client, location: object()
    )
    file = GDriveItem(id="file-1", name="term.docx", parent_id="folder", version="1")
    from fileroute.item import FileMatch

    monkeypatch.setattr(
        "fileroute.discovery._iter_file_matches",
        lambda root, parser: iter([FileMatch(file, "term.docx", {"name": "term"})]),
    )
    # Root validation only needs provider and directory identity before traversal.
    monkeypatch.setattr(
        "fileroute.discovery.lookup_item",
        lambda client, location: GDriveItem(
            id="folder", name="Folder", mime_type="application/vnd.google-apps.folder"
        ),
    )
    report = discover_report(root_path, root=tmp_path)

    assert report.complete and list(report.writes) == [child_path]
    assert isinstance(load(root_path).catalogs["terms"], CatalogLink)
    assert (root_path.read_bytes(), child_path.read_bytes()) == initial
    written = report.writes[child_path]
    assert written.sources[0].resource_path_template == "{name}.docx"
    assert next(iter(written.resources.values())).path == "data/term.docx"
    assert list(written.resources) == ["term-docx"]
    assert report.as_dict()["complete"] is True
    # Applying only the reviewed owner proposal makes the next scan idempotent.
    save(written, child_path)
    repeated = discover_report(root_path, root=tmp_path)
    assert repeated.writes == {}
    assert repeated.observations[0].statuses == ["unchanged"]


def test_move_between_scopes_keeps_original_owner_key_and_local_path(tmp_path):
    prior = observed(scope="left")
    catalog = Catalog(
        catalogs={"left": Catalog(resources={"stable": prior}), "right": Catalog()}
    )
    fresh = observed(scope="right", parent="another-folder")
    report = reconcile(
        tmp_path,
        catalog,
        [
            DiscoveryScan(scope="left"),
            DiscoveryScan(scope="right", resources={"candidate_name": fresh}),
        ],
    )
    assert report.observations[0].statuses == ["moved"]
    assert list(report.catalog.catalogs["left"].resources) == ["stable"]
    assert report.catalog.catalogs["right"].resources == {}
    assert report.catalog.catalogs["left"].resources["stable"].path == prior.path


def test_disappearing_optional_metadata_reports_unknown(tmp_path):
    current = observed()
    fresh = observed()
    fresh.sources[0].model_extra["discovery"].pop("version")
    report = reconcile(
        tmp_path,
        Catalog(resources={"term": current}),
        [DiscoveryScan(scope="", resources={"term": fresh})],
    )
    assert report.observations[0].statuses == ["unknown"]


def test_real_drive_item_version_reaches_retained_pull_receipt(tmp_path, monkeypatch):
    from unittest.mock import Mock

    client = Mock()
    client.download_file.return_value = b"original bytes"
    item = GDriveItem(
        client=client,
        id="file",
        name="term.docx",
        version="42",
        mime_type="application/octet-stream",
    )
    client.get_from_location.return_value = item

    @contextmanager
    def clients(entries, *, profile):
        yield {(ServiceType.GOOGLE_DRIVE, None): client}

    monkeypatch.setattr("fileroute.transfer._transfer_clients", clients)
    entry = PullEntry(
        local=tmp_path / "term.docx",
        remote="https://drive.google.com/file/d/file/view",
        service_type=ServiceType.GOOGLE_DRIVE,
        root=tmp_path,
        location=Location(
            path="https://drive.google.com/file/d/file/view", serviceId="file"
        ),
    )
    (receipt,) = pull((entry,), run_dir=tmp_path / "runs")
    assert receipt.source_version == "42"
    assert receipt.raw_source.read_bytes() == b"original bytes"
    assert '"source_version": "42"' in receipt.report.read_text()


def test_sharepoint_rename_updates_parsed_remote_context_without_changing_authored_processing(
    tmp_path,
):
    def resource(filename):
        source = parse_location(
            Location(
                path=f"https://contoso.sharepoint.com/sites/review/Shared%20Documents/{filename}",
                serviceId="same-file",
                driveId="same-drive",
                discovery={"scope": "", "name": filename, "relativePath": filename},
            )
        )
        return Resource(path="authored/term.md", sources=[source])

    prior = resource("old.docx")
    fresh = resource("renamed.docx")
    report = reconcile(
        tmp_path,
        Catalog(resources={"stable": prior}),
        [DiscoveryScan(scope="", resources={"candidate": fresh})],
    )
    updated = report.catalog.resources["stable"]
    assert updated.path == "authored/term.md"
    assert updated.sources[0].remote_path == "renamed.docx"
    assert parse_location(updated.sources[0]).service_id == "same-file"


def test_inventory_preserves_authored_artifacts_and_publication_choices(tmp_path):
    raw = observed("raw.docx", item_id="existing")
    raw.targets = []
    catalog = Catalog(
        resources={"raw": raw, "markdown": Resource(path="reviewed.md", format="md")},
        targets=[Location(path="https://drive.google.com/drive/folders/published")],
        params={"type": "literal", "steps": ["retained metadata"]},
    )
    report = reconcile(
        tmp_path,
        catalog,
        [
            DiscoveryScan(
                scope="",
                resources={
                    "renamed": observed("renamed.docx", item_id="existing"),
                    "new": observed("new.docx", item_id="new"),
                },
            )
        ],
    )
    assert set(report.catalog.resources) == {"raw", "markdown", "new"}
    assert report.catalog.resources["raw"].path == "data/raw.docx"
    assert report.catalog.resources["raw"].targets == []
    assert report.catalog.resources["markdown"] == catalog.resources["markdown"]
    assert report.catalog.model_extra == catalog.model_extra
    assert report.catalog.resources["new"].path == "data/new.docx"
    assert report.catalog.resources["new"].targets is None
    assert not (tmp_path / "data").exists()


def test_inventory_keeps_native_google_document_observation_without_declaring_conversion(
    tmp_path,
):
    native = observed("Native document")
    native.sources[0].model_extra["discovery"]["mimeType"] = (
        "application/vnd.google-apps.document"
    )
    report = reconcile(
        tmp_path, Catalog(), [DiscoveryScan(scope="", resources={"native": native})]
    )
    assert report.catalog.resources == {"native": native}
    assert report.catalog.resources["native"].path == "data/Native document"
    assert not (tmp_path / "data").exists()


def test_inventory_rejects_duplicate_acquisition_paths_before_writes(tmp_path):
    scan = DiscoveryScan(
        scope="",
        resources={
            "one": observed("same.docx"),
            "two": observed("same.docx", item_id="other"),
        },
    )
    with pytest.raises(ValueError, match="Duplicate discovery destination"):
        reconcile(tmp_path, Catalog(), [scan])
    assert list(tmp_path.iterdir()) == []
