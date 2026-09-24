# Transfers

`pull` reads one remote source into each artifact's local `path`. `push`
publishes the file at `path` to **every** target. They are separate steps:
Fileroute neither streams directly between providers nor transforms files.

| Direction | SharePoint | Google Drive | S3 |
| --- | --- | --- | --- |
| Remote source for `pull` | Supported | Supported | Supported |
| Publication target for `push` | Supported | Not implemented | Not implemented |

Descriptors and diagrams may include unsupported publication targets. However,
`push --dry-run` also fails planning if **any** target is unsupported; it
does not partially publish to the supported targets. To publish just the
SharePoint copy now, use a separate descriptor or remove the other targets.
`resolve`, `diagram`, and dry runs do not authenticate or inspect remote
permissions.

## Push

- Catalog targets identify existing remote folders. Children inherit them
  using paths relative to the declaring catalog's `path` (or the working
  root when the catalog has no path).
- An explicit child `targets` list replaces inherited targets. `targets: []`
  disables publication for it and its descendants unless a descendant
  declares its own targets.
- A catalog with children publishes only those children. A leaf catalog with
  a path publishes the files in its directory tree.
- An explicit resource target is an exact file URL (and can rename the file).
  Set `entityType: Directory` on a target to append the local filename to
  a folder URL.
- Multiple targets receive multiple copies. Conflicting files aimed at one
  destination fail before transfer.

The planner validates local inputs and target support before authentication.
SharePoint execution creates missing child folders and creates/replaces files;
it never deletes remote files. Files above Microsoft Graph's 250 MB
single-request limit fail planning. A dry run cannot verify remote folders,
access, or permissions.

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

See [Use cases](use-cases.md) for runnable and metadata-only examples.
