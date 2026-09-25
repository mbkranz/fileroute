import pytest

from fileroute.descriptor import resolve
from fileroute.models import Catalog, Location, Resource, ServiceType


@pytest.mark.parametrize(
    "alias", ["GoogleDrive", "google_drive", "google-drive", "google", "gdrive"]
)
def test_service_alias_is_enum(alias):
    location = Location(path="https://drive.google.com/file/d/123", service_type=alias)
    assert location.service_type is ServiceType.GOOGLE_DRIVE
    assert (
        location.model_dump(mode="json", by_alias=True, exclude_unset=True)[
            "serviceType"
        ]
        == "GoogleDrive"
    )


def test_resolution_preserves_clickable_urls_and_local_provenance():
    url = "https://tenant.sharepoint.com/sites/dev/Shared%20Documents/Guide.docx"
    original = Catalog(
        resources={
            "entry0": Resource(
                path="out.docx",
                sources=[Location(path="guide.qmd")],
                targets=[Location(path=url)],
            )
        }
    )
    result = resolve(original)
    assert list(original.resources.values())[0].targets[0].service_type is None
    assert (
        list(result.resources.values())[0].targets[0].service_type
        is ServiceType.SHAREPOINT
    )
    assert list(result.resources.values())[0].targets[0].path == url
    assert list(result.resources.values())[0].sources[0].service_type is None
    assert resolve(result) == result


@pytest.mark.parametrize(
    "url",
    [
        "https://sharepoint.com.evil.example/file",
        "https://notsharepoint.com/file",
        "https://s3.evil.example/bucket/file",
        "https://example.com/link",
        "relative/file",
    ],
)
def test_unknown_targets_require_explicit_provider(url):
    with pytest.raises(ValueError, match="Cannot resolve target"):
        resolve(Catalog(targets=[Location(path=url)]))


def test_unknown_source_can_be_provenance():
    result = resolve(Catalog(sources=[Location(path="https://example.org/article")]))
    assert result.sources[0].service_type is None


def test_conflicting_provider_fails():
    with pytest.raises(ValueError, match="conflicts"):
        resolve(
            Catalog(
                targets=[Location(path="s3://bucket/file", service_type="SharePoint")]
            )
        )


def test_explicit_provider_allows_nonstandard_remote_locator():
    result = resolve(
        Catalog(
            targets=[
                Location(path="https://example.org/file", service_type="SharePoint")
            ]
        )
    )
    assert result.targets[0].service_type is ServiceType.SHAREPOINT


def test_resolve_cli_preview_write_and_idempotence(tmp_path, monkeypatch):
    import json
    import yaml_support as yaml
    from typer.testing import CliRunner
    from fileroute.cli import app

    path = tmp_path / "catalog.yaml"
    url = "https://tenant.sharepoint.com/sites/dev/Docs/guide%20one.docx"
    path.write_text(
        yaml.safe_dump({
            "resources": {
                "guide": {
                    "path": "out.docx",
                    "sources": [{"path": "guide.qmd"}],
                    "targets": [{"path": url}],
                }
            }
        })
    )
    before = path.read_bytes()
    monkeypatch.setattr(
        "fileroute.clients.get_provider", lambda _: pytest.fail("provider lookup")
    )
    runner = CliRunner()
    preview = runner.invoke(app, ["resolve", str(path)])
    assert preview.exit_code == 0, preview.output
    target = json.loads(preview.output)["resources"]["guide"]["targets"][0]
    assert target == {
        "path": url,
        "serviceType": "SharePoint",
        "site": "dev",
        "drive": "Docs",
        "remotePath": "guide one.docx",
    }
    assert path.read_bytes() == before
    written = runner.invoke(app, ["resolve", str(path), "--write"])
    assert written.exit_code == 0, written.output
    first = path.read_bytes()
    assert runner.invoke(app, ["resolve", str(path), "--write"]).exit_code == 0
    assert path.read_bytes() == first
    assert yaml.safe_load(first)["resources"]["guide"]["sources"] == [
        {"path": "guide.qmd"}
    ]


def test_resolve_preserves_references_and_does_not_edit_referenced_files(tmp_path):
    from typer.testing import CliRunner
    from fileroute.cli import app
    from fileroute.descriptor import load
    from fileroute.models import CatalogLink

    child = tmp_path / "child.yaml"
    child.write_text(
        "resources:\n  entry0:\n    path: file\n    sources:\n    - path: s3://bucket/file\n"
    )
    before = child.read_bytes()
    parent = tmp_path / "parent.yaml"
    parent.write_text("catalogs:\n  entry0:\n    descriptor: child.yaml\n")
    result = CliRunner().invoke(app, ["resolve", str(parent), "--write"])
    assert result.exit_code == 0, result.output
    assert isinstance(list(load(parent).catalogs.values())[0], CatalogLink)
    assert child.read_bytes() == before


def test_failed_resolve_write_leaves_descriptor_unchanged(tmp_path):
    from typer.testing import CliRunner
    from fileroute.cli import app

    path = tmp_path / "catalog.yaml"
    path.write_text("targets:\n - path: s3://bucket/file\n   serviceType: SharePoint\n")
    before = path.read_bytes()
    result = CliRunner().invoke(app, ["resolve", str(path), "--write"])
    assert result.exit_code == 1
    assert "conflicts" in result.output
    assert path.read_bytes() == before


def test_offline_provider_paths_and_url_ids():
    doc = resolve(
        Catalog(
            targets=[
                Location(
                    path="Reports/a.docx",
                    serviceType="SharePoint",
                    site="PPSC",
                    drive="Shared Documents",
                ),
                Location(
                    path="Reports/a.docx",
                    serviceType="GoogleDrive",
                    drive="PPSC Shared Drive",
                ),
                Location(path="reports/a.csv", serviceType="S3", bucket="data-bucket"),
                Location(path="https://drive.google.com/file/d/abc_DEF-123/view"),
                Location(path="s3://data-bucket/reports/a.csv"),
            ]
        )
    )
    sp, google, s3, google_url, s3_url = doc.targets
    assert (sp.site, sp.drive, sp.remote_path) == (
        "PPSC",
        "Shared Documents",
        "Reports/a.docx",
    )
    assert (google.drive, google.remote_path) == ("PPSC Shared Drive", "Reports/a.docx")
    assert (s3.bucket, s3.remote_path) == ("data-bucket", "reports/a.csv")
    assert google_url.service_id == "abc_DEF-123"
    assert (s3_url.bucket, s3_url.remote_path) == ("data-bucket", "reports/a.csv")
    assert resolve(doc) == doc


def test_provider_neutral_resolved_location_round_trip():
    from fileroute.resolution import ResolvedLocation

    loc = Location(
        path="Reports/a.docx",
        serviceType="SharePoint",
        site="PPSC",
        drive="Docs",
        remotePath="Reports/a.docx",
    )
    resolved = ResolvedLocation.from_location(loc)
    assert resolved.context == {"site": "PPSC", "drive": "Docs"}
    assert resolved.remote_path == "Reports/a.docx"
    resolved.apply(loc)
    assert loc.service_type is ServiceType.SHAREPOINT


@pytest.mark.parametrize(
    "location,match",
    [
        (
            Location(path="Reports/a.docx", serviceType="SharePoint", site="PPSC"),
            "drive",
        ),
        (Location(path="Reports/a.docx", serviceType="GoogleDrive"), "drive"),
        (Location(path="reports/a.csv", serviceType="S3"), "bucket"),
        (Location(path="s3://bucket/a.csv", bucket="another"), "conflicts"),
        (
            Location(
                path="https://tenant.sharepoint.com/sites/PPSC/Docs/file", site="Other"
            ),
            "conflicts",
        ),
    ],
)
def test_under_scoped_or_conflicting_metadata(location, match):
    with pytest.raises(ValueError, match=match):
        resolve(Catalog(targets=[location]))


def test_online_sharepoint_populates_site_drive_item_ids(monkeypatch):
    from fileroute.clients.sharepoint import SharepointClient

    client = SharepointClient(access_token="fake")
    calls = []
    monkeypatch.setattr(
        client, "get_site_id", lambda name: calls.append(("site", name)) or "site-id"
    )
    monkeypatch.setattr(
        client,
        "get_drive_id",
        lambda site_id, drive_name: calls.append(("drive", drive_name)) or "drive-id",
    )
    monkeypatch.setattr(
        client,
        "get_item_metadata",
        lambda drive_id, *, item_path: {
            "id": "item-id",
            "name": "a.docx",
            "file": {},
            "parentReference": {"driveId": drive_id},
        },
    )
    monkeypatch.setattr(SharepointClient, "build_default", lambda: client)
    location = Location(
        path="Reports/a.docx", serviceType="SharePoint", site="PPSC", drive="Docs"
    )
    result = resolve(Catalog(targets=[location]), online=True).targets[0]
    assert (result.site_id, result.drive_id, result.service_id, result.entity_type) == (
        "site-id",
        "drive-id",
        "item-id",
        "File",
    )
    assert calls == [("site", "PPSC"), ("drive", "Docs")]
    assert location.service_id is None


def test_sharepoint_site_url_uses_default_drive_online(monkeypatch):
    from fileroute.clients.sharepoint import SharepointClient

    client = SharepointClient(access_token="fake")
    monkeypatch.setattr(client, "get_site_id", lambda _: "site-id")
    monkeypatch.setattr(client, "get_drive_id", lambda site_id: "default-drive")
    monkeypatch.setattr(
        client,
        "get_item_metadata",
        lambda drive_id, *, item_path: {
            "id": "root",
            "folder": {},
            "parentReference": {"driveId": drive_id},
        },
    )
    result = resolve(
        Catalog(targets=[Location(path="https://tenant.sharepoint.com/sites/PPSC")])
    ).targets[0]
    assert (result.site, result.drive) == ("PPSC", None)
    online = client.resolve_location(result)
    assert (
        online.context["site_id"],
        online.context["drive_id"],
        online.service_id,
    ) == ("site-id", "default-drive", "root")


def test_online_google_named_drive_and_id_url(monkeypatch):
    from fileroute.clients.googledrive import GoogleDriveClient

    calls = []

    def lookup(self, location):
        calls.append((location.drive, location.remote_path, location.service_id))
        return type(
            "Item",
            (),
            {
                "id": "abc" if location.service_id else "file-id",
                "is_directory": False,
                "_drive_id": "drive-id",
            },
        )()

    monkeypatch.setattr(GoogleDriveClient, "get_from_location", lookup)
    # Exercise provider behavior with a client instance that needs no credentials.
    client = object.__new__(GoogleDriveClient)
    result = GoogleDriveClient.resolve_location(
        client,
        resolve(
            Catalog(
                targets=[
                    Location(
                        path="Reports/a.docx", serviceType="GoogleDrive", drive="PPSC"
                    )
                ]
            )
        ).targets[0],
    )
    assert result.context["drive_id"] == "drive-id"
    assert result.service_id == "file-id"
    url = resolve(
        Catalog(targets=[Location(path="https://drive.google.com/file/d/abc/view")])
    ).targets[0]
    GoogleDriveClient.resolve_location(client, url)
    assert calls == [("PPSC", "Reports/a.docx", None), (None, None, "abc")]


def test_online_s3_verifies_prefix_and_bucket():
    from fileroute.clients.s3 import S3Client

    calls = []
    api = type(
        "Api",
        (),
        {
            "list_objects_v2": lambda self, **kw: (
                calls.append(("list", kw["Prefix"])) or {"KeyCount": 1}
            ),
            "head_bucket": lambda self, **kw: calls.append(("head", kw["Bucket"])),
        },
    )()
    client = S3Client(client=api)
    for path in ("s3://bucket/root/", "s3://bucket"):
        loc = resolve(Catalog(targets=[Location(path=path)])).targets[0]
        result = client.resolve_location(loc)
        assert result.entity_type == "Directory"
        assert result.service_id.startswith("bucket:")
    assert calls == [("list", "root/"), ("head", "bucket")]


def test_online_cli_write_preserves_url_and_enriches_ids(tmp_path, monkeypatch):
    import yaml_support as yaml
    from typer.testing import CliRunner
    from fileroute.cli import app
    from fileroute.clients.sharepoint import SharepointClient

    path = tmp_path / "catalog.yaml"
    url = "https://tenant.sharepoint.com/sites/PPSC/Docs/Reports/a.docx"
    path.write_text(f"targets:\n  - path: {url}\n")
    client = SharepointClient(access_token="fake")
    monkeypatch.setattr(SharepointClient, "build_default", lambda: client)
    monkeypatch.setattr(client, "get_site_id", lambda _: "site-id")
    monkeypatch.setattr(client, "get_drive_id", lambda *args, **kw: "drive-id")
    monkeypatch.setattr(
        client,
        "get_item_metadata",
        lambda *args, **kw: {
            "id": "item-id",
            "file": {},
            "name": "a.docx",
            "parentReference": {"driveId": "drive-id"},
        },
    )
    runner = CliRunner()
    result = runner.invoke(app, ["resolve", str(path), "--online", "--write"])
    assert result.exit_code == 0, result.output
    target = yaml.safe_load(path.read_text())["targets"][0]
    assert target["path"] == url
    assert (
        target["siteId"],
        target["driveId"],
        target["serviceId"],
        target["entityType"],
    ) == ("site-id", "drive-id", "item-id", "File")
    first = path.read_bytes()
    assert (
        runner.invoke(app, ["resolve", str(path), "--online", "--write"]).exit_code == 0
    )
    assert path.read_bytes() == first
