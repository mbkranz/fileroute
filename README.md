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
**descriptor's directory**. References are loaded lazily, stay references on
save, and cannot escape that directory. Cycles raise an error.

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
sharedrive pull config/sharedrive.yaml --dry-run
sharedrive push config/sharedrive.yaml --dry-run
sharedrive push config/sharedrive.yaml
```

`checkout` saves the active descriptor in `.sharedrive/sharedrive_set.json`.
The optional descriptor argument also accepts an explicit override. `update`
selects an exact name or dot-path and reports ambiguous names; `clone descriptor`
copies a single authored document. For a resource with one source,
`update --name export --service-type S3` edits that source's provider.
Edit `sources`/`targets` in YAML for more involved changes, or supply a JSON array
using `--sources`/`--targets`.

### Push / upload

`push` publishes `path` to `targets`; `upload` is an alias for the same operation.
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
from sharedrive.upload import plan_upload, upload

catalog = Catalog(resources=[Resource(
    name="guide", path="docs/guide.docx",
    sources=[Location(path="docs/guide.qmd")],
    targets=[Location(path="https://contoso.sharepoint.com/sites/dev/Docs/guide.docx")],
)])
catalog.to_path("config/sharedrive.yaml")
plan = plan_upload(Path("config/sharedrive.yaml"), root=Path.cwd())
# Inspect plan before transfer.
upload(plan)
```

`models.py` owns validation, serialization, reference loading, and one structural
walker shared by lookup/list and authored-document editing. `upload.py` and
`download.py` bridge descriptors to providers. `migration.py` is an explicit
conversion utility, separate from normal loading and transfers. No `dplib` or
OmegaConf layer is involved.

`ServiceItem` and provider clients retain their runtime roles. Clients own
provider-specific authentication and HTTP behavior; runtime items expose
`refresh()`, `children`, `get_path()`, `iter_items()`, `iter_files()`, and
`download()`. `item.to_catalog()` emits canonical artifact paths and sources.
Runtime paths remain relative to the provider container. `get_path()` is
relative to the current item. Parent relationships and recursive traversal use
a snapshot until refresh or mutation invalidates it.

## Migrating old descriptors

This refactor intentionally changes the model API and serialized format. The
`Drive*` classes, Package model/collection, selector framework, `_cache`, and
artifact-level remote provider fields have been removed. Import the four models
above. Old descriptors fail with guidance rather than silently changing meaning.

```bash
sharedrive migrate old.yaml new.yaml --direction pull
sharedrive migrate old-upload.yaml new-upload.yaml --direction push
```

Migration converts `packages` into `catalogs`, `_cache` into `path`, and remote
URLs into either `sources` or `targets` according to the explicit direction.
It preserves existing explicit provenance and does not turn it into a target.
Local references are inlined so the output can be saved elsewhere. Pathless
legacy resources need a local path before conversion. Output must be a new file.
There are no compatibility aliases in the runtime model.

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
