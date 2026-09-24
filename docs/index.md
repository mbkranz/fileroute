# sharedrive

Sharedrive catalogs local artifacts, their sources, and their publication
destinations across SharePoint, Google Drive, and S3. Pull supports all three
providers; push currently publishes to SharePoint targets only.

Descriptors use `Catalog`, `Resource`, `Location`, and `CatalogReference`:
`path` identifies the artifact, `sources` records upstream inputs, and `targets`
identifies publication destinations. `pull` reads sources; `push`
writes targets. Both support offline `--dry-run` planning.

```bash
sharedrive resolve config/retrieval.yaml --write
sharedrive pull config/retrieval.yaml --dry-run
sharedrive push config/publication.yaml --dry-run
sharedrive list config/publication.yaml --format json
```

See the [README](https://github.com/mbkranz/sharedrive#readme) for descriptor
examples, [project and standalone usage](https://github.com/mbkranz/sharedrive#choose-how-to-run-it),
[multi-destination use cases](https://github.com/mbkranz/sharedrive#common-use-cases),
target inheritance, path rules, and URL resolution.

## Architecture

- `models.py`: four declarative Pydantic models and `ServiceType`.
- `descriptor.py`: load, save, walk, find, and offline URL/provider resolution.
- `transfer.py`: plan pull/push, then dispatch through the resolved provider enum.
- `item.py`: live provider-backed items and runtime hierarchy snapshots.
- `clients/` and `auth/`: provider API and credential behavior.
- `commands/`: CLI input, saved descriptor selection, and editing.

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

## Package releases

See the [release workflow and retry instructions](https://github.com/mbkranz/sharedrive#package-releases).
Use `poe release-check` to validate and `poe build` to build locally.
