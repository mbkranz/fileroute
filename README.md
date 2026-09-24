# sharedrive

Sharedrive describes where project artifacts come from, where they live locally,
and where they should be published. A small YAML or JSON catalog connects local
files with SharePoint, Google Drive, and S3 URLs, so a document or data export
can keep its provenance and multiple destinations in one place. The CLI can
preview changes without credentials, pull remote inputs, and push supported
outputs; the same descriptor and transfer planners are available from Python.
Use it inside a project for repeatable, versioned workflows, or run it as a
standalone tool to inspect and retrieve individual files. Python 3.11 or newer
is required.

## Choose how to run it

**Within a project:** add Sharedrive to that project's dependencies, commit its
`uv.lock` and descriptor, and run commands from the project root. `uv run` uses
the project's environment and resolves its declared dependencies before running
the command. This is the better fit for automation and Python API imports.

```bash
uv add git+https://github.com/mbkranz/sharedrive.git@dev
uv run sharedrive pull config/sharedrive.yaml --dry-run
uv run sharedrive push config/sharedrive.yaml --dry-run
```

Use a commit SHA instead of `dev` in the Git dependency for a fixed revision,
or `uv add ./path/to/sharedrive` when developing against a local checkout. See
uv's [project command guide](https://docs.astral.sh/uv/concepts/projects/run/)
and [dependency guide](https://docs.astral.sh/uv/concepts/projects/dependencies/).

**As a standalone tool:** `uvx` (an alias for `uv tool run`) runs Sharedrive in
its own cached, disposable environment, separate from any project environment.
It reads descriptor paths and files from your current working directory, but it
does not add Sharedrive to the project's dependencies or make it importable by
that project's Python code. Use this for ad hoc CLI operations:

```bash
uvx --from git+https://github.com/mbkranz/sharedrive.git@dev sharedrive list config/sharedrive.yaml
uvx --from git+https://github.com/mbkranz/sharedrive.git@dev sharedrive pull config/sharedrive.yaml --dry-run
```

Once the intended Sharedrive distribution is available from your package index,
the shorter form is `uvx sharedrive list config/sharedrive.yaml` (or
`uvx sharedrive --help`). For a fixed tool version, pin the package version or
Git commit. See uv's [tool guide](https://docs.astral.sh/uv/concepts/tools/).

To develop this repository itself:

```bash
uv sync
uv run sharedrive --help
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

Unrecognized metadata fields survive model round trips. Existing YAML descriptors
are round-trip edited where practical, retaining comments and authored styles.
This is a Sharedrive format inspired by Data Package
and DCAT, not a full implementation of either standard. `$schema` is an optional
profile label; loading does not fetch a schema from the network.

## Common use cases

Each resource has one local artifact `path`. Its `sources` record where the
artifact came from; its `targets` list every intended publication destination.
One resource can have multiple targets. `pull` downloads **one remote source**
to `path`, and `push` uploads the file at `path` to **all targets**. Run them as
separate steps; Sharedrive does not stream directly between cloud providers or
convert source formats. These examples can be saved as `config/sharedrive.yaml`.

### One SharePoint source and two SharePoint targets

Retrieve a report to the project, then publish copies to two SharePoint sites:

```yaml
resources:
  - name: monthly-report
    path: artifacts/monthly-report.csv
    sources:
      - path: https://contoso.sharepoint.com/sites/data/Shared%20Documents/monthly-report.csv
    targets:
      - path: https://contoso.sharepoint.com/sites/reports/Shared%20Documents/monthly-report.csv
      - path: https://contoso.sharepoint.com/sites/archive/Shared%20Documents/monthly-report.csv
```

For this supported combination, run `sharedrive pull config/sharedrive.yaml`
and then `sharedrive push config/sharedrive.yaml`, with `--dry-run` on either
command to inspect its plan first. Each SharePoint site needs working access.

### SharePoint source and Google Drive target

Describe retrieval from SharePoint and intended publication to Google Drive:

```yaml
resources:
  - name: partner-report
    path: artifacts/partner-report.csv
    sources:
      - path: https://contoso.sharepoint.com/sites/data/Shared%20Documents/partner-report.csv
    targets:
      - path: https://drive.google.com/file/d/GOOGLE_FILE_ID/view
```

### SharePoint source, SharePoint and Google Drive targets

Use the same local copy for a supported SharePoint publication and a planned
Google Drive publication:

```yaml
resources:
  - name: partner-report
    path: artifacts/partner-report.csv
    sources:
      - path: https://contoso.sharepoint.com/sites/data/Shared%20Documents/partner-report.csv
    targets:
      - path: https://contoso.sharepoint.com/sites/reports/Shared%20Documents/partner-report.csv
      - path: https://drive.google.com/file/d/GOOGLE_FILE_ID/view
```

### Local authoring source with SharePoint, Google Drive, and S3 targets

Keep the input document as provenance while publishing its rendered output:

```yaml
resources:
  - name: guide
    path: docs/_output/guide.docx
    sources:
      - path: docs/guide.qmd
    targets:
      - path: https://contoso.sharepoint.com/sites/docs/Shared%20Documents/guide.docx
      - path: https://drive.google.com/file/d/GOOGLE_FILE_ID/view
      - path: s3://example-docs/guide.docx
```

Render `docs/guide.qmd` to `docs/_output/guide.docx` with Quarto before
publishing. A local source is provenance; `pull` does not render or copy it.

**Current transfer support:** SharePoint, Google Drive, and S3 remote sources
can be pulled; only SharePoint targets can be pushed. A descriptor with a Google
Drive or S3 target is valid metadata, but `push` **and `push --dry-run` fail at
planning** until upload support is implemented. If you need the SharePoint
destination now, put it in a separate descriptor (or remove the unsupported
targets) for that run. `resolve` can still infer providers and preview the
descriptor without authentication. Source and target URLs above are examples;
replace them with your own accessible files and sites.

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
Repeated resolution is idempotent. Existing YAML comments and styles are retained
where practical; byte-for-byte whitespace preservation is not guaranteed.
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

## Package releases

Install the repository task runner with `uv tool install poethepoet==0.48.0`,
then run `poe release-check` for validation and `poe build` for a local wheel
and source distribution. One-off usage is
`uvx --from poethepoet==0.48.0 poe release-check`.

The `publish-to-pypi.yml` workflow has three jobs: **prepare**, **publish**, and
**release**. Pushes to `dev` start or increment a patch development version
(`1.0.0 → 1.0.1.dev1 → 1.0.1.dev2`). Pushes to `main` promote a prerelease to
stable, or increment the patch when the source already has a stable version.
After a stable tag exists, `dev` starts the next patch series. Normal releases
should not edit the version manually. Merge updated `main` back into `dev`
when needed to keep the branches' version baselines aligned.

`poe release --branch dev --source <full-commit-sha>` runs `scripts/release.py`
in a clean checkout of that source. It uses `uv version --no-sync` to update
`pyproject.toml` and `uv.lock`, invokes `poe build`, and creates a local release
commit and annotated tag. This is the CI preparation command: it changes the
local checkout but does not push or publish. The workflow first saves the built
distributions, then atomically pushes the release commit and tag. Both branches
share one release concurrency group; a stale run fails rather than overwriting
newer work. Rapid pushes may supersede pending runs; the latest source should
be released. Workflow pushes use `GITHUB_TOKEN` and do not recursively trigger
another push workflow.

PyPI publication uses the `pypi` GitHub environment and Trusted Publishing bound
to this repository and **`publish-to-pypi.yml`**. No long-lived PyPI token is
needed. Only stable `main` versions get a GitHub Release, after PyPI succeeds.
The branch rules must permit the workflow's version commit; rejected pushes
leave both remote refs unchanged.

For a failed publish, use **Re-run failed jobs** on the original Actions run.
The publish job downloads the saved wheel and source distribution without
rebuilding. Identical PyPI uploads can be retried, including a partially
completed upload. A full rerun recognizes the tagged source/branch before
calculating a version and reuses its original artifact. Existing GitHub Releases
are left intact. If preparation failed before the atomic push, a fresh attempt
can rebuild and replace that run's unpublished artifact. If the original
artifact has expired or been deleted after the push, stop and recover those
exact files; the workflow deliberately does not rebuild a published version.

`poe release-test` exercises the release helper with temporary local Git remotes
and real uv version changes; distribution builds are mocked. No test publishes
packages or contacts cloud providers.
