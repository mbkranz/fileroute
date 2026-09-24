"""Explicit conversion for retired remote-path/_cache and package descriptors.

Kept outside the normal model/transfer path. URLs have no inherent direction;
the caller must decide whether old descriptors describe pull or push workflows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from sharedrive.models import Catalog, CATALOG_PROFILE, local_path, read_descriptor


def migrate_descriptor(
    path: Path, *, direction: Literal["pull", "push"] = "pull"
) -> Catalog:
    """Read a legacy descriptor and return canonical models, inlining references.

    Pathless legacy resources need a local _cache or path before conversion.
    Extension metadata survives; obsolete syncTarget and provider fields move
    out of the artifact. Packages become catalogs without an extra model type.
    """
    if direction not in {"pull", "push"}:
        raise ValueError("direction must be pull or push")

    def convert(
        data: dict[str, Any], folder: Path, seen: frozenset[Path]
    ) -> dict[str, Any]:
        result = dict(data)
        cache = result.pop("_cache", result.pop("cache", None))
        remote = result.pop(
            "accessURL", result.pop("accessUrl", result.pop("url", None))
        )
        if remote is None and "://" in str(result.get("path", "")):
            remote = result["path"]
        metadata = {
            key: result.pop(key)
            for key in ("serviceType", "serviceId")
            if key in result
        }
        if remote and not result.get("sources") and "targets" not in result:
            result["targets" if direction == "push" else "sources"] = [
                {"path": str(remote), **metadata}
            ]
            result["path"] = cache
        elif cache is not None:
            if result.get("path") not in (None, cache) and "://" not in str(
                result["path"]
            ):
                raise ValueError(
                    "Conflicting path and _cache; choose the artifact path before migrating"
                )
            result["path"] = cache
        result.pop("syncTarget", None)
        result["resources"] = [
            convert(child, folder, seen) for child in result.get("resources", [])
        ]
        children = [*result.pop("catalogs", []), *result.pop("packages", [])]
        catalogs = []
        for child in children:
            if "$ref" in child or (
                "path" in child and set(child) <= {"name", "path", "conformsTo"}
            ):
                target = local_path(child.get("$ref", child.get("path")), folder)
                if target in seen:
                    raise ValueError(f"Cyclic catalog reference: {target}")
                loaded = convert(
                    read_descriptor(target), target.parent, seen | {target}
                )
                if child.get("name"):
                    loaded["name"] = child["name"]
                catalogs.append(loaded)
            else:
                catalogs.append(convert(child, folder, seen))
        result["catalogs"] = catalogs
        # Avoid inventing collections on resources.
        for key in ("resources", "catalogs"):
            if not result[key]:
                result.pop(key)
        if "$schema" in result or "profile" in result:
            result.pop("profile", None)
            result["$schema"] = CATALOG_PROFILE
        return result

    path = path.resolve()
    return Catalog.model_validate(
        convert(read_descriptor(path), path.parent, frozenset({path}))
    )


__all__ = ["migrate_descriptor"]
