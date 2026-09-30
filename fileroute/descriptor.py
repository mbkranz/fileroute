"""Local descriptor I/O, traversal, lookup and location resolution.

Models contain metadata only. Loading optionally expands links. Walking never
performs I/O; online location verification is explicit.
"""

from __future__ import annotations

import json
import re
import tempfile
from io import StringIO
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import ScalarString
from fileroute.models import Catalog, CatalogLink, Location, Resource
from fileroute.resolution import parse_location, resolve_online


def _yaml() -> YAML:
    parser = YAML(typ="rt")
    parser.preserve_quotes = True
    parser.width = 4096
    parser.indent(mapping=2, sequence=4, offset=2)
    return parser


def _container(value: object) -> object:
    if isinstance(value, dict):
        return CommentedMap()
    if isinstance(value, list):
        return CommentedSeq()
    return None


def _move_named_sequence_comments(parent: dict, key: str) -> None:
    """Attach preceding sequence comments to names before entries move."""
    sequence = parent.get(key)
    if not isinstance(sequence, CommentedSeq) or not sequence:
        return

    def attach(entry: object, tokens: list) -> None:
        if not isinstance(entry, CommentedMap) or not tokens:
            return
        lines = [token.value.strip().removeprefix("#").strip() for token in tokens]
        entry.yaml_set_start_comment(
            "\n".join(lines), indent=tokens[0].start_mark.column
        )

    marker = parent.ca.items.get(key)
    if marker and marker[3]:
        attach(sequence[0], marker[3])
        marker[3] = None
        sequence.ca.comment = None
    for before, after in zip(sequence, sequence[1:]):
        if not isinstance(before, CommentedMap) or not before:
            continue
        last = next(reversed(before))
        comment = before.ca.items.get(last)
        if comment and comment[2]:
            attach(after, [comment[2]])
            comment[2] = None


def _synchronize(authored: object, canonical: object) -> object:
    """Update matching YAML nodes in place so their comments and styles survive."""
    if isinstance(authored, dict) and isinstance(canonical, dict):
        for key in list(authored):
            if key not in canonical:
                del authored[key]
        for key, value in canonical.items():
            if key in authored:
                if key in {"resources", "catalogs"} and isinstance(value, list):
                    _move_named_sequence_comments(authored, key)
                authored[key] = _synchronize(authored[key], value)
            else:
                authored[key] = _synchronize(_container(value), value)
        return authored
    if isinstance(authored, list) and isinstance(canonical, list):
        if all(
            isinstance(item, dict) and isinstance(item.get("name"), str)
            for item in [*authored, *canonical]
        ):
            by_name = {item["name"]: item for item in authored}
            original_comments = getattr(authored, "ca", None)
            comments = dict(original_comments.items) if original_comments else {}
            positions = {item["name"]: index for index, item in enumerate(authored)}
            updated = [
                _synchronize(by_name.get(item["name"], _container(item)), item)
                for item in canonical
            ]
            authored[:] = updated
            if original_comments is not None:
                original_comments.items.clear()
                for index, item in enumerate(canonical):
                    old = positions.get(item["name"])
                    if old in comments:
                        original_comments.items[index] = comments[old]
            return authored
        for index, value in enumerate(canonical):
            if index < len(authored):
                authored[index] = _synchronize(authored[index], value)
            else:
                authored.append(_synchronize(_container(value), value))
        del authored[len(canonical) :]
        return authored
    if authored == canonical and (
        type(authored) is type(canonical)
        or isinstance(authored, ScalarString)
        and isinstance(canonical, str)
    ):
        return authored
    if isinstance(authored, ScalarString) and isinstance(canonical, str):
        return type(authored)(canonical)
    return canonical


def _read_document(path: Path) -> dict:
    """Read ordinary JSON/YAML mappings; reject duplicate keys and custom tags."""

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate key: {key}")
            result[key] = value
        return result

    def check_tags(value):
        tag = str(getattr(value, "tag", ""))
        if tag not in {"", "None"} and not tag.startswith("tag:yaml.org,2002:"):
            raise ValueError(f"Unsupported YAML tag {tag}; use descriptor mappings")
        if isinstance(value, dict):
            for key, child in value.items():
                check_tags(key)
                check_tags(child)
        elif isinstance(value, list):
            for child in value:
                check_tags(child)

    try:
        content = path.read_text(encoding="utf-8")
        data = (
            json.loads(content, object_pairs_hook=unique)
            if path.suffix.lower() == ".json"
            else _yaml().load(content)
        )
        check_tags(data)
        if not isinstance(data, dict):
            raise ValueError("expected a mapping")
        return data
    except (ValueError, YAMLError) as exc:
        raise ValueError(f"Invalid descriptor '{path}': {exc}") from exc


def load(path: Path | str, *, resolve_references: bool = False) -> Catalog:
    """Load named YAML/JSON lists; optionally expand links with provenance.

    The active file stack detects cycles; reusing a file in sibling branches is
    valid. Each occurrence gets an independent model and origin chain. Expanded
    views cannot be saved: write the physical source document instead.
    """
    origins = {}
    registered_names = {}

    def read(target: Path, stack: tuple[Path, ...], prefix: str) -> Catalog:
        target = target.resolve()
        if target in stack:
            raise ValueError(
                f"Cyclic catalog reference: {' -> '.join(map(str, (*stack, target)))}"
            )
        try:
            catalog = Catalog.model_validate(_read_document(target))
        except ValueError as exc:
            raise ValueError(f"Invalid descriptor '{target}': {exc}") from exc
        chain = (*stack, target)

        def visit(current: Catalog, pointer: str, source_pointer: str):
            current._expanded = resolve_references
            origins[pointer] = (target, source_pointer, chain)
            for index, child in enumerate(current.resources):
                origins[f"{pointer}/resources/{index}"] = (
                    target,
                    f"{source_pointer}/resources/{index}",
                    chain,
                )
            for index, child in enumerate(current.catalogs):
                child_pointer = f"{pointer}/catalogs/{index}"
                local_pointer = f"{source_pointer}/catalogs/{index}"
                if isinstance(child, CatalogLink):
                    origins[child_pointer] = (target, local_pointer, chain)
                    if resolve_references:
                        registered_names[child_pointer] = child.name
                        current.catalogs[index] = read(
                            local_path(child.descriptor, target.parent),
                            chain,
                            child_pointer,
                        )
                else:
                    visit(child, child_pointer, local_pointer)

        visit(catalog, prefix, "")
        return catalog

    result = read(Path(path), (), "")
    result._origins = origins
    result._registered_names = registered_names
    result._expanded = resolve_references
    return result


def save(catalog: Catalog, path: Path | str) -> None:
    """Atomically save canonical metadata, including authored defaults/extensions.

    Existing YAML is edited in place where practical, preserving authored
    comments, styles and ordering. Unexpanded descriptor links remain links.
    """
    if catalog._expanded:
        raise ValueError("Cannot save an expanded view; edit its origin descriptor")
    path = Path(path)
    catalog = catalog.model_copy(deep=True)
    # Appending to a default list does not update Pydantic's fields_set.
    for row in walk(catalog, include_self=True):
        for field in ("sources", "targets", "resources", "catalogs"):
            if getattr(row.model, field, None):
                row.model.model_fields_set.add(field)
    data = catalog.model_dump(mode="json", by_alias=True, exclude_unset=True)
    Catalog.model_validate(data)
    if path.suffix.lower() == ".json":
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    else:
        parser = _yaml()
        authored = (
            parser.load(path.read_text(encoding="utf-8"))
            if path.exists()
            else CommentedMap()
        )
        if not isinstance(authored, dict):
            raise ValueError(f"Invalid descriptor '{path}': expected a YAML mapping")
        output = StringIO()
        parser.dump(_synchronize(authored, data), output)
        content = output.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if path.exists():
            temporary.chmod(path.stat().st_mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class EntityPath:
    """Registered identity and editable physical origin of an expanded entry."""

    name_path: str
    model: Catalog | Resource | CatalogLink
    json_pointer: str
    origin_descriptor: Path | None = None
    origin_pointer: str = ""
    reference_chain: tuple[Path, ...] = ()
    effective_path: str | None = None
    effective_path_template: str | None = None

    @property
    def name(self) -> str | None:
        return self.name_path.rpartition(".")[2] or None

    @property
    def json_path(self) -> str:
        return _pointer_json_path(self.json_pointer)

    @property
    def entity_type(self) -> str:
        return "resource" if isinstance(self.model, Resource) else "catalog"


def walk(catalog: Catalog, *, include_self: bool = False) -> Iterator[EntityPath]:
    """Walk named metadata without I/O, retaining origins from load()."""

    def row(name, model, pointer, base):
        origin = catalog._origins.get(pointer, (None, pointer, ()))
        if isinstance(model, Catalog):
            effective = _join_artifact_path(base, model.base_path)
            template = None
        elif isinstance(model, Resource):
            effective = (
                _join_artifact_path(base, model.path)
                if model.path is not None
                else None
            )
            template = (
                _join_artifact_path(base, model.path_template)
                if model.path_template is not None
                else None
            )
        else:
            effective = template = None
        return EntityPath(name, model, pointer, *origin, effective, template)

    def descend(parent: Catalog, prefix: str, pointer: str, base: str | None):
        for collection in ("resources", "catalogs"):
            for index, child in enumerate(getattr(parent, collection)):
                child_pointer = f"{pointer}/{collection}/{index}"
                name = catalog._registered_names.get(child_pointer, child.name)
                name_path = ".".join(filter(None, (prefix, name)))
                entry = row(name_path, child, child_pointer, base)
                yield entry
                if isinstance(child, Catalog):
                    yield from descend(
                        child, name_path, child_pointer, entry.effective_path
                    )

    root = row("", catalog, "", None)
    if include_self:
        yield root
    yield from descend(catalog, "", "", root.effective_path)


def _join_artifact_path(base: str | None, relative: str | None) -> str | None:
    """Compose portable local paths without filesystem access or template expansion."""
    if relative is None:
        return base
    if (
        not relative.strip()
        or "\\" in relative
        or ":" in relative
        or relative.startswith("/")
        or ".." in relative.split("/")
    ):
        raise ValueError(
            f"Expected a relative local artifact path without traversal: {relative!r}"
        )
    return str(PurePosixPath(base or ".") / relative)


def find(
    catalog: Catalog,
    name: str,
    *,
    kind: type[Catalog] | type[Resource] | type[Location] | None = None,
) -> Catalog | Resource | CatalogLink | Location:
    """Find one registered name, JSON Pointer, or exact JSONPath (optional $).

    Use fileroute list for names and addresses. Only catalog/resource indices
    and final sources/targets list indices are addressable, never projections.
    """
    is_path = (
        name.startswith(("$", ".", "["))
        or re.match(r"^(?:resources|catalogs|sources|targets)[.\[]", name) is not None
    )
    rows = list(walk(catalog, include_self=True))
    if not name.startswith(("$", ".", "[", "/")) and any(
        row.name and name.casefold() in {row.name_path.casefold(), row.name.casefold()}
        for row in rows
    ):
        is_path = (
            False  # Registered names win; $ explicitly requests a structural address.
        )
    pointer = _json_path_pointer(name) if is_path else name
    matches = []
    for row in rows:
        if is_path or name.startswith("/"):
            if row.json_pointer == pointer and (
                kind is None or isinstance(row.model, kind)
            ):
                matches.append(row.model)
            if isinstance(row.model, CatalogLink):
                continue
            for field in ("sources", "targets"):
                for index, location in enumerate(getattr(row.model, field) or []):
                    if f"{row.json_pointer}/{field}/{index}" == pointer and (
                        kind is None or isinstance(location, kind)
                    ):
                        matches.append(location)
        elif row.name and (kind is None or isinstance(row.model, kind)):
            if name.casefold() in {row.name_path.casefold(), row.name.casefold()}:
                matches.append(row.model)
    if not matches:
        raise ValueError(f'Entity selector "{name}" was not found; use fileroute list')
    if len(matches) > 1:
        candidates = [
            row.name_path
            for row in walk(catalog)
            if any(row.model is m for m in matches)
        ]
        raise ValueError(
            f'Entity selector "{name}" is ambiguous; use a qualified name: {candidates}'
        )
    return matches[0]


# Tokenize only exact keys and indices; validate the entity grammar separately.
_TOKEN = re.compile(
    r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[([0-9]+)\]|\[('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")\]"
)


def _json_path_pointer(path: str) -> str:
    import ast

    original = path
    if not path.startswith("$"):
        path = "$" + (path if path.startswith((".", "[")) else "." + path)
    cursor, tokens = 1, []
    while cursor < len(path):
        token = _TOKEN.match(path, cursor)
        if token is None:
            raise ValueError(
                f"Unsupported JSONPath {original!r}; use exact keys and indices (no wildcards or filters)"
            )
        key, index, quoted = token.groups()
        tokens.append(
            int(index)
            if index is not None
            else ast.literal_eval(quoted)
            if quoted
            else key
        )
        cursor = token.end()
    if len(tokens) % 2:
        raise ValueError(
            f"Unsupported JSONPath {original!r}; select an entity or location"
        )
    for index in range(0, len(tokens), 2):
        field, key = tokens[index : index + 2]
        if field in {"catalogs", "resources"}:
            if not isinstance(key, int):
                raise ValueError(
                    "Catalog/resource structural selectors need array indices"
                )
            if (
                field == "resources"
                and index + 2 < len(tokens)
                and tokens[index + 2] not in {"sources", "targets"}
            ):
                raise ValueError("Resources contain only final source/target locations")
        elif field in {"sources", "targets"}:
            if index + 2 != len(tokens):
                raise ValueError("Location must be the final step in JSONPath")
            if not isinstance(key, int):
                raise ValueError("Location selector needs a numeric index")
        else:
            raise ValueError(
                "JSONPath must use catalogs/resources and final sources/targets"
            )
    return "".join(
        "/" + str(key).replace("~", "~0").replace("/", "~1") for key in tokens
    )


def _pointer_json_path(pointer: str) -> str:
    parts = (
        [
            key.replace("~1", "/").replace("~0", "~")
            for key in pointer.lstrip("/").split("/")
        ]
        if pointer
        else []
    )
    result = "$"
    for index, key in enumerate(parts):
        if index % 2 and parts[index - 1] in {
            "sources",
            "targets",
            "resources",
            "catalogs",
        }:
            result += f"[{key}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            result += "." + key
        else:
            result += "[" + json.dumps(key) + "]"
    return result


def local_path(path: str, root: Path, *, reject_symlinks: bool = False) -> Path:
    """Resolve a local artifact inside root; never accept URLs or escapes."""
    if "://" in path:
        raise ValueError(f"Expected a local artifact path, got {path!r}")
    root = root.resolve()
    candidate = root / path
    if reject_symlinks and any(
        p.is_symlink() for p in (candidate, *candidate.parents) if p != root
    ):
        raise ValueError(f"Refusing symlink in path: {candidate}")
    if any(parent.exists() and not parent.is_dir() for parent in candidate.parents):
        raise ValueError(f"Path parent is not a directory: {candidate}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path is outside working directory {root}: {path}")
    return resolved


def resolve(
    catalog: Catalog,
    *,
    direction: Literal["pull", "push"] | None = None,
    online: bool = False,
) -> Catalog:
    """Return resolved metadata without mutating the input or authored paths.

    Offline parsing constructs no client. Online lookup verifies locations and
    enriches IDs and types, but never transfers content. References are not
    expanded or modified; resolve their documents individually for write-back.
    """
    if direction not in {None, "pull", "push"}:
        raise ValueError("direction must be pull or push")
    result = catalog.model_copy(deep=True)
    clients = {}

    def enrich(location, *, required: bool) -> None:
        parse_location(location, required=required)
        if online and location.service_type is not None:
            if location.service_type not in clients:
                from fileroute.clients import get_provider

                clients[location.service_type] = get_provider(
                    location.service_type
                ).build_default()
            resolve_online(location, clients[location.service_type])

    for row in walk(result, include_self=True):
        if isinstance(row.model, CatalogLink):
            continue
        if direction != "push":
            for location in row.model.sources:
                enrich(location, required=False)
        if direction != "pull":
            for location in row.model.targets or []:
                enrich(location, required=True)
    return result


@dataclass(frozen=True)
class Selection:
    """One selected entity/location and its physical source address."""

    model: Catalog | Resource | CatalogLink | Location
    entry: EntityPath
    origin_pointer: str
    effective_targets: tuple[Location, ...] = ()
    requested_selector: str = "$"

    @property
    def effective_path(self) -> str | None:
        """Working-root-relative artifact path including ancestor catalog bases."""
        return None if isinstance(self.model, Location) else self.entry.effective_path

    @property
    def effective_path_template(self) -> str | None:
        return (
            None
            if isinstance(self.model, Location)
            else self.entry.effective_path_template
        )

    def as_dict(self) -> dict:
        entity = self.model
        return {
            "selector": self.requested_selector,
            "name": self.entry.name if not isinstance(entity, Location) else None,
            "qualifiedName": self.entry.name_path,
            "kind": "location"
            if isinstance(entity, Location)
            else self.entry.entity_type,
            "originDescriptor": str(self.entry.origin_descriptor)
            if self.entry.origin_descriptor
            else None,
            "originSelector": _pointer_json_path(self.origin_pointer),
            "referenceChain": [str(path) for path in self.entry.reference_chain],
            "artifactPath": getattr(entity, "path", None)
            if not isinstance(entity, Location)
            else None,
            "effectivePath": self.effective_path,
            "effectivePathTemplate": self.effective_path_template,
            "referenceDescriptor": (
                str(self.entry.origin_descriptor)
                if not self.entry.origin_pointer and len(self.entry.reference_chain) > 1
                else getattr(entity, "descriptor", None)
            ),
            "entity": entity.model_dump(mode="json", by_alias=True, exclude_unset=True),
            "effectiveTargets": [
                item.model_dump(mode="json", by_alias=True, exclude_unset=True)
                for item in self.effective_targets
            ],
        }


def select(catalog: Catalog, selector: str) -> Selection:
    """Select a node with origin and inherited target context; never perform I/O."""
    selected = find(catalog, selector)
    rows = list(walk(catalog, include_self=True))
    for row in rows:
        pointer = row.origin_pointer
        if row.model is selected:
            break
        if isinstance(row.model, CatalogLink):
            continue
        found = False
        for field in ("sources", "targets"):
            for index, location in enumerate(getattr(row.model, field) or []):
                if location is selected:
                    pointer += f"/{field}/{index}"
                    found = True
                    break
            if found:
                break
        if found:
            break
    else:
        raise ValueError(f"No origin for {selector}")
    targets = []
    for ancestor in rows:
        if ancestor.json_pointer == row.json_pointer or row.json_pointer.startswith(
            ancestor.json_pointer + "/"
        ):
            if (
                isinstance(ancestor.model, (Catalog, Resource))
                and ancestor.model.targets is not None
            ):
                targets = ancestor.model.targets
    return Selection(
        selected,
        row,
        pointer,
        () if isinstance(selected, Location) else tuple(targets),
        selector,
    )


def resolve_selection(
    path: Path | str, selector: str, *, online: bool = False, write: bool = False
) -> Selection:
    """Resolve one selected scope; optional writes affect its origin file only.

    Inherited targets are reported but never materialized onto the child. A
    catalog write resolves only its authored document, preserving nested links.
    """
    from dataclasses import replace

    selection = select(load(path, resolve_references=True), selector)
    source = selection.entry.origin_descriptor
    assert source is not None

    def enrich(model):
        if isinstance(model, Catalog):
            return resolve(model, online=online)
        if isinstance(model, Resource):
            return resolve(Catalog(resources=[model]), online=online).resources[0]
        if isinstance(model, Location):
            field = "targets" if "/targets/" in selection.origin_pointer else "sources"
            return getattr(resolve(Catalog(**{field: [model]}), online=online), field)[
                0
            ]
        return model

    resolved = enrich(selection.model)
    if isinstance(resolved, (Catalog, Resource)) and resolved.targets is not None:
        effective = resolved.targets
    else:
        effective = (
            resolve(
                Catalog(targets=list(selection.effective_targets)), online=online
            ).targets
            or []
        )
    if write:
        document = load(source)
        raw_selector = selection.origin_pointer or "$"
        raw_model = find(document, raw_selector)
        updated = resolved
        if isinstance(raw_model, Catalog):
            # Reuse verified metadata but keep the physical document's links.
            updated = raw_model.model_copy(deep=True)
            assert isinstance(resolved, Catalog)
            for row in walk(updated, include_self=True):
                if isinstance(row.model, CatalogLink):
                    continue
                enriched = find(resolved, row.json_pointer or "$")
                for field in ("sources", "targets"):
                    if field in row.model.model_fields_set:
                        setattr(row.model, field, getattr(enriched, field))
        if not selection.origin_pointer:
            assert isinstance(updated, Catalog)
            document = updated
        else:
            parts = selection.origin_pointer.strip("/").split("/")
            parent = document
            for field, key in zip(parts[:-2:2], parts[1:-2:2]):
                parent = getattr(parent, field)[int(key)]
            field, key = parts[-2:]
            collection = getattr(parent, field)
            collection[int(key)] = updated
        save(document, source)
    return replace(selection, model=resolved, effective_targets=tuple(effective))


__all__ = [
    "load",
    "save",
    "walk",
    "find",
    "resolve",
    "EntityPath",
    "Selection",
    "select",
    "resolve_selection",
    "local_path",
]
