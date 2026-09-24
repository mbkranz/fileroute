# sharedrive

Experimental file retrieval and publication for SharePoint, Google Drive, and S3.
Python 3.11 or newer is required.

## Install

```bash
uv tool install .                 # CLI from a checkout
uv add ./path/to/sharedrive       # Python dependency in another project
uv sync                          # Develop this repository
```

Configure credentials using `.env-sample`. SharePoint uses the `AZURE_*` and
`SHAREPOINT_*` settings; Google supports ADC, service accounts, and user OAuth;
S3 uses the standard AWS credential chain. Descriptor loading and dry runs do
not authenticate.

```bash
sharedrive auth login gdrive
sharedrive auth login microsoft --auth-mode delegated
sharedrive auth login sharepoint --auth-mode delegated
```

See [Google authentication](docs/google-auth.md) and the generated
[CLI](docs/cli.md) and [Python API](docs/api.md) references.

## Descriptor model

Four models define the public descriptor API: `Catalog`, `Resource`, `Location`,
and `CatalogReference`. A catalog groups resources and nested catalogs. A
resource describes one artifact. Sources and targets are lists of locations.

| Field | Meaning |
| --- | --- |
| `path` | Artifact location; a local file/directory for transfers |
| `sources` | Upstream inputs or provenance, including local authoring files |
| `targets` | Downstream publication destinations |
| `serviceType` | Optional location provider: `GoogleDrive`, `SharePoint`, or `S3` |
| `serviceId` | Optional provider-native identifier on a location |
| `$ref` | Another local catalog document, resolved beside its containing document |

For example, a rendered Word document can identify its Quarto source without
making that source a publication destination:

```yaml
$schema: sharedrive-catalog
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

A retrieval descriptor explicitly identifies its remote source:

```yaml
resources:
  - name: source-export
    path: downloads/source.csv
    sources:
      - path: s3://my-bucket/exports/source.csv
```

All artifact paths are relative to the **working directory**, even when the
descriptor is in `config/` or references another descriptor. The Python planners
also accept an explicit `root`. Paths are preserved when loading and saving.
Transfers reject paths outside that root and symbolic links.

Reference paths are the exception: `$ref` is relative to the containing
**descriptor's directory**. References stay references on normal load/save. Transfer planning explicitly
expands them once; they cannot escape the containing directory. Cycles raise an error.

```yaml
catalogs:
  - name: research
    $ref: catalogs/research.yaml
```

Unrecognized metadata fields survive model round trips. YAML formatting and
comments are not preserved. This is a Sharedrive format inspired by Data Package
and DCAT, not a full implementation of either standard. `$schema` is an optional
profile label; loading does not fetch a schema from the network.

## Commands

```bash
sharedrive add export --path downloads/source.csv --source s3://my-bucket/source.csv
sharedrive add documentation --catalog --path docs/_output --target https://contoso.sharepoint.com/sites/dev/Docs
sharedrive list config/sharedrive.yaml --format json
sharedrive checkout config/sharedrive.yaml
sharedrive update --name documentation --title "Published documentation"
sharedrive resolve config/sharedrive.yaml
sharedrive resolve config/sharedrive.yaml --write
sharedrive migrate old.yaml new.yaml --direction push
sharedrive pull config/sharedrive.yaml --dry-run
sharedrive push config/sharedrive.yaml --dry-run
sharedrive push config/sharedrive.yaml
```

`checkout` saves the active descriptor in `.sharedrive/descriptor`.
The optional descriptor argument also accepts an explicit override. `update`
selects an exact name or dot-path and reports ambiguous names; `clone descriptor`
copies a single authored document. For a resource with one source,
`update --name export --service-type S3` edits that source's provider.
Edit `sources`/`targets` in YAML for more involved changes, or supply a JSON array
using `--sources`/`--targets`.

### Resolve URLs

`resolve` previews canonical JSON without authenticating or contacting a remote
service. `resolve --write` saves the result to the selected YAML/JSON descriptor.
It infers `serviceType` from `s3://` URLs, `drive.google.com` / `docs.google.com`,
and SharePoint hosts, while preserving the original URL exactly as a clickable
link. For example:

```yaml
path: https://contoso.sharepoint.com/sites/dev/Docs/guide.docx
serviceType: SharePoint
```

A conflicting explicit provider is an error. Unrecognized target URLs require
an explicit `serviceType`; local files and general web citations remain valid
provider-less sources. Pull requires a supported remote source. Resolution
does not follow redirects, fetch remote IDs, or check remote permissions.

Write-back updates only the selected document and preserves `$ref` entries;
resolve referenced descriptors separately to persist their inferred metadata.
Repeated resolution is idempotent. YAML comments and formatting are not retained.
Transfers expand references and resolve the relevant sources or targets in
memory, so write-back is optional. `push --dry-run` and `pull --dry-run` provide
concrete transfer plans; there is no separate `plan` command or stored lockfile.

### Push

`push` publishes `path` to `targets`.
Sources are never treated as targets. Only SharePoint uploads are currently
implemented. Other providers fail during planning, before authentication.

- Catalog targets denote existing remote folders. Children inherit those
  targets using paths relative to the declaring catalog's `path` (or the working
  root when no catalog path is given).
- A child's explicit `targets` replace inherited targets. `targets: []` disables
  publication for that child and its descendants unless a descendant declares
  its own targets.
- A catalog with children publishes only its declared children. A leaf catalog
  with a path publishes all files in that directory tree.
- Explicit resource targets are exact file URLs, allowing renaming. Set a
  target's `entityType: Directory` to append the local filename to a folder URL.
- Multiple targets publish multiple copies. Conflicting files aimed at the same
  destination fail before any transfer.

The planner checks all inputs before authentication. SharePoint transfers
create missing child folders and create/replace files; they never delete remote
files. Files above Microsoft Graph's 250 MB single-request limit fail planning.
Dry runs show the proposed destinations without contacting the remote service;
they cannot verify remote permissions or folder existence.

### Pull

`pull` materializes one remote `sources` entry into each artifact's `path`.
A leaf catalog can retrieve a whole remote directory. A catalog with children
retrieves only those children. Targets never affect retrieval.

Multiple sources may represent a transformation, so pull refuses to choose one.
Local provenance such as `.qmd` inputs is not a download operation: build those
artifacts with their authoring tool, then push them. Use separate catalogs for
retrieval and publication when their source semantics differ. Dry runs plan
remote directories as units; execution enumerates and checks remote file paths
before writing any files. Pull replaces existing local files at planned paths.

## Python API and architecture

```python
from pathlib import Path
from sharedrive import Catalog, Resource, Location
from sharedrive.descriptor import save
from sharedrive.transfer import plan_push, push

catalog = Catalog(resources=[Resource(
    name="guide", path="docs/guide.docx",
    sources=[Location(path="docs/guide.qmd")],
    targets=[Location(path="https://contoso.sharepoint.com/sites/dev/Docs/guide.docx")],
)])
save(catalog, "config/sharedrive.yaml")
plan = plan_push(Path("config/sharedrive.yaml"), root=Path.cwd())
# Inspect plan before transfer.
push(plan)
```

The core has one module per responsibility:

- `models.py`: declarative `Catalog`, `Resource`, `Location`, `CatalogReference`
  and `ServiceType` validation.
- `descriptor.py`: `load`, `save`, `walk`, `find`, and offline `resolve`.
- `migration.py`: explicit one-way conversion of legacy descriptors.
- `transfer.py`: `plan_pull` / `plan_push`, then `pull` / `push` execution.
- `item.py`: runtime `ServiceItem` hierarchy.
- `clients/sharepoint.py`, `clients/googledrive.py`, `clients/s3.py`: provider APIs.
- `auth/` and `commands/`: credential handling and CLI workflows.

After resolution, `location.service_type` is the authoritative `ServiceType`
enum used directly for provider dispatch. Pydantic accepts aliases such as
`gdrive` or `google_drive`; saved values are `GoogleDrive`, `SharePoint`, or `S3`.
Python attributes use `service_type`, `service_id`, and `entity_type`; descriptors
use `serviceType`, `serviceId`, and `entityType`. No provider-string conversion
layer or dynamic registry is needed.

`ServiceItem` and provider clients retain their runtime roles. Clients own
provider-specific authentication and HTTP behavior; runtime items expose
`refresh()`, `children`, `get_path()`, `iter_items()`, `iter_files()`, and
`download()`. `item.to_catalog()` emits canonical artifact paths and sources.
Runtime paths remain relative to the provider container. `get_path()` is
relative to the current item. Parent relationships and recursive traversal use
a snapshot until refresh or mutation invalidates it.

## Breaking API changes

Use the four models above and the functions in `descriptor` and `transfer`.
Model I/O and traversal methods, `upload.py`, `download.py`, `helpers.py`, the
provider registry have been removed. The CLI has no `upload` alias or `set`
command. Run `checkout` again to select
a descriptor using the new single-path selection file; obsolete saved workflow
defaults are no longer read.

Legacy `Drive*` classes, packages, `_cache`, and artifact-level provider fields
are unsupported. Update authored descriptors to `path`, `sources`, `targets`,
`resources`, and `catalogs`; provider metadata belongs on a location. Normal loading has no compatibility shims or automatic legacy conversion. Use
`sharedrive migrate OLD NEW --direction pull|push` for an explicit, one-way
conversion that preserves the input file.

## Development

```bash
uv run pytest
uv run ruff check .
uv run python scripts/update_docs_markdown.py
uv run python scripts/update_docs_markdown.py --check
uv sync --extra docs
uv run mkdocs serve
```

Provider tests use mocks; they do not prove live tenant permissions or transfers.

Field naming references: [Data Resource](https://datapackage.org/standard/data-resource/),
[DCAT](https://www.w3.org/TR/vocab-dcat-3/), and
[OpenMetadata Drive Service](https://docs.open-metadata.org/latest/main-concepts/metadata-standard/schemas/entity/services/driveservice).
