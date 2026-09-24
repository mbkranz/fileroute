from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from fileroute.exceptions import AmbiguousPathError
from fileroute.models import Catalog, Resource, Location, ServiceType


def _index_path(path: str | Path) -> str:
    return str(path).replace("\\", "/").strip("/")


@dataclass
class _TraversalIndex:
    """In-memory hierarchy snapshot shared by related runtime items."""

    items_by_id: dict[str, "ServiceItem"] = field(default_factory=dict)
    children_by_id: dict[str, list["ServiceItem"]] = field(default_factory=dict)
    scanned_roots: set[str] = field(default_factory=set)

    def add(self, item: "ServiceItem") -> None:
        item_id = str(item.id)
        self.items_by_id[item_id] = item
        item._traversal_index = self

    def add_children(
        self, parent: "ServiceItem", children: list["ServiceItem"]
    ) -> list["ServiceItem"]:
        ordered = sorted(
            children, key=lambda item: (item.path, item.name, str(item.id))
        )
        self.add(parent)
        parent_id = str(parent.id)
        for child in ordered:
            if getattr(child, "_parent_id", None) is None:
                child._traversal_parent_id = parent_id
            self.add(child)
        self.children_by_id[parent_id] = ordered
        return ordered

    def children(self, item: "ServiceItem") -> list["ServiceItem"] | None:
        return self.children_by_id.get(str(item.id))


class ServiceItem(ABC):
    """Base interface for files and directories in a remote service."""

    def __repr__(self) -> str:

        path = getattr(self, "path", None)
        if path is not None:
            name = getattr(self, "name", None)
            source_url = getattr(self, "source_url", None)
            return (
                f"{type(self).__name__}("
                f"service_type={getattr(self, 'service_type', None)!r}, "
                f"path={path!r}, "
                f"name={name!r}, "
                f"source_url={source_url!r}, "
                f"id={getattr(self, 'id', None)!r})"
            )
        return f"{type(self).__name__}()"

    @property
    @abstractmethod
    def id(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def path(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def service_type(self) -> ServiceType:
        raise NotImplementedError

    @property
    @abstractmethod
    def source_url(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def is_directory(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def refresh(self, *, include_children: bool = True) -> "ServiceItem":
        """Refresh this runtime item from its backing service."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Concrete shared behaviour
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def children(self) -> list["ServiceItem"]:
        """Direct child items for directories; always empty for files."""
        raise NotImplementedError

    @property
    def parent_id(self) -> str | None:
        """Best-known parent identifier for this item, when available.

        ``_parent_id`` is provider metadata from remote APIs. When unavailable,
        ``_traversal_parent_id`` is a runtime fallback inferred when caching
        direct children or scanning descendants.
        """
        return getattr(self, "_parent_id", None) or getattr(
            self, "_traversal_parent_id", None
        )

    @property
    def parent(self) -> "ServiceItem" | None:
        """Best-known parent item from the active traversal snapshot."""
        parent_id = self.parent_id
        if parent_id is None:
            return None
        index = getattr(self, "_traversal_index", None)
        if index is None:
            return None
        return index.items_by_id.get(str(parent_id))

    def _indexed_children(self) -> list["ServiceItem"] | None:
        index = getattr(self, "_traversal_index", None)
        return index.children(self) if index is not None else None

    def _cache_children(self, children: list["ServiceItem"]) -> list["ServiceItem"]:
        index = getattr(self, "_traversal_index", None) or _TraversalIndex()
        return index.add_children(self, children)

    def _resolve_children(self, name: str) -> list["ServiceItem"]:
        return [child for child in self.children if child.name == name]

    def _scan_descendants(self) -> list["ServiceItem"]:
        """Return all descendants, using direct children as the fallback."""
        descendants: list[ServiceItem] = []
        for child in self.children:
            child._traversal_parent_id = str(self.id)
            descendants.append(child)
            if child.is_directory:
                descendants.extend(child._scan_descendants())
        return descendants

    def _cache_descendants(self, descendants: list["ServiceItem"]) -> None:
        index = getattr(self, "_traversal_index", None) or _TraversalIndex()
        index.add(self)
        by_parent_id: dict[str, list[ServiceItem]] = {}

        for item in descendants:
            index.add(item)
            parent_id = getattr(item, "_parent_id", None) or getattr(
                item, "_traversal_parent_id", None
            )
            if parent_id is not None:
                by_parent_id.setdefault(str(parent_id), []).append(item)

        for parent_id, children in by_parent_id.items():
            index.children_by_id[parent_id] = sorted(
                children, key=lambda item: (item.path, item.name, str(item.id))
            )
        for item in descendants:
            if item.is_directory:
                index.children_by_id.setdefault(str(item.id), [])
        index.children_by_id.setdefault(str(self.id), [])
        index.scanned_roots.add(str(self.id))

    def _invalidate_traversal(self) -> None:
        index = getattr(self, "_traversal_index", None)
        if index is None:
            return
        for item in index.items_by_id.values():
            item._traversal_index = None
        self._traversal_index = None

    def get_path(self, relative_path: str | Path) -> "ServiceItem":
        """Resolve a descendant item by traversing child names in *relative_path*.

        The input path is normalized to POSIX-style segments (``\\`` → ``/``) and
        ignores empty segments and ``.`` markers. Providers use their native
        path or exact-child lookup where available.

        """
        raw_path = str(relative_path).replace("\\", "/")
        requires_directory = raw_path.endswith("/") and raw_path.strip("/") not in {
            "",
            ".",
        }
        parts = [part for part in raw_path.split("/") if part and part != "."]
        if ".." in parts:
            raise ValueError("Remote relative paths cannot contain '..'")
        if not parts:
            if requires_directory and not self.is_directory:
                raise NotADirectoryError(f"{self.path!r} is not a directory")
            return self

        current: ServiceItem = self
        for index, part in enumerate(parts):
            if not current.is_directory:
                raise NotADirectoryError(
                    f"Cannot resolve {part!r} below file {current.path!r}"
                )
            matches = current._resolve_children(part)
            if not matches:
                raise FileNotFoundError(
                    f"Path segment {part!r} was not found below {current.path!r}"
                )
            if len(matches) > 1:
                raise AmbiguousPathError(
                    f"Path segment {part!r} is ambiguous below {current.path!r}"
                )
            current = matches[0]
            if index < len(parts) - 1 and not current.is_directory:
                raise NotADirectoryError(
                    f"Cannot resolve descendants below file {current.path!r}"
                )

        if requires_directory and not current.is_directory:
            raise NotADirectoryError(f"{current.path!r} is not a directory")
        return current

    def iter_items(self, *, recursive: bool = True) -> Iterator["ServiceItem"]:
        """Yield child files and directories in deterministic path order."""
        if not self.is_directory:
            raise NotADirectoryError(
                f"{self.path!r} is not a directory, cannot iterate descendants"
            )
        if not recursive:
            yield from self.children
            return

        index = getattr(self, "_traversal_index", None)
        if index is None or str(self.id) not in index.scanned_roots:
            self._cache_descendants(self._scan_descendants())
            index = self._traversal_index

        descendants: list[ServiceItem] = []
        pending = list(index.children_by_id.get(str(self.id), []))
        while pending:
            item = pending.pop(0)
            descendants.append(item)
            if item.is_directory:
                pending.extend(index.children_by_id.get(str(item.id), []))
        yield from sorted(
            descendants, key=lambda item: (item.path, item.name, str(item.id))
        )

    def iter_files(self, *, recursive: bool = True) -> Iterator["ServiceItem"]:
        """Yield file descendants, excluding directories and the starting item."""
        yield from (
            item
            for item in self.iter_items(recursive=recursive)
            if not item.is_directory
        )

    def download(self, target: Path | str) -> None:
        """Download this item to *target*.

        For directories, walks all leaf files via :meth:`iter_files` and
        writes each one relative to *target*.  For files, concrete
        subclasses must override this method; the default raises
        :exc:`NotImplementedError`.
        """
        if self.is_directory:
            target_root = Path(target)
            target_root.mkdir(parents=True, exist_ok=True)
            root_path = Path(_index_path(self.path))
            for child in self.iter_files():
                child_path = Path(_index_path(child.path))
                try:
                    relative_path = child_path.relative_to(root_path)
                except ValueError:
                    relative_path = child_path
                child.download(target_root / relative_path)
            return
        raise NotImplementedError(
            f"File download is not implemented for {type(self).__name__}. "
            "Concrete subclasses must override download()."
        )

    def to_catalog(self) -> Catalog | Resource:
        """Export local artifact paths and remote provenance as owned models.

        Provider ServiceItem traversal remains independent of descriptor traversal.
        """
        source = Location(
            path=self.source_url,
            serviceId=self.id,
            serviceType=selfileroutetype,
            entityType="Directory" if self.is_directory else "File",
        )fileroute
        if not self.is_directory:
            format_str = None
            if "." in self.name:
                format_str = self.name.rsplit(".", 1)[-1].lower()
            return Resource(
                name=self.path,
                path=self.path,
                sources=[source],
                entityType="File",
                format=format_str,
            )
        resources: list[Resource] = []
        catalogs: list[Catalog] = []
        for child in self.children:
            entry = child.to_catalog()
            if isinstance(entry, Catalog):
                catalogs.append(entry)
            elif isinstance(entry, Resource):
                resources.append(entry)
        return Catalog(
            name=self.path,
            path=self.path,
            sources=[source],
            entityType="Directory",
            resources=resources,
            catalogs=catalogs,
        )


__all__ = ["ServiceItem"]
