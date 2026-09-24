"""Offline descriptor reports sharing the graph and SVG renderer.

HTML uses native disclosure widgets and a small inline script; no CDN, server,
provider authentication, or file-content reads are required.
"""

from __future__ import annotations

from hashlib import sha256
from html import escape
import json
from pathlib import Path
from typing import Any, Iterator, Literal
from urllib.parse import quote, urlsplit

from fileroute.diagram import DescriptorGraph, DiagramNode, _svg, render_svg

Detail = Literal["summary", "full"]


def _anchor(node: DiagramNode) -> str:
    return node.anchor or "node-" + sha256(node.key.encode()).hexdigest()[:20]


def _metadata(node: DiagramNode, detail: Detail) -> dict[str, Any]:
    fields = {
        "kind": node.kind,
        "path": node.path,
        "serviceType": node.service_type,
        "entityType": node.entity_type,
        **node.metadata,
    }
    if detail == "summary":
        fields = {
            key: value
            for key, value in fields.items()
            if key
            in {
                "name",
                "title",
                "description",
                "kind",
                "path",
                "format",
                "serviceType",
                "entityType",
            }
        }
    return {key: value for key, value in fields.items() if value is not None}


def _validate_detail(detail: Detail) -> None:
    if detail not in ("summary", "full"):
        raise ValueError("Detail must be 'summary' or 'full'.")


def _web_url(path: str | None) -> str | None:
    """Only explicit HTTP(S) links are actionable; other paths remain text."""
    if not path or any(ord(char) < 32 or char.isspace() for char in path):
        return None
    try:
        url = urlsplit(path)
        if (
            url.scheme.lower() in {"https", "http"}
            and url.hostname
            and not url.username
            and not url.password
        ):
            return path
    except ValueError:
        pass
    return None


def _relations(
    graph: DescriptorGraph, node: DiagramNode
) -> Iterator[tuple[str, DiagramNode, DiagramNode | None]]:
    nodes = {item.key: item for item in graph.nodes}
    for edge in graph.edges:
        if node.key not in (edge.source, edge.target):
            continue
        other = nodes[edge.target if edge.source == node.key else edge.source]
        label = {
            "source": "Source for" if edge.source == node.key else "Source",
            "target": "Target" if edge.source == node.key else "Target for",
            "contains": "Contains" if edge.source == node.key else "Contained in",
        }[edge.kind]
        owner = nodes.get(edge.inherited_from)
        yield label, other, owner


def _md(text: str) -> str:
    # Escape descriptor text before placing it into Markdown headings/links.
    text = escape(text).replace("\n", " ").replace("\r", " ")
    for char in "\\`*_{}[]()#+-.!|":
        text = text.replace(char, "\\" + char)
    return text


def render_markdown(
    graph: DescriptorGraph, output: Path | str, *, detail: Detail = "summary"
) -> Path:
    """Write a file dictionary and companion SVG; dictionary anchors are portable."""
    _validate_detail(detail)
    output = Path(output)
    if output.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("Markdown output must end in .md or .markdown.")
    svg = output.with_suffix(".svg")
    render_svg(graph, svg)
    parts = [
        "# Descriptor workflow",
        "",
        f"![Descriptor workflow](<{quote(svg.name)}>)",
        "",
        "Diagram of descriptor intent; no files or permissions were checked.",
        "",
        "## File dictionary",
        "",
    ]
    for node in graph.nodes:
        parts.append(f"- [{_md(node.label)}](#{_anchor(node)})")
    for node in graph.nodes:
        parts.extend([
            "",
            f'<a id="{_anchor(node)}"></a>',
            "",
            f"### {_md(node.label)}",
            "",
        ])
        for key, value in _metadata(node, detail).items():
            value = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )
            parts.append(f"- **{_md(key)}:** {_md(value)}")
        url = _web_url(node.path)
        if url:
            parts.append(f"- [Open file/location](<{quote(url, safe=':/?=&%#@+;,~')}>)")
        for label, other, owner in _relations(graph, node):
            relation = f"- {label}: [{_md(other.label)}](#{_anchor(other)})"
            if owner:
                relation += f" (inherited from [{_md(owner.label)}](#{_anchor(owner)}))"
            parts.append(relation)
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return output


def render_html(
    graph: DescriptorGraph, output: Path | str, *, detail: Detail = "summary"
) -> Path:
    """Write a self-contained searchable SVG and metadata inspector, usable offline."""
    _validate_detail(detail)
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>Descriptor workflow</title><style>" + _CSS + "</style></head><body>",
        "<header><h1>Descriptor workflow</h1><p>Descriptor intent only; files and permissions have not been checked.</p>",
        '<label for="search">Find a file or metadata</label> <input id="search" type="search">',
        '<p id="status" role="status">Select a node or dictionary entry to inspect its metadata.</p></header>',
        '<main><section class="diagram" aria-label="Workflow diagram">',
        _svg(graph, interactive=True),
        '</section><section class="dictionary" aria-label="File dictionary"><h2>File dictionary</h2>',
    ]
    for node in graph.nodes:
        anchor = _anchor(node)
        parts.append(
            f'<details class="entry" id="entry-{anchor}" data-node="{anchor}"><summary>{escape(node.label)} <small>{escape(node.kind)}</small></summary>'
        )
        parts.append(f'<p><a href="#{anchor}">Show in diagram / permalink</a></p><dl>')
        for key, value in _metadata(node, detail).items():
            value = (
                json.dumps(value, indent=2, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )
            parts.append(f"<dt>{escape(key)}</dt><dd>{escape(value)}</dd>")
        parts.append("</dl>")
        url = _web_url(node.path)
        if url:
            parts.append(
                f'<p><a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Open file/location</a></p>'
            )
        parts.append("<ul>")
        for label, other, owner in _relations(graph, node):
            relation = (
                f'<li>{label}: <a href="#{_anchor(other)}">{escape(other.label)}</a>'
            )
            if owner:
                relation += f' (inherited from <a href="#{_anchor(owner)}">{escape(owner.label)}</a>)'
            parts.append(relation + "</li>")
        parts.append("</ul></details>")
    parts.extend(["</section></main><script>", _JS, "</script></body></html>"])
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return output


_CSS = """
body{font:16px system-ui,sans-serif;margin:0;color:#172b3a;background:#f3f6f9}
header{padding:1rem 2rem;background:white;border-bottom:1px solid #ccd6df}
h1{margin:0}input{font:inherit;padding:.5rem;width:min(28rem,70%)}
main{display:grid;grid-template-columns:minmax(0,3fr) minmax(20rem,2fr);gap:1rem;padding:1rem}
.diagram,.dictionary{background:white;padding:1rem;border-radius:.5rem;overflow:auto;max-height:75vh}
.diagram svg{max-width:100%;height:auto}.diagram-node{cursor:pointer}
.diagram-node:focus rect,.diagram-node.selected rect{stroke:#005fcc;stroke-width:3;fill:#e6f1ff}
details{border-bottom:1px solid #ccd6df;padding:.7rem}summary{cursor:pointer;font-weight:600}
small{font-weight:400;color:#536574;margin-left:.5rem}dt{font-weight:600}
dd{margin:0 0 .6rem;white-space:pre-wrap;overflow-wrap:anywhere}a{color:#0055aa}
.entry.selected{background:#edf5ff}li{margin:.4rem 0}a:focus-visible,summary:focus-visible{outline:3px solid #005fcc}
@media(max-width:800px){main{grid-template-columns:1fr}.diagram,.dictionary{max-height:none}}
"""

_JS = """
const entries = [...document.querySelectorAll('.entry')];
const nodes = [...document.querySelectorAll('.diagram-node')];
const search = document.getElementById('search');
const status = document.getElementById('status');
function select(id) {
  const entry = entries.find(item => item.dataset.node === id);
  const node = document.getElementById(id);
  if (!entry || !node) return;
  search.value = '';
  entries.forEach(item => {item.hidden = false; item.classList.toggle('selected', item === entry);});
  nodes.forEach(item => item.classList.toggle('selected', item === node));
  entry.open = true;
  entry.scrollIntoView({block:'nearest'});
  node.scrollIntoView({block:'nearest'});
  status.textContent = 'Selected: ' + entry.querySelector('summary').textContent;
}
function navigate(id) {
  if (location.hash.slice(1) === id) select(id);
  else location.hash = id;
}
nodes.forEach(node => {
  node.addEventListener('click', () => navigate(node.id));
  node.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); navigate(node.id);}
  });
});
entries.forEach(entry => entry.querySelector('summary').addEventListener('click', event => {
  if (!entry.open) {event.preventDefault(); navigate(entry.dataset.node);}
}));
document.addEventListener('click', event => {
  const link = event.target.closest('a[href^="#node-"]');
  if (link) {event.preventDefault(); navigate(link.getAttribute('href').slice(1));}
});
search.addEventListener('input', () => {
  const query = search.value.toLowerCase();
  entries.forEach(entry => {entry.hidden = !entry.textContent.toLowerCase().includes(query);});
  status.textContent = entries.filter(entry => !entry.hidden).length + ' matching entries';
});
window.addEventListener('hashchange', () => select(location.hash.slice(1)));
select(location.hash.slice(1));
"""

__all__ = ["render_html", "render_markdown"]
