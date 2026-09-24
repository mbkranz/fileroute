"""Upload planning and SharePoint writes without Microsoft credentials."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from sharedrive.cli import app
from sharedrive.clients.sharepoint import SharepointClient
from sharedrive.exceptions import GraphApiDriveError
from sharedrive.upload import UploadFile, plan_upload, upload


def _descriptor(root: Path) -> Path:
    descriptor = root / "config" / "sharedrive.yaml"
    descriptor.parent.mkdir()
    descriptor.write_text(
        "$schema: sharedrive-catalog\n"
        "catalogs:\n  - name: documentation\n"
        "    path: docs/_output\n"
        "    targets:\n      - path: https://contoso.sharepoint.com/sites/dev/Shared%20Documents/Docs\n"
        "        serviceType: SharePoint\n"
    )
    return descriptor


def test_plan_nested_and_dry_run_does_not_authenticate(tmp_path, monkeypatch):
    descriptor = _descriptor(tmp_path)
    directory = tmp_path / "docs" / "_output"
    (directory / "surveys").mkdir(parents=True)
    (directory / "index.docx").write_bytes(b"doc")
    (directory / "surveys" / "catalog.xlsx").write_bytes(b"xls")
    files = plan_upload(descriptor, root=tmp_path)
    assert [f.relative.as_posix() for f in files] == [
        "index.docx",
        "surveys/catalog.xlsx",
    ]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sharedrive.upload.get_client",
        lambda _: pytest.fail("authenticated on dry run"),
    )
    result = CliRunner().invoke(app, ["upload", str(descriptor), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "surveys/catalog.xlsx" in result.output


def test_missing_or_symlink_fails_before_transfer(tmp_path):
    descriptor = _descriptor(tmp_path)
    with pytest.raises(ValueError, match="missing"):
        plan_upload(descriptor, root=tmp_path)
    directory = tmp_path / "docs" / "_output"
    directory.mkdir(parents=True)
    (directory / "ok.docx").write_bytes(b"ok")
    (directory / "escape").symlink_to(tmp_path / "docs", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        plan_upload(descriptor, root=tmp_path)


def test_placeholder_cannot_trigger_authentication(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sharedrive.upload.get_client", lambda _: pytest.fail("authenticated")
    )
    with pytest.raises(ValueError, match="placeholder"):
        upload((
            UploadFile(
                tmp_path / "a",
                "https://your-tenant.sharepoint.com/sites/YOUR-SITE/Docs",
                Path("a"),
                "SharePoint",
            ),
        ))


def test_file_resource_uses_root_and_remote_filename(tmp_path, monkeypatch):
    (tmp_path / "local.docx").write_bytes(b"document")
    descriptor = tmp_path / "sharedrive.yaml"
    descriptor.write_text(
        "$schema: sharedrive-catalog\nresources:\n  - name: guide\n"
        "    path: local.docx\n    targets:\n"
        "      - path: https://example.sharepoint.com/sites/dev/Shared%20Documents/Docs/published.docx\n"
    )
    files = plan_upload(descriptor, root=tmp_path)
    calls = []
    client = SimpleNamespace(upload_to_folder=lambda *args: calls.append(args))
    monkeypatch.setattr("sharedrive.upload.get_client", lambda adapter: client)
    upload(files)
    assert calls == [
        (
            "https://example.sharepoint.com/sites/dev/Shared%20Documents/Docs",
            Path("published.docx"),
            tmp_path / "local.docx",
        )
    ]


def test_upload_creates_missing_nested_folder_and_replaces_file(monkeypatch, tmp_path):
    local = tmp_path / "catalog.xlsx"
    local.write_bytes(b"xls")
    client = SharepointClient(access_token="fake", host_url="contoso.sharepoint.com")
    monkeypatch.setattr(
        client,
        "_resolve_weburl",
        lambda url: {"drive_id": "drive", "item_path": "/Docs"},
    )

    calls = []

    def metadata(drive, *, item_path):
        calls.append(("get", item_path))
        if item_path == "Docs/surveys":
            raise GraphApiDriveError("missing", status_code=404)
        return {"id": "folder", "folder": {}}

    monkeypatch.setattr(client, "get_item_metadata", metadata)
    monkeypatch.setattr(
        "sharedrive.clients.sharepoint.requests.post",
        lambda url, **kw: (
            calls.append(("post", kw["json"]["name"]))
            or SimpleNamespace(
                status_code=201, json=lambda: {"id": "child", "folder": {}}
            )
        ),
    )
    monkeypatch.setattr(
        client,
        "_put_file",
        lambda url, path: (
            calls.append(("put", url))
            or {"id": "file", "name": "catalog.xlsx", "file": {}}
        ),
    )
    client.upload_to_folder(
        "https://contoso.sharepoint.com/sites/dev/Shared%20Documents/Docs",
        Path("surveys/catalog.xlsx"),
        local,
    )
    assert ("post", "surveys") in calls
    assert calls[-1][0] == "put"
    assert "/root:/Docs/surveys/catalog.xlsx:/content" in calls[-1][1]
    # A repeat finds the existing folder, uses the same PUT path, and never deletes.
    monkeypatch.setattr(
        client,
        "get_item_metadata",
        lambda drive, *, item_path: {"id": "folder", "folder": {}},
    )
    client.upload_to_folder(
        "https://contoso.sharepoint.com/sites/dev/Shared%20Documents/Docs",
        Path("surveys/catalog.xlsx"),
        local,
    )
    assert len([kind for kind, _ in calls if kind == "post"]) == 1
    assert len([kind for kind, _ in calls if kind == "put"]) == 2
