"""Editor schema agrees with structural runtime validation."""

import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from fileroute.models import Catalog
from fileroute.descriptor import load, save
from fileroute.commands.config import resolve_descriptor_path, set_active_descriptor


@pytest.mark.parametrize(
    "value",
    [
        {
            "catalogs": [
                {
                    "name": "surveys",
                    "basePath": "data",
                    "resources": [{"name": "data", "pathTemplate": "{id}.csv"}],
                }
            ]
        },
        {"resources": [{"name": "guide", "path": "guide.txt", "custom": True}]},
        {"catalogs": [{"name": "linked", "descriptor": "child.yaml"}]},
    ],
)
def test_valid_schema(value):
    Catalog.model_validate(value)
    Draft202012Validator(Catalog.model_json_schema(by_alias=True)).validate(value)


@pytest.mark.parametrize(
    "value",
    [
        {"$schema": "fileroute-catalog"},
        {"resources": {}},
        {"resources": None},
        {"catalogs": [{"name": "bad", "descriptor": "x.yaml", "path": "out"}]},
        {"catalogs": [{"basePath": "missing-name"}]},
        {"resources": [{"name": "bad.key", "path": "x"}]},
        {
            "resources": [
                {
                    "name": "x",
                    "path": "x",
                    "sources": [{"path": "x", "descriptor": "y"}],
                }
            ]
        },
        {"pathTemplate": "old spelling"},
        {"resourcePathTemplate": ""},
        {"resourcePathTemplate": "   "},
        {"resourcePathTemplate": 2},
        {"catalogType": "template"},
        {"resources": [{"name": "x", "path": "x", "pathTemplate": "{id}.csv"}]},
        {"resources": [{"name": "x"}]},
    ],
)
def test_invalid_schema(value):
    with pytest.raises(ValueError):
        Catalog.model_validate(value)
    assert list(
        Draft202012Validator(Catalog.model_json_schema(by_alias=True)).iter_errors(
            value
        )
    )


def test_template_roundtrip(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(
        'basePath: data\nresources:\n  - name: report\n    pathTemplate: "{id}.csv"\n'
    )
    model = load(path)
    save(model, path)
    model = load(path)
    assert model.resources[0].path_template == "{id}.csv"
    assert model.model_dump(by_alias=True)["resources"][0]["pathTemplate"] == "{id}.csv"


def test_discovery_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resources").mkdir()
    legacy = Path("resources/descriptor.yaml")
    legacy.write_text("{}")
    assert resolve_descriptor_path() == legacy
    for suffix in ("json", "yml", "yaml"):
        path = Path(f"catalog.{suffix}")
        path.write_text("{}")
        assert resolve_descriptor_path() == path
    assert resolve_descriptor_path("explicit.yaml") == Path("explicit.yaml")
    set_active_descriptor(legacy)
    legacy.unlink()
    assert (
        resolve_descriptor_path() == legacy.resolve()
    )  # stale selection never falls back


def test_export_version_and_determinism(tmp_path):
    path = Path(__file__).parents[1] / "scripts/export_schema.py"
    spec = importlib.util.spec_from_file_location("export_schema", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="1.2.3"\n')
    destination = module.export(tmp_path)
    first = destination.read_bytes()
    assert module.export(tmp_path).read_bytes() == first
    schema = json.loads(first)
    assert "/v1.2.3/" in schema["$id"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate({
        "resources": [{"name": "report", "pathTemplate": "{id}.json"}],
        "catalogs": [{"name": "archive", "descriptor": "archive.yaml"}],
    })
    assert list(validator.iter_errors({"catalogs": [{"basePath": "unnamed"}]}))
    assert list(
        validator.iter_errors({
            "resources": [
                {"name": "report", "path": "report.json", "pathTemplate": "{id}.json"}
            ]
        })
    )


def test_template_does_not_expand_transfers(tmp_path):
    from fileroute.transfer import plan_push

    (tmp_path / "out").mkdir()
    (tmp_path / "out/actual.csv").write_text("data")
    descriptor = tmp_path / "catalog.yaml"
    descriptor.write_text("""catalogs:
  - name: output
    basePath: out
    resources:
      - name: report
        pathTemplate: "{missing}.csv"
    targets:
      - path: https://example.sharepoint.com/sites/dev/Shared%20Documents/output
""")
    with pytest.raises(ValueError, match="output.report uses pathTemplate"):
        plan_push(descriptor, root=tmp_path, selector="output")


@pytest.mark.parametrize("existing", [None, "same", "different"])
def test_release_schema_upload_is_idempotent(tmp_path, monkeypatch, existing):
    """Run the actual workflow shell with a local GitHub CLI stand-in."""
    import os
    import subprocess
    from ruamel.yaml import YAML

    workflow = YAML(typ="safe").load(
        (
            Path(__file__).parents[1] / ".github/workflows/publish-to-pypi.yaml"
        ).read_text()
    )
    command = workflow["jobs"]["release"]["steps"][-1]["run"]
    schema = tmp_path / "dist/schemas/fileroute-catalog.schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text("same")
    executable = tmp_path / "gh"
    executable.write_text("""#!/bin/sh
case "$2" in
view) if [ "$4" = "--json" ] && [ -n "$EXISTING" ]; then echo fileroute-catalog.schema.json; fi;;
download) printf %s "$EXISTING" > existing/fileroute-catalog.schema.json;;
upload) echo upload >> events;;
esac
""")
    executable.chmod(0o755)
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", command],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "TAG": "v1.2.3",
            "EXISTING": existing or "",
        },
        capture_output=True,
    )
    assert (result.returncode == 0) == (existing != "different")
    assert (tmp_path / "events").exists() == (existing is None)
