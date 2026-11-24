"""A source binding selects the existing ID or scoped-path lookup."""

import json

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.clients.googledrive import (
    FOLDER_MIME,
    GDriveApiDrive,
    GDriveApiFile,
    GoogleDriveClient,
)
from fileroute.descriptor import load
from fileroute.discovery import (
    DiscoveryScan,
    ReconcileDiscoveryRequest,
    reconcile_discovery,
)
from fileroute.exceptions import AmbiguousPathError
from fileroute.models import Catalog, Location, Resource
from fileroute.transfer import plan_pull, pull


def source(**fields):
    return {
        "serviceType": "GoogleDrive",
        "drive": "Reviews",
        "path": "Review/current.txt",
        **fields,
    }


def catalog(location, *, name="report"):
    return {"resources": {name: {"path": "local/current.txt", "sources": [location]}}}


@pytest.fixture
def drive(monkeypatch):
    client = GoogleDriveClient.__new__(GoogleDriveClient)
    files = {
        "review": GDriveApiFile(
            id="review",
            name="Review",
            driveId="reviews-drive",
            parents=["reviews-drive"],
            mimeType=FOLDER_MIME,
            trashed=False,
        ),
        "file-a": GDriveApiFile(
            id="file-a",
            name="current.txt",
            driveId="reviews-drive",
            parents=["ready"],
            mimeType="text/plain",
            trashed=False,
        ),
        "file-b": GDriveApiFile(
            id="file-b",
            name="current.txt",
            driveId="reviews-drive",
            parents=["review"],
            mimeType="text/plain",
            trashed=False,
        ),
    }

    def get_file(file_id):
        if file_id not in files:
            raise FileNotFoundError(file_id)
        return files[file_id]

    def resolve_descendant(*, parent_id, name, drive_id):
        return [
            item
            for item in files.values()
            if item.parents == [parent_id]
            and item.name == name
            and item.driveId == drive_id
        ]

    monkeypatch.setattr(client, "get_file", get_file)
    monkeypatch.setattr(client, "resolve_descendant", resolve_descendant)
    monkeypatch.setattr(
        client,
        "list_drives",
        lambda: [GDriveApiDrive(id="reviews-drive", name="Reviews")],
    )
    monkeypatch.setattr(client, "download_file", lambda file_id: file_id.encode())
    monkeypatch.setattr(client, "close", lambda: None)
    monkeypatch.setattr(GoogleDriveClient, "build_default", lambda: client)
    return client, files


@pytest.mark.parametrize(
    "binding_fields, expected_id",
    [
        ({}, "file-a"),
        ({"binding": None}, "file-a"),
        ({"binding": {"by": ["serviceId"]}}, "file-a"),
        ({"binding": {"by": ["path"]}}, "file-b"),
    ],
)
def test_pull_can_follow_the_saved_id_or_the_current_path(
    tmp_path, drive, binding_fields, expected_id
):
    path = tmp_path / "catalog.json"
    location = source(serviceId="file-a", **binding_fields)
    path.write_text(json.dumps(catalog(location)))

    (receipt,) = pull(plan_pull(path, root=tmp_path, selector="report"))

    assert receipt.source_id == expected_id
    assert (tmp_path / "local/current.txt").read_text() == expected_id


@pytest.mark.parametrize(
    "fields",
    [
        {"path": "https://drive.google.com/file/d/file-a/view"},
        {"path": "https://drive.google.com/file/d/file-b/view", "serviceId": "file-a"},
    ],
)
def test_direct_id_lookup_uses_the_saved_id_or_a_file_url(drive, fields):
    client, _ = drive
    location = Location.model_validate({**fields, "binding": {"by": ["serviceId"]}})
    assert client.get_from_location(location).id == "file-a"


@pytest.mark.parametrize("path", ["/drive/folders/file-a", "/drive/search?id=file-a"])
def test_id_binding_does_not_infer_ids_from_folder_or_search_urls(drive, path):
    client, _ = drive
    location = Location(
        path=f"https://drive.google.com{path}", binding={"by": ["serviceId"]}
    )
    with pytest.raises(ValueError, match="file URL"):
        client.get_from_location(location)


def test_direct_path_lookup_normalizes_and_prefers_drive_id_scope(drive):
    client, _ = drive
    by_path = Location.model_validate(
        source(
            path="Review//current.txt",
            drive="Ignored name",
            driveId="reviews-drive",
            serviceId="file-a",
            binding={"by": ["path"]},
        )
    )

    assert client.get_from_location(by_path).id == "file-b"


@pytest.mark.parametrize("failure", ["missing_id", "missing_path", "ambiguous_path"])
def test_selected_lookup_failure_never_falls_back(drive, failure):
    client, files = drive
    selector = "serviceId" if failure == "missing_id" else "path"
    location = Location.model_validate(
        source(serviceId="file-a", binding={"by": [selector]})
    )
    if failure == "missing_id":
        del files["file-a"]
    elif failure == "missing_path":
        del files["file-b"]
    else:
        files["extra"] = files["file-b"].model_copy(update={"id": "extra"})

    error = AmbiguousPathError if failure == "ambiguous_path" else FileNotFoundError
    with pytest.raises(error):
        client.get_from_location(location)


@pytest.mark.parametrize("drive_name", ["Reviews", None])
def test_pull_dry_run_validates_path_binding_without_authenticating(
    tmp_path, monkeypatch, drive_name
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        GoogleDriveClient, "build_default", lambda: pytest.fail("authenticated")
    )
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            catalog(
                source(drive=drive_name, serviceId="file-a", binding={"by": ["path"]})
            )
        )
    )

    result = CliRunner().invoke(app, ["pull", str(path), "--dry-run"])

    if drive_name is None:
        assert result.exit_code == 1, result.output
        assert "drive" in result.output.lower()
    else:
        assert result.exit_code == 0, result.output
        assert "Would download" in result.output
    assert not (tmp_path / "local/current.txt").exists()


@pytest.mark.parametrize("saved_id", [{}, {"serviceId": "file-a"}])
def test_resolve_selected_bound_source_preserves_authored_id_in_linked_owner(
    tmp_path, drive, saved_id
):
    parent = tmp_path / "catalog.json"
    owner = tmp_path / "review.json"
    parent.write_text(json.dumps({"catalogs": {"review": {"descriptor": owner.name}}}))
    before = parent.read_bytes()
    location = source(path="Review//current.txt", binding={"by": ["path"]}, **saved_id)
    owner.write_text(json.dumps(catalog(location, name="targets")))

    result = CliRunner().invoke(
        app,
        [
            "resolve",
            str(parent),
            "--select",
            "/catalogs/review/resources/targets/sources/0",
            "--online",
            "--write",
        ],
    )

    assert result.exit_code == 0, result.output
    stored = load(owner).resources["targets"].sources[0]
    metadata = stored.model_dump(mode="json", by_alias=True, exclude_unset=True)
    assert metadata == {
        **location,
        "remotePath": "Review/current.txt",
        "entityType": "File",
    }
    assert parent.read_bytes() == before


def test_discovery_updates_metadata_without_replacing_bound_locator(tmp_path):
    authored = source(serviceId="file-a", binding={"by": ["path"]})
    document = Catalog.model_validate(catalog(authored))
    observed = Resource(
        path="local/ready.txt",
        sources=[
            Location(
                path="https://drive.google.com/file/d/file-a/view",
                serviceId="file-a",
                discovery={"relativePath": "Ready/current.txt", "version": "2"},
            )
        ],
    )

    report = reconcile_discovery(
        ReconcileDiscoveryRequest(
            catalog=document,
            scans=[DiscoveryScan(scope="", resources={"new_name": observed})],
            descriptor=tmp_path / "catalog.json",
            root=tmp_path,
        )
    )

    assert report.complete
    assert list(report.catalog.resources) == ["report"]
    bound = report.catalog.resources["report"].sources[0]
    assert bound.model_dump(mode="json", by_alias=True, exclude_unset=True) == {
        **authored,
        "discovery": {"relativePath": "Ready/current.txt", "version": "2"},
    }


@pytest.mark.parametrize("by", [["path"], ["serviceId"]])
def test_catalog_schema_accepts_one_binding_selector(by):
    document = catalog(source(serviceId="file-a", binding={"by": by}))
    Catalog.model_validate(document)
    Draft202012Validator(Catalog.model_json_schema(by_alias=True)).validate(document)


BOUND_SOURCE = source(serviceId="file-a", binding={"by": ["path"]})


@pytest.mark.parametrize(
    "document",
    [
        catalog(source(binding={"by": []})),
        catalog(source(binding={"by": ["path", "serviceId"]})),
        catalog(source(binding={"by": ["unknown"]})),
        catalog(source(binding={"by": ["path"], "root": {"serviceId": "folder"}})),
        catalog({key: value for key, value in BOUND_SOURCE.items() if key != "path"}),
        {"sources": [BOUND_SOURCE]},
        {"targets": [BOUND_SOURCE]},
        {
            "resources": {
                "report": {"path": "local/current.txt", "targets": [BOUND_SOURCE]}
            }
        },
    ],
)
def test_catalog_schema_and_runtime_reject_invalid_binding_shape_or_role(document):
    with pytest.raises(ValueError):
        Catalog.model_validate(document)
    validator = Draft202012Validator(Catalog.model_json_schema(by_alias=True))
    assert not validator.is_valid(document)
