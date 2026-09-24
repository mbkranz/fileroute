import pytest
from sharedrive.clients import get_provider
from sharedrive.clients.base import BaseClient
from sharedrive.clients.googledrive import GoogleDriveClient
from sharedrive.clients.sharepoint import SharepointClient
from sharedrive.clients.s3 import S3Client
from sharedrive.models import ServiceType


@pytest.mark.parametrize(
    "service,expected",
    [
        (ServiceType.GOOGLE_DRIVE, GoogleDriveClient),
        (ServiceType.SHAREPOINT, SharepointClient),
        (ServiceType.S3, S3Client),
    ],
)
def test_provider_selection_does_not_authenticate(service, expected, monkeypatch):
    monkeypatch.setattr(expected, "build_default", lambda: pytest.fail("authenticated"))
    assert get_provider(service) is expected


def test_provider_requires_enum():
    with pytest.raises(ValueError, match="resolved ServiceType"):
        get_provider("googledrive")


def test_google_drive_client_is_base_client() -> None:
    assert issubclass(GoogleDriveClient, BaseClient)


def test_sharepoint_client_is_base_client() -> None:
    assert issubclass(SharepointClient, BaseClient)


def test_google_drive_client_auth_methods() -> None:
    assert "adc" in GoogleDriveClient.auth_methods
    assert "service_account" in GoogleDriveClient.auth_methods
    assert "user_oauth" in GoogleDriveClient.auth_methods


def test_sharepoint_client_auth_methods() -> None:
    assert "app_only" in SharepointClient.auth_methods
    assert "delegated" in SharepointClient.auth_methods
