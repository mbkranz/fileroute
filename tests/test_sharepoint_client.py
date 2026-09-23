from __future__ import annotations

from pathlib import Path

import pytest

from sharedrive.auth.microsoft import MicrosoftAuth
from sharedrive.clients.sharepoint import SharepointClient, SharepointItem
from sharedrive.exceptions import GraphApiDriveError


class DummyResponse:
    def __init__(
        self,
        *,
        status_code: int,
        reason: str = "Error",
        text: str = "",
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        ok: bool | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        self.status_code = status_code
        self.reason = reason
        self.text = text
        self.content = content
        self.headers = headers or {}
        self.ok = status_code == 200 if ok is None else ok
        self.payload = payload or {"status": "ok"}

    def json(self) -> dict[str, object]:
        return self.payload


def test_sharepoint_client_accepts_microsoft_auth() -> None:
    auth = MicrosoftAuth("my-bearer-token")
    client = SharepointClient(auth=auth, host_url="contoso.sharepoint.com")

    assert client.access_token == "my-bearer-token"
    assert client.host_url == "contoso.sharepoint.com"


def test_sharepoint_client_rejects_both_auth_and_access_token() -> None:
    auth = MicrosoftAuth("token")
    with pytest.raises(ValueError, match="either auth or access_token"):
        SharepointClient(auth=auth, access_token="token")


def test_sharepoint_client_requires_auth_or_access_token() -> None:
    with pytest.raises(ValueError):
        SharepointClient()


def test_sharepoint_item_move_is_explicitly_unsupported() -> None:
    client = SharepointClient(access_token="token")
    item = SharepointItem(
        client=client, id="item-1", name="report.csv", path="report.csv"
    )

    with pytest.raises(NotImplementedError, match="Moving SharePoint items"):
        item.move("new-parent")


def test_sharepoint_client_reports_write_capability() -> None:
    assert SharepointClient.capabilities.supports_write


def test_download_content_requires_identifiers_or_download_url() -> None:
    client = SharepointClient(access_token="token")

    with pytest.raises(GraphApiDriveError, match="Need drive_id and item_id"):
        client.download_content()


def test_download_content_raises_graph_error_for_http_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SharepointClient(access_token="token")

    monkeypatch.setattr(
        "sharedrive.clients.sharepoint.requests.get",
        lambda *_args, **_kwargs: DummyResponse(
            status_code=503, reason="Service Unavailable", text="upstream down"
        ),
    )

    with pytest.raises(GraphApiDriveError) as exc_info:
        client.download_content(drive_id="drive-1", item_id="item-1")

    assert exc_info.value.status_code == 503
    assert exc_info.value.response_text == "upstream down"


def test_download_raises_graph_error_before_writing_failed_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = SharepointClient(access_token="token")
    target = tmp_path / "report.csv"

    monkeypatch.setattr(
        "sharedrive.clients.sharepoint.requests.get",
        lambda *_args, **_kwargs: DummyResponse(
            status_code=500, reason="Server Error", text="bad gateway", ok=False
        ),
    )

    item = SharepointItem(
        client=client,
        api_payload={
            "file": {},
            "id": "item-1",
            "parentReference": {"driveId": "drive-1"},
        },
        id="item-1",
        name="report.csv",
        path="report.csv",
    )

    with pytest.raises(GraphApiDriveError) as exc_info:
        item.download(target)

    assert exc_info.value.status_code == 500
    assert not target.exists()


def test_update_file_replaces_existing_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = SharepointClient(access_token="token")
    local_file = tmp_path / "report.csv"
    local_file.write_text("updated", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(client, "get_site_id", lambda _site_name: "site-1")
    monkeypatch.setattr(client, "get_drive_id", lambda _site_id: "drive-1")
    monkeypatch.setattr(
        client, "get_item_metadata", lambda *_args, **_kwargs: {"id": "item-1"}
    )

    def fake_put(url: str, *, headers: dict[str, str], data) -> DummyResponse:
        captured.update(url=url, headers=headers, content=data.read())
        return DummyResponse(
            status_code=200,
            payload={
                "id": "item-1",
                "name": "report.csv",
                "file": {},
                "parentReference": {"driveId": "drive-1"},
            },
        )

    monkeypatch.setattr("sharedrive.clients.sharepoint.requests.put", fake_put)

    item = client.update_file(
        site_name="Test", folder_path="/reports", local_file_path=local_file
    )

    assert captured["url"] == (
        "https://graph.microsoft.com/v1.0/drives/drive-1/items/item-1/content"
    )
    assert captured["content"] == b"updated"
    assert item.id == "item-1"
    assert item.path == "reports/report.csv"


def test_upload_file_creates_missing_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = SharepointClient(access_token="token")
    local_file = tmp_path / "report.csv"
    local_file.write_text("created", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(client, "get_site_id", lambda _site_name: "site-1")
    monkeypatch.setattr(client, "get_drive_id", lambda _site_id: "drive-1")

    def missing(*_args, **_kwargs):
        raise GraphApiDriveError("missing", status_code=404)

    def fake_put(url: str, *, headers: dict[str, str], data) -> DummyResponse:
        captured.update(url=url, headers=headers, content=data.read())
        return DummyResponse(
            status_code=201,
            payload={
                "id": "item-1",
                "name": "report.csv",
                "file": {},
                "parentReference": {"driveId": "drive-1"},
            },
        )

    monkeypatch.setattr(client, "get_item_metadata", missing)
    monkeypatch.setattr("sharedrive.clients.sharepoint.requests.put", fake_put)

    item = client.upload_file(
        site_name="Test", folder_path="/reports", local_file_path=local_file
    )

    assert captured["url"] == (
        "https://graph.microsoft.com/v1.0/drives/drive-1"
        "/root:/reports/report.csv:/content"
    )
    assert captured["content"] == b"created"
    assert item.id == "item-1"


def test_sharepoint_item_iter_files_paths_are_relative_to_weburl_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SharepointClient(access_token="token")
    root_metadata = {
        "id": "root",
        "name": "Approvals",
        "folder": {},
        "webUrl": "https://contoso.sharepoint.com/sites/Test/Shared Documents/Approvals",
        "parentReference": {"id": "parent", "driveId": "drive-1"},
        "children": [
            {
                "id": "folder-1",
                "name": "01-proposal-process",
                "folder": {},
                "parentReference": {"id": "root", "driveId": "drive-1"},
                "children": [
                    {
                        "id": "file-1",
                        "name": "approval.docx",
                        "file": {},
                        "webUrl": (
                            "https://contoso.sharepoint.com/sites/Test/"
                            "Shared Documents/Approvals/01-proposal-process/"
                            "approval.docx"
                        ),
                        "parentReference": {"id": "folder-1", "driveId": "drive-1"},
                    }
                ],
            }
        ],
    }

    monkeypatch.setattr(client, "get_site_id", lambda _site_name: "site-1")
    monkeypatch.setattr(client, "get_drive_id", lambda *_args, **_kwargs: "drive-1")
    monkeypatch.setattr(
        client, "get_item_metadata", lambda *_args, **_kwargs: root_metadata
    )

    root = client.get_from_weburl(
        "https://contoso.sharepoint.com/sites/Test/Shared Documents/Approvals"
    )
    files = list(root.iter_files())

    assert root.path == "Approvals"
    assert len(files) == 1
    assert files[0].path == "Approvals/01-proposal-process/approval.docx"


def test_sharepoint_item_from_path_resolves_library_relative_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SharepointClient(access_token="token")
    calls: dict[str, object] = {}
    metadata = {
        "id": "file-1",
        "name": "approval.docx",
        "file": {},
        "webUrl": "https://contoso.sharepoint.com/sites/Test/Shared Documents/folder/approval.docx",
        "parentReference": {"id": "folder-1", "driveId": "drive-1"},
    }

    monkeypatch.setattr(client, "get_site_id", lambda site_name: f"site:{site_name}")

    def fake_get_drive_id(site_id: str, drive_name: str | None = None) -> str:
        calls["site_id"] = site_id
        calls["drive_name"] = drive_name
        return "drive-1"

    def fake_get_item_metadata(
        drive_id: str, *, item_path: str | None = None, **_kwargs
    ):
        calls["drive_id"] = drive_id
        calls["item_path"] = item_path
        return metadata

    monkeypatch.setattr(client, "get_drive_id", fake_get_drive_id)
    monkeypatch.setattr(client, "get_item_metadata", fake_get_item_metadata)

    item = client.get_from_path(
        site_name="Test", item_path="Shared Documents/folder/approval.docx"
    )

    assert calls == {
        "site_id": "site:Test",
        "drive_name": "Shared Documents",
        "drive_id": "drive-1",
        "item_path": "/folder/approval.docx",
    }
    assert item.path == "folder/approval.docx"
    assert not item.is_directory


def test_sharepoint_item_from_path_resolves_library_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SharepointClient(access_token="token")
    calls: dict[str, object] = {}
    metadata = {
        "id": "root",
        "name": "Shared Documents",
        "folder": {},
        "webUrl": "https://contoso.sharepoint.com/sites/Test/Shared Documents",
        "parentReference": {"id": "site-root", "driveId": "drive-1"},
    }

    monkeypatch.setattr(client, "get_site_id", lambda _site_name: "site-1")

    def fake_get_drive_id(_site_id: str, drive_name: str | None = None) -> str:
        calls["drive_name"] = drive_name
        return "drive-1"

    def fake_get_item_metadata(
        drive_id: str, *, item_path: str | None = None, **_kwargs
    ):
        calls["drive_id"] = drive_id
        calls["item_path"] = item_path
        return metadata

    monkeypatch.setattr(client, "get_drive_id", fake_get_drive_id)
    monkeypatch.setattr(client, "get_item_metadata", fake_get_item_metadata)

    item = client.get_from_path(site_name="Test", item_path="Shared Documents/")

    assert calls["drive_name"] == "Shared Documents"
    assert calls["drive_id"] == "drive-1"
    assert calls["item_path"] == "/"
    assert item.path == ""
    assert item.is_directory


def test_sharepoint_delta_traversal_deduplicates_and_filters_subtree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SharepointClient(access_token="token")
    root = SharepointItem(
        client=client,
        api_payload={
            "id": "root",
            "name": "Approvals",
            "folder": {},
            "parentReference": {"driveId": "drive-1"},
        },
        id="root",
        name="Approvals",
        path="Approvals",
        is_folder=True,
    )
    calls = 0

    def fake_scan_descendants(*, drive_id: str) -> list[dict[str, object]]:
        nonlocal calls
        assert drive_id == "drive-1"
        calls += 1
        return [
            {
                "id": "folder-1",
                "name": "draft-name",
                "folder": {},
                "parentReference": {"id": "root", "driveId": "drive-1"},
            },
            {
                "id": "folder-1",
                "name": "final-name",
                "folder": {},
                "parentReference": {"id": "root", "driveId": "drive-1"},
            },
            {
                "id": "file-1",
                "name": "approval.docx",
                "file": {},
                "parentReference": {"id": "folder-1", "driveId": "drive-1"},
            },
            {
                "id": "outside",
                "name": "outside.txt",
                "file": {},
                "parentReference": {"id": "other-root", "driveId": "drive-1"},
            },
        ]

    monkeypatch.setattr(client, "scan_descendants", fake_scan_descendants)

    descendants = list(root.iter_items())
    assert [(item.path, item.is_directory) for item in descendants] == [
        ("Approvals/final-name", True),
        ("Approvals/final-name/approval.docx", False),
    ]
    by_path = {item.path: item for item in descendants}
    assert by_path["Approvals/final-name"].parent is root
    assert (
        by_path["Approvals/final-name/approval.docx"].parent
        is by_path["Approvals/final-name"]
    )
    assert [item.path for item in root.iter_files()] == [
        "Approvals/final-name/approval.docx"
    ]
    assert calls == 1
