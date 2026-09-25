"""Explicit one-way conversion of legacy descriptors.

Compatibility stays isolated here: normal descriptor loading accepts only the
current path/sources/targets model. Migration never overwrites its input.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from fileroute.descriptor import local_path
from fileroute.models import CATALOG_PROFILE, Catalog


def _read_mapping(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    try:
        value = (
            json.loads(content)
            if path.suffix.lower() == ".json"
            else YAML(typ="safe").load(content)
        )
    except (ValueError, YAMLError) as exc:
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
        data: dict[str, Any], *, folder: Path, seen: frozenset[Path], is_catalog: bool
    ) -> dict[str, Any]:
        result = dict(data)
        result.pop("profile", None)
        result.pop("$schema", None)

        cache = result.pop("_cache", result.pop("cache", None))
        remote = result.pop(
            "accessURL", result.pop("accessUrl", result.pop("url", None))
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
            if (
                reference is None
                and "path" in child
                and set(child) <= {"name", "path", "conformsTo"}
            ):
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
    return Catalog.model_validate(keyed_document(converted))


def keyed_document(data: dict, *, label: str = "descriptor") -> dict:
    """Convert named lists to maps; old-format handling exists only here.

    Mutates a round-trip YAML document so comments/order survive where possible.
    Missing/invalid names fail rather than silently creating public identities.
    """
    from ruamel.yaml.comments import CommentedMap
    from fileroute.models import validate_name

    root_name = data.pop("name", None)
    if root_name and "title" not in data:
        data["title"] = root_name
    for field in ("resources", "catalogs"):
        children = data.get(field)
        if children is None:
            continue
        if isinstance(children, list):
            mapped = CommentedMap()
            for index, child in enumerate(children):
                if not isinstance(child, dict):
                    raise ValueError(f"{label}/{field}/{index}: expected mapping")
                name_comment = getattr(child, "ca", None)
                name_comment = name_comment.items.get("name") if name_comment else None
                name = child.pop("name", None)
                try:
                    validate_name(name)
                except ValueError as exc:
                    raise ValueError(
                        f"{label}/{field}/{index}: {exc}; supply an explicit name before migrating"
                    ) from exc
                if name.casefold() in {key.casefold() for key in mapped}:
                    raise ValueError(
                        f"{label}/{field}: Duplicate registered name {name}"
                    )
                mapped[name] = child
                if hasattr(children, "ca") and index in children.ca.items:
                    mapped.ca.items[name] = children.ca.items[index]
                if name_comment:
                    mapped.ca.items[name] = name_comment
            data[field] = children = mapped
        if not isinstance(children, dict):
            raise ValueError(f"{label}/{field}: expected mapping or legacy list")
        for name, child in children.items():
            validate_name(name)
            if not isinstance(child, dict):
                raise ValueError(f"{label}/{field}/{name}: expected mapping")
            if "$ref" in child:
                if "descriptor" in child:
                    raise ValueError(
                        f"{label}/{field}/{name}: both $ref and descriptor"
                    )
                keys = list(child)
                offset = keys.index("$ref")
                ref_comment = getattr(child, "ca", None)
                ref_comment = ref_comment.items.get("$ref") if ref_comment else None
                value = child.pop("$ref")
                if hasattr(child, "insert"):
                    child.insert(offset, "descriptor", value)
                else:
                    child["descriptor"] = value
                if ref_comment and hasattr(child, "ca"):
                    child.ca.items["descriptor"] = ref_comment
            if field == "catalogs" and "descriptor" not in child:
                keyed_document(child, label=f"{label}/catalogs/{name}")
    return data


def migrate_format(
    path: Path | str, output: Path | str, *, dry_run: bool = False
) -> list[dict[str, Any]]:
    """Convert a whole linked graph into a new directory, publishing atomically.

    All names and links are checked before writing. A shared child is written
    once; an active stack detects cycles. Inputs and existing outputs are never
    overwritten. The result reports every physical source/output mapping.
    """
    import os
    import shutil
    import tempfile
    from fileroute.descriptor import _read_document, _yaml, load
    from fileroute.models import CatalogLink

    source = Path(path).resolve()
    destination = Path(output).absolute()
    if destination.exists():
        raise ValueError(
            f"Output already exists: {destination}; choose a new directory"
        )
    root = source.parent
    documents = {}

    def visit(target, stack):
        target = target.resolve()
        if target in stack:
            raise ValueError(f"Cyclic catalog reference: {target}")
        if target in documents:
            return
        data = keyed_document(_read_document(target), label=str(target))
        model = Catalog.model_validate(data)
        documents[target] = data

        def links(current, authored):
            for name, child in current.catalogs.items():
                if isinstance(child, CatalogLink):
                    linked = local_path(child.descriptor, target.parent)
                    visit(linked, (*stack, target))
                    # Normalize symlink aliases to the one migrated physical file.
                    authored["catalogs"][name]["descriptor"] = os.path.relpath(
                        linked, target.parent
                    ).replace(os.sep, "/")
                else:
                    links(child, authored["catalogs"][name])

        links(model, data)

    visit(source, ())
    from fileroute.descriptor import walk

    report = [
        {
            "source": str(item),
            "output": str(destination / item.relative_to(root)),
            "registeredNames": [
                row.name_path for row in walk(Catalog.model_validate(data))
            ],
            "links": [
                row.model.descriptor
                for row in walk(Catalog.model_validate(data))
                if isinstance(row.model, CatalogLink)
            ],
        }
        for item, data in documents.items()
    ]
    if dry_run:
        return report
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent)
    )
    try:
        for target, data in documents.items():
            out = stage / target.relative_to(root)
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.suffix.lower() == ".json":
                out.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            else:
                with out.open("w", encoding="utf-8") as stream:
                    _yaml().dump(data, stream)
        load(stage / source.name, resolve_references=True)
        if destination.exists():
            raise ValueError(f"Output already exists: {destination}")
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return report


__all__ = ["migrate_descriptor", "migrate_format"]
