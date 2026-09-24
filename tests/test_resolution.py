import pytest

from sharedrive.descriptor import resolve
from sharedrive.models import Catalog, Location, Resource, ServiceType


@pytest.mark.parametrize(
    "alias", ["GoogleDrive", "google_drive", "google-drive", "google", "gdrive"]
)
def test_service_alias_is_enum(alias):
    location = Location(path="https://drive.google.com/file/d/123", service_type=alias)
    assert location.service_type is ServiceType.GOOGLE_DRIVE
    assert location.to_dict()["serviceType"] == "GoogleDrive"


def test_resolution_preserves_clickable_urls_and_local_provenance():
    url = "https://tenant.sharepoint.com/sites/dev/Shared%20Documents/Guide.docx"
    original = Catalog(
        resources=[
            Resource(
                path="out.docx",
                sources=[Location(path="guide.qmd")],
                targets=[Location(path=url)],
            )
        ]
    )
    result = resolve(original)
    assert original.resources[0].targets[0].service_type is None
    assert result.resources[0].targets[0].service_type is ServiceType.SHAREPOINT
    assert result.resources[0].targets[0].path == url
    assert result.resources[0].sources[0].service_type is None
    assert resolve(result) == result


@pytest.mark.parametrize(
    "url",
    [
        "https://sharepoint.com.evil.example/file",
        "https://notsharepoint.com/file",
        "https://example.com/link",
        "relative/file",
    ],
)
def test_unknown_targets_require_explicit_provider(url):
    with pytest.raises(ValueError, match="Cannot resolve target"):
        resolve(Catalog(targets=[Location(path=url)]))


def test_unknown_source_can_be_provenance():
    result = resolve(Catalog(sources=[Location(path="https://example.org/article")]))
    assert result.sources[0].service_type is None


def test_conflicting_provider_fails():
    with pytest.raises(ValueError, match="conflicts"):
        resolve(
            Catalog(
                targets=[Location(path="s3://bucket/file", service_type="SharePoint")]
            )
        )


def test_explicit_provider_allows_nonstandard_remote_locator():
    result = resolve(
        Catalog(
            targets=[
                Location(path="https://example.org/file", service_type="SharePoint")
            ]
        )
    )
    assert result.targets[0].service_type is ServiceType.SHAREPOINT
