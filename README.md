# fileroute

Fileroute records where project artifacts come from, where they live locally,
and where they should be published. A YAML or JSON descriptor connects local
files to SharePoint, Google Drive, and S3 locations. Preview transfers without
credentials, visualize the relationships, pull remote inputs, and publish to
supported targets through the CLI or Python API. Python 3.11+ is required.

**Current transfer support:** SharePoint, Google Drive, and S3 can be pulled;
SharePoint and Google Drive can be pushed. S3 targets can be described and
diagrammed, but `push --dry-run` rejects them until upload support exists.
Fileroute does not transform files or transfer directly between cloud providers.

## Descriptor format change

Catalogs and resources are now keyed maps: `resources: {report: {path: report.csv}}`.
The map key is the registered name. Cross-file catalogs use
`catalogs: {archive: {descriptor: catalogs/archive.yaml}}`.
Run `fileroute migrate OLD_DESCRIPTOR NEW_DIRECTORY --dry-run` before
converting existing named lists and `$ref` links. See the
[migration guide](docs/descriptors.md#migrate-the-old-format).

## Get started

For a repeatable project workflow, add Fileroute as a dependency and commit the
descriptor and `uv.lock`:

```bash
uv add fileroute
uv run fileroute diagram config/fileroute.yaml
uv run fileroute pull config/fileroute.yaml --dry-run
```

For occasional CLI use, run the published tool in a separate environment:

```bash
uvx fileroute diagram config/fileroute.yaml
uvx fileroute pull config/fileroute.yaml --dry-run
```

`uv run` uses the project's dependencies and supports Python imports;
`uvx` does not install Fileroute into the project. Pin a version with
`uv add 'fileroute==X.Y.Z'` or
`uvx --from 'fileroute==X.Y.Z' fileroute --help`.
See uv's [project](https://docs.astral.sh/uv/concepts/projects/run/),
[dependency](https://docs.astral.sh/uv/concepts/projects/dependencies/), and
[tool](https://docs.astral.sh/uv/concepts/tools/) guides.

One supported workflow downloads a selected SharePoint file to a local artifact
and publishes it to two SharePoint destinations:

```yaml
resources:
  monthly-report:
    path: artifacts/monthly-report.csv
    sources:
      - path: https://contoso.sharepoint.com/sites/data/Shared%20Documents/monthly-report.csv
    targets:
      - path: https://contoso.sharepoint.com/sites/reports/Shared%20Documents/monthly-report.csv
      - path: https://contoso.sharepoint.com/sites/archive/Shared%20Documents/monthly-report.csv
  annual-report:
    path: artifacts/annual-report.csv
    sources:
      - path: https://contoso.sharepoint.com/sites/data/Shared%20Documents/annual-report.csv
    targets:
      - path: https://contoso.sharepoint.com/sites/reports/Shared%20Documents/annual-report.csv
```

Save this as `config/fileroute.yaml`, replace the example URLs, and run:

```bash
uv run fileroute resolve config/fileroute.yaml
uv run fileroute diagram config/fileroute.yaml
uv run fileroute list config/fileroute.yaml
uv run fileroute pull config/fileroute.yaml --select monthly-report --dry-run
uv run fileroute pull config/fileroute.yaml --select monthly-report
uv run fileroute push config/fileroute.yaml --select monthly-report --dry-run
uv run fileroute push config/fileroute.yaml --select monthly-report
```

Omit `--select` to process all eligible resources; selecting a catalog includes
its descendants. A selected pull uses its remote source, while a selected push
publishes to all effective targets.

`resolve` parses URLs and scoped paths offline; `resolve --online --write`
verifies remote locations and saves their IDs and entity types. `diagram` and
dry runs also work without provider access; `--online` and actual transfers
require credentials. Configure a reusable authentication profile, then select it
for a transfer or set `authProfile` on individual source/target locations:

```bash
uv run fileroute auth configure work --provider sharepoint
uv run fileroute auth login sharepoint --profile work
uv run fileroute pull config/fileroute.yaml --profile work
```

Configuration guides you through the selected provider's settings; cloud app
registration and access grants are still required. Profiles store settings and
credential references, with session tokens cached privately outside the project.
Existing environment and `.env` workflows remain supported. See
[Authentication](docs/authentication.md) for setup, selection precedence, and
the [profile template](examples/auth/profiles.yaml).

To publish a new file to Google Drive, target an existing folder; Fileroute
creates or replaces the file below it. An exact file URL instead replaces that
file by ID and preserves its existing name:

```yaml
resources:
  report:
    path: artifacts/report.csv
    targets:
      - path: https://drive.google.com/drive/folders/FOLDER_ID
      - path: https://drive.google.com/file/d/EXISTING_FILE_ID/view
```

The two targets receive separate copies. See [Transfers](docs/transfers.md)
for nested folders, shared drives, and ambiguous names.

## Documentation

- [Descriptor model, paths, references, and resolution](docs/descriptors.md)
- [More use cases, saved YAML, and generated diagrams](docs/use-cases.md)
- [Transfer behavior and provider support](docs/transfers.md)
- [Diagram formats and Python graph API](docs/diagram.md)
- [CLI reference](docs/cli.md) and [Python API](docs/api.md)
- [Development, migration, and package releases](docs/contributing.md)

The descriptor `path` is a local artifact for transfers. `sources` are
upstream inputs or provenance; `targets` are publication destinations.
For nested edits, `fileroute list` shows exact JSONPath selectors; the leading
`$` is optional when passing one to `list`, `update`, or `add --parent`.
Diagrams show intent, not a completed transfer. Use `fileroute --help` for
commands and options. To develop this repository, run `uv sync` and see the
[contributor guide](docs/contributing.md).

### HTTP retries and upload recovery

Fileroute retries transient Microsoft Graph reads and content PUTs up to three times,
respecting `Retry-After` when supplied. A content PUT reopens the local file on
each attempt. Folder-creation POSTs are not automatically replayed after an
uncertain result. Google Drive resumable uploads query the upload session after
a transient or rate-limit error and continue from the byte offset confirmed by
the server. Network and HTTP failures retain their provider-specific exception
types and expose `status_code`, `response_text`, `response_json`, and
`response_headers` for callers that need details.

## Root catalogs and profile schemas

Use `catalog.yaml` (also `.yml` or `.json`) as a project resource inventory.
Discovery checks an explicit argument, then the activated descriptor, then a root
catalog, then `resources/descriptor.*`. A stale activation remains an error;
Fileroute does not search parent directories.

`Catalog.resourcePathTemplate` describes an artifact naming pattern relative to `path`.
`pathTemplate` is also accepted as an input alias. During `resolve`, Fileroute
uses the [`parse`](https://pypi.org/project/parse/) library to extract named
fields into each resource's `pathProperties` (`resource.path_properties` in
Python):

```yaml
profile: fileroute-catalog
targets: []
catalogs:
  surveys:
    path: data
    resourcePathTemplate: "{surveyid}/{env}/v{version}/schema.json"
```

Absolute paths and URI templates match the whole resource path. For example:

```yaml
pathTemplate: "drive://test/{var1}/{var2}/test.csv"
resources:
  file1:
    path: "drive://test/xx/yy/file1.csv"
```

`fileroute resolve catalog.yaml` returns `pathProperties: {var1: xx, var2: yy}`
for `file1`. A literal filename is a naming example: when it differs, directory
fields are still extracted. If the filename contains a field, the complete
template must match. Use `{filename}` to extract varying filenames. Matching is
case-sensitive, normalizes path separators, and supports `parse` format types
such as `{year:d}`. Named fields supply metadata; anonymous fields do not.

The nearest catalog template applies to its resources and descendants. Relative
templates use the declaring catalog's `path`; child templates replace inherited
ones. Resolution replaces computed properties, clearing them to `{}` for a
nonmatching path, so old folder metadata cannot survive a path change. Without
an applicable template, authored `pathProperties` are preserved. Loading alone
does not calculate properties; use `resolve` or `fileroute resolve --write` to
calculate or persist them. Saves use the canonical `resourcePathTemplate` name;
conflicting values for its two input names are rejected.

Transfers do not expand placeholders, discover matching files, or replace
explicit resource paths. Extracted properties are metadata, not authorization
or approval decisions. Select publication scope explicitly, for example
`fileroute push catalog.yaml --select documentation`.

`poe schema-export` generates `dist/schemas/fileroute-catalog.schema.json` from
the models. Stable releases attach that exact version's schema as a GitHub asset.
Associate YAML with a released schema using this editor comment (replace VERSION):

```yaml
# yaml-language-server: $schema=https://github.com/mbkranz/fileroute/releases/download/vVERSION/fileroute-catalog.schema.json
profile: fileroute-catalog
```

Keep `profile` as document data; `$schema` is only in the editor comment.
Schema validation covers structural constraints. Runtime checks still validate
case-insensitive name collisions, descriptor paths and cycles, filesystem state,
and operational transfer requirements. Service aliases normalized at runtime may
need canonical spelling for editor validation.

Release preparation saves schemas under `dist/schemas/` alongside distributions;
PyPI receives only distributions. Retries reuse the saved release artifact and
refuse to replace an existing GitHub schema asset with different bytes.

## License

Fileroute's code and generated profile schemas are covered by the MIT license
in `LICENSE`. Resources referenced by user catalogs retain their own terms.
