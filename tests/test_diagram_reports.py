"""Export contracts: metadata, navigation, escaping, and offline CLI formats."""

import re
from xml.etree import ElementTree

import pytest
from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.diagram import build_graph, render_svg
from fileroute.diagram_reports import render_html, render_markdown
from fileroute.models import Catalog, Location, Resource


def test_metadata_and_target_origin_survive_projection():
    target = Location(path="https://example.org/output", serviceId="folder-1")
    graph = build_graph(
        Catalog(
            name="root",
            targets=[target],
            catalogs=[
                Catalog(
                    name="nested",
                    resources=[
                        Resource(
                            name="inherited",
                            path="one.csv",
                            description="A table",
                            format="csv",
                            owner={"team": "analytics"},
                        ),
                        Resource(name="disabled", path="two.csv", targets=[]),
                        Resource(name="explicit", path="three.csv", targets=[target]),
                    ],
                )
            ],
        )
    )
    nodes = {node.label: node for node in graph.nodes}
    assert nodes["inherited"].metadata["owner"] == {"team": "analytics"}
    assert nodes["inherited"].metadata["description"] == "A table"
    assert nodes["inherited"].metadata["format"] == "csv"
    targets = [edge for edge in graph.edges if edge.kind == "target"]
    assert (
        next(
            edge for edge in targets if edge.source == nodes["inherited"].key
        ).inherited_from
        == "catalog:root"
    )
    assert (
        next(
            edge for edge in targets if edge.source == nodes["explicit"].key
        ).inherited_from
        is None
    )
    assert not any(edge.source == nodes["disabled"].key for edge in targets)
    assert (
        next(node for node in graph.nodes if node.kind == "target").metadata[
            "serviceId"
        ]
        == "folder-1"
    )


def test_anchors_survive_reordering_and_duplicate_labels_are_unique():
    a, b = Resource(name="a", path="a.csv"), Resource(name="b", path="b.csv")
    graph = build_graph(Catalog(resources=[a, b]))
    reverse = build_graph(Catalog(resources=[b, a]))
    assert {node.label: node.anchor for node in graph.nodes} == {
        node.label: node.anchor for node in reverse.nodes
    }
    duplicates = build_graph(Catalog(resources=[a, a.model_copy()]))
    assert len({node.anchor for node in duplicates.nodes}) == len(duplicates.nodes)


def test_locations_with_different_metadata_are_not_silently_merged():
    graph = build_graph(
        Catalog(
            resources=[
                Resource(
                    path="a",
                    sources=[
                        Location(path="https://example.org/file", description="first"),
                        Location(path="https://example.org/file", description="second"),
                    ],
                )
            ]
        )
    )
    sources = [node for node in graph.nodes if node.kind == "source"]
    assert len(sources) == 2
    assert {node.metadata["description"] for node in sources} == {"first", "second"}


@pytest.mark.parametrize(
    "render,extension", [(render_html, ".html"), (render_markdown, ".md")]
)
def test_detail_controls_export_not_just_visibility(tmp_path, render, extension):
    graph = build_graph(
        Catalog(
            resources=[
                Resource(
                    path="data.csv",
                    description="A table",
                    internal_note="PRIVATE-MARKER",
                )
            ]
        )
    )
    summary = render(graph, tmp_path / ("summary" + extension)).read_text()
    full = render(graph, tmp_path / ("full" + extension), detail="full").read_text()
    assert "PRIVATE" not in summary
    assert "PRIVATE" in full
    assert "A table" in summary
    with pytest.raises(ValueError, match="Detail"):
        render(graph, tmp_path / ("bad" + extension), detail="unsupported")


def test_html_escapes_metadata_and_rejects_executable_links(tmp_path):
    graph = build_graph(
        Catalog(
            resources=[
                Resource(
                    title="<img src=x onerror=alert(1)>",
                    path="javascript:alert(1)",
                    description="</script><script>alert(2)</script>",
                    sources=[Location(path="https://example.org/file?a=1&b=2")],
                )
            ]
        )
    )
    report = render_html(graph, tmp_path / "report.html").read_text()
    assert "<img src=x" not in report
    assert "<script>alert(2)" not in report
    assert 'href="javascript:' not in report
    assert 'href="https://example.org/file?a=1&amp;b=2"' in report
    assert "<script src=" not in report
    assert report.count("<script>") == 1
    ids = re.findall(r'\bid="([^"]+)"', report)
    assert len(ids) == len(set(ids))
    for anchor in re.findall(r'href="#([^"]+)"', report):
        assert anchor in ids


def test_markdown_companion_and_dictionary_links(tmp_path):
    graph = build_graph(
        Catalog(
            name="Root", resources=[Resource(name="[bad](javascript:x)", path="a.csv")]
        )
    )
    path = render_markdown(graph, tmp_path / "my report.md")
    text = path.read_text()
    assert "( <" not in text
    assert "(<my%20report.svg>)" in text
    assert "[bad](javascript:x)" not in text
    anchors = set(re.findall(r'<a id="([^"]+)"', text))
    assert set(re.findall(r"\]\(#([^)]*)\)", text)) <= anchors
    svg = ElementTree.parse(tmp_path / "my report.svg")
    groups = svg.findall(".//{http://www.w3.org/2000/svg}g")
    assert {group.attrib["id"] for group in groups} == anchors
    assert all(
        group.find("{http://www.w3.org/2000/svg}title") is not None for group in groups
    )


@pytest.mark.parametrize(
    "suffix", [".html", ".md", ".svg", ".HTML", ".htm", ".markdown", ".mmd"]
)
def test_cli_formats(tmp_path, suffix):
    descriptor = tmp_path / "fileroute.yaml"
    descriptor.write_text("resources:\n  - path: data.csv\n")
    output = tmp_path / ("workflow" + suffix)
    result = CliRunner().invoke(
        app, ["diagram", str(descriptor), "-o", str(output), "--detail", "full"]
    )
    assert result.exit_code == 0, result.output
    assert output.exists()


def test_cli_rejects_bad_format_before_writing(tmp_path):
    output = tmp_path / "report.json"
    result = CliRunner().invoke(app, ["diagram", "-o", str(output)])
    assert result.exit_code == 1
    assert "Output must end" in result.output
    assert not output.exists()


def test_svg_retains_full_label_tooltip(tmp_path):
    label = "A very long file title " * 5
    graph = build_graph(Catalog(resources=[Resource(title=label, path="data.csv")]))
    root = ElementTree.parse(render_svg(graph, tmp_path / "diagram.svg"))
    assert any(
        label in (title.text or "")
        for title in root.findall(".//{http://www.w3.org/2000/svg}title")
    )
