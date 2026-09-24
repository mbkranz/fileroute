"""Descriptor contracts independent of provider SDK behavior."""

import json

import pytest
from typer.testing import CliRunner

from sharedrive.cli import app
from sharedrive.models import Catalog, CatalogReference, Resource
from sharedrive.migration import migrate_descriptor


def test_roundtrip_preserves_metadata_and_target_opt_out(tmp_path):
    document = {
        "name": "docs",
        "custom": {"owner": "team"},
        "resources": [
            {
                "name": "guide",
                "path": "guide.docx",
                "targets": [],
                "sources": [{"path": "guide.qmd", "title": "Authoring source"}],
                "schema": {"fields": [{"name": "id"}]},
            }
        ],
    }
    catalog = Catalog.model_validate(document)
    for extension in ("yaml", "json"):
        path = tmp_path / f"catalog.{extension}"
        catalog.to_path(path)
        loaded = Catalog.from_path(path)
        assert loaded.to_dict() == document
        assert loaded.resources[0].targets == []
        assert catalog.resources[0].path == "guide.docx"


def test_nested_reference_load_is_lazy_relative_and_repeatable(tmp_path, monkeypatch):
    parent = tmp_path / "parent.yaml"
    parent.write_text("catalogs:\n - name: external\n   $ref: nested/child.yaml\n")
    catalog = Catalog.from_path(parent)
    assert isinstance(catalog.catalogs[0], CatalogReference)
    # Loading the parent did not attempt to read the missing child.
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/child.yaml").write_text(
        "name: original\ncatalogs:\n - $ref: leaf.yaml\n"
    )
    (tmp_path / "nested/leaf.yaml").write_text(
        "name: leaf\nresources:\n - name: file\n   path: out.csv\n"
    )
    monkeypatch.chdir(tmp_path.parent)
    for _ in range(2):
        assert catalog.get_resource("external.leaf.file").path == "out.csv"
        assert isinstance(catalog.catalogs[0], CatalogReference)
    assert catalog.to_dict()["catalogs"][0] == {
        "name": "external",
        "$ref": "nested/child.yaml",
    }


def test_reference_cycle_and_escape(tmp_path):
    path = tmp_path / "cycle.yaml"
    path.write_text("catalogs:\n - $ref: cycle.yaml\n")
    catalog = Catalog.from_path(path)
    with pytest.raises(ValueError, match="Cyclic"):
        list(catalog.iter_entity_paths())
    with pytest.raises(ValueError, match="Cyclic"):
        catalog.dereference()
    path.write_text("catalogs:\n - $ref: ../escape.yaml\n")
    with pytest.raises(ValueError, match="outside"):
        list(Catalog.from_path(path).iter_entity_paths())


def test_lookup_enforces_kind_and_ambiguity():
    catalog = Catalog(
        resources=[Resource(name="same", path="one")],
        catalogs=[Catalog(name="group", resources=[Resource(name="same", path="two")])],
    )
    with pytest.raises(ValueError, match="ambiguous"):
        catalog.get_resource("same")
    with pytest.raises(ValueError, match="not found"):
        catalog.get_catalog("same")
    assert catalog.get_resource("group.same").path == "two"
    catalog.catalogs.append(Catalog(name="group"))
    with pytest.raises(ValueError, match="Duplicate"):
        catalog.assert_valid_entity_paths()


@pytest.mark.parametrize("field", ["_cache", "packages", "serviceType", "syncTarget"])
def test_retired_fields_fail_actionably(field):
    with pytest.raises(ValueError, match="migrate"):
        Catalog.model_validate({field: []})


@pytest.mark.parametrize("direction,field", [("pull", "sources"), ("push", "targets")])
def test_explicit_migration_translates_packages_and_legacy_locations(
    tmp_path, direction, field
):
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps({
            "packages": [
                {
                    "name": "archive",
                    "resources": [
                        {
                            "name": "file",
                            "_cache": "out.csv",
                            "path": "s3://bucket/file.csv",
                            "serviceType": "s3",
                            "custom": 42,
                        }
                    ],
                }
            ]
        })
    )
    catalog = migrate_descriptor(path, direction=direction)
    resource = catalog.get_resource("archive.file")
    assert resource.path == "out.csv"
    assert getattr(resource, field)[0].path == "s3://bucket/file.csv"
    assert getattr(resource, field)[0].serviceType == "S3"
    assert resource.to_dict()["custom"] == 42
    assert "packages" not in catalog.to_dict()


def test_migrate_preserves_explicit_provenance_does_not_publish_it(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps({
            "resources": [
                {
                    "name": "file",
                    "path": "out.csv",
                    "sources": [{"path": "input.qmd"}],
                    "syncTarget": "path",
                }
            ]
        })
    )
    resource = migrate_descriptor(path, direction="push").resources[0]
    assert resource.sources[0].path == "input.qmd"
    assert resource.targets is None


def test_list_and_migrate_cli(tmp_path):
    original = tmp_path / "old.yaml"
    output = tmp_path / "new.yaml"
    original.write_text(
        "resources:\n - name: file\n   _cache: out.csv\n   path: s3://bucket/file.csv\n"
    )
    result = CliRunner().invoke(app, ["migrate", str(original), str(output)])
    assert result.exit_code == 0, result.output
    result = CliRunner().invoke(app, ["list", str(output), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["entities"][0]["path"] == "out.csv"
    assert (
        CliRunner().invoke(app, ["migrate", str(original), str(output)]).exit_code != 0
    )
    assert "_cache" in original.read_text()
