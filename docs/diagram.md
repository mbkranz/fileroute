# Descriptor diagrams

Sharedrive can visualize a descriptor without contacting any remote service. The diagram is derived from the same resolved descriptor model used by transfer planning: sources point to local artifacts, artifacts point to targets, nested catalogs remain visible, referenced catalogs are expanded, and catalog targets inherited by children are included.

```bash
sharedrive diagram config/sharedrive.yaml
```

The default output is `sharedrive-diagram.svg` in the current working directory. Choose another path with `--output` or `-o`:

```bash
sharedrive diagram config/sharedrive.yaml --output docs/sharedrive-workflow.svg
```

Like `pull`, `push`, and `resolve`, the descriptor argument is optional. After selecting a descriptor with `sharedrive checkout`, this is enough:

```bash
sharedrive diagram
```

For ad hoc use outside a project environment, the same command works through uv's tool runner:

```bash
uvx sharedrive diagram config/sharedrive.yaml
```

When running directly from the Git repository before an index release, use the explicit Git source documented in the README.

Diagram generation is offline. It does not authenticate, inspect remote permissions, or execute transfers. Known SharePoint, Google Drive, and S3 URLs are resolved to their `ServiceType` metadata in memory; the descriptor is not rewritten.

## Python API

`sharedrive.diagram` exposes a small semantic graph API independently of the SVG renderer:

```python
from sharedrive.diagram import load_graph, render_svg

graph = load_graph("config/sharedrive.yaml")
for node in graph.nodes:
    print(node.kind, node.path, node.service_type)

render_svg(graph, "sharedrive-diagram.svg")
```

`DescriptorGraph` contains `DiagramNode` and `DiagramEdge` values. The node kinds are `catalog`, `resource`, `source`, `target`, and, when `build_graph()` is called on an unresolved model, `reference`. Edge kinds are `source`, `target`, and `contains`.

This API is intentionally provider- and renderer-neutral. Repository-specific documentation systems can translate the graph into their own diagram model rather than duplicating Sharedrive descriptor traversal. The built-in SVG renderer stays small and dependency-free so `uvx sharedrive diagram` does not require Graphviz or another rendering runtime.
