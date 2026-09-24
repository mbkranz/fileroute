import pytest

from sharedrive.descriptor import resolve
from sharedrive.models import Catalog, Location, Resource, ServiceType


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
        resources=[
            Resource(
                path="out.docx",
                sources=[Location(path="guide.qmd")],
                targets=[Location(path=url)],
            )
        ]
    )
    result = resolve(original)
    assert original.resources[0].targets[0].service_type is None
    assert result.resources[0].targets[0].service_type is ServiceType.SHAREPOINT
    assert result.resources[0].targets[0].path == url
    assert result.resources[0].sources[0].service_type is None
    assert resolve(result) == result


@pytest.mark.parametrize(
    "url",
    [
        "https://sharepoint.com.evil.example/file",
        "https://notsharepoint.com/file",
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
    from sharedrive.cli import app

    path = tmp_path / "catalog.yaml"
    url = "https://tenant.sharepoint.com/sites/dev/Docs/guide%20one.docx"
    path.write_text(
        yaml.safe_dump({
            "resources": [
                {
                    "path": "out.docx",
                    "sources": [{"path": "guide.qmd"}],
                    "targets": [{"path": url}],
                }
            ]
        })
    )
    before = path.read_bytes()
    monkeypatch.setattr(
        "sharedrive.clients.get_provider", lambda _: pytest.fail("provider lookup")
    )
    runner = CliRunner()
    preview = runner.invoke(app, ["resolve", str(path)])
    assert preview.exit_code == 0, preview.output
    target = json.loads(preview.output)["resources"][0]["targets"][0]
    assert target == {"path": url, "serviceType": "SharePoint"}
    assert path.read_bytes() == before
    written = runner.invoke(app, ["resolve", str(path), "--write"])
    assert written.exit_code == 0, written.output
    first = path.read_bytes()
    assert runner.invoke(app, ["resolve", str(path), "--write"]).exit_code == 0
    assert path.read_bytes() == first
    assert yaml.safe_load(first)["resources"][0]["sources"] == [{"path": "guide.qmd"}]


def test_resolve_preserves_references_and_does_not_edit_referenced_files(tmp_path):
    from typer.testing import CliRunner
    from sharedrive.cli import app
    from sharedrive.descriptor import load
    from sharedrive.models import CatalogReference

    child = tmp_path / "child.yaml"
    child.write_text(
        "resources:\n - path: file\n   sources:\n    - path: s3://bucket/file\n"
    )
    before = child.read_bytes()
    parent = tmp_path / "parent.yaml"
    parent.write_text("catalogs:\n - $ref: child.yaml\n")
    result = CliRunner().invoke(app, ["resolve", str(parent), "--write"])
    assert result.exit_code == 0, result.output
    assert isinstance(load(parent).catalogs[0], CatalogReference)
    assert child.read_bytes() == before


def test_failed_resolve_write_leaves_descriptor_unchanged(tmp_path):
    from typer.testing import CliRunner
    from sharedrive.cli import app

    path = tmp_path / "catalog.yaml"
    path.write_text("targets:\n - path: s3://bucket/file\n   serviceType: SharePoint\n")
    before = path.read_bytes()
    result = CliRunner().invoke(app, ["resolve", str(path), "--write"])
    assert result.exit_code == 1
    assert "conflicts" in result.output
    assert path.read_bytes() == before
