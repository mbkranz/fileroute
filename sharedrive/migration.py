"""Explicit one-way conversion of legacy descriptors.

Compatibility stays isolated here: normal descriptor loading accepts only the
current path/sources/targets model. Migration never overwrites its input.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml

from sharedrive.descriptor import local_path
from sharedrive.models import CATALOG_PROFILE, Catalog


def _read_mapping(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    try:
        value = (
            json.loads(content)
            if path.suffix.lower() == ".json"
            else yaml.safe_load(content)
        )
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid legacy descriptor '{path}': {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Legacy descriptor '{path}' must contain a mapping")
    return value


def migrate_descriptor(
    path: Path | str, *, direction: Literal["pull", "push"] = "pull"
) -> Catalog:
    """Return a canonical catalog converted from one legacy descriptor.

    Direction is required conceptually because a legacy remote URL did not say
    whether it was a source or target. Referenced legacy catalogs are expanded
    into the returned model so the output is one self-contained file.
    """
    if direction not in {"pull", "push"}:
        raise ValueError("direction must be pull or push")

    def convert(
        data: dict[str, Any],
        *,
        folder: Path,
        seen: frozenset[Path],
        is_catalog: bool,
    ) -> dict[str, Any]:
        result = dict(data)
        result.pop("profile", None)
        result.pop("$schema", None)

        cache = result.pop("_cache", result.pop("cache", None))
        remote = result.pop(
            "accessURL",
            result.pop("accessUrl", result.pop("url", None)),
        )
        authored_path = result.get("path")
        if remote is None and "://" in str(authored_path or ""):
            remote = result.pop("path")

        provider = {
            key: result.pop(key)
            for key in ("serviceType", "serviceId")
            if key in result
        }
        if remote is not None:
            field = "targets" if direction == "push" else "sources"
            if result.get(field):
                raise ValueError(
                    f"Legacy entity {result.get('name')!r} has both {remote!r} "
                    f"and explicit {field}; choose one before migrating"
                )
            if cache is None:
                raise ValueError(
                    f"Legacy remote entity {result.get('name')!r} requires _cache "
                    "for its local artifact path"
                )
            result["path"] = cache
            result[field] = [{"path": str(remote), **provider}]
        elif cache is not None:
            if authored_path not in (None, cache):
                raise ValueError(
                    "Conflicting path and _cache; choose the artifact path "
                    "before migrating"
                )
            result["path"] = cache
            if provider:
                raise ValueError(
                    "Legacy provider metadata requires a remote URL to migrate"
                )
        elif provider:
            raise ValueError(
                "Legacy provider metadata requires a remote URL to migrate"
            )

        result.pop("syncTarget", None)
        result["resources"] = [
            convert(child, folder=folder, seen=seen, is_catalog=False)
            for child in result.get("resources", [])
        ]

        children = [*result.pop("catalogs", []), *result.pop("packages", [])]
        catalogs = []
        for child in children:
            if not isinstance(child, dict):
                raise ValueError("Legacy catalog entries must be mappings")
            reference = child.get("$ref")
            if reference is None and "path" in child and set(child) <= {
                "name",
                "path",
                "conformsTo",
            }:
                reference = child["path"]
            if reference is not None:
                target = local_path(str(reference), folder)
                if target in seen:
                    raise ValueError(f"Cyclic catalog reference: {target}")
                loaded = convert(
                    _read_mapping(target),
                    folder=target.parent,
                    seen=seen | {target},
                    is_catalog=True,
                )
                if child.get("name"):
                    loaded["name"] = child["name"]
                catalogs.append(loaded)
            else:
                catalogs.append(
                    convert(child, folder=folder, seen=seen, is_catalog=True)
                )
        if is_catalog:
            if not result["resources"]:
                result.pop("resources")
            if catalogs:
                result["catalogs"] = catalogs
        else:
            result.pop("resources", None)
        return result

    source = Path(path).resolve()
    converted = convert(
        _read_mapping(source),
        folder=source.parent,
        seen=frozenset({source}),
        is_catalog=True,
    )
    converted["$schema"] = CATALOG_PROFILE
    return Catalog.model_validate(converted)


__all__ = ["migrate_descriptor"]
