from pathlib import Path

import pytest
from typer.testing import CliRunner

from sharedrive.cli import app
from sharedrive.descriptor import load
from sharedrive.migration import migrate_descriptor
from sharedrive.models import ServiceType

RUNNER = CliRunner()


def test_migrate_legacy_directory_for_push(tmp_path: Path) -> None:
    source = tmp_path / "old.yaml"
    source.write_text(
        """
$schema: data-package-catalog
catalogs:
  - name: documentation
    _cache: docs/_output
    accessURL: https://tenant.sharepoint.com/sites/dev/Shared%20Documents/Docs
    serviceType: SharePoint
    entityType: Directory
""".strip()
    )

    catalog = migrate_descriptor(source, direction="push")
    documentation = catalog.catalogs[0]

    assert documentation.path == "docs/_output"
    assert documentation.sources == []
    assert documentation.targets is not None
    assert documentation.targets[0].path.endswith("/Shared%20Documents/Docs")
    assert documentation.targets[0].service_type is ServiceType.SHAREPOINT
    assert documentation.entity_type == "Directory"


def test_migrate_legacy_resource_for_pull(tmp_path: Path) -> None:
    source = tmp_path / "old.yaml"
    source.write_text(
        """
resources:
  - name: export
    path: s3://bucket/export.csv
    _cache: data/export.csv
    serviceType: S3
""".strip()
    )

    export = migrate_descriptor(source, direction="pull").resources[0]

    assert export.path == "data/export.csv"
    assert export.targets is None
    assert export.sources[0].path == "s3://bucket/export.csv"
    assert export.sources[0].service_type is ServiceType.S3


def test_migrate_cli_writes_new_file_and_preserves_input(tmp_path: Path) -> None:
    source = tmp_path / "old.yaml"
    output = tmp_path / "new.yaml"
    source.write_text(
        """
catalogs:
  - name: documentation
    _cache: docs/_output
    accessURL: https://tenant.sharepoint.com/sites/dev/Docs
    serviceType: SharePoint
""".strip()
    )
    before = source.read_bytes()

    result = RUNNER.invoke(
        app,
        ["migrate", str(source), str(output), "--direction", "push"],
        prog_name="sharedrive",
    )

    assert result.exit_code == 0, result.output
    assert source.read_bytes() == before
    migrated = load(output)
    assert migrated.catalogs[0].path == "docs/_output"
    assert migrated.catalogs[0].targets[0].service_type is ServiceType.SHAREPOINT


def test_migrate_cli_refuses_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "old.yaml"
    output = tmp_path / "new.yaml"
    source.write_text("resources: []\n")
    output.write_text("existing\n")

    result = RUNNER.invoke(app, ["migrate", str(source), str(output)])

    assert result.exit_code != 0
    assert "does not overwrite files" in result.output
    assert output.read_text() == "existing\n"


def test_normal_load_still_rejects_legacy_fields(tmp_path: Path) -> None:
    source = tmp_path / "old.yaml"
    source.write_text(
        "catalogs:\n  - name: docs\n    _cache: docs/_output\n"
        "    accessURL: https://tenant.sharepoint.com/sites/dev/Docs\n"
    )

    with pytest.raises(ValueError, match="Unsupported fields"):
        load(source)
