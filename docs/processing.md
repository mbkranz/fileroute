# Endpoint processing

A source's `afterPull` runs immediately after its bytes are downloaded and before
the local artifact is replaced. A target's `beforePush` runs before publication
to that target. The canonical local artifact remains unchanged during push.

```yaml
resources:
  document:
    path: acquired/document.md
    sources:
      - path: https://drive.google.com/file/d/EXISTING_FILE_ID/view
        serviceType: GoogleDrive
        afterPull:
          params:
            input:
              default: "${FILEROUTE_INPUT}"
            output:
              default: "${FILEROUTE_OUTPUT}"
          steps:
            - cmd: fileroute steps convert --input "${input}" --output "${output}"
```

`steps` is an alias for Poe's native `sequence`; `params` is an alias for native
`args`. Both native spellings are accepted. Supplying both aliases on one task
is an error. Fileroute imports Poe's validation and schema rather than defining
another task language. A hook must be a sequence, including a single command.
Arguments belong on the outer sequence: native Poe disallows `args` on inline
sequence children. Use explicit `cmd` entries; native string entries refer to
named tasks and must resolve.

The complete sequence is run by the same Python environment's native Poe CLI in
a child process. An explicit temporary configuration in the execution root
preserves Poe's root-relative file semantics and is removed afterward. Commands
are catalog-authored executable code. Loading, schema validation, diagramming,
and transfer dry runs do not execute them.

## Arguments and file handoffs

Each invocation supplies `FILEROUTE_INPUT`, `FILEROUTE_OUTPUT`,
`FILEROUTE_WORK_DIR`, `FILEROUTE_CATALOG_DIR`, and `FILEROUTE_CONTEXT`. The context
is a JSON file containing selected resource/endpoint metadata. Original acquired
bytes are retained separately from processing copies. Use these variables in
native argument defaults and quote expansions in `cmd` definitions.

Every hook declares an outer string option argument named `output`. This is the
exact file Fileroute validates and promotes or uploads. Its resolved value is
frozen when invoking Poe and must stay inside the invocation's work directory.
Declare intermediate files as additional native arguments; steps do not
automatically pipe results or change environment variables for the next step.

Templates, mappings, and converter options are command arguments. For example:

```yaml
afterPull:
  params:
    output:
      default: "${FILEROUTE_OUTPUT}"
    template:
      default: "${FILEROUTE_CATALOG_DIR}/templates/document.md.jinja"
    mapping_file:
      default: "${FILEROUTE_CATALOG_DIR}/mappings/document.yaml"
  steps:
    - cmd: >-
        fileroute steps convert --input "${FILEROUTE_INPUT}" --output "${output}"
        --template "${template}" --mapping-file "${mapping_file}"
```

Mapping YAML/JSON associates template variable names with RFC 6901 JSON
Pointers. Templates receive `conversion.markdown`, `conversion.title`, `source`,
optional `data`, and selected values under `mapped`. Jinja uses `StrictUndefined`.
Small inline maps can use a native string argument and `--mappings-json`.
`fileroute steps render` accepts structured YAML/JSON directly without document
extraction. Relative paths follow the task's working directory; the catalog-dir
variable makes linked descriptor origins explicit.

## Conversion dependencies

Install `fileroute[markdown]` for Microsoft's `markitdown` and DOCX/PDF extras.
Other formats require their corresponding installed MarkItDown extras. Missing
converters fail visibly; Fileroute does not install packages or enable cloud
services/plugins automatically. A scanned PDF with no extracted text requires
explicit OCR configuration.

Legacy DOC normalization optionally uses an installed LibreOffice executable.
It runs headless with a separate temporary profile, a timeout, and a verified
DOCX intermediate. The original DOC remains unchanged. Normalization is not
original approval evidence.

## Transfer results

Pull processing writes only a staged candidate until all steps and validation
succeed. It then atomically replaces `Resource.path`. Failures preserve the old
artifact and retain acquisition/processing receipts in the run directory.

Each resource/target pair gets an independent push candidate. A different
`params.output` suffix can produce DOCX for one target and PDF for another.
Folder uploads use the candidate basename and original relative parent path;
exact file targets keep their configured identity. Fileroute validates all
prepared outputs, collisions, and provider size limits before its first upload.
Failures during uploads report completed, failed, or unknown effects without
claiming a cross-provider transaction.

Use `--run-dir` to select receipt storage and `--format json` for CLI results.
Ordinary transfers without hooks retain their existing behavior. Processing
hooks currently require concrete file resources; use discovery before pulling a
folder's processed members.

## Discovery and editing

`discover` reports metadata differences by default. `--write` applies a complete
proposal to the physical owning catalogs, preserving authored keys, paths, and
hooks. `--to markdown` generates Markdown outputs and `afterPull` defaults for
new supported resources. Existing `--output` discovery remains available for a
separate concrete catalog. Discovery never downloads content or proves approval.

A templated catalog source may declare an `afterPull` sequence as the default
for newly discovered files. Both discovery APIs copy that sequence and its native
parameters onto new concrete source endpoints. `--to markdown` keeps this custom
sequence instead of inserting the generic converter. Reconciliation preserves
each existing resource's authored sequence; changing the template's default does
not replace processing choices already reviewed on concrete resources. The
template source itself is inventory configuration and does not run a directory
processing task.

Use `fileroute steps add --help` and `fileroute steps run --help` for editing and
local replay. Source and target selectors include their location index. Hook
execution never searches for arbitrary task names in another project.
