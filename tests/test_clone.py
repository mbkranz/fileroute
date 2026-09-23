from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from sharedrive.cli import app

RUNNER = CliRunner()


def _write_descriptor(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "$schema": "data-package-catalog",
                "resources": [
                    {
                        "name": "source-export",
                        "path": "downloads/source.csv",
                        "syncTarget": "path",
                        "sources": [
                            {
                                "path": "s3://bucket/source.csv",
                                "serviceType": "S3",
                                "entityType": "File",
                            }
                        ],
                    }
                ],
                "packages": [],
                "catalogs": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_clone_descriptor_writes_target_yaml(tmp_path: Path) -> None:
    source_descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(source_descriptor)
    target_descriptor = tmp_path / "descriptor-copy.yaml"

    result = RUNNER.invoke(
        app,
        [
            "clone",
            "descriptor",
            str(target_descriptor),
            "--descriptor",
            str(source_descriptor),
        ],
        prog_name="sharedrive",
    )

    assert result.exit_code == 0
    cloned = yaml.safe_load(target_descriptor.read_text(encoding="utf-8"))
    source = yaml.safe_load(source_descriptor.read_text(encoding="utf-8"))
    assert cloned["$schema"] == source["$schema"]
    assert cloned["resources"] == source["resources"]


def test_clone_descriptor_uses_target_suffix_format(tmp_path: Path) -> None:
    source_descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(source_descriptor)
    target_descriptor = tmp_path / "descriptor-copy.json"

    result = RUNNER.invoke(
        app,
        [
            "clone",
            "descriptor",
            str(target_descriptor),
            "--descriptor",
            str(source_descriptor),
        ],
        prog_name="sharedrive",
    )

    assert result.exit_code == 0
    payload = json.loads(target_descriptor.read_text(encoding="utf-8"))
    assert payload["$schema"] == "data-package-catalog"
    assert payload["resources"][0]["name"] == "source-export"
    assert payload["resources"][0]["syncTarget"] == "path"


def test_clone_descriptor_dry_run_does_not_write(tmp_path: Path) -> None:
    source_descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(source_descriptor)
    target_descriptor = tmp_path / "descriptor-copy.yaml"

    result = RUNNER.invoke(
        app,
        [
            "clone",
            "descriptor",
            str(target_descriptor),
            "--descriptor",
            str(source_descriptor),
            "--dry-run",
        ],
        prog_name="sharedrive",
    )

    assert result.exit_code == 0
    assert "Would clone descriptor" in result.stdout
    assert not target_descriptor.exists()


def test_clone_descriptor_rejects_existing_target_without_force(tmp_path: Path) -> None:
    source_descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(source_descriptor)
    target_descriptor = tmp_path / "descriptor-copy.yaml"
    target_descriptor.write_text(
        "$schema: data-package-catalog\nresources: []\npackages: []\ncatalogs: []\n",
        encoding="utf-8",
    )

    result = RUNNER.invoke(
        app,
        [
            "clone",
            "descriptor",
            str(target_descriptor),
            "--descriptor",
            str(source_descriptor),
        ],
        prog_name="sharedrive",
    )

    assert result.exit_code != 0
    assert "Refusing to overwrite existing descriptor" in result.output


def test_clone_descriptor_force_overwrites_existing_target(tmp_path: Path) -> None:
    source_descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(source_descriptor)
    target_descriptor = tmp_path / "descriptor-copy.yaml"
    target_descriptor.write_text(
        "$schema: data-package-catalog\nresources: []\npackages: []\ncatalogs: []\n",
        encoding="utf-8",
    )

    result = RUNNER.invoke(
        app,
        [
            "clone",
            "descriptor",
            str(target_descriptor),
            "--descriptor",
            str(source_descriptor),
            "--force",
        ],
        prog_name="sharedrive",
    )

    assert result.exit_code == 0
    assert (
        yaml.safe_load(target_descriptor.read_text(encoding="utf-8"))["resources"][0][
            "name"
        ]
        == "source-export"
    )
