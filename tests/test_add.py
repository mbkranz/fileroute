from __future__ import annotations

from pathlib import Path

import pytest
import yaml_support as yaml

from sharedrive.commands.descriptor import _add_resource_to_descriptor
from sharedrive.models import Catalog, Location
from sharedrive.descriptor import resolve


def _write_catalog_descriptor(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {"$schema": "sharedrive-catalog", "resources": [], "catalogs": []},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_add_resource_writes_artifact_path_and_source(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_catalog_descriptor(descriptor)

    resource = _add_resource_to_descriptor(
        descriptor,
        name="source-export",
        path="background/exports/source-export.csv",
        sources=[
            {"path": "s3://my-bucket/path/to/source-export.csv", "serviceType": "S3"}
        ],
        title="Source export",
        description="Exported source data",
    )

    document = yaml.safe_load(descriptor.read_text(encoding="utf-8"))

    # assert
    assert resource["path"] == "background/exports/source-export.csv"
    assert "_cache" not in resource
    assert resource["sources"][0]["path"] == "s3://my-bucket/path/to/source-export.csv"
    assert document["resources"][0]["sources"][0]["serviceType"] == "S3"


def test_add_resource_to_descriptor_rejects_duplicate_names(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_catalog_descriptor(descriptor)
    _add_resource_to_descriptor(
        descriptor,
        name="source-export",
        path="existing.csv",
        source="s3://bucket/existing.csv",
    )

    with pytest.raises(ValueError, match="already exists"):
        _add_resource_to_descriptor(
            descriptor,
            name="source-export",
            path="background/exports/source-export.csv",
            source="s3://my-bucket/path/to/source-export.csv",
        )


def test_add_resource_requires_artifact_path(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_catalog_descriptor(descriptor)
    with pytest.raises(ValueError, match="path"):
        _add_resource_to_descriptor(
            descriptor, name="missing", source="s3://bucket/file"
        )


def test_add_catalog_with_target(tmp_path: Path) -> None:
    descriptor = tmp_path / "descriptor.yaml"
    _write_catalog_descriptor(descriptor)
    catalog = _add_resource_to_descriptor(
        descriptor,
        name="docs",
        path="docs/_output",
        catalog=True,
        target="https://tenant.sharepoint.com/sites/dev/Docs",
    )
    assert catalog["path"] == "docs/_output"
    assert catalog["targets"][0]["path"].endswith("/Docs")
    assert yaml.safe_load(descriptor.read_text())["catalogs"][0] == catalog


def test_add_resource_to_descriptor_requires_existing_descriptor_by_default(
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "missing.yaml"

    with pytest.raises(FileNotFoundError, match="does not exist"):
        _add_resource_to_descriptor(
            descriptor,
            name="source-export",
            path="background/exports/source-export.csv",
            source="s3://my-bucket/path/to/source-export.csv",
        )


def test_add_resource_to_descriptor_allows_create_if_missing(tmp_path: Path) -> None:
    descriptor = tmp_path / "created.yaml"

    resource = _add_resource_to_descriptor(
        descriptor,
        name="source-export",
        path="background/exports/source-export.csv",
        sources=[
            {"path": "s3://my-bucket/path/to/source-export.csv", "serviceType": "S3"}
        ],
        create_if_missing=True,
    )

    assert descriptor.exists()
    assert resource["name"] == "source-export"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("s3://bucket/raw.csv", "S3"),
        (
            "https://tenant.sharepoint.com/sites/Test/Shared%20Documents/spec.xlsx",
            "SharePoint",
        ),
        ("https://docs.google.com/spreadsheets/d/test-sheet/edit", "GoogleDrive"),
    ],
)
def test_infer_service_type(source: str, expected: str) -> None:
    assert (
        resolve(Catalog(targets=[Location(path=source)])).targets[0].service_type
        == expected
    )


def test_infer_service_type_raises_when_unknown() -> None:
    with pytest.raises(ValueError, match="Cannot resolve target"):
        resolve(Catalog(targets=[Location(path="C:/tmp/local-file.txt")]))
