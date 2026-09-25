"""Canonical keyed models reject ambiguous and legacy shapes."""

import pytest
from fileroute.models import Catalog, CatalogLink, Location, Resource, ServiceType


def test_location_aliases_and_metadata():
    location = Location(path="s3://bucket/key", serviceType="s3", custom=3)
    assert location.service_type is ServiceType.S3
    assert location.model_dump(by_alias=True)["custom"] == 3


def test_keyed_children_and_strict_link_dispatch():
    model = Catalog.model_validate({
        "resources": {"guide": {"path": "guide.csv", "targets": []}},
        "catalogs": {"child": {"descriptor": "child.yaml"}},
    })
    assert model.resources["guide"].targets == []
    assert isinstance(model.catalogs["child"], CatalogLink)
    for bad in (
        {"descriptor": "child.yaml", "path": "out"},
        {"descriptor": "child.yaml", "resources": {}},
        {"$ref": "child.yaml"},
    ):
        with pytest.raises(ValueError):
            Catalog(catalogs={"bad": bad})


@pytest.mark.parametrize(
    "data",
    [
        {"resources": []},
        {"catalogs": []},
        {"resources": {"bad.name": {"path": "x"}}},
        {"resources": {"file": {"path": "x"}, "FILE": {"path": "y"}}},
        {"catalogs": {"file": {}}, "resources": {"file": {"path": "x"}}},
        {"name": "old"},
        {"serviceType": "S3"},
        {"packages": []},
    ],
)
def test_invalid_or_legacy_shape(data):
    with pytest.raises(ValueError):
        Catalog.model_validate(data)


def test_resource_has_no_duplicate_name_and_preserves_extensions():
    with pytest.raises(ValueError):
        Resource(name="file", path="x")
    item = Resource(path="x", custom={"owner": "team"})
    assert item.model_dump()["custom"] == {"owner": "team"}
