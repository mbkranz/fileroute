from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests

from fileroute.clients._http import (
    is_transient_status,
    retry_after_seconds,
    retry_delay,
)
from fileroute.clients.googledrive import GoogleDriveClient
from fileroute.clients.sharepoint import SharepointClient
from fileroute.exceptions import GoogleDriveError, GraphApiDriveError, GraphApiSiteError


class DummyResponse:
    def __init__(self, status_code=200, *, headers=None, text="", payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.content = b"content"
        self.reason = "Error"
        self.payload = payload or {}

    def json(self):
        return self.payload


@pytest.mark.parametrize(
    "code, expected",
    [
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
        (400, False),
        (401, False),
        (404, False),
    ],
)
def test_transient_status(code: int, expected: bool) -> None:
    assert is_transient_status(code) is expected


def test_retry_after_seconds_and_date() -> None:
    assert retry_delay(1, headers={"rEtRy-AfTeR": "7"}) == 7
    date = format_datetime(
        datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True
    )
    assert 18 <= retry_after_seconds({"Retry-After": date}) <= 21
    assert retry_after_seconds({"Retry-After": "not a date"}) is None
    assert 0 <= retry_delay(4, max_delay=2) <= 2


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("rateLimitExceeded", True),
        ("userRateLimitExceeded", True),
        ("insufficientPermissions", False),
    ],
)
def test_google_403_reason(reason: str, expected: bool) -> None:
    error = GoogleDriveError(
        "forbidden",
        status_code=403,
        response_json={"error": {"errors": [{"reason": reason}]}},
    )
    assert GoogleDriveClient._retryable_upload_error(error) is expected


def test_graph_get_retries_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("fileroute.clients.sharepoint.time.sleep", sleeps.append)
    responses = iter([
        DummyResponse(status_code=429, headers={"Retry-After": "4"}, text="slow"),
        DummyResponse(status_code=200, payload={"id": "site"}),
    ])
    client = SharepointClient(access_token="token")
    calls: list[dict] = []

    def request(method, url, **kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(client.session, "request", request)
    assert client.get_site_id("Research") == "site"
    assert sleeps == [4]
    assert len(calls) == 2
    assert calls[0]["timeout"] == 120
    assert calls[0]["headers"]["Authorization"] == "Bearer token"


def test_graph_site_error_keeps_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SharepointClient(access_token="token")
    monkeypatch.setattr(
        client.session,
        "request",
        lambda *_a, **_kw: DummyResponse(
            status_code=404, text="missing", headers={"x-ms": "trace"}
        ),
    )
    with pytest.raises(GraphApiSiteError) as exc:
        client.get_site_id("missing")
    assert exc.value.status_code == 404
    assert exc.value.response_text == "missing"
    assert exc.value.response_headers["x-ms"] == "trace"


def test_graph_post_is_not_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SharepointClient(access_token="token")
    calls = 0

    def request(*_a, **_kw):
        nonlocal calls
        calls += 1
        raise requests.ConnectionError("lost reply")

    monkeypatch.setattr(client.session, "request", request)
    with pytest.raises(GraphApiDriveError):
        client._request("POST", "https://graph.microsoft.com/v1.0/create")
    assert calls == 1


def test_graph_put_reopens_file_on_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    client = SharepointClient(access_token="token")
    local = tmp_path / "upload.txt"
    local.write_bytes(b"content")
    bodies: list[bytes] = []
    sleeps: list[float] = []
    monkeypatch.setattr("fileroute.clients.sharepoint.time.sleep", sleeps.append)

    def request(_method, _url, **kwargs):
        bodies.append(kwargs["data"].read())
        if len(bodies) == 1:
            raise requests.ConnectionError("lost reply")
        return DummyResponse(status_code=200, payload={"id": "file"})

    monkeypatch.setattr(client.session, "request", request)
    assert client._put_file("https://graph.microsoft.com/content", local) == {
        "id": "file"
    }
    assert bodies == [b"content", b"content"]
    assert len(sleeps) == 1


def test_graph_pagination_uses_same_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SharepointClient(access_token="token")
    urls: list[str] = []
    pages = iter([
        DummyResponse(
            payload={
                "value": [{"id": "first"}],
                "@odata.nextLink": "https://graph.microsoft.com/next",
            }
        ),
        DummyResponse(payload={"value": [{"id": "second"}]}),
    ])

    def request(_method, url, **_kwargs):
        urls.append(url)
        return next(pages)

    monkeypatch.setattr(client.session, "request", request)
    assert client._request_json("https://graph.microsoft.com/items") == {
        "value": [{"id": "first"}, {"id": "second"}]
    }
    assert urls == [
        "https://graph.microsoft.com/items",
        "https://graph.microsoft.com/next",
    ]


def test_presigned_download_omits_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SharepointClient(access_token="secret")
    headers: list[dict] = []

    def request(_method, _url, **kwargs):
        headers.append(kwargs["headers"])
        return DummyResponse()

    monkeypatch.setattr(client.session, "request", request)
    client.download_content(download_url="https://storage.example/file")
    assert headers == [{}]
