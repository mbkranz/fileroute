"""Descriptors describe artifacts; local operations are called explicitly."""

import subprocess
import sys

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest
from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.commands.descriptor import _add_resource_to_descriptor
from fileroute.descriptor import load, save
from fileroute.discovery import (
    DiscoveryRequest,
    ReconcileDiscoveryRequest,
    discover_report,
)
from fileroute.models import Catalog, Location, Resource


@pytest.mark.parametrize(
    "field", ["afterPull", "after_pull", "beforePush", "before_push"]
)
@pytest.mark.parametrize("payload", [None, {"steps": [{"type": "convert"}]}])
def test_hooks_fail_in_runtime_and_schema(field, payload):
    value = {"path": "s3://bucket/file", field: payload}
    with pytest.raises(ValidationError, match="explicit Python"):
        Location.model_validate(value)
    assert not Draft202012Validator(Location.model_json_schema()).is_valid(value)


@pytest.mark.parametrize("nested", [False, True])
def test_catalog_execution_declarations_are_not_silently_ignored(nested):
    value = {"pipelines": {}}
    if nested:
        value = {"catalogs": {"child": value}}
    with pytest.raises(ValidationError, match="explicit Python"):
        Catalog.model_validate(value)
    assert not Draft202012Validator(Catalog.model_json_schema()).is_valid(value)


def test_linked_execution_declaration_fails_without_modifying_descriptors(tmp_path):
    root, child = tmp_path / "catalog.yaml", tmp_path / "child.yaml"
    root.write_text("catalogs:\n  child:\n    descriptor: child.yaml\n")
    child.write_text("pipelines: {}\nresources:\n  result:\n    path: result.md\n")
    before = root.read_bytes(), child.read_bytes()
    with pytest.raises(ValueError, match="pipelines"):
        load(root, resolve_references=True)
    assert (root.read_bytes(), child.read_bytes()) == before


def test_unrelated_extensions_and_literal_arguments_roundtrip(tmp_path):
    literal = {
        "steps": ["$HOME", "${output}", "$(literal)"],
        "params": {"type": "ordinary metadata", "kwargs": {"args": "sequence"}},
    }
    catalog = Catalog(
        resources={
            "raw": Resource(path="input.docx", targets=[], **literal),
            "derived": Resource(path="output.md", format="md"),
        },
        sources=[Location(path="citation.txt", **literal)],
        targets=[Location(path="https://drive.google.com/drive/folders/folder")],
        **literal,
    )
    descriptor = tmp_path / "catalog.yaml"
    save(catalog, descriptor)
    restored = load(descriptor)
    assert restored.model_dump() == catalog.model_dump()
    assert restored.resources["raw"].targets == []
    assert restored.resources["derived"].targets is None


def test_removed_python_conversion_flags_fail_before_writes(tmp_path):
    descriptor = tmp_path / "absent.yaml"
    with pytest.raises(ValueError, match="conversion is not supported"):
        _add_resource_to_descriptor(
            descriptor,
            name="input",
            create_if_missing=True,
            path="input.docx",
            to="markdown",
        )
    with pytest.raises(TypeError, match="to_markdown"):
        discover_report(descriptor, to_markdown=True)
    with pytest.raises(ValidationError, match="to_markdown"):
        DiscoveryRequest(descriptor=descriptor, to_markdown=True)
    with pytest.raises(ValidationError, match="to_markdown"):
        ReconcileDiscoveryRequest(
            catalog=Catalog(),
            scans=[],
            descriptor=descriptor,
            root=tmp_path,
            to_markdown=True,
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", ["add", "discover"])
def test_removed_cli_conversion_flags_do_not_modify_the_catalog(
    tmp_path, monkeypatch, command
):
    monkeypatch.chdir(tmp_path)
    descriptor = tmp_path / "catalog.yaml"
    save(Catalog(resources={"raw": Resource(path="input.docx")}), descriptor)
    before = descriptor.read_bytes()
    args = (
        ["add", "derived", "--descriptor", str(descriptor), "--path", "output.md"]
        if command == "add"
        else ["discover", str(descriptor)]
    )
    result = CliRunner().invoke(app, [*args, "--to", "markdown"])
    assert result.exit_code != 0
    assert descriptor.read_bytes() == before
    assert list(tmp_path.iterdir()) == [descriptor]


@pytest.mark.parametrize("command", ["process", "steps"])
def test_descriptor_execution_commands_are_absent(command):
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code != 0 and "No such command" in result.output


def test_base_commands_need_no_conversion_libraries_or_pandoc(tmp_path):
    script = """
import importlib.abc
from pathlib import Path
import sys
import shutil

class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"poethepoet", "markitdown", "jinja2", "docx", "lxml"}:
            raise ImportError("retired conversion dependency blocked")
sys.meta_path.insert(0, BlockOptional())
from typer.testing import CliRunner
from fileroute.cli import app
from fileroute.descriptor import load
Path("catalog.yaml").write_text("resources: {}\\n")
assert not load("catalog.yaml").resources
runner = CliRunner()
for args in [["--help"], ["pull", "--help"], ["push", "--help"], ["list", "catalog.yaml"], ["resolve", "catalog.yaml"]]:
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
shutil.which = lambda name: None
Path("input.docx").write_bytes(b"original")
from fileroute.conversion import DocxToMarkdownRequest, docx_to_markdown
try:
    docx_to_markdown(DocxToMarkdownRequest(source=Path("input.docx"), output=Path("out.md")))
except ValueError as error:
    assert "Install Pandoc" in str(error)
else:
    raise AssertionError("Conversion requires Pandoc")
assert not Path("out.md").exists()
print("base import/CLI smoke passed")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "base import/CLI smoke passed" in result.stdout
