"""Canonical named models reject ambiguous and legacy shapes."""

import pytest
from fileroute.models import Catalog, CatalogLink, Location, Resource, ServiceType


def test_location_aliases_and_metadata():
    location = Location(path="s3://bucket/key", serviceType="s3", custom=3)
    assert location.service_type is ServiceType.S3
    assert location.model_dump(by_alias=True)["custom"] == 3


def test_catalog_profile_is_the_serialized_key():
    catalog = Catalog.model_validate({"profile": "custom-catalog"})
    assert catalog.profile == "custom-catalog"
    assert catalog.model_dump(by_alias=True)["profile"] == "custom-catalog"
    assert "$schema" not in catalog.model_dump(by_alias=True)
    with pytest.raises(ValueError, match=r"Use 'profile' instead of '\$schema'"):
        Catalog.model_validate({"$schema": "custom-catalog"})


def test_named_children_and_strict_link_dispatch():
    model = Catalog.model_validate({
        "resources": [{"name": "guide", "path": "guide.csv", "targets": []}],
        "catalogs": [{"name": "child", "descriptor": "child.yaml"}],
    })
    assert model.resources[0].targets == []
    assert isinstance(model.catalogs[0], CatalogLink)
    for bad in (
        {"name": "bad", "descriptor": "child.yaml", "path": "out"},
        {"name": "bad", "descriptor": "child.yaml", "resources": []},
        {"name": "bad", "$ref": "child.yaml"},
    ):
        with pytest.raises(ValueError):
            Catalog(catalogs=[bad])


@pytest.mark.parametrize(
    "data",
    [
        {"resources": {}},
        {"catalogs": None},
        {"resources": {"bad.name": {"path": "x"}}},
        {"resources": {"file": {"path": "x"}, "FILE": {"path": "y"}}},
        {"catalogs": {"file": {}}, "resources": {"file": {"path": "x"}}},
        {"catalogs": [{"basePath": "unnamed"}]},
        {"serviceType": "S3"},
        {"packages": []},
    ],
)
def test_invalid_or_legacy_shape(data):
    with pytest.raises(ValueError):
        Catalog.model_validate(data)


def test_resource_requires_name_and_preserves_extensions():
    with pytest.raises(ValueError):
        Resource(path="x")
    item = Resource(name="file", path="x", custom={"owner": "team"})
    assert item.model_dump()["custom"] == {"owner": "team"}
