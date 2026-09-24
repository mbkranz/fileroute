"""Descriptor I/O, references, traversal, lookup and listing."""

import json

import pytest
from typer.testing import CliRunner

from sharedrive.cli import app
from sharedrive.models import Catalog, CatalogReference, Resource
from sharedrive.descriptor import load, save, walk, find


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
        save(catalog, path)
        loaded = load(path)
        assert (
            loaded.model_dump(mode="json", by_alias=True, exclude_unset=True)
            == document
        )
        assert loaded.resources[0].targets == []
        assert catalog.resources[0].path == "guide.docx"


def test_nested_reference_load_is_lazy_relative_and_repeatable(tmp_path, monkeypatch):
    parent = tmp_path / "parent.yaml"
    parent.write_text("catalogs:\n - name: external\n   $ref: nested/child.yaml\n")
    catalog = load(parent)
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
        assert (
            find(
                load(parent, resolve_references=True),
                "external.leaf.file",
                kind=Resource,
            ).path
            == "out.csv"
        )
        assert isinstance(catalog.catalogs[0], CatalogReference)
    assert catalog.model_dump(mode="json", by_alias=True, exclude_unset=True)[
        "catalogs"
    ][0] == {"name": "external", "$ref": "nested/child.yaml"}


def test_reference_cycle_and_escape(tmp_path):
    path = tmp_path / "cycle.yaml"
    path.write_text("catalogs:\n - $ref: cycle.yaml\n")
    with pytest.raises(ValueError, match="Cyclic"):
        load(path, resolve_references=True)
    path.write_text("catalogs:\n - $ref: ../escape.yaml\n")
    with pytest.raises(ValueError, match="outside"):
        load(path, resolve_references=True)


def test_lookup_enforces_kind_and_ambiguity():
    catalog = Catalog(
        resources=[Resource(name="same", path="one")],
        catalogs=[Catalog(name="group", resources=[Resource(name="same", path="two")])],
    )
    with pytest.raises(ValueError, match="ambiguous"):
        find(catalog, "same", kind=Resource)
    with pytest.raises(ValueError, match="not found"):
        find(catalog, "same", kind=Catalog)
    assert find(catalog, "group.same", kind=Resource).path == "two"
    catalog.catalogs.append(Catalog(name="group"))
    with pytest.raises(ValueError, match="Duplicate"):
        list(walk(catalog))


def test_list_cli(tmp_path):
    path = tmp_path / "catalog.yaml"
    save(Catalog(resources=[Resource(name="file", path="out.csv")]), path)
    result = CliRunner().invoke(app, ["list", str(path), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["entities"][0]["path"] == "out.csv"


def test_walk_never_loads_references(tmp_path):
    path = tmp_path / "root.yaml"
    path.write_text("catalogs:\n - name: external\n   $ref: missing.yaml\n")
    assert isinstance(list(walk(load(path)))[0].model, CatalogReference)


def test_save_preserves_explicit_defaults_and_null_metadata(tmp_path):
    path = tmp_path / "catalog.json"
    data = {"$schema": "sharedrive-catalog", "resources": [], "custom": None}
    save(Catalog.model_validate(data), path)
    assert json.loads(path.read_text()) == data


def test_save_includes_mutated_default_collections(tmp_path):
    catalog = Catalog()
    catalog.resources.append(Resource(path="new.csv"))
    path = tmp_path / "catalog.yaml"
    save(catalog, path)
    assert load(path).resources[0].path == "new.csv"
