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
| `site`, `siteId` | SharePoint site name and Graph ID |
| `drive`, `driveId` | SharePoint library or Google shared drive name and ID |
| `bucket` | S3 bucket name |
| `remotePath` | Path within the provider namespace; filled by offline resolution |
| `entityType` | `File` or `Directory` after online resolution (can also guide upload targets) |
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

## Descriptor commands

The descriptor commands operate on local metadata. An explicit descriptor
path overrides the active/default selection; `activate` saves a selection
for later commands.

| Goal | CLI command |
| --- | --- |
| Select a descriptor | [`activate`](cli.md#fileroute-activate) |
| Add a resource or catalog | [`add`](cli.md#fileroute-add) |
| Inspect local entries | [`list`](cli.md#fileroute-list) |
| Edit an entry | [`update`](cli.md#fileroute-update) |
| Resolve remote locations | [`resolve`](cli.md#fileroute-resolve) |
| Copy a descriptor | [`clone descriptor`](cli.md#fileroute-clone-descriptor) |
| Convert legacy metadata | [`migrate`](cli.md#fileroute-migrate) |

See [Transfers](transfers.md) for `pull` and `push`, and
[Diagrams](diagram.md) for `diagram`. The generated
[CLI reference](cli.md) lists every argument and option.

### Selecting nested entries

`activate` selects a descriptor **file**. To edit an entry within that file,
use its name/dot-path, an exact JSONPath, or the JSON Pointer printed by
`list`. The JSONPath form addresses array positions directly:

```bash
fileroute list config/fileroute.yaml --format json
fileroute list config/fileroute.yaml --select '$.catalogs[0].resources[1]'
fileroute update --descriptor config/fileroute.yaml \
  --select '$.catalogs[0].resources[1].targets[0]' --drive-id 'b!ABC'
fileroute add appendix --descriptor config/fileroute.yaml \
  --parent '$.catalogs[0]' --path docs/appendix.docx
```

`list --format json` includes exact `jsonPath` and `jsonPointer` addresses
for entries and locations. JSONPath accepts fixed `resources`, `catalogs`,
`sources`, or `targets` steps with numeric indices; sources and targets can
only be the final step. Quoted keys such as `$['catalogs'][0]` also work.
Wildcards, filters, and arbitrary metadata fields are not CLI selectors.
Named dot-paths remain useful for stable selectors when array ordering changes.

Referenced catalogs (`$ref`) are shown when listing an expanded descriptor.
Edit a referenced child using its own descriptor file; writes to the parent
preserve its reference instead of changing the child's file. `pull`, `push`,
`resolve`, and `diagram` operate on the descriptor as a whole so catalog
target inheritance and cross-entity relationships remain intact.

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

## Location resolution

`fileroute resolve config/fileroute.yaml` previews normalized JSON offline;
add `--write` to persist it. It parses `s3://`, Google Drive/Docs, and
SharePoint URLs while preserving the original clickable URL. For example:

```yaml
path: https://contoso.sharepoint.com/sites/dev/Docs/guide.docx
serviceType: SharePoint
site: dev
drive: Docs
remotePath: guide.docx
```

Google Drive URLs containing an item ID also yield `serviceId` offline. A bare
remote path needs a provider and namespace; Fileroute never searches all
accessible sites or drives to guess one:

```yaml
# SharePoint: site and drive, or siteId and driveId
path: Reports/a.docx
serviceType: SharePoint
site: PPSC
drive: Shared Documents
```

For Google Drive use `drive: PPSC Shared Drive` (or `driveId`); for S3 use
`bucket: ppsc-data`. Offline resolution fills `remotePath` and rejects
conflicting metadata. Unrecognized remote targets need explicit `serviceType`;
local provenance and ordinary web citations may be provider-less sources.

Run `fileroute resolve config/fileroute.yaml --online` to verify existence and
populate `serviceId`, `entityType`, and available provider IDs; add `--write`
to persist them. Online resolution uses configured provider credentials and
only reads metadata. Paths and URLs remain authored locators, and IDs remain
optional. An S3 directory prefix should end in `/` to distinguish it from an
object key.

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
