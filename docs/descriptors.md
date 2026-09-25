# Descriptors

A descriptor is a local YAML or JSON `Catalog`. Its `catalogs` and `resources`
are **keyed maps**: each key is the registered name, with no repeated `name`
field. `CatalogLink` composes another catalog file through `descriptor`.
`Location` records upstream provenance or a publication destination.

```yaml
$schema: fileroute-catalog
catalogs:
  documentation:
    path: docs/_output
    targets:
      - path: https://contoso.sharepoint.com/sites/dev/Shared%20Documents/Docs
        serviceType: SharePoint
    resources:
      guide:
        path: docs/_output/guide.docx
        sources:
          - path: docs/guide.qmd
      internal:
        path: docs/_output/internal.docx
        targets: []
  research:
    descriptor: catalogs/research.yaml
```

Build the guide with Quarto before pushing it. `internal` opts out of publication.
The research file is another catalog document with the same keyed structure;
its root is registered as `research` by the parent key. It does not repeat that
name. Root descriptors may have a `title` for display.

## Fields and identity

| Field | Meaning |
| --- | --- |
| `catalogs`, `resources` | Maps of registered names to catalogs/links or resources |
| `descriptor` | Link to another **local catalog document**, relative to the containing file |
| Catalog/resource `path` | Local artifact or directory, relative to the transfer working root |
| `sources`, `targets` | Ordered location lists; missing/null targets inherit, `[]` opts out |
| Location `path` | Local provenance, remote path, or clickable URL |
| `serviceType` | Location provider: `GoogleDrive`, `SharePoint`, or `S3` |
| `serviceId`, `entityType` | Provider-native item ID and file/directory/container type |
| `site`, `siteId`, `drive`, `driveId`, `bucket` | Provider namespace metadata |
| `remotePath` | Path within the provider namespace, filled by offline resolution |

Names match `[A-Za-z_][A-Za-z0-9_-]*`. Use `title` for spaces and punctuation.
Names are case-insensitive for lookup and cannot collide within one parent,
including across its resource and catalog maps. Names reused under different
parents are addressed by a qualified name such as `documentation.guide`.
A bare name works when unique. Links cannot mix `descriptor` with inline fields.

Unknown extension metadata survives round trips. YAML comments, styles, and
mapping order are preserved where practical; exact whitespace is not guaranteed.
Custom tags such as `!include` are rejected. `$schema` is an optional profile
label, not a network-fetched schema. The format uses Data Package and
OpenMetadata vocabulary but is not an implementation of their full schemas.

## Discover and select

`activate` selects a descriptor file. Explicit paths override the active/default
file. Use registered names for routine operations:

```bash
fileroute activate config/fileroute.yaml
fileroute list
fileroute list --kind resource --format json
fileroute list --select documentation.guide
fileroute resolve --select documentation.guide
fileroute update --select documentation.guide --title "Guide"
fileroute add appendix --parent documentation --path docs/_output/appendix.docx
```

The positional argument of `list` and `resolve` is always a descriptor **file**;
`--select` chooses an entry within it. `list` shows exact names, qualified names,
structural selectors, and origin files. JSON retains the `entities`, `selectors`,
and `locations` arrays and adds registered names, kinds, and source addresses.
A catalog can involve several services, so it has no single provider identity.

Exact JSONPath and JSON Pointer addresses remain available:

```bash
fileroute list --select 'catalogs.documentation.resources.guide'
fileroute update --select '$.catalogs.documentation.resources.guide.targets[0]' --drive-id 'b!ABC'
fileroute list --select '/catalogs/documentation/resources/guide'
```

The target-edit example requires an authored target on `guide`; inherited targets
must be edited on their owner. The leading `$` is optional. Quoted keys such as
`$['resources']['monthly-report']` work. `sources` and `targets` use numeric
indices and must be the final step. Wildcards, filters, projections, and arbitrary
extension-field selection are not supported. Use `fileroute list` to copy exact
addresses. Names remain stable when map order changes; location indices do not.
Registered names take precedence over rootless structural syntax. Use the explicit
`$`/Pointer address shown by `list` to request a structural address unambiguously.

## Linked files and write boundaries

Links resolve relative to each containing file and cannot escape its directory.
Canonical paths detect cycles, including symlink aliases. Reusing the same child
under different registered names is allowed. Artifact paths remain relative to
the **transfer working root** even inside linked documents. Transfers reject
escaping paths and symlinks as before.

Normal load/save keeps links authored. Read-only listing and planning expand
links and retain the physical origin of every entry. Expanded views cannot be
saved directly. `list` reports the source-file selector for linked entries.
`update` and `add --parent` operate on the chosen physical file: use the child
file explicitly to edit inside it.

`resolve FILE --write` edits only FILE and preserves child links.
`resolve FILE --select NAME --write` edits only the selected entry's **origin
file**, identified in the JSON response. A selected catalog writes its authored
subtree while preserving deeper links. Inherited targets are reported as
`effectiveTargets` but are never copied onto a child by write-back.

## Location resolution

`resolve` previews normalized JSON offline. It parses supported `s3://`, Google
Drive/Docs, and SharePoint URLs while preserving the authored path/URL. A scoped
remote path needs a provider and namespace:

```yaml
path: Reports/a.docx
serviceType: SharePoint
site: PPSC
drive: Shared Documents
```

For Google Drive use `drive` or `driveId`; for S3 use `bucket`. Offline resolution
fills `remotePath` and rejects conflicting metadata. Local provenance and ordinary
web citations may be provider-less sources. Unrecognized remote targets require
explicit provider information; resolution never searches all sites/drives to guess.

```bash
fileroute resolve config/fileroute.yaml
fileroute resolve config/fileroute.yaml --online --write
fileroute resolve config/fileroute.yaml --select documentation.guide --online
```

`--online` uses configured credentials to verify remote metadata; it never transfers
content. Offline resolution and `list` do not authenticate. Selected output separates
`artifactPath`, `referenceDescriptor`, origin file, and reference chain. Provider
IDs remain optional; transfer planning resolves relevant locations in memory.
An S3 directory prefix should end in `/` to distinguish it from an object key.

## Migrate the old format

This is a breaking descriptor-format change. Normal loading accepts keyed maps
and `descriptor` links. For the previous named lists and `$ref` syntax:

```bash
fileroute migrate-format config/fileroute.yaml migrated/ --dry-run
fileroute migrate-format config/fileroute.yaml migrated/
fileroute list migrated/fileroute.yaml
```

The command converts the whole linked file graph, preserves separate child files,
and reports every source/output path, registered name, and link. Output must be
a new directory. Conversion stages and validates all files before publishing the
directory atomically; inputs remain untouched. Shared children are written once.
Missing, invalid, or colliding names produce a file/entry error; edit those names
explicitly and rerun. URI fragments are not silently translated. Root `name`
metadata becomes `title` when no title was supplied.

Artifact paths are **not rebased** into the migration directory. Run transfers
from the original project working root or supply `root` through the Python API.
The separate `migrate OLD NEW --direction pull|push` command continues to convert
older `_cache`/remote-location descriptors into the new keyed format, flattening
those older references as documented by that command.

## Python API

```python
from fileroute import Catalog, Resource, Location
from fileroute.descriptor import save, load, find, resolve_selection

catalog = Catalog(resources={
    "guide": Resource(
        path="docs/guide.docx",
        sources=[Location(path="docs/guide.qmd")],
        targets=[Location(
            path="https://contoso.sharepoint.com/sites/dev/Docs/guide.docx",
            serviceType="SharePoint",
        )],
    ),
})
save(catalog, "config/fileroute.yaml")
assert find(load("config/fileroute.yaml"), "guide").path == "docs/guide.docx"
selection = resolve_selection("config/fileroute.yaml", "guide")
print(selection.as_dict())
```

Python attributes use snake_case; descriptor location fields use camelCase.
`location.service_type` is the authoritative enum after resolution. See the
generated [API reference](api.md), [CLI reference](cli.md), [Transfers](transfers.md),
and [Diagrams](diagram.md).
