# sharedrive

!!!warning

  **IN DEVELOPMENT**

Experimental connectors and workflows for moving files across SharePoint, Google Drive, and S3.

> **TODO: align fetch and pull commands with git fetch and git pull**

## Current scope

- `sharedrive/clients/sharepoint.py`: Microsoft Graph SharePoint client (`SharepointClient`)
- `sharedrive/clients/googledrive.py`: Google Drive client (`GoogleDriveClient`)
- `sharedrive/item.py`: runtime item abstractions for live remote files and folders
- `sharedrive/models.py`: descriptor and metadata models for persisted catalog/package/resource state (`DriveCatalog`)
- `sharedrive/auth/microsoft.py`: SharePoint access-token strategies
- `sharedrive/auth/settings.py`: Google and SharePoint auth settings/factories
- `sharedrive/clients/aws.py`: boto3-backed S3 client and runtime item adapter
- `sharedrive/cli.py`: Typer CLI (`sharedrive`)
- `sharedrive/commands/`: Core command implementations (`auth`, `config`, `descriptor`, `toolkit`)

## Architecture

`sharedrive` uses a layered adapter-oriented architecture.

- Descriptor models in `sharedrive/models.py` represent persisted catalog metadata. They validate YAML/JSON descriptor files, normalize fields such as `serviceType` and `entityType`, and are the source of truth for what gets written back to disk.
- Runtime items in `sharedrive/item.py` represent live remote files and folders. They are adapter-backed objects returned by service clients and expose runtime behavior such as `download()`, `refresh()`, `children`, and `iter_files()`.
- Clients in `sharedrive/clients/*.py` translate provider APIs into runtime items. They own provider-specific HTTP calls, URL resolution, pagination, and item construction.
- The CLI in `sharedrive/cli.py` and modular commands in `sharedrive/commands/` form the outer interface. The CLI delegates user input into specific workflows configured by `auth`, `config` and `descriptor` logic.

The important separation is between persisted descriptor state and live runtime state:

- Descriptor models describe what is stored.
- Runtime items describe what is currently available from a remote service.

## Setup

### Prerequisites

- Python 3.12+ (see `requires-python` in `pyproject.toml`)
- `uv` installed (recommended)
  - Install instructions: https://docs.astral.sh/uv/getting-started/installation/

### Install (CLI tool)

If you primarily want the `sharedrive` CLI, install it as a `uv` tool (no repo checkout / venv activation needed):

```bash
# from a local clone (path to the git repo)
uv tool install "<path-to-sharedrive-repo>"

# if you're already in the repo directory
uv tool install .

# OR from a git repo URL
uv tool install "sharedrive @ git+https://github.com/<org>/<repo>.git"
```

After install, run:

```bash
sharedrive --help
```

### Install (Python API dependency)

If you want to call `sharedrive` from your own Python project:

```bash
# from a local clone (path to the git repo)
uv add "<path-to-sharedrive-repo>"

# OR from a git repo URL
uv add "sharedrive @ git+https://github.com/<org>/<repo>.git"
```

Alternative (without `uv`):

```bash
pip install "<path-to-sharedrive-repo>"

# or, if you're already in the repo directory
pip install .
```

### Install (repo development)

If you are developing in this repository:

```bash
uv sync
uv sync --extra dev --extra test
```

Configure `.env` from `.env-sample` and set:

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `SHAREPOINT_AUTH_MODE`
- `GOOGLE_APPLICATION_CREDENTIALS`
- AWS credentials for S3 access

## Microsoft Auth For SharePoint

`sharedrive` now separates Microsoft token acquisition from `SharepointClient` itself.

The primary Microsoft auth surface is now:

- `sharedrive.auth.microsoft` for delegated and app-only Microsoft Graph token strategies
- `sharedrive.auth.settings.MicrosoftAuthConfig` for env-validated configuration
- `sharedrive.auth.settings.make_sharepoint_client_from_microsoft_auth()` for SharePoint client construction

Backward-compatible aliases remain available under `sharedrive.auth.sharepoint`, `SharepointAuthConfig`, and `make_sharepoint_client_from_settings()`.

Supported first-class SharePoint auth patterns:

- App-only Microsoft Graph auth via `AppOnlyStrategy`
- Delegated interactive auth via `DelegatedStrategy`

For CLI operators:

- `sharedrive auth login microsoft` validates Microsoft auth directly using the configured mode
- `sharedrive auth login sharepoint` validates SharePoint auth using the configured mode
- `sharedrive auth check <descriptor>` includes SharePoint in descriptor-aware preflight checks
- `sharedrive download ... --check-auth` validates SharePoint credentials before downloading selected resources

For Python API usage:

```python
from sharedrive.auth.settings import (
  MicrosoftAuthConfig,
  make_sharepoint_client_from_microsoft_auth,
)

config = MicrosoftAuthConfig()
client = make_sharepoint_client_from_microsoft_auth(config)
```

Compatibility note:

- `sharedrive.azure.SpoConfig` remains as a thin shim over the new settings layer.

## Google Auth

`sharedrive` now separates Google credential acquisition from `GoogleDriveClient` itself.

The Google Drive client falls back to Application Default Credentials if specific authentication details are omitted.

For CLI operators, there are now explicit auth-oriented commands:

- `sharedrive auth login gdrive` runs the installed-app Google OAuth flow and can persist an authorized-user token to `GOOGLE_OAUTH_TOKEN_PATH` or an explicit `--oauth-token-path`.
- `sharedrive auth login microsoft` validates Microsoft auth used by SharePoint workflows.
- `sharedrive auth login sharepoint` validates SharePoint auth using app-only or delegated mode.

For Python API usage, construct clients with explicit auth objects:

```python
from sharedrive.auth.google import GoogleAuth
from sharedrive.clients.googledrive import GoogleDriveClient

adc_client = GoogleDriveClient(
  auth=GoogleAuth.from_adc(),
)

oauth_client = GoogleDriveClient(
  auth=GoogleAuth.from_user_oauth(
    scopes=["https://www.googleapis.com/auth/drive.readonly"],
    client_secrets_path=".google/oauth-credentials.json",
    token_path=".google/oauth-token.json",
  )
)
```

Supported first-class Google auth patterns:

- Application Default Credentials via `GoogleAuth.from_adc(...)`
- Service account JSON via `GoogleAuth.from_service_account(...)`
- Installed-app user OAuth via `GoogleAuth.from_user_oauth(...)`

Token persistence for user OAuth uses JSON token files via `token_path`.

If you prefer env-validated configuration instead of building strategies manually, use `GoogleAuthConfig`:

```python
from sharedrive.auth.settings import GoogleAuthConfig
from sharedrive.clients.googledrive import GoogleDriveClient

config = GoogleAuthConfig()
client = GoogleDriveClient(auth=config.to_auth())
```

The settings layer is additive. Existing `GOOGLE_APPLICATION_CREDENTIALS` behavior in the CLI and retrieval flows still works.

## CLI Usage Examples

```bash
# Descriptor configuration commands
sharedrive checkout resources/descriptor.yaml
sharedrive set --global --output-dir resources

# Authentication commands
sharedrive auth login gdrive --oauth-client-secrets .google/oauth-credentials.json --oauth-token-path .google/oauth-token.json
sharedrive auth login microsoft --auth-mode delegated
sharedrive auth login sharepoint --auth-mode delegated

# Descriptor definition commands
sharedrive add spec-workbook --path https://tenant.sharepoint.com/sites/Test/Shared%20Documents/spec.xlsx --cache background/specs/spec-workbook.xlsx
sharedrive add census-docs --catalog --access-url https://drive.google.com/drive/folders/<id> --service-type googledrive
sharedrive update --title "Hello" --description "hello"
sharedrive update --resource spec-workbook --title "Hello"
```

If you are running from a repo checkout without activating an environment, prefix commands with `uv run`:

```bash
uv run sharedrive checkout resources/descriptor.yaml
uv run sharedrive download --dry-run
uv run sharedrive download resources/descriptor.yaml --dry-run
```

Saved defaults are persisted in `.sharedrive/sharedrive_set.json`.
Use `sharedrive checkout ...` to avoid repeating the descriptor path for `sharedrive auth check`, `sharedrive add`, `sharedrive fetch`, `sharedrive download`, and `sharedrive update`.
Use `sharedrive set --global --output-dir ...` to save a reusable output directory for `sharedrive download`.
Use `sharedrive set <descriptor> --output-dir ...` to save descriptor-scoped defaults that apply when you explicitly download that descriptor.

Python API:

```python
from pathlib import Path
from sharedrive import SharedriveCatalog

catalog = SharedriveCatalog.from_path(Path("resources/descriptor.yaml"))
summary = catalog.download(
    None,  # or: "sharepoint", "s3", "googledrive", ["s3", "sharepoint"]
    output_dir=Path("resources"),
    dry_run=True,
)

if not summary.ok:
  raise RuntimeError(f"Download failed for {summary.failures} resources")
```

Runtime item API:

```python
from sharedrive.auth.google import GoogleAuth
from sharedrive.clients.googledrive import GoogleDriveClient

client = GoogleDriveClient(auth=GoogleAuth.from_settings())
item = client.get_from_weburl("https://drive.google.com/drive/folders/<id>")

item.refresh()
for child in item.children:
  print(child.path)

proposal = item.get_path("01-proposal-process")
for entry in proposal.iter_items():
  print(entry.path, entry.is_directory)

for file_item in proposal.iter_files():
  print(file_item.path)
```

Runtime items returned by service clients are live adapter-backed objects.
Use `refresh()` to reload a file or folder from the backing service.
Runtime `path` values are relative to the provider container: an S3 bucket,
Google Drive root, or SharePoint document library. `get_path()` resolves a path
relative to the current item. For folders, `children` exposes immediate child
items, `iter_items()` yields files and directories, and `iter_files()` yields
only files. `parent_id` exposes the best-known parent identifier: provider
metadata where available, otherwise a relationship inferred during traversal.
`parent` resolves that identifier only when the parent item exists in the
current traversal snapshot. Exact `get_path()` lookup does not necessarily
populate that snapshot. Recursive traversal uses a stable in-memory snapshot until
`refresh()` or a successful mutation invalidates it.
Descriptor fetch remains a catalog workflow: `sharedrive fetch ...` updates descriptor metadata, while runtime item refresh updates in-memory remote objects. In Python, use `SharedriveCatalog.fetch(..., persist=True)` when fetched metadata should be written back to the descriptor.

## Descriptor format

`resources/descriptor.yaml` (or json/yml) is a Data Package catalog. File
resources use `path` for the canonical remote data locator and `_cache` for the
local materialized copy. Remote folders are catalogs with `accessURL`.

```yaml
resources:
  - name: spec-workbook
    path: https://norc.sharepoint.com/sites/.../spec-workbook.xlsx
    _cache: background/specs/spec-workbook.xlsx
    serviceType: SharePoint
    entityType: File

  - name: source-export
    path: s3://my-bucket/path/to/source-export.csv
    _cache: background/exports/source-export.csv
    serviceType: S3
    entityType: File

catalogs:
  - name: census-docs
    accessURL: https://drive.google.com/drive/folders/<id>
    serviceType: GoogleDrive
    entityType: Directory
    resources: []
    catalogs: []
```

Folder-backed entries are authored explicitly with `sharedrive add --catalog`
and then populated with nested resources/catalogs using `sharedrive fetch
<catalog-name>`. Fetch supports Google Drive and SharePoint catalogs and writes
deterministic nested file resources into the descriptor.

Download behavior:

- file resources download from `path` to `_cache`
- catalog `accessURL` is used for discovery/fetch, not direct file download
- `sources[]` is reserved for Data Package provenance/citation metadata

Add a descriptor resource from the CLI:

```bash
sharedrive add spec-workbook \
  --path https://tenant.sharepoint.com/sites/Test/Shared%20Documents/spec.xlsx \
  --cache background/specs/spec-workbook.xlsx \
  --descriptor resources/descriptor.yaml \
  --title "Spec workbook" \
  --description "Source workbook for specs"
```

Add a remote folder catalog from the CLI:

```bash
sharedrive add census-docs \
  --catalog \
  --access-url https://drive.google.com/drive/folders/<id> \
  --descriptor resources/descriptor.yaml
```

Migrate a legacy descriptor that used `sources[].path` for remote access and
resource `path` for local output:

```bash
sharedrive migrate resources/descriptor.yaml --output resources/descriptor.v2.yaml
```

## Documentation site (MkDocs)

This repository now uses MkDocs for docs-site navigation and static markdown docs.

Install docs dependencies:

```bash
uv sync --group docs
```

Run docs locally:

```bash
uv run mkdocs serve
```

Build static docs:

```bash
uv run mkdocs build
```

Regenerate GitHub-viewable CLI/API markdown docs:

```bash
uv run python scripts/update_docs_markdown.py
```

Docs sources:

- `mkdocs.yml`
- `docs/index.md`
- `docs/cli.md`
- `docs/api.md`
- `scripts/update_docs_markdown.py`

## Repository layout

```text
sharedrive/
  docs/
    index.md
    cli.md
    api.md
  scripts/
    dev_adapters.py
  sharedrive/
    aws.py
    azure.py
    actions/
      fetch.py
      add.py
    cli.py
    app.py
  resources/
    descriptor.yaml
```

## References

CLI interface inspiration:

1. `uv`: https://docs.astral.sh/uv/
2. `git`: https://git-scm.com/docs
3. GitHub CLI: https://cli.github.com/manual/

- Data Package Resource `path`: https://datapackage.org/standard/data-resource/
- Data Package `_cache` recipe: https://datapackage.org/recipes/caching-of-resources/
- Data Package private `_` property convention: https://datapackage.org/recipes/private-properties/
- DCAT `accessURL` for indirect access/discovery locations: https://www.w3.org/TR/vocab-dcat-3/
- Catalog organization is inspired by Data Package catalogs and DCAT catalog/dataset/distribution structure: https://datapackage.org/recipes/data-catalog/
- `serviceType` follows OpenMetadata Drive Service naming: https://docs.open-metadata.org/latest/main-concepts/metadata-standard/schemas/entity/services/driveservice
- `entityType` values such as `Directory` and `File` follow OpenMetadata drive data-asset modeling: https://docs.open-metadata.org/latest/main-concepts/metadata-standard/schemas/entity/data/directory
