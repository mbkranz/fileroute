"""Exact addresses for keyed entities and ordered locations."""

import json
import pytest
from typer.testing import CliRunner
from fileroute.cli import app
from fileroute.descriptor import find, load, save, walk, resolve_selection
from fileroute.models import Catalog, Resource, Location


def document():
    return Catalog(
        catalogs=[
            Catalog(
                name="docs",
                resources=[
                    Resource(
                        name="report",
                        path="report.csv",
                        targets=[Location(path="s3://bucket/report.csv")],
                    )
                ],
            )
        ]
    )


@pytest.mark.parametrize(
    "address",
    [
        "docs.report",
        "report",
        "$.catalogs[0].resources[0]",
        "catalogs[0].resources[0]",
        ".catalogs[0].resources[0]",
        "$['catalogs'][0]['resources'][0]",
        "/catalogs/0/resources/0",
    ],
)
def test_exact_selector(address):
    assert find(document(), address).path == "report.csv"


@pytest.mark.parametrize(
    "address",
    [
        "$.catalogs[*]",
        "$.catalogs[-1]",
        "$.catalogs[0].resources[0].targets[0].sources[0]",
        "$.catalogs[0].resources[0].path",
    ],
)
def test_unsupported_queries(address):
    with pytest.raises(ValueError):
        find(document(), address)


def test_locations_and_cli_edits(tmp_path):
    path = tmp_path / "root.yaml"
    save(document(), path)
    address = "catalogs[0].resources[0].targets[0]"
    result = CliRunner().invoke(
        app,
        [
            "update",
            "--descriptor",
            str(path),
            "--select",
            address,
            "--bucket",
            "new-bucket",
        ],
    )
    assert result.exit_code == 0, result.output
    assert find(load(path), address).bucket == "new-bucket"
    assert list(walk(load(path)))[1].json_path == "$.catalogs[0].resources[0]"
    result = CliRunner().invoke(
        app, ["list", str(path), "--select", address, "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)["locations"]) == 1


def test_add_under_keyed_parent(tmp_path):
    path = tmp_path / "root.yaml"
    save(Catalog(catalogs=[Catalog(name="docs")]), path)
    result = CliRunner().invoke(
        app,
        [
            "add",
            "guide",
            "--descriptor",
            str(path),
            "--parent",
            "docs",
            "--path",
            "guide.docx",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        next(
            (
                _entry
                for _entry in next(
                    (_entry for _entry in load(path).catalogs if _entry.name == "docs")
                ).resources
                if _entry.name == "guide"
            )
        ).path
        == "guide.docx"
    )


def test_selected_resolution_writes_only_origin(tmp_path):
    child = tmp_path / "child.yaml"
    child.write_text(
        "resources:\n  - name: report\n    path: report.csv\n    targets:\n      - path: s3://bucket/report.csv\n"
    )
    root = tmp_path / "root.yaml"
    root.write_text("catalogs:\n  - name: external\n    descriptor: child.yaml\n")
    before = root.read_bytes()
    result = resolve_selection(root, "external.report", write=True)
    assert result.entry.origin_descriptor == child
    assert (
        next((_entry for _entry in load(child).resources if _entry.name == "report"))
        .targets[0]
        .bucket
        == "bucket"
    )
    assert root.read_bytes() == before
    edit = CliRunner().invoke(
        app,
        [
            "update",
            "--descriptor",
            str(root),
            "--select",
            "external.report",
            "--title",
            "new",
        ],
    )
    assert edit.exit_code != 0 and "descriptor link" in edit.output


def test_selected_resolution_keeps_inheritance_out_of_child(tmp_path):
    path = tmp_path / "root.yaml"
    save(
        Catalog(
            targets=[Location(path="s3://bucket/root/")],
            resources=[Resource(name="file", path="file.csv")],
        ),
        path,
    )
    result = resolve_selection(path, "file", write=True)
    assert result.effective_targets[0].bucket == "bucket"
    assert (
        next(
            (_entry for _entry in load(path).resources if _entry.name == "file")
        ).targets
        is None
    )


def test_selected_location_does_not_resolve_unrelated_targets(tmp_path, monkeypatch):
    path = tmp_path / "root.yaml"
    save(
        Catalog(
            resources=[
                Resource(
                    name="file",
                    path="x",
                    sources=[Location(path="local.qmd")],
                    targets=[Location(path="unresolved-remote-target")],
                )
            ]
        ),
        path,
    )
    monkeypatch.setattr(
        "fileroute.clients.get_provider",
        lambda _: pytest.fail("unrelated provider lookup"),
    )
    result = resolve_selection(path, "$.resources[0].sources[0]", online=True)
    assert result.model.path == "local.qmd"
    assert result.effective_targets == ()


def test_scoped_catalog_write_keeps_nested_links(tmp_path):
    child = tmp_path / "child.yaml"
    child.write_text("catalogs:\n  - name: nested\n    descriptor: leaf.yaml\n")
    leaf = tmp_path / "leaf.yaml"
    leaf.write_text(
        "resources:\n  - name: file\n    path: x\n    targets:\n      - path: s3://bucket/x\n"
    )
    root = tmp_path / "root.yaml"
    root.write_text("catalogs:\n  - name: external\n    descriptor: child.yaml\n")
    before = leaf.read_bytes()
    resolve_selection(root, "external", write=True)
    assert leaf.read_bytes() == before
    assert "descriptor: leaf.yaml" in child.read_text()


def test_registered_names_take_precedence_over_rootless_address():
    model = Catalog(
        catalogs=[
            Catalog(name="catalogs", resources=[Resource(name="foo", path="x")]),
            Catalog(name="foo", base_path="y"),
        ]
    )
    assert find(model, "catalogs.foo").path == "x"
    assert find(model, "$.catalogs[1]").base_path == "y"


def test_selected_online_write_reuses_verified_metadata(tmp_path, monkeypatch):
    from types import SimpleNamespace

    path = tmp_path / "root.yaml"
    save(
        Catalog(
            resources=[
                Resource(
                    name="file", path="x", targets=[Location(path="s3://bucket/x")]
                )
            ]
        ),
        path,
    )
    calls = []
    monkeypatch.setattr(
        "fileroute.clients.get_provider",
        lambda _: SimpleNamespace(build_default=lambda: object()),
    )
    monkeypatch.setattr(
        "fileroute.descriptor.resolve_online",
        lambda location, client: calls.append(location.path),
    )
    resolve_selection(path, "file", online=True, write=True)
    assert calls == ["s3://bucket/x"]
