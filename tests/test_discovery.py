"""Discovery through the CLI, native catalog, and existing provider clients."""

from datetime import date
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.clients.googledrive import (
    FOLDER_MIME,
    GoogleDriveClient,
    GoogleMimeTypes,
)
from fileroute.clients.s3 import S3Client
from fileroute.clients.sharepoint import SharepointClient, SharepointItem
from fileroute.descriptor import load, resolve, save
from fileroute.discovery import discover_resources
from fileroute.exceptions import GoogleDriveError, GraphApiDriveError
from fileroute.models import Catalog, CatalogLink, Location, Resource, ServiceType
from fileroute.transfer import plan_pull, plan_push, pull
from test_googledrive_client import DummyCreds, DummyResponse, DummySession
from test_sharepoint_client import DummyResponse as GraphResponse


def collection(
    path="s3://bucket/definitions/",
    template="{day:%Y-%m-%d}/v{revision:d}.json",
    **source,
):
    return Catalog(
        path="data",
        sources=[Location(path=path, resourcePathTemplate=template, **source)],
    )


def save_catalog(tmp_path, catalog):
    path = tmp_path / "catalog.yaml"
    save(catalog, path)
    return path


def test_linked_s3_discovery_and_pull_use_exact_keys_and_profile(tmp_path, monkeypatch):
    nested = collection(authProfile="reader")
    nested.resource_path_template = "{local}.csv"
    nested.resources["existing"] = Resource(path="data/manual.csv")
    child = tmp_path / "definitions.yaml"
    save(nested, child)
    descriptor = save_catalog(
        tmp_path, Catalog(catalogs={"definitions": CatalogLink(descriptor=child.name)})
    )
    original = child.read_bytes()
    sdk = Mock()
    sdk.list_objects_v2.side_effect = [
        {"IsTruncated": True, "NextContinuationToken": "p2", "Contents": []},
        {"CommonPrefixes": [{"Prefix": "definitions/2026-11-07/"}]},
        {
            "Contents": [
                {"Key": "definitions/2026-11-07/v12.JSON"},
                {"Key": "definitions/2026-11-07/v2.json"},
                {"Key": "definitions/2026-11-07/v1.json"},
            ]
        },
    ]
    remote = S3Client(client=sdk, owns_client=True)
    profiles = Mock(return_value=remote)
    monkeypatch.setattr("fileroute.auth.profiles.build_profile_client", profiles)

    result = discover_resources(
        descriptor, root=tmp_path, selector="/catalogs/definitions", profile="default"
    )
    resources = result.catalogs["definitions"].resources
    assert list(resources) == ["item-2026-11-07-v1-json", "item-2026-11-07-v2-json"]
    assert [entry.path_properties for entry in resources.values()] == [
        {"day": date(2026, 11, 7), "revision": 1},
        {"day": date(2026, 11, 7), "revision": 2},
    ]
    profiles.assert_called_once_with("reader", ServiceType.S3)
    assert [call.kwargs["Prefix"] for call in sdk.list_objects_v2.call_args_list] == [
        "definitions/",
        "definitions/",
        "definitions/2026-11-07/",
    ]
    assert sdk.list_objects_v2.call_args_list[1].kwargs["ContinuationToken"] == "p2"
    assert child.read_bytes() == original
    assert not (tmp_path / "data").exists()
    sdk.download_file.assert_not_called()
    sdk.head_object.assert_not_called()
    sdk.close.assert_called_once()

    output = tmp_path / "reports/discovered.yaml"
    save(result, output)
    resolved = resolve(load(output))
    assert resolved.catalogs["definitions"].resources[
        "item-2026-11-07-v1-json"
    ].path_properties == {"day": "2026-11-07", "revision": 1}
    plan = plan_pull(output, root=tmp_path)
    assert [entry.location.service_id for entry in plan] == [
        "bucket:definitions/2026-11-07/v1.json",
        "bucket:definitions/2026-11-07/v2.json",
    ]
    download = Mock()
    profiles.return_value = S3Client(client=download, owns_client=True)
    pull(plan, profile="different-default")
    assert [call.args[1] for call in download.download_file.call_args_list] == [
        "definitions/2026-11-07/v1.json",
        "definitions/2026-11-07/v2.json",
    ]
    assert all(entry.local.is_relative_to(tmp_path / "data") for entry in plan)
    download.close.assert_called_once()


def test_item_matches_use_strict_paths_and_keep_anonymous_captures_out():
    sdk = Mock()
    pages = {
        "base/": {"CommonPrefixes": [{"Prefix": "base/dev/"}]},
        "base/dev/": {"CommonPrefixes": [{"Prefix": "base/dev/us/"}]},
        "base/dev/us/": {
            "Contents": [
                {"Key": "base/dev/us/definition-v7.json"},
                {"Key": "base/dev/us/definition-v7.JSON"},
                {"Key": "base/dev/us/another.json"},
            ]
        },
    }
    sdk.list_objects_v2.side_effect = lambda **request: pages[request["Prefix"]]
    root = S3Client(client=sdk).get_from_weburl("s3://bucket/base/")
    matches = list(root.iter_file_matches("{scope}/definition-{}.json"))
    assert [(m.relative_path, m.path_properties) for m in matches] == [
        ("dev/us/definition-v7.json", {"scope": "dev/us"})
    ]
    assert not list(root.iter_file_matches("{scope}/missing.json"))
    with pytest.raises(NotADirectoryError):
        list(matches[0].item.iter_file_matches("{name}"))
    sdk.close.assert_not_called()


@pytest.mark.parametrize(
    "template",
    ["/{name}", "../{name}", "C:/{name}", "s3:bucket/{name}", "{name", "{name:bogus}"],
)
def test_preflight_rejects_invalid_templates_without_auth(
    tmp_path, monkeypatch, template
):
    descriptor = save_catalog(tmp_path, collection(template=template))
    factory = Mock(side_effect=AssertionError("unexpected auth"))
    monkeypatch.setattr("fileroute.transfer.get_provider", factory)
    with pytest.raises(ValueError, match="resourcePathTemplate"):
        discover_resources(descriptor, root=tmp_path)
    factory.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "https://drive.google.com/drive/folders/id?resourcekey=hidden",
        "s3://bucket/definitions//",
        "s3://bucket//definitions/",
        "relative/path/",
    ],
)
def test_preflight_rejects_unsupported_roots(tmp_path, path):
    descriptor = save_catalog(tmp_path, collection(path=path))
    with pytest.raises(ValueError, match="root"):
        discover_resources(descriptor, root=tmp_path)


def test_dry_run_and_guards_observe_selection_and_links(tmp_path, monkeypatch):
    child = tmp_path / "child.yaml"
    save(collection(), child)
    descriptor = save_catalog(
        tmp_path,
        Catalog(
            catalogs={"nested": CatalogLink(descriptor=child.name)},
            resources={
                "concrete": Resource(
                    path="ready.json", sources=[Location(path="s3://bucket/ready.json")]
                )
            },
        ),
    )
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    with monkeypatch.context() as patch:
        factory = Mock(side_effect=AssertionError("unexpected auth"))
        patch.setattr("fileroute.transfer.get_provider", factory)
        result = runner.invoke(
            app, ["discover", str(descriptor), "--select", "nested", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "s3://bucket/definitions/" in result.stdout
        assert "v{revision:d}.json" in result.stdout
        assert not (tmp_path / "data").exists()
        invalid = runner.invoke(
            app, ["discover", str(descriptor), "--dry-run", "--format", "json"]
        )
        assert invalid.exit_code != 0
        aliased = runner.invoke(
            app, ["discover", str(descriptor), "--output", str(child)]
        )
        assert aliased.exit_code != 0 and "aliases an input" in aliased.stderr
        factory.assert_not_called()

    with pytest.raises(ValueError, match="fileroute discover"):
        plan_pull(descriptor, root=tmp_path)
    assert (
        plan_pull(descriptor, root=tmp_path, selector="concrete")[0].local.name
        == "ready.json"
    )

    invalid_target = save_catalog(
        tmp_path,
        Catalog(
            resources={
                "file": Resource(
                    path="ready.json",
                    targets=[
                        Location(
                            path="https://tenant.sharepoint.com/sites/test/Docs",
                            resourcePathTemplate="{file}",
                        )
                    ],
                )
            }
        ),
    )
    with pytest.raises(ValueError, match="resourcePathTemplate"):
        plan_push(invalid_target, root=tmp_path, selector="file")
    with pytest.raises(ValueError, match="resourcePathTemplate"):
        discover_resources(invalid_target, root=tmp_path)


@pytest.mark.parametrize(
    "page",
    [
        {"IsTruncated": True},
        {"IsTruncated": True, "NextContinuationToken": "repeated"},
        {},
    ],
)
def test_cli_failed_discovery_does_not_write_output(tmp_path, monkeypatch, page):
    path = save_catalog(tmp_path, collection())
    output = tmp_path / "result.json"
    output.write_text('{"existing":true}')
    sdk = Mock()
    sdk.list_objects_v2.return_value = page
    factory = Mock()
    factory.build_default.return_value = S3Client(client=sdk, owns_client=True)
    monkeypatch.setattr("fileroute.transfer.get_provider", lambda _: factory)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app, ["discover", str(path), "--format", "json", "--output", str(output)]
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "s3://bucket/definitions/" in result.stderr
    assert json.loads(output.read_text()) == {"existing": True}
    sdk.close.assert_called_once()
    sdk.download_file.assert_not_called()


def test_setup_closes_previous_client_before_any_listing(tmp_path, monkeypatch):
    descriptor = save_catalog(
        tmp_path,
        Catalog(
            catalogs={
                "one": collection(authProfile="valid"),
                "two": collection(
                    "https://drive.google.com/drive/folders/root", authProfile="invalid"
                ),
            }
        ),
    )
    sdk = Mock()
    profiles = Mock(
        side_effect=[
            S3Client(client=sdk, owns_client=True),
            ValueError("wrong provider"),
        ]
    )
    monkeypatch.setattr("fileroute.auth.profiles.build_profile_client", profiles)
    with pytest.raises(ValueError, match="wrong provider"):
        discover_resources(descriptor, root=tmp_path)
    assert [call.args for call in profiles.call_args_list] == [
        ("valid", ServiceType.S3),
        ("invalid", ServiceType.GOOGLE_DRIVE),
    ]
    sdk.close.assert_called_once()
    sdk.list_objects_v2.assert_not_called()


def test_output_cannot_replace_a_discovered_artifact(tmp_path, monkeypatch):
    descriptor = save_catalog(
        tmp_path, collection(template="{name}.json", authProfile="s3")
    )
    sdk = Mock()
    sdk.list_objects_v2.return_value = {
        "Contents": [{"Key": "definitions/report.json"}]
    }
    monkeypatch.setattr(
        "fileroute.auth.profiles.build_profile_client", lambda *_: S3Client(client=sdk)
    )
    output = tmp_path / "data/report.json"
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app, ["discover", str(descriptor), "--output", str(output)]
    )
    assert result.exit_code == 1
    assert "overlaps a pull destination" in result.stderr
    assert not output.parent.exists()


@pytest.mark.parametrize(
    "key", ["definitions-elsewhere/report.json", "definitions//report.json"]
)
def test_s3_walk_rejects_keys_outside_the_exact_walked_path(key):
    sdk = Mock()
    sdk.list_objects_v2.return_value = {"Contents": [{"Key": key}]}
    root = S3Client(client=sdk).get_from_weburl("s3://bucket/definitions/")
    with pytest.raises(ValueError, match="Invalid S3 key"):
        list(root.iter_file_matches("{name}.json"))


def drive_entry(id, name, *, parent=None, drive=None, mime="text/csv", **fields):
    return {
        "id": id,
        "name": name,
        "mimeType": mime,
        "parents": [parent] if parent else [],
        "driveId": drive,
        **fields,
    }


@pytest.mark.parametrize("shared_drive", [None, "shared-drive"])
def test_drive_folder_paging_and_exact_sources(tmp_path, monkeypatch, shared_drive):
    folder = drive_entry("folder", "Root", drive=shared_drive, mime=FOLDER_MIME)
    nested = drive_entry(
        "sub", "group", parent="folder", drive=shared_drive, mime=FOLDER_MIME
    )
    shortcut = drive_entry(
        "shortcut",
        "shortcut.csv",
        parent="folder",
        drive=shared_drive,
        mime=GoogleMimeTypes.SHORT.value,
    )
    native = drive_entry(
        "native",
        "Native report",
        parent="folder",
        drive=shared_drive,
        mime="application/vnd.google-apps.document",
    )
    file = drive_entry(
        "report", "Mike's café 10%.csv", parent="sub", drive=shared_drive
    )
    session = DummySession([
        DummyResponse(payload=folder),
        DummyResponse(payload={"files": [nested, shortcut], "nextPageToken": "p2"}),
        DummyResponse(payload={"files": [], "nextPageToken": "p3"}),
        DummyResponse(payload={"files": [native]}),
        DummyResponse(payload={"files": [file]}),
    ])
    client = GoogleDriveClient(credentials=DummyCreds(), session=session)
    profiles = Mock(return_value=client)
    monkeypatch.setattr("fileroute.auth.profiles.build_profile_client", profiles)
    path = save_catalog(
        tmp_path,
        collection(
            "https://drive.google.com/drive/folders/folder",
            "{name}.csv",
            authProfile="drive-read",
        ),
    )
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "result.json"
    cli = CliRunner().invoke(
        app, ["discover", str(path), "--output", str(output), "--format", "json"]
    )
    assert cli.exit_code == 0, cli.output
    result = load(output)
    assert json.loads(cli.stdout) == json.loads(output.read_text())
    (entry,) = result.resources.values()
    assert entry.path_properties == {"name": "group/Mike's café 10%"}
    (source,) = entry.sources
    assert source.service_id == "report"
    assert source.path == "https://drive.google.com/file/d/report/view"
    assert source.drive_id == shared_drive
    assert source.auth_profile == "drive-read"
    assert [url.rsplit("/", 1)[-1] for _, url, _ in session.calls] == [
        "folder",
        "files",
        "files",
        "files",
        "files",
    ]
    params = [request["params"] for _, _, request in session.calls[1:]]
    assert [p.get("pageToken") for p in params] == [None, "p2", "p3", None]
    assert [p["q"] for p in params] == [
        "'folder' in parents and trashed = false",
        "'folder' in parents and trashed = false",
        "'folder' in parents and trashed = false",
        "'sub' in parents and trashed = false",
    ]
    assert all(p["corpora"] == ("drive" if shared_drive else "user") for p in params)
    assert all(p.get("driveId") == shared_drive for p in params)
    assert all("incompleteSearch" in p["fields"] for p in params)

    renamed = drive_entry("report", "renamed.csv", parent="sub", drive=shared_drive)
    session.responses.append(DummyResponse(payload=renamed))
    download = Mock()
    monkeypatch.setattr(client, "download_file", download)
    pull(plan_pull(output, root=tmp_path))
    assert session.calls[-1][1].endswith("/files/report")
    assert download.call_args.args == ("report",)


@pytest.mark.parametrize(
    "failure", [{"incompleteSearch": True}, {"nextPageToken": "p2"}, {"files": {}}]
)
def test_incomplete_drive_listing_is_never_cached(failure):
    session = DummySession([
        DummyResponse(payload=drive_entry("root", "Root", mime=FOLDER_MIME)),
        DummyResponse(payload={"files": [], "nextPageToken": "p2"}),
        DummyResponse(payload=failure),
        DummyResponse(
            payload={"files": [drive_entry("file", "report.csv", parent="root")]}
        ),
    ])
    client = GoogleDriveClient(credentials=DummyCreds(), session=session)
    root = client.get_from_id("root")
    with pytest.raises(GoogleDriveError):
        list(root.iter_file_matches("{name}.csv"))
    assert [match.item.id for match in root.iter_file_matches("{name}.csv")] == ["file"]
    assert "pageToken" not in session.calls[-1][2]["params"]


def test_same_drive_names_are_distinct_matches_but_conflicting_destinations(
    tmp_path, monkeypatch
):
    folder = drive_entry("folder", "Root", mime=FOLDER_MIME)
    children = {
        "files": [
            drive_entry("one", "report.csv", parent="folder"),
            drive_entry("two", "report.csv", parent="folder"),
        ]
    }
    session = DummySession([
        DummyResponse(payload=d) for d in [folder, children, folder, children]
    ])
    client = GoogleDriveClient(credentials=DummyCreds(), session=session)
    assert [
        m.item.id for m in client.get_from_id("folder").iter_file_matches("{name}.csv")
    ] == ["one", "two"]
    monkeypatch.setattr(
        "fileroute.auth.profiles.build_profile_client", lambda *_: client
    )
    path = save_catalog(
        tmp_path,
        collection(
            "https://drive.google.com/drive/folders/folder",
            "{name}.csv",
            authProfile="drive",
        ),
    )
    with pytest.raises(ValueError, match="Duplicate destination.*'one'.*'two'"):
        discover_resources(path, root=tmp_path)


def test_sharepoint_walk_follows_children_links_and_rejects_repetition(monkeypatch):
    client = SharepointClient(access_token="test")
    root = SharepointItem._from_api_response(
        {"id": "root", "folder": {}, "parentReference": {"driveId": "drive"}}, client
    )
    link = "https://graph.microsoft.com/v1.0/drives/drive/items/root/children?$skiptoken=next"
    file = {
        "id": "one",
        "name": "report.csv",
        "file": {},
        "parentReference": {"driveId": "drive", "id": "root"},
    }
    request = Mock(
        side_effect=[
            GraphResponse(
                status_code=200, payload={"value": [], "@odata.nextLink": link}
            ),
            GraphResponse(
                status_code=200, payload={"value": [], "@odata.nextLink": link}
            ),
            GraphResponse(
                status_code=200, payload={"value": [], "@odata.nextLink": link}
            ),
            GraphResponse(status_code=200, payload={"value": [file]}),
        ]
    )
    monkeypatch.setattr(client.session, "request", request)
    with pytest.raises(GraphApiDriveError, match="repeated"):
        list(root.iter_file_matches("{name}.csv"))
    assert [match.item.id for match in root.iter_file_matches("{name}.csv")] == ["one"]
    assert all(
        "/items/root/children" in call.args[1] for call in request.call_args_list
    )


def test_documented_catalog_matches_schema_and_can_dry_run(monkeypatch, tmp_path):
    example = Path(__file__).resolve().parents[1] / "examples/use-cases/discover.yaml"
    catalog = load(example)
    Draft202012Validator(Catalog.model_json_schema(by_alias=True)).validate(
        catalog.model_dump(mode="json", by_alias=True, exclude_unset=True)
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["discover", str(example), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert len(result.stdout.splitlines()) == 3
    assert not (tmp_path / "data").exists()
