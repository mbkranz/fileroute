"""Explicit one-way migration to the canonical keyed descriptor format.

Compatibility stays isolated here: normal loading accepts only keyed
resources/catalogs and descriptor links. Migration never overwrites inputs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fileroute.descriptor import local_path
from fileroute.models import Catalog


def _keyed_document(data: dict, *, label: str = "descriptor") -> dict:
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
                _keyed_document(child, label=f"{label}/catalogs/{name}")
    return data


def migrate(
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
        data = _keyed_document(_read_document(target), label=str(target))
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


__all__ = ["migrate"]
