"""A single parent-relative contract serves selection, diagrams, and transfers."""

import pytest
from fileroute.descriptor import load, select, walk
from fileroute.diagram import build_graph
from fileroute.models import Catalog, Resource
from fileroute.transfer import plan_push


def test_composition_and_inheritance():
    catalog = Catalog(
        base_path="build/resources",
        catalogs=[
            Catalog(
                name="schemas",
                base_path="schemas/surveys",
                catalogs=[
                    Catalog(
                        name="group",
                        resources=[Resource(name="survey", path="1001/schema.json")],
                    )
                ],
            )
        ],
    )
    selected = select(catalog, "survey")
    assert selected.effective_path == "build/resources/schemas/surveys/1001/schema.json"
    assert selected.model.path == "1001/schema.json"
    assert selected.as_dict()["effectivePath"] == selected.effective_path
    assert (
        next(
            (node for node in build_graph(catalog).nodes if node.kind == "resource")
        ).path
        == selected.effective_path
    )


def test_link_file_location_is_independent_of_artifact_base(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "catalog.yaml").write_text(
        "basePath: build/resources\ncatalogs:\n  - name: schemas\n    descriptor: nested/child.yaml\n"
    )
    (tmp_path / "nested/child.yaml").write_text(
        "basePath: schemas\nresources:\n  - name: survey\n    path: survey.json\n    sources:\n      - path: input.json\n"
    )
    selected = select(
        load(tmp_path / "catalog.yaml", resolve_references=True), "survey"
    )
    assert selected.effective_path == "build/resources/schemas/survey.json"
    assert selected.model.sources[0].path == "input.json"


@pytest.mark.parametrize(
    "path", ["../escape", "/absolute", "C:/drive", "s3://bucket", "a\\b"]
)
def test_unsafe_composed_paths_fail(path):
    with pytest.raises(ValueError, match="relative local artifact path"):
        list(walk(Catalog(base_path=path)))


def test_selected_push_keeps_nested_base(tmp_path):
    (tmp_path / "build/resources/schemas").mkdir(parents=True)
    (tmp_path / "build/resources/schemas/one.json").write_text("{}")
    descriptor = tmp_path / "catalog.yaml"
    descriptor.write_text(
        "basePath: build/resources\ntargets:\n  - path: https://example.sharepoint.com/sites/dev/Docs\ncatalogs:\n  - name: schemas\n    basePath: schemas\n    resources:\n      - name: one\n        path: one.json\n"
    )
    entries = plan_push(descriptor, root=tmp_path, selector="one")
    assert entries[0].local == tmp_path / "build/resources/schemas/one.json"
    assert entries[0].relative.as_posix() == "schemas/one.json"


def test_unbased_group_does_not_publish_working_directory(tmp_path):
    descriptor = tmp_path / "catalog.yaml"
    descriptor.write_text(
        "targets:\n  - path: https://example.sharepoint.com/sites/dev/Docs\n"
    )
    with pytest.raises(ValueError, match="directory missing"):
        plan_push(descriptor, root=tmp_path)
