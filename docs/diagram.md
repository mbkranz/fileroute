# Descriptor diagrams

Fileroute can visualize a descriptor without contacting any remote service. The diagram is derived from the same resolved descriptor model used by transfer planning: sources point to local artifacts, artifacts point to targets, nested catalogs remain visible, referenced catalogs are expanded, and catalog targets inherited by children are included.

```bash
fileroute diagram config/fileroute.yaml
```

The default output is `fileroute-diagram.svg` in the current working directory. Choose another path with `--output` or `-o`:

```bash
fileroute diagram config/fileroute.yaml --output docs/fileroute-workflow.svg
```

Like `pull`, `push`, and `resolve`, the descriptor argument is optional. After selecting a descriptor with `fileroute activate`, this is enough:

```bash
fileroute diagram
```

For ad hoc use outside a project environment, the same command works through uv's tool runner:

```bash
uvx fileroute diagram config/fileroute.yaml
```

Mermaid source is available for embedding directly in GitHub Markdown:

```bash
uvx fileroute diagram config/fileroute.yaml -o workflow.mmd
```

Place the `.mmd` content inside a fenced `mermaid` block in a README. GitHub
Markdown does not embed an external `.mmd` file as a diagram. The four README
examples are saved under `examples/use-cases/`; run
`uv run python scripts/update_readme_diagrams.py` after editing the descriptors
to regenerate their `.mmd` files and inline blocks. Use `--check` to verify them.

Diagram generation is offline. It does not authenticate, inspect remote permissions, or execute transfers. Known SharePoint, Google Drive, and S3 URLs are resolved to their `ServiceType` metadata in memory; the descriptor is not rewritten.

## Python API

`fileroute.diagram` exposes a small semantic graph API independently of the SVG renderer:

```python
from fileroute.diagram import load_graph, render_svg

graph = load_graph("config/fileroute.yaml")
for node in graph.nodes:
    print(node.kind, node.path, node.service_type)

render_svg(graph, "fileroute-diagram.svg")
```

`DescriptorGraph` contains `DiagramNode` and `DiagramEdge` values. The node kinds are `catalog`, `resource`, `source`, `target`, and, when `build_graph()` is called on an unresolved model, `reference`. Edge kinds are `source`, `target`, and `contains`.

This API is intentionally provider- and renderer-neutral. Repository-specific documentation systems can translate the graph into their own diagram model rather than duplicating Fileroute descriptor traversal. The built-in SVG renderer stays small and dependency-free so `uvx fileroute diagram` does not require Graphviz or another rendering runtime.

## HTML inspector and Markdown dictionary

Select the output format by its extension (SVG remains the default):

```bash
fileroute diagram config/fileroute.yaml -o workflow.html
fileroute diagram config/fileroute.yaml -o workflow.md
fileroute diagram config/fileroute.yaml -o workflow.html --detail full
uvx fileroute diagram config/fileroute.yaml -o workflow.html
```

`.html`/`.htm` produces one self-contained offline file. Open it in a browser;
select a diagram node or file-dictionary entry to inspect its metadata. Search
matches exported metadata. Related-node links and URL fragments select and
highlight the corresponding node. Nodes support Enter/Space, dictionary entries
support keyboard navigation, and native disclosure widgets remain usable with
JavaScript disabled. There are no CDN dependencies or provider requests.

`.md`/`.markdown` produces a Markdown dictionary and a same-stem companion SVG
(e.g. `workflow.md` and `workflow.svg`). Keep both files together when sharing or
including them in documentation. Dictionary entries link to each other through
anchors. Navigation inside an embedded SVG varies by Markdown renderer; use
HTML for bidirectional diagram/dictionary navigation. Existing output files,
including the Markdown companion SVG, are replaced.

`--detail summary` is the default: name, title, description, kind, path, format,
provider, and entity type where available. `--detail full` also exports additional
descriptor metadata, including custom fields and service IDs. This controls the
actual exported content, not just its initial visibility. SVG stays compact at
either setting, with full labels and paths in tooltips. Metadata is escaped as
text, and only explicit HTTP(S) URLs without embedded credentials become open
links. Local paths and S3 URIs remain copyable text. Reports do not read or embed
file contents, check existence/permissions, or authenticate.

Relationships identify the catalog that declared inherited targets. Unmarked
target relationships are explicitly declared. `targets: []` continues to disable
publication, so no target edges are added for that artifact.

### Node identity

Existing `DiagramNode.key` values remain available to graph consumers. The new
`anchor` is separate: it hashes catalog ancestry plus a resource's name (or path
when unnamed). Catalogs use name, path, title, then an anonymous fallback;
locations use their role and complete metadata. Reordering uniquely identified
siblings leaves anchors unchanged. Duplicate identities receive encounter-order
suffixes; permalinks to indistinguishable duplicates are not stable across their
reordering. Renaming an identity or changing location metadata changes its
anchor. Give catalogs and resources distinct names for durable links.

Locations with different metadata remain distinct even when their paths match,
so a report does not silently discard conflicting metadata. Node metadata keeps
non-relationship fields; graph edges represent sources, targets, and containment.
`DiagramEdge.inherited_from` points to the declaring catalog's graph key.

### Python report API

```python
from fileroute.diagram import load_graph
from fileroute.diagram_reports import render_html, render_markdown

graph = load_graph("config/fileroute.yaml")
render_html(graph, "workflow.html")
render_markdown(graph, "workflow.md", detail="full")
```

The report module reuses the graph and SVG layout. Plain JavaScript handles
selection and filtering; embedded file previews and additional graph layout
libraries are outside this feature's scope.
