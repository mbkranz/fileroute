"""Exact selectors for nested descriptor edits and location metadata."""

import json

import pytest
from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.descriptor import find, load, save, walk
from fileroute.models import Catalog, CatalogReference, Location, Resource


RUNNER = CliRunner()


def test_jsonpath_and_pointer_select_named_unnamed_and_location():
    document = Catalog(
        catalogs=[
            Catalog(
                name="a.b",
                resources=[
                    Resource(
                        path="one.csv", sources=[Location(path="s3://bucket/one.csv")]
                    ),
                    Resource(name="file", path="two.csv"),
                ],
            )
        ]
    )
    assert find(document, "$.catalogs[0].resources[0]").path == "one.csv"
    assert find(document, "catalogs[0].resources[0]").path == "one.csv"
    assert find(document, ".catalogs[0].resources[0]").path == "one.csv"
    assert find(document, '["catalogs"][0]["resources"][1]').path == "two.csv"
    assert find(document, '$["catalogs"][0]["resources"][1]').path == "two.csv"
    assert (
        find(document, "/catalogs/0/resources/0/sources/0").path
        == "s3://bucket/one.csv"
    )
    assert find(document, "$.catalogs[0].resources[0].sources[0]").service_type is None
    assert (
        find(document, "catalogs[0].resources[0].sources[0]").path
        == "s3://bucket/one.csv"
    )
    assert find(document, "$") is document
    assert list(walk(document))[1].json_path == "$.catalogs[0].resources[0]"
    with pytest.raises(ValueError, match="no wildcards or filters"):
        find(document, "$.catalogs[*]")
    with pytest.raises(ValueError, match="no wildcards or filters"):
        find(document, "catalogs[*]")
    with pytest.raises(ValueError, match="final step"):
        find(document, "$.resources[0].sources[0].targets[0]")


@pytest.mark.parametrize("suffix", ["yaml", "json"])
def test_cli_selectors_edit_and_inspect_nested_location(tmp_path, suffix):
    path = tmp_path / f"catalog.{suffix}"
    save(
        Catalog(
            catalogs=[
                Catalog(
                    name="docs",
                    resources=[
                        Resource(
                            name="report",
                            path="report.csv",
                            targets=[
                                Location(
                                    path="Reports/report.csv",
                                    serviceType="SharePoint",
                                    site="PPSC",
                                    drive="Documents",
                                )
                            ],
                        )
                    ],
                )
            ]
        ),
        path,
    )
    selector = "$.catalogs[0].resources[0].targets[0]"
    result = RUNNER.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(path),
            "--select",
            selector.removeprefix("$"),
            "--drive-id",
            "drive-123",
            "--siteId",
            "site-456",
        ],
    )
    assert result.exit_code == 0, result.output
    target = find(load(path), selector.removeprefix("$."))
    assert isinstance(target, Location)
    assert (target.drive_id, target.site_id) == ("drive-123", "site-456")
    assert find(load(path), "$.catalogs[0].resources[0]").path == "report.csv"

    listing = RUNNER.invoke(app, ["list", str(path), "--format", "json"])
    assert listing.exit_code == 0, listing.output
    data = json.loads(listing.output)
    assert selector in [entry["jsonPath"] for entry in data["locations"]]
    selected = RUNNER.invoke(
        app,
        ["list", str(path), "--select", selector.removeprefix("$."), "--format", "json"],
    )
    selected_data = json.loads(selected.output)
    assert selected_data["entities"] == []
    assert [row["jsonPath"] for row in selected_data["locations"]] == [selector]


def test_add_under_nested_catalog_and_reject_reference_parent(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(
        "catalogs:\n  - name: docs\n  - name: external\n    $ref: child.yaml\n"
    )
    result = RUNNER.invoke(
        app,
        [
            "add",
            "guide",
            "--descriptor",
            str(path),
            "--parent",
            "catalogs[0]",
            "--path",
            "guide.docx",
        ],
    )
    assert result.exit_code == 0, result.output
    assert find(load(path), "$.catalogs[0].resources[0]").path == "guide.docx"
    assert isinstance(load(path).catalogs[1], CatalogReference)

    result = RUNNER.invoke(
        app,
        [
            "add",
            "other",
            "--descriptor",
            str(path),
            "--parent",
            "/catalogs/1",
            "--path",
            "other.docx",
        ],
    )
    assert result.exit_code != 0
    assert "must select a catalog" in result.output
    assert len(load(path).catalogs[0].resources) == 1


def test_location_fields_require_location_selector(tmp_path):
    path = tmp_path / "catalog.json"
    save(Catalog(resources=[Resource(name="report", path="report.csv")]), path)
    result = RUNNER.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(path),
            "--name",
            "report",
            "--drive-id",
            "wrong",
        ],
    )
    assert result.exit_code != 0
    assert "requires a source or target location" in result.output
    assert "drive_id" not in load(path).resources[0].model_extra


def test_reference_selectors_are_read_only_in_parent(tmp_path):
    child = tmp_path / "child.yaml"
    child.write_text("resources:\n  - name: guide\n    path: guide.docx\n")
    parent = tmp_path / "parent.yaml"
    parent.write_text("catalogs:\n  - name: external\n    $ref: child.yaml\n")
    selector = "$.catalogs[0].resources[0]"
    listing = RUNNER.invoke(app, ["list", str(parent), "--format", "json"])
    assert listing.exit_code == 0, listing.output
    assert selector in [row["jsonPath"] for row in json.loads(listing.output)["selectors"]]

    before = child.read_bytes()
    result = RUNNER.invoke(app, [
        "update", "--descriptor", str(parent), "--select", selector,
        "--title", "Updated",
    ])
    assert result.exit_code != 0
    assert "inside a $ref" in result.output
    assert child.read_bytes() == before
    assert "$ref: child.yaml" in parent.read_text()
