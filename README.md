# fileroute

Fileroute records where project artifacts come from, where they live locally,
and where they should be published. A YAML or JSON descriptor connects local
files to SharePoint, Google Drive, and S3 locations. Preview transfers without
credentials, visualize the relationships, pull remote inputs, and publish to
supported targets through the CLI or Python API. Python 3.11+ is required.

**Current transfer support:** SharePoint, Google Drive, and S3 can be pulled;
only SharePoint can be pushed. Google Drive and S3 targets can be described and
diagrammed, but even `push --dry-run` rejects them until upload support exists.
Fileroute does not transform files or transfer directly between cloud providers.

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

One supported workflow downloads a SharePoint file to a local artifact and
publishes that artifact to two SharePoint destinations:

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

Save this as `config/fileroute.yaml`, replace the example URLs, and run:

```bash
uv run fileroute resolve config/fileroute.yaml
uv run fileroute diagram config/fileroute.yaml
uv run fileroute pull config/fileroute.yaml --dry-run
uv run fileroute pull config/fileroute.yaml
uv run fileroute push config/fileroute.yaml --dry-run
uv run fileroute push config/fileroute.yaml
```

`resolve`, `diagram`, and dry runs do not authenticate or contact providers;
actual pull/push operations require access. Configure credentials using
[.env-sample](.env-sample); see [Authentication](docs/authentication.md) for
provider setup.

## Documentation

- [Descriptor model, paths, references, and resolution](docs/descriptors.md)
- [More use cases, saved YAML, and generated diagrams](docs/use-cases.md)
- [Transfer behavior and provider support](docs/transfers.md)
- [Diagram formats and Python graph API](docs/diagram.md)
- [CLI reference](docs/cli.md) and [Python API](docs/api.md)
- [Development, migration, and package releases](docs/contributing.md)

The descriptor `path` is a local artifact for transfers. `sources` are
upstream inputs or provenance; `targets` are publication destinations.
Diagrams show intent, not a completed transfer. Use `fileroute --help` for
commands and options. To develop this repository, run `uv sync` and see the
[contributor guide](docs/contributing.md).
