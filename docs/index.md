# sharedrive

Sharedrive catalogs local artifacts, their sources, and their publication
destinations across SharePoint, Google Drive, and S3. Pull supports all three
providers; push currently publishes to SharePoint targets only.

Descriptors use `Catalog`, `Resource`, `Location`, and `CatalogReference`:
`path` identifies the artifact, `sources` records upstream inputs, and `targets`
identifies publication destinations. `pull` reads sources; `push`
writes targets. Both support offline `--dry-run` planning. `diagram` turns the
same resolved descriptor relationships into SVG, an offline HTML inspector, or a
Markdown file dictionary without
contacting a remote service.

```bash
sharedrive resolve config/retrieval.yaml --write
sharedrive pull config/retrieval.yaml --dry-run
sharedrive push config/publication.yaml --dry-run
sharedrive diagram config/publication.yaml
sharedrive list config/publication.yaml --format json
```

See the [README](https://github.com/mbkranz/sharedrive#readme) for descriptor
examples, [project and standalone usage](https://github.com/mbkranz/sharedrive#choose-how-to-run-it),
[multi-destination use cases](https://github.com/mbkranz/sharedrive#common-use-cases),
target inheritance, path rules, and URL resolution. See
[Descriptor diagrams](diagram.md) for the CLI and reusable semantic graph API.

## Architecture

- `models.py`: four declarative Pydantic models and `ServiceType`.
- `descriptor.py`: load, save, walk, find, and offline URL/provider resolution.
- `diagram.py`: provider-neutral descriptor graph plus dependency-free SVG rendering.
- `diagram_reports.py`: offline HTML inspector and Markdown dictionary.
- `transfer.py`: plan pull/push, then dispatch through the resolved provider enum.
- `item.py`: live provider-backed items and runtime hierarchy snapshots.
- `clients/` and `auth/`: provider API and credential behavior.
- `commands/`: CLI input, saved descriptor selection, editing, and diagram invocation.

The descriptor layer has no `dplib` dependency. Provider/runtime traversal is
separate because it represents live remote state rather than authored metadata.
Diagram generation also remains offline: it expands local `$ref` descriptors and
resolves known provider URLs in memory but does not authenticate or transfer data.

## Documentation

- [Descriptor diagrams](diagram.md)
- [CLI reference](cli.md)
- [Python API](api.md)
- [Google authentication](google-auth.md)
- [Next steps](next-steps.md)

```bash
poe docs-update
poe docs-check
poe docs-build
poe docs-serve
```

Install Poe with `uv tool install poethepoet==0.48.0`, or run a task using
`uvx --from poethepoet==0.48.0 poe <task>`.

## Package releases

See the [release workflow and retry instructions](https://github.com/mbkranz/sharedrive#package-releases).
Use `poe release-check` to validate and `poe build` to build locally.
