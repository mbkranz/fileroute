# sharedrive

Sharedrive retrieves and publishes files through SharePoint, Google Drive, and S3.

Descriptors use `Catalog`, `Resource`, `Location`, and `CatalogReference`:
`path` identifies the artifact, `sources` records upstream inputs, and `targets`
identifies publication destinations. `pull` reads sources; `push` (also `upload`)
writes targets. Both support offline `--dry-run` planning.

```bash
sharedrive pull config/retrieval.yaml --dry-run
sharedrive push config/publication.yaml --dry-run
sharedrive list config/publication.yaml --format json
```

See the [README](https://github.com/mbkranz/sharedrive#readme) for descriptor
examples, target inheritance, path rules, and explicit migration commands.

## Architecture

- `models.py`: owned Pydantic descriptors, local references, and structural traversal.
- `download.py` / `upload.py`: plan transfers, then dispatch to providers.
- `item.py`: live provider-backed items and runtime hierarchy snapshots.
- `clients/` and `auth/`: provider API and credential behavior.
- `commands/`: CLI input, saved descriptor selection, and editing.
- `migration.py`: explicit legacy conversion outside normal model loading.

The descriptor layer has no `dplib` dependency. Provider/runtime traversal is
separate because it represents live remote state rather than authored metadata.

## Documentation

- [CLI reference](cli.md)
- [Python API](api.md)
- [Google authentication](google-auth.md)
- [Next steps](next-steps.md)

```bash
uv run python scripts/update_docs_markdown.py
uv run python scripts/update_docs_markdown.py --check
uv sync --extra docs
uv run mkdocs serve
```
