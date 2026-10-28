"""Path captures are metadata; resolution never generates or moves artifacts."""

from datetime import date
from decimal import Decimal

import pytest
from jsonschema import Draft202012Validator

from fileroute.descriptor import load, resolve, resolve_selection, save
from fileroute.models import Catalog, Resource


def test_uri_template_extracts_directory_values_with_literal_filename_example():
    catalog = Catalog.model_validate({
        "path": "an/unrelated/local/root",
        "pathTemplate": "drive://test/{var1}/{var2}/test.csv",
        "resources": {"file1": {"path": "drive://test/xx/yy/file1.csv"}},
    })
    result = resolve(catalog)
    assert result.resources["file1"].path_properties == {"var1": "xx", "var2": "yy"}
    assert catalog.resources["file1"].path_properties == {}
    assert result.resources["file1"].path == "drive://test/xx/yy/file1.csv"
    assert resolve(result) == result


def test_typed_named_captures_and_json_serialization():
    catalog = Catalog(
        path="data",
        resourcePathTemplate="{day:%Y-%m-%d}/{round:d}/{price:F}/{filename}.csv",
        resources={"file": Resource(path="data/2026-10-28/2/1.50/result.csv")},
    )
    result = resolve(catalog).resources["file"]
    assert result.path_properties == {
        "day": date(2026, 10, 28),
        "round": 2,
        "price": Decimal("1.50"),
        "filename": "result",
    }
    assert result.model_dump(mode="json", by_alias=True)["pathProperties"] == {
        "day": "2026-10-28",
        "round": 2,
        "price": "1.50",
        "filename": "result",
    }


def test_nearest_template_inherits_declaring_base_and_can_override():
    catalog = Catalog(
        path="data",
        resourcePathTemplate="{phase}/{filename}.csv",
        catalogs={
            "inherited": Catalog(
                path="unrelated",
                resources={"file": Resource(path="data/review/result.csv")},
            ),
            "override": Catalog(
                path="other",
                resourcePathTemplate="{term:d}.yaml",
                resources={"file": Resource(path="other/42.yaml")},
            ),
        },
    )
    result = resolve(catalog)
    assert result.catalogs["inherited"].resources["file"].path_properties == {
        "phase": "review",
        "filename": "result",
    }
    assert result.catalogs["override"].resources["file"].path_properties == {"term": 42}


@pytest.mark.parametrize(
    "path", ["data-other/ready/a.csv", "data/../ready/a.csv", "data/Ready/a.csv"]
)
def test_outside_or_nonmatching_paths_clear_old_values(path):
    catalog = Catalog(
        path="data",
        resourcePathTemplate="ready/{filename}.csv",
        resources={"file": Resource(path=path, pathProperties={"phase": "stale"})},
    )
    assert resolve(catalog).resources["file"].path_properties == {}


def test_changed_path_recomputes_values_and_missing_template_preserves_authored():
    catalog = Catalog(
        path="data",
        resourcePathTemplate="{phase}/{filename}.csv",
        resources={"file": Resource(path="data/review/a.csv")},
    )
    first = resolve(catalog)
    first.resources["file"].path = "data/ready/a.csv"
    assert resolve(first).resources["file"].path_properties["phase"] == "ready"
    authored = Catalog(
        resources={"file": Resource(path="file", pathProperties={"custom": 3})}
    )
    assert resolve(authored).resources["file"].path_properties == {"custom": 3}


def test_windows_separators_and_anonymous_captures():
    catalog = Catalog(
        path=r"data\root",
        resourcePathTemplate=r"{phase}\{}.csv",
        resources={"file": Resource(path=r"data\root\review\a.csv")},
    )
    assert resolve(catalog).resources["file"].path_properties == {"phase": "review"}


@pytest.mark.parametrize(
    "basename", ["{filename}.csv", "{}.csv", "{filename:w}.csv", "{file[name]}.csv"]
)
def test_captured_basename_must_match_completely(basename):
    catalog = Catalog(
        resourcePathTemplate="drive://test/{phase}/" + basename,
        resources={"file": Resource(path="drive://test/ready/a.docx")},
    )
    assert resolve(catalog).resources["file"].path_properties == {}


@pytest.mark.parametrize("path", ["drive://other/review/a.csv", "drive://test/review/"])
def test_literal_filename_fallback_preserves_directory_scope_and_requires_file(path):
    catalog = Catalog(
        pathTemplate="drive://test/{phase}/test.csv",
        resources={"file": Resource(path=path)},
    )
    assert resolve(catalog).resources["file"].path_properties == {}


@pytest.mark.parametrize(
    "template", ["{broken", "{value!r}", "{value:unknown}", "{same:d}/{same:w}"]
)
def test_invalid_templates_fail_with_actionable_error(template):
    with pytest.raises(ValueError, match="Invalid resourcePathTemplate"):
        resolve(Catalog(pathTemplate=template))


def test_aliases_agree_and_canonical_serialization_roundtrips(tmp_path):
    value = {
        "pathTemplate": "{phase}/{file}.csv",
        "resourcePathTemplate": "{phase}/{file}.csv",
        "resources": {"file": {"path": "ready/a.csv"}},
    }
    Draft202012Validator(Catalog.model_json_schema(by_alias=True)).validate(value)
    result = resolve(Catalog.model_validate(value))
    destination = tmp_path / "catalog.yaml"
    save(result, destination)
    assert "pathTemplate:" not in destination.read_text()
    assert load(destination).resources["file"].path_properties == {
        "phase": "ready",
        "file": "a",
    }
    with pytest.raises(ValueError, match="must agree"):
        Catalog(pathTemplate="a", resourcePathTemplate="b")


def test_template_alias_and_native_python_field_cannot_disagree():
    with pytest.raises(ValueError, match="must agree"):
        Catalog(pathTemplate="{id}.csv", resource_path_template="{id:d}.csv")
    catalog = Catalog(pathTemplate="{id}.csv", resource_path_template="{id}.csv")
    assert "resource_path_template" not in catalog.model_dump(by_alias=True)


def test_save_keeps_properties_added_to_default_mapping(tmp_path):
    catalog = Catalog(resources={"file": Resource(path="file.csv")})
    catalog.resources["file"].path_properties["phase"] = "review"
    destination = tmp_path / "catalog.yaml"
    save(catalog, destination)
    assert load(destination).resources["file"].path_properties == {"phase": "review"}


@pytest.mark.parametrize("selector", ["nested", "nested.file"])
def test_selection_preserves_ancestor_template_and_writes_properties(
    tmp_path, selector
):
    descriptor = tmp_path / "catalog.yaml"
    descriptor.write_text("""path: data
pathTemplate: "{phase}/{filename}.csv"
catalogs:
  nested:
    path: ignored
    resources:
      file:
        path: data/ready/a.csv
""")
    result = resolve_selection(descriptor, selector, write=True)
    resource = (
        result.model
        if isinstance(result.model, Resource)
        else result.model.resources["file"]
    )
    assert resource.path_properties == {"phase": "ready", "filename": "a"}
    assert (
        load(descriptor).catalogs["nested"].resources["file"].path_properties
        == resource.path_properties
    )


def test_expanded_link_inherits_template_but_write_targets_only_origin(tmp_path):
    child = tmp_path / "child.yaml"
    child.write_text("resources:\n  file:\n    path: data/ready/a.csv\n")
    parent = tmp_path / "parent.yaml"
    parent.write_text(
        'path: data\npathTemplate: "{phase}/{filename}.csv"\ncatalogs:\n  linked:\n    descriptor: child.yaml\n'
    )
    before = parent.read_bytes()
    expanded = resolve(load(parent, resolve_references=True))
    assert (
        expanded.catalogs["linked"].resources["file"].path_properties["phase"]
        == "ready"
    )
    resolve_selection(parent, "linked", write=True)
    assert load(child).resources["file"].path_properties == {
        "phase": "ready",
        "filename": "a",
    }
    assert parent.read_bytes() == before
