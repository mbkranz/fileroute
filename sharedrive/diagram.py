"""Semantic graphs and dependency-free SVG rendering for Sharedrive descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

from sharedrive.descriptor import load, resolve
from sharedrive.models import Catalog, CatalogReference, Location, Resource, ServiceType


@dataclass(frozen=True)
class DiagramNode:
    """One artifact, source, target, or unresolved catalog reference."""

    key: str
    label: str
    kind: str
    path: str | None = None
    service_type: ServiceType | None = None
    entity_type: str | None = None


@dataclass(frozen=True)
class DiagramEdge:
    """A semantic relationship between two descriptor nodes."""

    source: str
    target: str
    kind: str


@dataclass(frozen=True)
class DescriptorGraph:
    """Provider-independent graph derived from one resolved descriptor."""

    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...]


def _artifact_label(artifact: Catalog | Resource, fallback: str) -> str:
    return artifact.title or artifact.name or artifact.path or fallback


def _location_label(location: Location) -> str:
    if location.service_type is not None:
        return location.service_type.value
    return "Local" if "://" not in location.path else "External"


def build_graph(catalog: Catalog) -> DescriptorGraph:
    """Project descriptor sources, artifacts, inherited targets, and nesting."""

    nodes: list[DiagramNode] = []
    edges: list[DiagramEdge] = []
    locations: dict[tuple[str, str, ServiceType | None], str] = {}

    def add_location(location: Location, kind: str) -> str:
        identity = (kind, location.path, location.service_type)
        if identity in locations:
            return locations[identity]
        key = f"{kind}:{len(locations) + 1}"
        locations[identity] = key
        nodes.append(
            DiagramNode(
                key=key,
                label=_location_label(location),
                kind=kind,
                path=location.path,
                service_type=location.service_type,
                entity_type=location.entity_type,
            )
        )
        return key

    def add_locations(
        artifact_key: str,
        sources: list[Location],
        targets: list[Location],
    ) -> None:
        for source in sources:
            source_key = add_location(source, "source")
            edges.append(DiagramEdge(source_key, artifact_key, "source"))
        for target in targets:
            target_key = add_location(target, "target")
            edges.append(DiagramEdge(artifact_key, target_key, "target"))

    def descend_catalog(
        current: Catalog,
        *,
        key: str,
        parent_key: str | None,
        inherited_targets: list[Location],
        fallback: str,
    ) -> None:
        effective_targets = (
            current.targets if current.targets is not None else inherited_targets
        )
        nodes.append(
            DiagramNode(
                key=key,
                label=_artifact_label(current, fallback),
                kind="catalog",
                path=current.path,
                entity_type=current.entity_type,
            )
        )
        if parent_key is not None:
            edges.append(DiagramEdge(parent_key, key, "contains"))
        add_locations(key, current.sources, effective_targets)

        for index, resource in enumerate(current.resources):
            resource_key = f"{key}/resource:{index + 1}"
            resource_targets = (
                resource.targets
                if resource.targets is not None
                else effective_targets
            )
            nodes.append(
                DiagramNode(
                    key=resource_key,
                    label=_artifact_label(resource, f"Resource {index + 1}"),
                    kind="resource",
                    path=resource.path,
                    entity_type=resource.entity_type,
                )
            )
            edges.append(DiagramEdge(key, resource_key, "contains"))
            add_locations(resource_key, resource.sources, resource_targets)

        for index, child in enumerate(current.catalogs):
            child_key = f"{key}/catalog:{index + 1}"
            if isinstance(child, CatalogReference):
                nodes.append(
                    DiagramNode(
                        key=child_key,
                        label=child.name or child.path,
                        kind="reference",
                        path=child.path,
                    )
                )
                edges.append(DiagramEdge(key, child_key, "contains"))
                continue
            descend_catalog(
                child,
                key=child_key,
                parent_key=key,
                inherited_targets=effective_targets,
                fallback=f"Catalog {index + 1}",
            )

    descend_catalog(
        catalog,
        key="catalog:root",
        parent_key=None,
        inherited_targets=[],
        fallback="Catalog",
    )
    return DescriptorGraph(tuple(nodes), tuple(edges))


def load_graph(path: Path | str) -> DescriptorGraph:
    """Load references, resolve provider metadata offline, and build a graph."""

    catalog = resolve(load(path, resolve_references=True))
    return build_graph(catalog)


def render_svg(graph: DescriptorGraph, output: Path | str) -> Path:
    """Render a compact left-to-right SVG with no optional runtime dependency."""

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    source_keys = {edge.source for edge in graph.edges if edge.kind == "source"}
    target_keys = {edge.target for edge in graph.edges if edge.kind == "target"}
    columns = (
        [node for node in graph.nodes if node.key in source_keys],
        [
            node
            for node in graph.nodes
            if node.key not in source_keys and node.key not in target_keys
        ],
        [node for node in graph.nodes if node.key in target_keys],
    )

    box_width = 260
    box_height = 74
    x_positions = (30, 350, 670)
    row_gap = 28
    max_rows = max((len(column) for column in columns), default=1)
    width = 960
    height = max(160, 40 + max_rows * (box_height + row_gap))
    positions: dict[str, tuple[int, int]] = {}

    for x, column in zip(x_positions, columns, strict=True):
        for index, node in enumerate(column):
            positions[node.key] = (x, 30 + index * (box_height + row_gap))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        "<defs>",
        '<marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
        'orient="auto" markerUnits="strokeWidth">',
        '<path d="M0,0 L0,6 L9,3 z" fill="#555"/>',
        "</marker>",
        "</defs>",
        '<rect width="100%" height="100%" fill="white"/>',
    ]

    for edge in graph.edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        sx, sy = positions[edge.source]
        tx, ty = positions[edge.target]
        start_x = sx + box_width
        start_y = sy + box_height // 2
        end_x = tx
        end_y = ty + box_height // 2
        if edge.kind == "contains" and sx == tx:
            start_x = sx + box_width // 2
            start_y = sy + box_height
            end_x = tx + box_width // 2
            end_y = ty
        parts.append(
            f'<line x1="{start_x}" y1="{start_y}" x2="{end_x}" y2="{end_y}" '
            'stroke="#666" stroke-width="1.5" marker-end="url(#arrow)"/>'
        )

    for node in graph.nodes:
        x, y = positions[node.key]
        parts.append(
            f'<rect x="{x}" y="{y}" width="{box_width}" height="{box_height}" '
            'rx="8" fill="#f8f9fa" stroke="#555"/>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{y + 25}" font-family="sans-serif" '
            f'font-size="14" font-weight="600">{escape(node.label)}</text>'
        )
        if node.path:
            path = node.path if len(node.path) <= 44 else f"{node.path[:41]}..."
            parts.append(
                f'<text x="{x + 12}" y="{y + 49}" font-family="monospace" '
                f'font-size="10">{escape(path)}</text>'
            )
        parts.append(
            f'<text x="{x + 12}" y="{y + 65}" font-family="sans-serif" '
            f'font-size="9">{escape(node.kind)}</text>'
        )

    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return output


__all__ = [
    "DescriptorGraph",
    "DiagramEdge",
    "DiagramNode",
    "build_graph",
    "load_graph",
    "render_svg",
]
