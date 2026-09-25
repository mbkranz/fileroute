from __future__ import annotations

from typing import Any
from google.auth.exceptions import RefreshError


class AmbiguousPathError(ValueError):
    """Raised when a path segment matches more than one remote item."""


class RemoteApiError(Exception):
    """Shared HTTP metadata for provider-specific exceptions."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_text: str | None = None,
        response_json: dict[str, Any] | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.response_json = response_json
        self.response_headers = response_headers or {}


class GoogleApiError(RemoteApiError):
    """Base exception for Google API failures."""


class GoogleRefreshError(RefreshError):
    """Raised when Google credentials cannot be refreshed."""


class GoogleAuthError(GoogleApiError):
    """Raised when Google credentials cannot be acquired or refreshed."""


class GoogleDriveError(GoogleApiError):
    """Raised when a Google Drive request fails."""


class GraphApiError(RemoteApiError):
    """Base exception for Microsoft Graph failures."""


class GraphAuthError(GraphApiError):
    pass


class GraphApiDriveError(GraphApiError):
    pass


class GraphApiSiteError(GraphApiError):
    pass


__all__ = [
    "AmbiguousPathError",
    "GoogleApiError",
    "GoogleAuthError",
    "GoogleDriveError",
    "GoogleRefreshError",
    "GraphApiDriveError",
    "GraphApiError",
    "GraphApiSiteError",
    "GraphAuthError",
    "RemoteApiError",
]
