"""Link expansion, physical provenance, and canonical I/O."""

import json
import pytest
from typer.testing import CliRunner
from fileroute.cli import app
from fileroute.models import Catalog, CatalogLink, Resource
from fileroute.descriptor import load, save, walk, find, select


def test_roundtrip_preserves_metadata_and_mutated_defaults(tmp_path):
    model = Catalog(custom={"owner": "team"})
    model.resources["guide"] = Resource(
        path="guide.docx", targets=[], schema={"fields": [{"name": "id"}]}
    )
    for suffix in ("json", "yaml"):
        path = tmp_path / f"catalog.{suffix}"
        save(model, path)
        result = load(path)
        assert result.resources["guide"].targets == []
        assert (
            result.resources["guide"].model_extra["schema"]["fields"][0]["name"] == "id"
        )
        assert result.model_extra["custom"] == {"owner": "team"}


def test_nested_links_lazy_provenance_and_shared_child(tmp_path):
    path = tmp_path / "root.yaml"
    path.write_text(
        "catalogs:\n  left:\n    descriptor: nested/child.yaml\n  right:\n    descriptor: nested/child.yaml\n"
    )
    assert isinstance(load(path).catalogs["left"], CatalogLink)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/child.yaml").write_text(
        "catalogs:\n  leaf:\n    descriptor: leaf.json\n"
    )
    (tmp_path / "nested/leaf.json").write_text(
        json.dumps({"resources": {"file": {"path": "out.csv"}}})
    )
    expanded = load(path, resolve_references=True)
    left = select(expanded, "left.leaf.file")
    right = select(expanded, "right.leaf.file")
    assert left.model is not right.model
    assert left.entry.origin_descriptor == tmp_path / "nested/leaf.json"
    assert left.origin_pointer == "/resources/file"
    assert len(left.entry.reference_chain) == 3
    with pytest.raises(ValueError, match="ambiguous"):
        find(expanded, "file")
    with pytest.raises(ValueError, match="expanded"):
        save(expanded, path)
    assert "descriptor: nested/child.yaml" in path.read_text()


@pytest.mark.parametrize(
    "reference,pattern", [("root.yaml", "Cyclic"), ("../escape.yaml", "outside")]
)
def test_link_errors(tmp_path, reference, pattern):
    path = tmp_path / "root.yaml"
    path.write_text(f"catalogs:\n  child:\n    descriptor: {reference}\n")
    with pytest.raises(ValueError, match=pattern):
        load(path, resolve_references=True)


def test_custom_tags_and_duplicate_keys_fail(tmp_path):
    path = tmp_path / "root.yaml"
    for content in (
        "catalogs:\n  child: !include child.yaml\n",
        "resources:\n  file: {path: a}\n  file: {path: b}\n",
    ):
        path.write_text(content)
        with pytest.raises(ValueError):
            load(path)
    path = tmp_path / "root.json"
    path.write_text('{"resources": {}, "resources": {}}')
    with pytest.raises(ValueError, match="Duplicate"):
        load(path)


def test_list_names_and_editable_origins(tmp_path):
    child = tmp_path / "child.yaml"
    child.write_text("resources:\n  guide:\n    path: guide.docx\n")
    root = tmp_path / "root.yaml"
    root.write_text("catalogs:\n  docs:\n    descriptor: child.yaml\n")
    result = CliRunner().invoke(
        app, ["list", str(root), "--format", "json", "--kind", "resource"]
    )
    assert result.exit_code == 0, result.output
    row = json.loads(result.output)["entities"][0]
    assert row["name"] == "guide" and row["qualifiedName"] == "docs.guide"
    assert row["originDescriptor"] == str(child)
    assert row["originSelector"] == "$.resources.guide"
    assert [r.name for r in walk(load(root))] == ["docs"]
