"""Validation contracts for descriptor models."""

import pytest

from fileroute.models import Catalog, CatalogReference, Location, Resource, ServiceType


def test_location_aliases_and_metadata_validation():
    location = Location.model_validate({
        "path": "s3://bucket/key",
        "serviceType": "s3",
        "custom": 3,
    })
    assert location.service_type is ServiceType.S3
    assert location.model_dump(by_alias=True, exclude_unset=True)["custom"] == 3
    assert location.model_dump(by_alias=True)["serviceType"] == "S3"
    with pytest.raises(ValueError):
        Location.model_validate({"path": "", "serviceType": "S3"})


def test_resource_catalog_and_reference_models():
    catalog = Catalog(resources=[Resource(path="out.csv", targets=[])])
    assert catalog.resources[0].targets == []
    reference = CatalogReference.model_validate({"$ref": "child.yaml"})
    assert reference.model_dump(by_alias=True, exclude_unset=True) == {
        "$ref": "child.yaml"
    }


@pytest.mark.parametrize("field", ["_cache", "packages", "serviceType", "syncTarget"])
def test_retired_fields_fail_actionably(field):
    with pytest.raises(ValueError, match="Unsupported fields"):
        Catalog.model_validate({field: []})
