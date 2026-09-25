# Contributing and releases

Run the checks from a repository checkout:

```bash
uv tool install poethepoet==0.48.0
poe check
poe docs-check
poe docs-build
```

`poe docs-update` refreshes the generated CLI/API reference and the YAML
and Mermaid blocks on the [use-cases page](use-cases.md). Commit generated
changes with their source changes; `poe docs-check` detects stale output.
`poe docs-build` runs a strict MkDocs build; `poe docs-serve` previews it.
One-off task usage is `uvx --from poethepoet==0.48.0 poe <task>`.
Provider tests use mocks and do not establish live tenant access.

Field names draw on [Data Resource](https://datapackage.org/standard/data-resource/),
[DCAT](https://www.w3.org/TR/vocab-dcat-3/), and
[OpenMetadata Drive Service](https://docs.open-metadata.org/latest/main-concepts/metadata-standard/schemas/entity/services/driveservice).

## Keyed descriptor transition (0.2)

The keyed format is a breaking change. Use `fileroute migrate-format INPUT OUTPUT_DIR`
for the preceding named-list format and `$ref` links; see the
[descriptor migration guide](descriptors.md#migrate-the-old-format).
The normal loader has one canonical representation. Legacy interpretation stays
in the migration module. The release baseline is intentionally `0.2.0.dev0` so
the existing main-branch release workflow promotes it to `0.2.0`.

## Migrating older remote-location descriptors

The public descriptor models are `Catalog`, `Resource`, `Location`, and
`CatalogLink`. Legacy `Drive*` classes and artifact-level provider
fields, as well as the old `upload` alias and `set` command, are removed.
Use `path`, `sources`, `targets`, `resources`, and `catalogs`, with
provider information on a location. The new active descriptor is stored at
`.fileroute/descriptor`; run `activate` again after upgrading.

Normal loading does not convert legacy descriptors. Migrate explicitly to a
**new** file, keeping the input intact:

```bash
fileroute migrate old.yaml new.yaml --direction pull
fileroute migrate old.yaml new.yaml --direction push
```

Choose the direction matching the intended legacy remote URLs; review the
result before using it for transfers. The generated [CLI reference](cli.md)
lists the current commands.

## Package releases

`poe release-check` validates code and generated docs; `poe build` creates
a local wheel and source distribution. Normal releases should not edit the
version manually. The `publish-to-pypi.yaml` workflow handles three jobs:
prepare, publish, and (on `main`) GitHub Release.

Pushes to `dev` begin or increment a patch prerelease
(`1.0.0 → 1.0.1.dev1 → 1.0.1.dev2`). Pushes to `main` promote a
prerelease or advance a stable patch. After a stable release, merge updated
`main` into `dev` as needed to align version baselines.

`poe release --branch dev --source <full-commit-sha>` prepares a release in
a clean checkout: it uses `uv version --no-sync`, builds distributions,
commits the version and creates an annotated tag locally. CI saves the
distributions before atomically pushing the commit and tag. The workflow
shares one release concurrency group across branches; a stale push fails
instead of overwriting newer work. Its `GITHUB_TOKEN` push does not
recursively trigger another push workflow.

PyPI uses Trusted Publishing bound to this repository and the exact
`publish-to-pypi.yaml` workflow filename. Stable `main` versions receive
a GitHub Release after PyPI succeeds. Branch rules must permit the version
commit. README-only changes publish the documentation site, not a new
package; PyPI displays the updated README at the next package release.

For a failed publish, rerun failed jobs on the **original Actions run**;
the job reuses its saved distributions. A full rerun locates the unexpired
artifact from the original source commit. An existing GitHub Release is
left intact. If the artifact expired or was deleted after the version push,
stop and recover the exact files; never rebuild an already published
version. If preparation failed before the atomic push, a new attempt may
rebuild the still-unpublished artifact.

`poe release-test` tests the helper with temporary local Git remotes and
real uv version changes; builds are mocked. No test publishes a package or
contacts cloud providers.

## Architecture

`models.py` owns the four descriptor models and provider enum;
`descriptor.py` handles local I/O, traversal, references, and offline
resolution. `diagram.py` and `diagram_reports.py` project that metadata
into a provider-neutral graph and SVG/HTML/Markdown outputs. `transfer.py`
plans and executes pull/push; `migration.py` performs explicit legacy
conversion. `item.py` models live provider-backed hierarchies, while
`clients/` and `auth/` own provider API and credentials and `commands/`
owns CLI interaction. Descriptor traversal is separate from live remote
traversal: diagramming does not authenticate or transfer data.

Runtime items expose `refresh()`, `children`, `get_path()`,
`iter_items()`, `iter_files()`, and `download()`.
`item.to_catalog()` emits local artifact paths and remote provenance.
Runtime paths are relative to the provider container; `get_path()` is
relative to the current item. Recursive traversal uses a snapshot until
refresh or mutation invalidates it.

## Roadmap

Live development-tenant validation is still needed for canonical descriptors;
mocked tests cannot prove access or real transfers. Future capabilities should
follow demand: S3 upload, SharePoint upload sessions for files
above 250 MB, descriptor discovery with a defined operator workflow, and a
small Rich terminal diagram view. Keep transformation tools such as Quarto
outside Fileroute.

After the first documentation deployment, evaluate Zensical in a separate PR
against the same pages, diagrams, and strict build checks before changing the
site generator.
