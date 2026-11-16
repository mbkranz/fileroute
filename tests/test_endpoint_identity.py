"""Public provider operations keep exact item identities after remote renames."""

from pathlib import Path
from unittest.mock import create_autospec

import pytest

from fileroute.clients.googledrive import GDriveItem, GoogleDriveClient
from fileroute.clients.sharepoint import SharepointClient
from fileroute.exceptions import GraphApiDriveError
from fileroute.models import Location


def test_google_item_move_obeys_real_client_signature():
    client = create_autospec(GoogleDriveClient, instance=True)
    item = GDriveItem(client=client, id="file", name="term.docx", parent_id="old")
    assert item.move("new") is item
    client.update_file.assert_called_once_with(
        "file", params={"addParents": "new", "removeParents": "old"}
    )
    assert item.parent_id == "new"


@pytest.mark.parametrize("nested", [False, True])
def test_sharepoint_saved_folder_id_survives_moved_authored_path(
    tmp_path, monkeypatch, nested
):
    local = tmp_path / "term.md"
    local.write_text("# Term\n")
    client = SharepointClient(access_token="test", host_url="contoso.sharepoint.com")
    calls = []

    def root_metadata(drive, *, item_id):
        assert (drive, item_id) == ("drive", "stable-folder")
        calls.append(("root", item_id))
        return {"id": item_id, "name": "Moved folder", "folder": {}}

    def child_metadata(url):
        calls.append(("child", url))
        assert url.endswith("/items/stable-folder:/nested%20folder")
        return {
            "id": "nested",
            "name": "nested folder",
            "folder": {},
            "parentReference": {"id": "stable-folder"},
        }

    def put(url, source):
        calls.append(("put", url))
        assert source == local
        parent = "nested" if nested else "stable-folder"
        assert url.endswith(f"/items/{parent}:/term.md:/content")
        assert "Old%20folder" not in url
        return {"id": "published", "name": "term.md", "file": {}}

    monkeypatch.setattr(client, "get_item_metadata", root_metadata)
    monkeypatch.setattr(client, "_request_json", child_metadata)
    monkeypatch.setattr(client, "_put_file", put)
    result = client.upload_to_location(
        Location(
            path="https://contoso.sharepoint.com/sites/review/Shared%20Documents/Old%20folder",
            serviceId="stable-folder",
            driveId="drive",
            remotePath="Old folder",
        ),
        Path("nested folder/term.md" if nested else "term.md"),
        local,
    )
    assert result.id == "published"
    assert len(calls) == (3 if nested else 2)
    client.close()


def test_sharepoint_saved_id_nested_creation_and_conflict_recheck_stay_id_relative(
    tmp_path, monkeypatch
):
    local = tmp_path / "term.md"
    local.write_text("# Term\n")
    client = SharepointClient(access_token="test")
    monkeypatch.setattr(
        client,
        "get_item_metadata",
        lambda drive, *, item_id: {"id": item_id, "name": "Renamed", "folder": {}},
    )
    reads = []

    def read(url):
        reads.append(url)
        assert url.endswith("/items/target:/child")
        if len(reads) == 1:
            raise GraphApiDriveError("missing", status_code=404)
        return {"id": "child-id", "name": "child", "folder": {}}

    def create(method, url, **options):
        assert method == "POST" and url.endswith("/items/target/children")
        assert options["json"]["name"] == "child"
        raise GraphApiDriveError("created concurrently", status_code=409)

    def put(url, source):
        assert url.endswith("/items/child-id:/term.md:/content")
        return {"id": "file", "name": "term.md", "file": {}}

    monkeypatch.setattr(client, "_request_json", read)
    monkeypatch.setattr(client, "_request", create)
    monkeypatch.setattr(client, "_put_file", put)
    client._upload_to_drive_folder(
        "drive", "old-name", Path("child/term.md"), local, folder_id="target"
    )
    assert len(reads) == 2
    client.close()


@pytest.mark.parametrize(
    "child",
    [
        {"id": "unexpected", "name": "wrong", "folder": {}},
        {"id": "", "name": "child", "folder": {}},
        {
            "id": "child-id",
            "name": "child",
            "folder": {},
            "parentReference": {"id": "outside"},
        },
    ],
)
def test_sharepoint_saved_id_rejects_conflicting_child_metadata(
    tmp_path, monkeypatch, child
):
    local = tmp_path / "term.md"
    local.write_text("# Term\n")
    client = SharepointClient(access_token="test")
    monkeypatch.setattr(
        client,
        "get_item_metadata",
        lambda drive, *, item_id: {"id": item_id, "folder": {}},
    )
    monkeypatch.setattr(client, "_request_json", lambda url: child)
    monkeypatch.setattr(
        client, "_put_file", lambda *args: pytest.fail("Must not upload")
    )
    with pytest.raises(ValueError, match="child folder metadata"):
        client._upload_to_drive_folder(
            "drive", "old", Path("child/term.md"), local, folder_id="target"
        )
    client.close()
