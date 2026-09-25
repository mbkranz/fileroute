from __future__ import annotations

from pathlib import Path

import yaml_support as yaml
from typer.testing import CliRunner

from fileroute.cli import app

RUNNER = CliRunner()


def _write_descriptor(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "$schema": "fileroute-catalog",
                "title": "Original title",
                "description": "Original description",
                "resources": {
                    "spec-workbook": {
                        "path": "background/specs/spec-workbook.xlsx",
                        "sources": [
                            {
                                "path": "https://tenant.sharepoint.com/sites/Test/Shared%20Documents/spec.xlsx",
                                "serviceType": "SharePoint",
                                "entityType": "File",
                            }
                        ],
                    },
                    "other-resource": {
                        "path": "background/specs/other-resource.xlsx",
                        "sources": [
                            {
                                "path": "https://tenant.sharepoint.com/sites/Test/Shared%20Documents/other-resource.xlsx",
                                "serviceType": "SharePoint",
                                "entityType": "File",
                            }
                        ],
                    },
                },
                "catalogs": {},
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
        prog_name="fileroute",
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
        prog_name="fileroute",
    )

    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert document["resources"]["spec-workbook"]["title"] == "Updated title"
    assert (
        document["resources"]["spec-workbook"]["description"] == "Updated description"
    )
    assert "title" not in document["resources"]["other-resource"]
    assert document["$schema"] == "fileroute-catalog"
    assert (
        document["resources"]["spec-workbook"]["path"]
        == "background/specs/spec-workbook.xlsx"
    )


def test_update_nested_entity_by_dot_path(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(descriptor)
    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    nested = document["resources"].pop("spec-workbook")
    document["catalogs"] = {"archive": {"resources": {"spec-workbook": nested}}}
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
        prog_name="fileroute",
    )

    assert result.exit_code == 0
    updated = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    assert (
        updated["catalogs"]["archive"]["resources"]["spec-workbook"]["title"]
        == "Nested"
    )
    assert "title" not in updated["resources"]["other-resource"]


def test_update_resource_uses_active_descriptor(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    descriptor = tmp_path / "resources" / "descriptor.yaml"
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    _write_descriptor(descriptor)

    activate_result = RUNNER.invoke(
        app, ["activate", "resources/descriptor.yaml"], prog_name="fileroute"
    )
    assert activate_result.exit_code == 0

    result = RUNNER.invoke(
        app,
        ["update", "--name", "spec-workbook", "--title", "Active title"],
        prog_name="fileroute",
    )

    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert document["resources"]["spec-workbook"]["title"] == "Active title"


def test_update_descriptor_override_with_resource(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    active = tmp_path / "resources" / "descriptor.yaml"
    active.parent.mkdir(parents=True, exist_ok=True)
    _write_descriptor(active)
    override = tmp_path / "override.yaml"
    _write_descriptor(override)

    activate_result = RUNNER.invoke(
        app, ["activate", "resources/descriptor.yaml"], prog_name="fileroute"
    )
    assert activate_result.exit_code == 0

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
        prog_name="fileroute",
    )

    active_doc = yaml.safe_load(active.read_text(encoding="utf-8"))
    override_doc = yaml.safe_load(override.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert "title" not in active_doc["resources"]["spec-workbook"]
    assert override_doc["resources"]["spec-workbook"]["title"] == "Override title"


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
        prog_name="fileroute",
    )

    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert (
        document["resources"]["spec-workbook"]["sources"][0]["serviceType"]
        == "SharePoint"
    )


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
        prog_name="fileroute",
    )

    assert result.exit_code == 0
    assert "Would update" in result.stdout
    assert descriptor.read_text(encoding="utf-8") == before


def test_update_requires_fields(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_descriptor(descriptor)

    result = RUNNER.invoke(
        app, ["update", "--descriptor", str(descriptor)], prog_name="fileroute"
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
        prog_name="fileroute",
    )

    assert result.exit_code != 0
    assert "was not found" in result.output
