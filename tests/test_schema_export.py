"""Generated editor/release schemas remain deterministic and version synchronized."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest


@pytest.fixture
def exporter():
    script = Path(__file__).parents[1] / "scripts/export_schema.py"
    spec = importlib.util.spec_from_file_location("export_schema", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project(root: Path, version: str = "1.2.3") -> None:
    (root / "pyproject.toml").write_text(f'[project]\nversion="{version}"\n')


def test_exported_bundle_contains_both_complete_model_schemas(tmp_path, exporter):
    _project(tmp_path)
    outputs = exporter.export_all(tmp_path)
    assert {path.name for path in outputs} == {
        "fileroute-catalog.schema.json",
        "fileroute-auth-profiles.schema.json",
    }
    for destination in outputs:
        schema = json.loads(destination.read_bytes())
        Draft202012Validator.check_schema(schema)
        assert schema["$id"].endswith(f"/v1.2.3/{destination.name}")
    profiles = json.loads(outputs[1].read_bytes())
    assert "profiles" in profiles["properties"]
    assert "AuthProfile" in profiles["$defs"]
    assert exporter.export(tmp_path) == outputs[0]


def test_source_and_release_bundles_are_byte_identical_and_deterministic(
    tmp_path, exporter
):
    _project(tmp_path)
    sources = exporter.export_all(tmp_path, source=True)
    release = exporter.export_all(tmp_path)
    before = {path.name: path.read_bytes() for path in sources}
    assert {path.name: path.read_bytes() for path in release} == before
    exporter.export_all(tmp_path, source=True)
    assert {path.name: path.read_bytes() for path in sources} == before
    assert exporter.export_all(tmp_path, check=True) == sources


def test_check_missing_sources_does_not_create_directory(tmp_path, exporter):
    _project(tmp_path)
    with pytest.raises(ValueError, match="poe schema-update"):
        exporter.export_all(tmp_path, check=True)
    assert not (tmp_path / "schemas").exists()


@pytest.mark.parametrize("failure", ["stale", "missing", "version"])
def test_check_rejects_stale_or_missing_schemas_without_modifying_files(
    tmp_path, exporter, failure
):
    _project(tmp_path)
    outputs = exporter.export_all(tmp_path, source=True)
    if failure == "stale":
        outputs[1].write_text('{"stale": true}\n')
    elif failure == "missing":
        outputs[1].unlink()
    else:
        _project(tmp_path, "1.2.4")
    before = {path.name: path.read_bytes() for path in (tmp_path / "schemas").iterdir()}
    with pytest.raises(ValueError, match="Missing or stale"):
        exporter.export_all(tmp_path, check=True)
    assert {
        path.name: path.read_bytes() for path in (tmp_path / "schemas").iterdir()
    } == before
    if failure == "version":
        refreshed = exporter.export_all(tmp_path, source=True)
        assert all(
            "/v1.2.4/" in json.loads(path.read_bytes())["$id"] for path in refreshed
        )


def test_exporter_cli_check_reports_failure_without_writing(
    tmp_path, exporter, monkeypatch, capsys
):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as error:
        exporter.main(["--check"])
    assert error.value.code == 1
    assert "poe schema-update" in capsys.readouterr().err
    assert not (tmp_path / "schemas").exists()
    assert exporter.main(["--source"]) == 0
    assert exporter.main(["--check"]) == 0
