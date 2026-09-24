# Descriptors

A descriptor is a local YAML or JSON catalog. `Catalog` groups resources and
nested catalogs; `Resource` describes one local artifact; `Location` records
an upstream source or publication target; `CatalogReference` links another
local descriptor. The same metadata drives planning and diagrams.

| Field | Meaning |
| --- | --- |
| `path` | Artifact location: a local file/directory for transfers |
| `sources` | Upstream inputs or provenance, including local authoring files |
| `targets` | Downstream publication destinations |
| `serviceType` | Optional location provider: `GoogleDrive`, `SharePoint`, `S3` |
| `serviceId` | Optional provider-native identifier on a location |
| `$ref` | Another local catalog document |

For example, a rendered Word document can record its Quarto source without
making that source a download operation:

```yaml
$schema: fileroute-catalog
catalogs:
  - name: documentation
    path: docs/_output
    targets:
      - path: https://contoso.sharepoint.com/sites/dev/Shared%20Documents/Docs
        serviceType: SharePoint
    resources:
      - name: guide
        path: docs/_output/guide.docx
        sources:
          - path: docs/guide.qmd
      - name: internal
        path: docs/_output/internal.docx
        targets: []
```

Build `guide.docx` with Quarto before pushing it. The `internal` resource
opts out of the catalog's publication target.

## Paths and references

Artifact paths are relative to the **working directory**, even when the
descriptor is under `config/` or references another descriptor. Python
planners accept an explicit `root`. Transfers reject paths outside the root
and symbolic links. Loading and saving preserve authored paths.

`$ref` paths, by contrast, are relative to the **containing descriptor's
directory**. References remain references on ordinary load/save; planning
expands them once. They cannot escape the containing directory, and cycles
raise an error.

```yaml
catalogs:
  - name: research
    $ref: catalogs/research.yaml
```

Unrecognized metadata survives model round trips. Existing YAML is edited
with comments and styles preserved where practical, but byte-for-byte
whitespace preservation is not guaranteed. The format is inspired by Data
Package and DCAT, not a full implementation of either standard. `$schema`
is an optional profile label, not a network-fetched schema.

## URL resolution

`fileroute resolve config/fileroute.yaml` previews canonical JSON offline;
add `--write` to persist inferred metadata in that document. It recognizes
`s3://`, Google Drive/Docs, and SharePoint URLs as providers while preserving
the original, clickable URL. For example:

```yaml
path: https://contoso.sharepoint.com/sites/dev/Docs/guide.docx
serviceType: SharePoint
```

Conflicting explicit providers are errors. Unrecognized remote target URLs
need an explicit `serviceType`; local provenance and ordinary web citations
can be provider-less sources. Resolution does not follow redirects, fetch IDs,
or check permissions.

Write-back changes only the selected document and retains `$ref` entries.
Resolve referenced descriptors separately to persist their own inferred
metadata. Write-back is optional: transfer planning expands references and
resolves the relevant locations in memory. Use `pull --dry-run` or
`push --dry-run` for a concrete plan; there is no stored plan lockfile.

See [Transfers](transfers.md) for target inheritance and execution, and
[Use cases](use-cases.md) for complete descriptors.

## Python API

The same descriptor, graph, and transfer planning are available in Python:

```python
from pathlib import Path
from fileroute import Catalog, Resource, Location
from fileroute.descriptor import save
from fileroute.diagram import load_graph, render_svg
from fileroute.transfer import plan_push, push

catalog = Catalog(resources=[Resource(
    name="guide",
    path="docs/guide.docx",
    sources=[Location(path="docs/guide.qmd")],
    targets=[Location(
        path="https://contoso.sharepoint.com/sites/dev/Docs/guide.docx"
    )],
)])
save(catalog, "config/fileroute.yaml")
graph = load_graph("config/fileroute.yaml")
render_svg(graph, "fileroute-diagram.svg")
plan = plan_push(Path("config/fileroute.yaml"), root=Path.cwd())
# Inspect the plan before publishing.
push(plan)
```

After resolution, `location.service_type` is the authoritative
`ServiceType` enum for provider dispatch. Pydantic accepts aliases such as
`gdrive`; saved descriptors use `GoogleDrive`, `SharePoint`, and `S3`.
Python attributes use snake_case (`service_type`, `service_id`,
`entity_type`); descriptors use camelCase. See the generated
[Python API reference](api.md) for signatures.
