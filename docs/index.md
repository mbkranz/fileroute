# sharedrive

Shared-drive adapters and retrieval workflows for SharePoint, Google Drive, and S3.

## Architecture

`sharedrive` is organized as a layered adapter-oriented application.

- Descriptor models in `sharedrive/models.py` are the persisted metadata layer. They validate and normalize descriptor documents stored on disk.
- Runtime items in `sharedrive/item.py` are live remote objects for files and folders. They expose runtime behavior such as `refresh()`, `download()`, `children`, and `iter_files()`.
- Clients in `sharedrive/clients/*.py` talk to provider APIs and build runtime items from Google Drive or SharePoint metadata.
- Actions in `sharedrive/actions/*.py` orchestrate workflows over descriptors and runtime items. This is where descriptor fetch, sync, and download flows live.
- The CLI in `sharedrive/cli.py` is the outer layer that resolves defaults and invokes the action layer.

The key boundary is that descriptor models are persisted metadata, while runtime items are live adapter-backed state. Action modules bridge those two worlds.

See [Next Steps](next-steps.md) for the planned catalog model simplification after the working version reaches `main`.


## TODO

- finish google drive authentication doc page with info from ppsc-pmd-utils/docs 
- create the Sharepoint auth documentation page
- build out the "list" action
- keep a single provider client surface for read + write operations for now; consider an explicit read/write client split later if write workflows grow.



## Quick start

```bash
uv sync
```

Configure `.env` with:

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `GOOGLE_APPLICATION_CREDENTIALS`

Google Drive auth supports two layers:

- Compatibility mode for CLI and descriptor retrieval using `GOOGLE_APPLICATION_CREDENTIALS` or ADC.
- Explicit Python auth strategies via `sharedrive.auth.google` for ADC, service account, user OAuth, and chained fallback.

See also: [Google Auth Credentials](google-auth.md) for manual OAuth setup and non-interactive automation options.

## Retrieval

CLI:

```bash
sharedrive add census-docs --catalog --access-url https://drive.google.com/drive/folders/<id> --service-type googledrive
sharedrive fetch census-docs --descriptor resources/descriptor.yaml --dry-run
```

## Folder catalogs

`sharedrive` models remote folders as catalogs with `accessURL`. `sharedrive fetch <catalog-name>` populates that catalog with nested file resources from Google Drive or SharePoint. File resources keep their canonical remote URL in `path` and their local materialized copy in `_cache`.

Python:

```python
from sharedrive.auth.google import GoogleAuth
from sharedrive.clients.googledrive import GoogleDriveClient

client = GoogleDriveClient(auth=GoogleAuth.from_settings())
item = client.get_from_weburl("https://drive.google.com/drive/folders/<id>")

item.refresh()
for child in item.children:
    print(child.path)

folder = item.get_path("reports")
for entry in folder.iter_items():
    print(entry.path, entry.is_directory)

for file_item in folder.iter_files():
    print(file_item.path)
```

Runtime item paths are relative to their bucket, Google Drive root, or
SharePoint document library. `get_path()` is relative to the current item,
`iter_items()` yields both directories and files, and `iter_files()` filters the
same traversal to files. Recursive traversal reuses an in-memory hierarchy
snapshot until `refresh()` or a mutation invalidates it. Descriptor metadata
updates use `SharedriveCatalog.fetch(..., persist=True)`.

## Machine-readable transfer output

`fetch` and `download` support structured JSON output for automation:

```bash
sharedrive fetch research --descriptor resources/descriptor.yaml --dry-run --format json
sharedrive download --descriptor resources/descriptor.yaml --dry-run --format json
```

## Docs site

```bash
uv run mkdocs serve
```

## GitHub-rendered markdown docs

To regenerate static Markdown pages that render directly on GitHub:

```bash
uv run python scripts/update_docs_markdown.py
```

To verify that both generated pages are current without changing them:

```bash
uv run python scripts/update_docs_markdown.py --check
```
