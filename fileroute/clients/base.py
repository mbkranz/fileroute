from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from fileroute.item import ServiceItem
    from fileroute.models import Location
    from fileroute.resolution import ResolvedLocation


@dataclass(frozen=True)
class ClientCapabilities:
    supports_download: bool = True
    supports_upload: bool = False


class BaseClient(ABC):
    """Abstract base for all drive service clients.

    Concrete providers define:

    - ``auth_methods``: supported auth-mode identifiers.
    - ``capabilities``: supported transfer operations.

    Implementations must provide:

    - ``build_default`` to construct a client from environment/settings.
    - ``check_auth`` to validate credentials without performing a transfer.
    - ``get_from_weburl`` to resolve a remote locator into a runtime ServiceItem.
    """

    auth_methods: ClassVar[list[str]] = []
    capabilities: ClassVar[ClientCapabilities] = ClientCapabilities()

    @classmethod
    @abstractmethod
    def build_default(cls) -> "BaseClient":
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def check_auth(cls) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_from_weburl(self, url: str) -> ServiceItem:
        raise NotImplementedError

    @classmethod
    def recognizes_url(cls, url: str) -> bool:
        return False

    @classmethod
    def parse_location(cls, location: "Location") -> "ResolvedLocation":
        raise NotImplementedError

    def get_from_location(self, location: "Location") -> ServiceItem:
        raise NotImplementedError

    def resolve_location(self, location: "Location") -> "ResolvedLocation":
        raise NotImplementedError

    def upload_to_folder(
        self, folder_url: str, relative_path: Path, local_file_path: str | Path
    ) -> ServiceItem:
        """Create or replace a file relative to a remote directory URL."""
        raise NotImplementedError(
            f"Directory uploads are not supported by {type(self).__name__}"
        )


__all__ = ["ClientCapabilities", "BaseClient"]
