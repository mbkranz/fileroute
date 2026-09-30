# Descriptors

A descriptor is a local YAML or JSON `Catalog`. Its `catalogs` and `resources`
are **lists of named entries**. `CatalogLink` composes another catalog file through `descriptor`.
`Location` records upstream provenance or a publication destination.

```yaml
profile: fileroute-catalog
catalogs:
  - name: documentation
    basePath: docs/_output
    targets:
      - path: https://contoso.sharepoint.com/sites/dev/Shared%20Documents/Docs
        serviceType: SharePoint
    resources:
      - name: guide
        path: guide.docx
        sources:
          - path: docs/guide.qmd
      - name: internal
        path: internal.docx
        targets: []
  - name: research
    descriptor: catalogs/research.yaml
```

Build the guide with Quarto before pushing it. `internal` opts out of publication.
The research file is another catalog document. Its parent link registers it as
`research` without changing its root name. Root descriptors may omit `name`.

## Fields and identity

| Field | Meaning |
| --- | --- |
| `profile` | Optional catalog profile label (defaults to `fileroute-catalog`) |
| `catalogs`, `resources` | Lists of entries with explicit child `name` |
| `descriptor` | Link to another **local catalog document**, relative to the containing file |
| Catalog `basePath` | Directory relative to the parent catalog base |
| Resource `path` | Local artifact relative to its containing catalog base |
| Resource `pathTemplate` | Parameterized artifact location; applications supply concrete paths before transfer |
| `sources`, `targets` | Ordered location lists; missing/null targets inherit, `[]` opts out |
| Location `path` | Local provenance, remote path, or clickable URL |
| `serviceType` | Location provider: `GoogleDrive`, `SharePoint`, or `S3` |
| `serviceId`, `entityType` | Provider-native item ID and file/directory/container type |
| `site`, `siteId`, `drive`, `driveId`, `bucket` | Provider namespace metadata |
| `remotePath` | Path within the provider namespace, filled by offline resolution |

Names match `[A-Za-z_][A-Za-z0-9_-]*`. Use `title` for spaces and punctuation.
Names are case-insensitive for lookup and cannot collide within one parent,
including across its resource and catalog lists. Names reused under different
parents are addressed by a qualified name such as `documentation.guide`.
A bare name works when unique. Links cannot mix `descriptor` with inline fields.

Unknown extension metadata survives round trips. YAML comments, styles, and
entry order are preserved where practical; exact whitespace is not guaranteed.
Custom tags such as `!include` are rejected. `profile` is a label, not a
network-fetched schema. Legacy descriptors are not supported. The format uses Data Package and
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
fileroute add appendix --parent documentation --path appendix.docx
```

The positional argument of `list` and `resolve` is always a descriptor **file**;
`--select` chooses an entry within it. `list` shows exact names, qualified names,
structural selectors, and origin files. JSON retains the `entities`, `selectors`,
and `locations` arrays and adds registered names, kinds, and source addresses.
A catalog can involve several services, so it has no single provider identity.
`pull` and `push` also accept `--select` for one resource or catalog. A catalog
includes its descendants, including linked catalogs; locations cannot be
transferred individually. Push retains ancestor targets and path anchors.

Exact JSONPath and JSON Pointer addresses remain available:

```bash
fileroute list --select 'documentation.guide'
fileroute update --select '$.catalogs[0].resources[0].targets[0]' --drive-id 'b!ABC'
fileroute list --select '/catalogs/0/resources/0'
```

The target-edit example requires an authored target on `guide`; inherited targets
must be edited on their owner. The leading `$` is optional. Quoted keys such as
`$['resources'][0]` work. `sources` and `targets` use numeric
indices and must be the final step. Wildcards, filters, projections, and arbitrary
extension-field selection are not supported. Use `fileroute list` to copy exact
addresses. Names remain stable when lists are reordered; structural indices do not.
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

## Root catalogs and path templates

A root `catalog.yaml` (also `.yml` or `.json`) is discovered after an explicitly
activated descriptor and before `resources/descriptor.*`. Explicit arguments win.
No parent-directory search is performed, and stale activations remain errors.

`pathTemplate` on a named Resource describes a parameterized location relative
to the enclosing catalog's effective `basePath`. Exactly one of `path` and
`pathTemplate` is required. Applications interpret placeholders and supply
concrete resources before transfer; Fileroute does not expand or match them.
Direct transfer of a template fails. A template participating in a parent
transfer fails the whole plan; unrelated and opted-out templates do not.

Stable releases attach `fileroute-catalog.schema.json`, generated from these
models. Associate its versioned GitHub asset URL using a YAML language-server
comment, retaining `profile: fileroute-catalog` as document data. Editor validation
covers structural constraints; runtime checks still cover name collisions,
local descriptor validity, filesystem state, and operational requirements.
