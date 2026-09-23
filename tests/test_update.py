from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from sharedrive.cli import app

RUNNER = CliRunner()


def _write_descriptor(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "$schema": "data-package-catalog",
                "title": "Original title",
                "description": "Original description",
                "resources": [
                    {
                        "name": "spec-workbook",
                        "path": "background/specs/spec-workbook.xlsx",
                        "syncTarget": "path",
                        "sources": [
                            {
                                "path": "https://tenant.sharepoint.com/sites/Test/Shared%20Documents/spec.xlsx",
                                "serviceType": "SharePoint",
                                "entityType": "File",
                            }
                        ],
                    },
                    {
                        "name": "other-resource",
                        "path": "background/specs/other-resource.xlsx",
                        "syncTarget": "path",
                        "sources": [
                            {
                                "path": "https://tenant.sharepoint.com/sites/Test/Shared%20Documents/other-resource.xlsx",
                                "serviceType": "SharePoint",
                                "entityType": "File",
                            }
                        ],
                    },
                ],
                "packages": [],
                "catalogs": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_update_descriptor_root_properties(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(descriptor)

    result = RUNNER.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(descriptor),
            "--title",
            "Hello",
            "--description",
            "hello",
        ],
        prog_name="sharedrive",
    )

    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert document["title"] == "Hello"
    assert document["description"] == "hello"


def test_update_resource_properties_exact_match(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(descriptor)

    result = RUNNER.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(descriptor),
            "--name",
            "spec-workbook",
            "--title",
            "Updated title",
            "--description",
            "Updated description",
        ],
        prog_name="sharedrive",
    )

    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert document["resources"][0]["title"] == "Updated title"
    assert document["resources"][0]["description"] == "Updated description"
    assert "title" not in document["resources"][1]
    assert document["$schema"] == "data-package-catalog"
    assert document["resources"][0]["syncTarget"] == "path"


def test_update_nested_entity_by_dot_path(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(descriptor)
    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    nested = document["resources"].pop(0)
    document["packages"] = [{"name": "archive", "resources": [nested]}]
    descriptor.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    result = RUNNER.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(descriptor),
            "--name",
            "archive.spec-workbook",
            "--title",
            "Nested",
        ],
        prog_name="sharedrive",
    )

    assert result.exit_code == 0
    updated = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    assert updated["packages"][0]["resources"][0]["title"] == "Nested"
    assert "title" not in updated["resources"][0]


def test_update_resource_uses_checked_out_descriptor(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    descriptor = tmp_path / "resources" / "descriptor.yaml"
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    _write_descriptor(descriptor)

    checkout_result = RUNNER.invoke(
        app, ["checkout", "resources/descriptor.yaml"], prog_name="sharedrive"
    )
    assert checkout_result.exit_code == 0

    result = RUNNER.invoke(
        app,
        ["update", "--name", "spec-workbook", "--title", "Checked out title"],
        prog_name="sharedrive",
    )

    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert document["resources"][0]["title"] == "Checked out title"


def test_update_descriptor_override_with_resource(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    checked_out = tmp_path / "resources" / "descriptor.yaml"
    checked_out.parent.mkdir(parents=True, exist_ok=True)
    _write_descriptor(checked_out)
    override = tmp_path / "override.yaml"
    _write_descriptor(override)

    checkout_result = RUNNER.invoke(
        app, ["checkout", "resources/descriptor.yaml"], prog_name="sharedrive"
    )
    assert checkout_result.exit_code == 0

    result = RUNNER.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(override),
            "--name",
            "spec-workbook",
            "--title",
            "Override title",
        ],
        prog_name="sharedrive",
    )

    checked_out_doc = yaml.safe_load(checked_out.read_text(encoding="utf-8"))
    override_doc = yaml.safe_load(override.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert "title" not in checked_out_doc["resources"][0]
    assert override_doc["resources"][0]["title"] == "Override title"


def test_update_resource_normalizes_service_type(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(descriptor)

    result = RUNNER.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(descriptor),
            "--name",
            "spec-workbook",
            "--service-type",
            "sharepoint",
        ],
        prog_name="sharedrive",
    )

    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert document["resources"][0]["sources"][0]["serviceType"] == "SharePoint"


def test_update_dry_run_does_not_write(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(descriptor)
    before = descriptor.read_text(encoding="utf-8")

    result = RUNNER.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(descriptor),
            "--name",
            "spec-workbook",
            "--title",
            "Dry run title",
            "--dry-run",
        ],
        prog_name="sharedrive",
    )

    assert result.exit_code == 0
    assert "Would update" in result.stdout
    assert descriptor.read_text(encoding="utf-8") == before


def test_update_requires_fields(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(descriptor)

    result = RUNNER.invoke(
        app, ["update", "--descriptor", str(descriptor)], prog_name="sharedrive"
    )

    assert result.exit_code != 0
    assert "Provide one or more field values to update" in result.output


def test_update_missing_resource_errors(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(descriptor)

    result = RUNNER.invoke(
        app,
        [
            "update",
            "--descriptor",
            str(descriptor),
            "--name",
            "missing",
            "--title",
            "Hello",
        ],
        prog_name="sharedrive",
    )

    assert result.exit_code != 0
    assert "was not found" in result.output
