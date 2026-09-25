# Fileroute

Describe local artifacts, their upstream sources, and their publication
destinations in one versioned YAML or JSON descriptor. Fileroute can inspect
that descriptor offline, draw a workflow diagram, pull remote inputs, and push
to supported targets.

**Today:** pull from SharePoint, Google Drive, or S3; push to SharePoint or
Google Drive. S3 targets can be described and diagrammed, but `push` rejects
an upload plan containing one.
Fileroute does not render source documents or stream between providers.

## Start with a descriptor

Install it in a project with `uv add fileroute`, or run the CLI without adding
a dependency using `uvx fileroute`. The project installation is appropriate
when Python code also imports Fileroute or automation must use a locked version.

```yaml
resources:
  report:
    path: artifacts/report.csv
    sources:
      - path: s3://my-bucket/exports/report.csv
```

Save this as `config/fileroute.yaml` and preview the transfer:

```bash
uvx fileroute pull config/fileroute.yaml --dry-run
uvx fileroute diagram config/fileroute.yaml
```

These commands do not authenticate. Run `pull` without `--dry-run` when
credentials are configured and you want to download the file. See
[Descriptors](descriptors.md) for paths and references, [Use cases](use-cases.md)
for multi-destination examples, and [Transfers](transfers.md) for the exact
execution rules. The [README](https://github.com/mbkranz/fileroute#readme)
also gives a quick start for GitHub visitors.

## Go further

- [Descriptor diagrams](diagram.md): SVG, Mermaid, HTML, Markdown, and graph API.
- [Authentication](authentication.md): SharePoint, Google Drive, and S3 setup.
- [CLI reference](cli.md) and [Python API](api.md): generated from the code.
- [Contributing and releases](contributing.md): development, migration, CI.
