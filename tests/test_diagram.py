from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.diagram import build_graph, load_graph, render_mermaid, render_svg
from fileroute.models import Catalog, Location, Resource, ServiceType


def test_build_graph_applies_catalog_targets_to_resources():
    target = Location(
        path="https://tenant.sharepoint.com/sites/docs/Shared%20Documents/output",
        service_type=ServiceType.SHAREPOINT,
    )
    graph = build_graph(
        Catalog(
            name="docs",
            path="docs/_output",
            targets=[target],
            resources=[
                Resource(
                    name="guide",
                    path="docs/_output/guide.docx",
                    sources=[Location(path="docs/guide.qmd")],
                )
            ],
        )
    )

    nodes = {node.key: node for node in graph.nodes}
    guide = next(node for node in graph.nodes if node.label == "guide")
    target_node = next(node for node in graph.nodes if node.kind == "target")
    source_node = next(node for node in graph.nodes if node.kind == "source")

    assert nodes["catalog:root"].path == "docs/_output"
    assert target_node.service_type is ServiceType.SHAREPOINT
    assert any(
        edge.source == guide.key
        and edge.target == target_node.key
        and edge.kind == "target"
        for edge in graph.edges
    )
    assert any(
        edge.source == source_node.key
        and edge.target == guide.key
        and edge.kind == "source"
        for edge in graph.edges
    )


def test_load_graph_resolves_providers_and_references(tmp_path):
    child = tmp_path / "child.yaml"
    child.write_text(
        "resources:\n"
        "  - name: data\n"
        "    path: data.csv\n"
        "    sources:\n"
        "      - path: s3://bucket/data.csv\n",
        encoding="utf-8",
    )
    descriptor = tmp_path / "fileroute.yaml"
    descriptor.write_text(
        "catalogs:\n"
        "  - $ref: child.yaml\n"
        "targets:\n"
        "  - path: https://tenant.sharepoint.com/sites/docs/Shared%20Documents/out\n",
        encoding="utf-8",
    )

    graph = load_graph(descriptor)
    assert any(
        node.kind == "source" and node.service_type is ServiceType.S3
        for node in graph.nodes
    )
    assert any(
        node.kind == "target" and node.service_type is ServiceType.SHAREPOINT
        for node in graph.nodes
    )
    assert all(node.kind != "reference" for node in graph.nodes)


def test_mermaid_shows_distinct_destinations_and_local_provenance(tmp_path):
    graph = build_graph(
        Catalog(
            resources=[
                Resource(
                    name="guide",
                    path="output/guide.docx",
                    sources=[Location(path="docs/guide.qmd")],
                    targets=[
                        Location(
                            path="https://tenant.sharepoint.com/sites/docs/Shared%20Documents/guide.docx",
                            service_type=ServiceType.SHAREPOINT,
                        ),
                        Location(
                            path="s3://bucket/guide.docx", service_type=ServiceType.S3
                        ),
                    ],
                )
            ]
        )
    )
    content = render_mermaid(graph, tmp_path / "guide.mmd").read_text()
    assert 'n1["docs/guide.qmd"]' in content
    assert 'n2["SharePoint: docs/guide.docx"]' in content
    assert 'n3["S3: bucket/guide.docx"]' in content
    assert "n1 --> n0" in content
    assert "n0 --> n2" in content and "n0 --> n3" in content
    assert "catalog:root" not in content


def test_render_svg_and_cli_default_output(tmp_path, monkeypatch):
    descriptor = tmp_path / "fileroute.yaml"
    descriptor.write_text(
        "resources:\n"
        "  - name: guide\n"
        "    path: guide.docx\n"
        "    sources:\n"
        "      - path: guide.qmd\n"
        "    targets:\n"
        "      - path: https://tenant.sharepoint.com/sites/docs/Shared%20Documents/guide.docx\n",
        encoding="utf-8",
    )

    direct = render_svg(load_graph(descriptor), tmp_path / "direct.svg")
    assert direct.read_text(encoding="utf-8").startswith("<svg")
    assert "guide.qmd" in direct.read_text(encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["diagram", str(descriptor)])
    assert result.exit_code == 0, result.output
    output = tmp_path / "fileroute-diagram.svg"
    assert output.exists()
    assert "Rendered descriptor diagram" in result.output
