"""Authored descriptors keep comments and styles across CLI edits."""

from pathlib import Path

from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.descriptor import load, save


AUTHORED = """# Catalog introduction
title: "Original" # title comment
custom: {owner: 'team'}
catalogs:
  - $ref: 'child.yaml' # reference comment
resources:
  - name: 'guide'
    path: "guide.docx"
    sources:
      - path: "https://drive.google.com/file/d/123"
        extension: {code: 'abc'}
"""


def _assert_authored(text: str) -> None:
    assert text.startswith("# Catalog introduction\n")
    assert 'title: "' in text and "# title comment" in text
    assert "custom: {owner: 'team'}" in text
    assert "$ref: 'child.yaml'" in text and "# reference comment" in text
    assert "name: 'guide'" in text
    assert text.index("custom:") < text.index("catalogs:") < text.index("resources:")


def test_save_preserves_authored_yaml(tmp_path: Path) -> None:
    path = tmp_path / "descriptor.yaml"
    path.write_text(AUTHORED)
    catalog = load(path)
    catalog.title = "Revised"
    save(catalog, path)
    content = path.read_text()
    _assert_authored(content)
    assert 'title: "Revised"' in content and "# title comment" in content
    assert load(path).resources[0].sources[0].path.endswith("/123")


def test_resolve_update_and_add_keep_authored_yaml(tmp_path: Path) -> None:
    path = tmp_path / "descriptor.yaml"
    path.write_text(AUTHORED)
    runner = CliRunner()
    for args in (
        ["resolve", str(path), "--write"],
        ["update", "--descriptor", str(path), "--title", "New title"],
        ["add", "second", "--descriptor", str(path), "--path", "second.csv"],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        _assert_authored(path.read_text())
    assert 'title: "New title"' in path.read_text()
    assert load(path).resources[0].sources[0].service_type.value == "GoogleDrive"
    assert load(path).resources[1].path == "second.csv"


def test_save_preserves_unchanged_yaml_anchor(tmp_path: Path) -> None:
    path = tmp_path / "descriptor.yaml"
    path.write_text(
        "sources: &inputs\n  - path: 'guide.qmd'\n"
        "resources:\n  - path: guide.docx\n    sources: *inputs\n"
    )
    save(load(path), path)
    content = path.read_text()
    assert "&inputs" in content and "*inputs" in content
    assert load(path).resources[0].sources[0].path == "guide.qmd"
