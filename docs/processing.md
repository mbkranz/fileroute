# DOCX and Markdown review documents

Fileroute infers one built-in document conversion from the descriptor: pull a
DOCX source into a Markdown local `path`, then push that review to DOCX targets
with native Word comments and named paragraph/character styles.
The YAML header stores comment text and reviewer attributes, observed style names,
and hashes of retained files. Explicit markers in the body record comment ranges.
The original DOCX supplies the full style definitions and remains available for
comparison and recovery.

This is a semantic round trip for a defined subset, not a guarantee of identical
Word bytes, pagination, or arbitrary layout. The implementation delegates document
reading/writing to [Pandoc](https://pandoc.org/MANUAL.html), using its
[custom styles](https://pandoc.org/MANUAL.html#custom-styles) and native comment
representation. No workflow engine, registry, or descriptor hooks are involved.

## Pull, edit, push

Install **Pandoc 3.1 or newer** separately and put `pandoc` on `PATH`. Conversion
does not download it. Integration tests currently exercise Pandoc 3.1.3. The base
Fileroute installation supplies the Python dependencies; there is no conversion
extra. Other Fileroute commands and command help work without Pandoc installed.

```yaml
resources:
  report:
    path: review/report.md
    sources:
      - path: https://tenant.sharepoint.com/sites/review/Docs/report.docx
    targets:
      - path: https://tenant.sharepoint.com/sites/review/Docs/revised.docx
```

```bash
fileroute pull catalog.yaml --select report --dry-run
fileroute pull catalog.yaml --select report
# Edit review/report.md, preserving its header, style attributes, and markers.
fileroute push catalog.yaml --select report --dry-run
fileroute push catalog.yaml --select report
```

The source/path pair selects DOCX → Markdown on pull; the path/target pair selects
Markdown → DOCX on push. No separate conversion commands or processing fields are
needed. Dry runs show conversions inferred offline without requiring Pandoc or
authentication. Opaque URLs can declare `format: docx` on the location. During
execution, source item names/provider export metadata and exact Google Drive target
names also resolve file formats. For a folder target, set `format: docx` to publish
`report.docx`; otherwise the local filename/bytes are used.

Use `--format json` for transfer receipts with source/input and output hashes and
the actual conversion. Pull also reports detected unsupported features. Conversion
uses a positive finite 120-second timeout per Pandoc call. The lower-level typed
request models allow callers to choose another timeout.

Import creates a sibling directory such as
`review/report.assets/<source-sha256>/`. It contains `original.docx` and extracted
images. Keep this directory with the Markdown when copying or versioning the
review. Paths in the header and body are relative to the Markdown file's directory.
Different source versions receive different bundles; repeated imports verify and
reuse the same bundle. Do not edit retained files or their hashes. Re-import a
revised DOCX to start from a new original.

Markdown → DOCX requires the `fileroute-review-v1` header and its retained files.
It does not export arbitrary plain Markdown. Start a review by importing a DOCX,
including a suitable starter DOCX for a new document. Automatic transfers use
remote DOCX and local Markdown. Remote Markdown review bundles would need their
retained files transferred too; the reverse layout is rejected rather than
publishing or acquiring an incomplete review.

## Review format

The following illustrates the header and body. `<sha256>` abbreviates actual
64-character digests and path components; import writes their complete values.

```yaml
---
docx_profile: fileroute-review-v1
schema_version: 1
source:
  path: report.assets/<sha256>/original.docx
  sha256: <sha256>
reference_docx:
  path: report.assets/<sha256>/original.docx
  sha256: <sha256>
converter: pandoc 3.1.3
styles:
  PolicyBlock: Policy Block
  PolicyTerm: Policy Term
comments:
  c7:
    text: Clarify scope.
    author: Reviewer
    date: '2026-11-16T12:00:00Z'
    initials: RV
assets: {}
unsupported: []
properties: {}
---
```

```markdown
::: {custom-style="Policy Block"}
A []{#docx-c7-start .docx-comment-start}[special]{custom-style="Policy Term"}[]{#docx-c7-end .docx-comment-end} sentence.
:::
```

- Edit comment payloads in `comments`. Text supports one inline Markdown
  paragraph; images, notes, raw markup, and multiple paragraphs are rejected.
  Author, date, and initials become native Word comment attributes.
- Keep each comment's empty start/end spans exactly once, with the start preceding
  its end. Move the pair deliberately to change the range. Repeated wording does
  not matter: the exporter never guesses an attachment by matching text.
- To delete a comment, remove its header entry **and both markers**. To add one,
  choose an unused `c` followed by decimal digits and add the entry and marker pair.
  Missing, duplicated, reversed, or unknown markers stop export.
- `styles` records observed Word IDs and names for inspection. Style assignments
  live on body spans/divs, and full definitions live in `reference_docx`. Editing
  the header's style map does not create styles. Export rejects names absent from
  the reference DOCX.
- `assets` maps image paths to SHA-256 hashes. Body image links must identify
  declared, unchanged local assets. Export does not retrieve remote images.
- `properties` preserves Pandoc's native JSON metadata. It is for retained document
  properties, not application configuration; malformed metadata stops export.

Comment IDs are stable within an edited Markdown review. Pandoc may renumber
native Word IDs when writing a DOCX; re-import can therefore produce new keys.
The contract concerns comment content, attributes, and attachment to the marked
range, not identical internal IDs.

## Supported subset and limits

The profile supports ordinary paragraphs/headings, emphasis, links, lists,
ordinary tables, named paragraph/character styles, inline comments, and embedded
PNG/JPEG/GIF images. It preserves semantic structure through Pandoc Markdown;
table appearance, direct formatting, drawing placement, and page layout are not
equivalent to Word's full document model.

Import reports known unsupported features in `unsupported`, including tracked
changes, fields, content controls, text boxes, floating drawings, merged table
cells, multiple sections, direct formatting outside the profile, embedded objects,
charts/diagrams, complex or multi-paragraph comments, threaded/resolved comments,
and other media formats. Detection is conservative and does not certify arbitrary
Word documents as lossless. The retained original is the recovery source.

Export blocks reported unsupported features and independently checks the retained
original/reference again. Deleting the YAML list cannot bypass this check. Resolve
these features in Word and import again. Tracked changes are neither silently
accepted nor rejected. Externally linked resources other than ordinary hyperlinks
must be embedded before import. Pandoc errors or conversion warnings stop the
operation rather than being hidden.

The profile replaces the experimental generic `convert`/`render` interfaces,
`ConversionRequest`, `convert_document`, `render_document`, and mapping/template
helpers. MarkItDown, the `markdown` extra, legacy DOC/LibreOffice normalization,
PDF extraction, and Jinja rendering are removed from this surface. Application
specific extraction or templating belongs in the application that needs it.

## Python API and operation boundaries

Python callers use the same plans and operations as the CLI:

```python
from pathlib import Path
from fileroute.transfer import plan_pull, plan_push, pull, push

root = Path.cwd()
catalog = root / "catalog.yaml"
acquired = pull(plan_pull(catalog, root=root, selector="report"))
# The application chooses when review is complete.
published = push(plan_push(catalog, root=root, selector="report"))
```

Typed request/result/profile models and ordinary conversion functions remain
together in `fileroute.conversion`, independently callable from Python. Transfer
operations adapt those functions for this fixed format pair. Provider clients only
move bytes. There is no configurable processor registry or replacement pipeline
language. Constructing a conversion request validates arguments without filesystem,
tool, or provider calls. Operations validate actual files before conversion. Source and retained
input aliases, including symlinks/hardlinks, cannot be selected as output.
Retained asset paths must stay within the Markdown directory and cannot use
symlinks. Original and media hashes are checked before and after export.

Completed files atomically replace one output through a temporary sibling.
Failures preserve the previous output. Import publishes an immutable asset bundle
before replacing the Markdown; a failed final write may leave an unreferenced
bundle. These separate writes are not a transaction. Operations preserve their
inputs and reject observed source changes during conversion.

## Catalog and transfer boundaries

Catalogs describe artifacts and locations; pull/push own the built-in review
conversion and byte transfer. The application owns custom transformations and
the decision to publish. The DOCX/Markdown review needs only one resource entry.
For an editable review, retain the Markdown together with its referenced asset
directory, not just the `.md` file.

Transfer paths use the working root, independently of descriptor locations.
Discovery inventories
source files without producing derived artifacts or changing targets. Provider
native exports, such as Google documents exported as DOCX, remain acquisition
behavior; a `format` label never converts bytes.

The runtime Poe adapter, `steps`/`process` commands, `Catalog.pipelines`/`Pipeline`,
and source/target processing hooks are removed. Catalog `pipelines` and location
`afterPull`, `after_pull`, `beforePush`, and `before_push` are rejected with migration
guidance. Unrelated metadata extensions remain supported.

The conversion-generating `add --to markdown` and `discover --to markdown` options,
and their Python `to`/`to_markdown` arguments, are also removed. Existing resource
entries, paths, targets, and files are not automatically rewritten or deleted.
Poe remains the separately installed repository development task runner. The
[scope plan](plans/function-first-processing.md) records why a generic processing
runtime is deferred.
