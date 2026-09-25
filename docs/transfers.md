# Transfers

`pull` reads one remote source into each artifact's local `path`. `push`
publishes the file at `path` to **every** target. They are separate steps:
Fileroute neither streams directly between providers nor transforms files.

```bash
uv run fileroute pull config/fileroute.yaml --dry-run
uv run fileroute push config/fileroute.yaml --dry-run
```

See the exact [`pull`](cli.md#fileroute-pull) and
[`push`](cli.md#fileroute-push) options in the CLI reference.

| Direction | SharePoint | Google Drive | S3 |
| --- | --- | --- | --- |
| Remote source for `pull` | Supported | Supported | Supported |
| Publication target for `push` | Supported | Supported | Not implemented |

Descriptors and diagrams may include unsupported publication targets. However,
`push --dry-run` also fails planning if **any** target is unsupported; it
does not partially publish to the supported targets. To publish without S3,
use a separate descriptor or remove that target.
Offline `resolve`, `diagram`, and dry runs do not authenticate or inspect remote
permissions. `resolve --online` reads provider metadata to verify locations.

## Push

- Catalog targets identify existing remote folders. Children inherit them
  using paths relative to the declaring catalog's `path` (or the working
  root when the catalog has no path).
- An explicit child `targets` list replaces inherited targets. `targets: []`
  disables publication for it and its descendants unless a descendant
  declares its own targets.
- A catalog with children publishes only those children. A leaf catalog with
  a path publishes the files in its directory tree.
- An explicit resource target is an exact file URL. SharePoint URLs can rename
  the file. Google Drive file URLs update that exact ID and keep its remote
  name; the target must already exist. Set `entityType: Directory` to append
  the local filename to a folder target. Google Drive `/folders/ID` URLs are
  recognized as folder targets without an explicit entity type.
- Multiple targets receive multiple copies. Conflicting files aimed at one
  destination fail before transfer.

The planner validates local inputs and target support before authentication.
SharePoint execution creates missing child folders and creates/replaces files;
it never deletes remote files. Files above Microsoft Graph's 250 MB
single-request limit fail planning. A dry run cannot verify remote folders,
access, or permissions.
For a folder target enriched by `resolve --online --write`, push uses its
`driveId` and `serviceId` directly. An exact file target uses its parent folder
path on SharePoint. Google Drive instead updates the saved file ID directly.

### Google Drive publication

The destination folder must exist. A folder target creates missing child
folders, then creates or replaces the named binary file. A repeated push
updates the same file ID. Google Drive permits duplicate names in one folder;
Fileroute refuses to choose among duplicate files or folders. Use an exact
file URL when you need to disambiguate an existing file. Native Google Docs,
Sheets, shortcuts, and folders cannot be replaced with binary file content.
Remote files absent from the plan are never deleted.

```yaml
catalogs:
  - path: docs/_output
    targets:
      - path: https://drive.google.com/drive/folders/FOLDER_ID
    resources:
      - path: docs/_output/reports/summary.pdf
```

For a path-based target, specify the namespace explicitly, for example
`path: Reports`, `serviceType: GoogleDrive`, `drive: My Drive`, and
`entityType: Directory`. A shared drive name or `driveId` works too. The
planner remains offline: `--dry-run` can show the folder and relative file
path, but only execution can tell whether a file will be created or updated.
Small files use multipart upload; files above 5 MiB use a streaming resumable
upload. Existing Google OAuth credentials need write access to the target;
the configured `drive` scope covers arbitrary accessible files, while
`drive.file` is limited to files the app created or the user opened with it.

## Pull

A leaf catalog can retrieve a remote directory; a catalog with children
retrieves only those children. Targets never affect retrieval. Multiple
sources may describe a transformation, so `pull` refuses to choose among
them. A local `.qmd` source is provenance, not a download request: build
the artifact using its authoring tool, then push it.

Pull dry runs plan remote directories as units. Execution enumerates the
remote files and checks their paths before writing; it replaces existing
local files at planned paths. Use separate retrieval and publication
catalogs when their source semantics differ.
Pull prefers a saved `serviceId`, then a provider-scoped `remotePath`, then a
URL. Online resolution therefore makes subsequent pulls more precise without
requiring IDs when authoring a descriptor.

See [Use cases](use-cases.md) for runnable and metadata-only examples.
