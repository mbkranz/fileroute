"""Named descriptors and unresolved resource templates across public boundaries."""

import json

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.descriptor import find, load, save, select, walk
from fileroute.models import Catalog, Location, Resource
from fileroute.transfer import plan_pull, plan_push


REMOTE = "https://tenant.sharepoint.com/sites/dev/Docs"


def test_schema_and_runtime_agree_on_child_names_and_locator_exclusivity():
    schema = Catalog.model_json_schema()
    good = {"resources": [{"name": "one", "pathTemplate": "{id}.csv"}]}
    Catalog.model_validate(good)
    Draft202012Validator(schema).validate(good)
    for bad in (
        {"catalogs": [{"basePath": "folder"}]},
        {"catalogs": [{"name": None}]},
        {"resources": [{"path": "x"}]},
        {"resources": [{"name": "x", "path": "x", "pathTemplate": "{id}"}]},
        {"resources": [{"name": "x"}]},
        {"catalogs": [{"name": "x", "descriptor": "other.yaml", "basePath": "x"}]},
        {"resourcePathTemplate": "{id}"},
    ):
        with pytest.raises(ValueError):
            Catalog.model_validate(bad)
        assert list(Draft202012Validator(schema).iter_errors(bad)), bad
    for names in (
        {"resources": [{"name": "one", "path": "a"}, {"name": "ONE", "path": "b"}]},
        {"resources": [{"name": "one", "path": "a"}], "catalogs": [{"name": "One"}]},
        {"catalogs": [{"name": "one"}, {"name": "ONE"}]},
    ):
        with pytest.raises(ValueError, match="Duplicate"):
            Catalog.model_validate(names)


def test_selection_reorders_by_name_and_preserves_numeric_addresses():
    catalog = Catalog(
        catalogs=[
            Catalog(
                name="group",
                basePath="build",
                resources=[
                    Resource(name="fixed", path="a.csv"),
                    Resource(name="variable", pathTemplate="{id}.csv"),
                ],
            )
        ]
    )
    template = select(catalog, "group.variable")
    assert template.effective_path is None
    assert template.effective_path_template == "build/{id}.csv"
    assert template.as_dict()["effectivePathTemplate"] == "build/{id}.csv"
    assert template.entry.json_path == "$.catalogs[0].resources[1]"
    assert find(catalog, "/catalogs/0/resources/1") is template.model
    catalog.catalogs[0].resources.reverse()
    assert select(catalog, "group.variable").model is template.model
    assert (
        select(catalog, "group.variable").entry.json_path
        == "$.catalogs[0].resources[0]"
    )
    assert select(catalog, "group.fixed").effective_path == "build/a.csv"


def test_link_registration_keeps_physical_root_name(tmp_path):
    child = tmp_path / "child.yaml"
    child.write_text("name: physical\nresources:\n  - name: item\n    path: item.csv\n")
    root = tmp_path / "root.yaml"
    root.write_text(
        "catalogs:\n  - name: public\n    descriptor: child.yaml\n  - name: alias\n    descriptor: child.yaml\n"
    )
    loaded = load(root, resolve_references=True)
    assert [row.name_path for row in walk(loaded)] == [
        "public",
        "public.item",
        "alias",
        "alias.item",
    ]
    assert select(loaded, "public.item").origin_pointer == "/resources/0"
    assert select(loaded, "alias.item").model is not select(loaded, "public.item").model
    assert load(child).name == "physical"
    assert child.read_text().startswith("name: physical")


def test_template_planning_is_atomic_and_scoped(tmp_path, monkeypatch):
    (tmp_path / "fixed.csv").write_text("data")
    catalog = tmp_path / "catalog.yaml"
    save(
        Catalog(
            targets=[Location(path=REMOTE)],
            resources=[
                Resource(name="fixed", path="fixed.csv"),
                Resource(
                    name="variable",
                    pathTemplate="{id}.csv",
                    sources=[Location(path="s3://bucket/x")],
                ),
            ],
        ),
        catalog,
    )
    monkeypatch.setattr(
        "fileroute.clients.sharepoint.SharepointClient.build_default",
        lambda: pytest.fail("authenticated"),
    )
    assert len(plan_push(catalog, root=tmp_path, selector="fixed")) == 1
    for direction in (plan_push, plan_pull):
        with pytest.raises(ValueError, match="variable uses pathTemplate"):
            direction(catalog, root=tmp_path, selector="variable")
    with pytest.raises(ValueError, match="variable uses pathTemplate"):
        plan_push(catalog, root=tmp_path)
    with pytest.raises(ValueError, match="variable uses pathTemplate"):
        plan_pull(catalog, root=tmp_path)
    assert (tmp_path / "fixed.csv").read_text() == "data"
    excluded = load(catalog)
    excluded.resources[1].targets = []
    excluded.resources[1].sources = []
    save(excluded, catalog)
    assert len(plan_push(catalog, root=tmp_path)) == 1


def test_cli_add_and_switch_locator_without_partial_write(tmp_path):
    path = tmp_path / "catalog.yaml"
    save(Catalog(), path)
    runner = CliRunner()
    added = runner.invoke(
        app,
        ["add", "schema", "--descriptor", str(path), "--path-template", "{id}.json"],
    )
    assert added.exit_code == 0, added.output
    assert load(path).resources[0].path_template == "{id}.json"
    before = path.read_bytes()
    both = runner.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(path),
            "--select",
            "schema",
            "--path",
            "real.json",
            "--path-template",
            "{id}.json",
        ],
    )
    assert both.exit_code != 0
    assert path.read_bytes() == before
    concrete = runner.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(path),
            "--select",
            "schema",
            "--path",
            "real.json",
        ],
    )
    assert concrete.exit_code == 0, concrete.output
    assert load(path).resources[0].path == "real.json"
    assert load(path).resources[0].path_template is None
    template = runner.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(path),
            "--select",
            "schema",
            "--path-template",
            "{id}.csv",
        ],
    )
    assert template.exit_code == 0, template.output
    assert load(path).resources[0].path is None
    listing = runner.invoke(app, ["list", str(path), "--format", "json"])
    assert listing.exit_code == 0, listing.output
    assert (
        json.loads(listing.output)["entities"][0]["effectivePathTemplate"] == "{id}.csv"
    )
