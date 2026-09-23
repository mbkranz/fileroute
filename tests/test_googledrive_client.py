from __future__ import annotations

import json

import pytest

from sharedrive.auth.google import GoogleAuth
from sharedrive.clients.googledrive import (
    FOLDER_MIME,
    GoogleBaseClient,
    GoogleDriveClient,
)
from sharedrive.exceptions import GoogleDriveError


class DummyCreds:
    def __init__(self, *, valid: bool = True, token: str | None = "token") -> None:
        self.valid = valid
        self.token = token
        self.refresh_calls = 0

    def refresh(self, _request) -> None:
        self.refresh_calls += 1
        self.valid = True
        self.token = "refreshed-token"


class DummyResponse:
    def __init__(
        self,
        *,
        ok: bool = True,
        status_code: int = 200,
        payload: dict | None = None,
        text: str = "",
        chunks: list[bytes] | None = None,
    ) -> None:
        self.ok = ok
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)
        self._chunks = chunks or []

    def json(self):
        return self._payload

    def iter_content(self, chunk_size: int = 0):
        del chunk_size
        yield from self._chunks


class DummySession:
    def __init__(self, responses: list[DummyResponse]) -> None:
        self.responses = list(responses)
        self.calls = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


class DummyGoogleClient(GoogleBaseClient):
    def get_from_weburl(self, url: str):
        raise NotImplementedError


def test_client_uses_explicit_auth() -> None:
    creds = DummyCreds(valid=True)
    auth = GoogleAuth(creds)

    client = GoogleDriveClient(auth=auth)

    assert client._auth is auth
    assert client._auth.credentials is creds


def test_google_drive_client_reports_write_capability() -> None:
    assert GoogleDriveClient.capabilities.supports_write


def test_client_uses_credentials_escape_hatch() -> None:
    creds = DummyCreds(valid=True)

    client = GoogleDriveClient(credentials=creds)

    assert client._auth.credentials is creds


def test_request_refreshes_credentials_before_call() -> None:
    creds = DummyCreds(valid=False, token=None)
    response = DummyResponse(payload={"id": "123"})
    session = DummySession([response])
    client = GoogleDriveClient(credentials=creds, session=session)

    result = client.get_file("123")

    assert result.id == "123"
    assert creds.refresh_calls == 1
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/files/123")
    assert kwargs["headers"]["Authorization"] == "Bearer refreshed-token"


def test_request_wraps_drive_errors() -> None:
    creds = DummyCreds(valid=True)
    response = DummyResponse(
        ok=False, status_code=404, payload={"error": {"message": "File not found"}}
    )
    session = DummySession([response])
    client = GoogleDriveClient(credentials=creds, session=session)

    with pytest.raises(GoogleDriveError) as exc_info:
        client.get_file("missing")

    assert exc_info.value.status_code == 404
    assert "File not found" in str(exc_info.value)


def test_item_from_weburl_downloads_to_path(tmp_path) -> None:
    creds = DummyCreds(valid=True)
    item_response = DummyResponse(
        payload={
            "id": "file123",
            "name": "result.pdf",
            "mimeType": "application/pdf",
            "parents": ["root"],
        }
    )
    metadata_response = DummyResponse(payload={"mimeType": "application/pdf"})
    file_response = DummyResponse(chunks=[b"abc", b"123"])
    session = DummySession([item_response, metadata_response, file_response])
    client = GoogleDriveClient(credentials=creds, session=session)

    output_path = tmp_path / "result.pdf"
    item = client.get_from_weburl("https://drive.google.com/file/d/file123/view")
    item.download(output_path)

    assert output_path.read_bytes() == b"abc123"


def test_extract_id_from_url_supports_spreadsheets() -> None:
    assert (
        GoogleDriveClient._extract_id_from_url(
            "https://docs.google.com/spreadsheets/d/sheet123/edit#gid=0"
        )
        == "sheet123"
    )


def test_google_base_client_requires_auth_or_credentials() -> None:
    with pytest.raises(ValueError):
        DummyGoogleClient()


def test_google_base_client_rejects_both_auth_and_credentials() -> None:
    creds = DummyCreds(valid=True)
    auth = GoogleAuth(creds)
    with pytest.raises(ValueError):
        DummyGoogleClient(auth=auth, credentials=creds)


def test_google_base_client_uses_generic_google_api_error() -> None:
    creds = DummyCreds(valid=True)
    response = DummyResponse(
        ok=False, status_code=500, payload={"error": {"message": "backend error"}}
    )
    session = DummySession([response])
    client = DummyGoogleClient(credentials=creds, session=session)

    with pytest.raises(Exception) as exc_info:
        client._request("GET", "https://example.test")

    assert exc_info.value.__class__.__name__ == "GoogleApiError"
    assert "backend error" in str(exc_info.value)


def test_gdrive_item_iter_files_paths_are_relative_to_weburl_root() -> None:
    creds = DummyCreds(valid=True)
    root_metadata = DummyResponse(
        payload={"id": "root", "name": "NIH approvals", "mimeType": FOLDER_MIME}
    )
    flat_inventory = DummyResponse(
        payload={
            "files": [
                {
                    "id": "proposal-folder",
                    "name": "01-proposal-process",
                    "mimeType": FOLDER_MIME,
                    "parents": ["root"],
                },
                {
                    "id": "term-folder",
                    "name": "PPI000001",
                    "mimeType": FOLDER_MIME,
                    "parents": ["proposal-folder"],
                },
                {
                    "id": "doc-1",
                    "name": "PPI000001 approval.docx",
                    "mimeType": (
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document"
                    ),
                    "parents": ["term-folder"],
                    "webViewLink": "https://drive.google.com/file/d/doc-1/view",
                },
                {
                    "id": "unrelated",
                    "name": "outside.txt",
                    "mimeType": "text/plain",
                    "parents": ["another-root"],
                },
            ]
        }
    )
    session = DummySession([root_metadata, flat_inventory])
    client = GoogleDriveClient(credentials=creds, session=session)

    root = client.get_from_weburl("https://drive.google.com/drive/folders/root")
    files = list(root.iter_files())

    assert root.path in ("", "NIH approvals")
    assert len(files) == 1
    assert files[0].id == "doc-1"
    assert files[0].name == "PPI000001 approval.docx"
    assert (
        files[0].path
        == "NIH approvals/01-proposal-process/PPI000001/PPI000001 approval.docx"
    )
    assert files[0].service_type == "GoogleDrive"
    all_items = list(root.iter_items())
    assert [item.name for item in all_items] == [
        "01-proposal-process",
        "PPI000001",
        "PPI000001 approval.docx",
    ]
    by_name = {item.name: item for item in all_items}
    assert by_name["01-proposal-process"].parent is root
    assert by_name["PPI000001"].parent is by_name["01-proposal-process"]
    assert by_name["PPI000001 approval.docx"].parent is by_name["PPI000001"]
    assert list(root.iter_files()) == files
    assert len(session.calls) == 2


def test_gdrive_item_from_path_resolves_my_drive_path_hierarchically() -> None:
    creds = DummyCreds(valid=True)
    session = DummySession([
        DummyResponse(
            payload={"id": "root", "name": "My Drive", "mimeType": FOLDER_MIME}
        ),
        DummyResponse(
            payload={
                "files": [
                    {
                        "id": "folder-1",
                        "name": "reports",
                        "mimeType": FOLDER_MIME,
                        "parents": ["root"],
                    }
                ]
            }
        ),
        DummyResponse(
            payload={
                "files": [
                    {
                        "id": "file-1",
                        "name": "summary.csv",
                        "mimeType": "text/csv",
                        "parents": ["folder-1"],
                        "webViewLink": "https://drive.google.com/file/d/file-1/view",
                    }
                ]
            }
        ),
    ])
    client = GoogleDriveClient(credentials=creds, session=session)

    item = client.get_from_path("My Drive", "reports/summary.csv")

    assert item.id == "file-1"
    assert item.path == "reports/summary.csv"
    assert not item.is_directory
    assert session.calls[1][2]["params"]["supportsAllDrives"] == "true"
    assert session.calls[1][2]["params"]["includeItemsFromAllDrives"] == "true"
    assert session.calls[2][2]["params"]["supportsAllDrives"] == "true"
    assert session.calls[2][2]["params"]["includeItemsFromAllDrives"] == "true"


def test_gdrive_item_from_path_resolves_shared_drive_root() -> None:
    creds = DummyCreds(valid=True)
    session = DummySession([
        DummyResponse(
            payload={"drives": [{"id": "drive-1", "name": "Research Drive"}]}
        ),
        DummyResponse(
            payload={"id": "drive-1", "name": "Research Drive", "mimeType": FOLDER_MIME}
        ),
    ])
    client = GoogleDriveClient(credentials=creds, session=session)

    item = client.get_from_path("Research Drive", "")

    assert item.id == "drive-1"
    assert item.path == ""
    assert item.is_directory


def test_gdrive_item_from_path_rejects_missing_segment() -> None:
    creds = DummyCreds(valid=True)
    session = DummySession([
        DummyResponse(
            payload={"id": "root", "name": "My Drive", "mimeType": FOLDER_MIME}
        ),
        DummyResponse(payload={"files": []}),
    ])
    client = GoogleDriveClient(credentials=creds, session=session)

    with pytest.raises(FileNotFoundError, match="missing"):
        client.get_from_path("My Drive", "missing")


def test_gdrive_item_from_path_rejects_ambiguous_segment() -> None:
    creds = DummyCreds(valid=True)
    session = DummySession([
        DummyResponse(
            payload={"id": "root", "name": "My Drive", "mimeType": FOLDER_MIME}
        ),
        DummyResponse(
            payload={
                "files": [
                    {
                        "id": "folder-1",
                        "name": "reports",
                        "mimeType": FOLDER_MIME,
                        "parents": ["root"],
                    },
                    {
                        "id": "folder-2",
                        "name": "reports",
                        "mimeType": FOLDER_MIME,
                        "parents": ["root"],
                    },
                ]
            }
        ),
    ])
    client = GoogleDriveClient(credentials=creds, session=session)

    with pytest.raises(ValueError, match="ambiguous"):
        client.get_from_path("My Drive", "reports")


def test_gdrive_item_from_path_trailing_slash_requires_directory() -> None:
    creds = DummyCreds(valid=True)
    session = DummySession([
        DummyResponse(
            payload={"id": "root", "name": "My Drive", "mimeType": FOLDER_MIME}
        ),
        DummyResponse(
            payload={
                "files": [
                    {
                        "id": "file-1",
                        "name": "summary.csv",
                        "mimeType": "text/csv",
                        "parents": ["root"],
                    }
                ]
            }
        ),
    ])
    client = GoogleDriveClient(credentials=creds, session=session)

    with pytest.raises(NotADirectoryError, match="directory"):
        client.get_from_path("My Drive", "summary.csv/")

