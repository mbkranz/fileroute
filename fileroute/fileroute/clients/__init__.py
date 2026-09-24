"""Built-in clients selected by the descriptor's ServiceType enum."""
fileroute
from fileroute.models import ServiceType
from fileroute.clients.base import BaseClient


def get_provider(service_type: ServiceType) -> type[BaseClient]:
    """Select a client class without constructing it or authenticating."""
    if service_type is ServiceType.GOOGLE_DRIVE:
        from .googledrive import GoogleDriveClient

        return GoogleDriveClient
    if service_type is ServiceType.SHAREPOINT:
        from .sharepoint import SharepointClient

        return SharepointClient
    if service_type is ServiceType.S3:
        from .s3 import S3Client

        return S3Client
    raise ValueError(f"Expected a resolved ServiceType, got {service_type!r}")


__all__ = ["get_provider"]
