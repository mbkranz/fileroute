# Function-first processing and core-scope implementation plan

Status: implemented locally and validated on 2026-11-16; not released.
The subsequent approved DOCX/Markdown specialization replaces the generic
conversion/rendering surface described in the historical implementation below.
The latest user correction folds that fixed pair into ordinary pull/push by
inferring DOCX source/target and Markdown local path formats, and removes the
separate conversion commands. This supersedes earlier byte-only-transfer
statements in this historical record. The current contract is documented in
[DOCX and Markdown reviews](../processing.md).
Recorded: 2026-11-16. Inspected `feat/poe-tasks` at `e71406a`, including its
existing uncommitted processing changes. The implementation results below record
completion of this decision without implying release or live-provider acceptance.

## Decision and scope

Defer descriptor-driven resource processing: neither `Catalog.pipelines` nor
`afterPull`/`beforePush` hooks becomes the target public interface in this work.
Keep Fileroute focused on artifact metadata, discovery, and explicit transfers.
Applications compose retrieval, local processing, and publication in Python.

Retain the useful independent document functions and their current standalone
`convert`/`render` commands for this bounded pass. Their request/result contracts
stay beside the functions in `conversion.py`. This keeps a concrete document
use case available without requiring a workflow runtime. Reassess their package
ownership from real usage; survey-specific cleaning and governance belong in
their owning applications.

Retain removal of the obsolete runtime Poe adapter and old `steps` commands.
Keep repository developer tasks and the separately installed Poe bootstrap.
Add no Hamilton, Dagster, private PRS dependency, replacement task language,
processor registry, or automatic plugin/import discovery.

The earlier root-pipeline implementation and subsequent lifecycle-hook proposal
are superseded. The local implementation and user-facing guides now follow this
function-first decision. The sections below retain the implementation plan and
its acceptance criteria; completion evidence is recorded under Implementation result.

## Evidence and architectural boundary

- `ppsc-survey-prs` commit `1596072` replaced executable operation classes with
  typed ordinary functions; `201e519` simplified descriptor-heavy registration.
  Current `cleaning/columns.py` co-locates TypedDict arguments and functions;
  `cleaning/registration.py` maps argument annotations to the same callables.
  Its remaining dispatch supports actual authored cleaning rules and does not
  imply that Fileroute needs a generic execution model.
- `ppsc-calculations-gov` commit `851384e43` removed `pipeline.yaml` and custom
  workflow execution models. `456c7af2a` extracted explicit domain handoffs.
  Current `products.py` pairs `ProductRequest`/`ProductResult` with
  `build_products`; `integration.py` injects parsing and provider dependencies.
  Its repository conventions assign artifact locations to Fileroute and
  governance/parser behavior to Python.
- Those local reference checkouts were inspected under
  `$HOME/projects/allofus/`; they are design evidence, not dependencies or files
  to modify during this implementation.

The intended flow is:

```text
Project CLI or workflow function
  -> Fileroute metadata/discovery and explicit pull
  -> Named local operation with typed request/result
  -> Fileroute explicit push
```

The application owns ordering and the decision to publish. Conversion operations
do not import transfer clients, resolve catalogs, authenticate, or publish remotely.
Poe owns developer commands; DVC may own reproducible products in a consuming
project. Neither is added to Fileroute's runtime execution path.

The local processing call uses the existing operation directly:

```python
from pathlib import Path
from fileroute.conversion import ConversionRequest, convert_document

request = ConversionRequest(
    source=Path("raw/report.docx"),
    output=Path("build/report.md"),
)
result = convert_document(request)
```

The surrounding project function chooses when to acquire `raw/report.docx` and
whether to publish `result.output`. No registration or descriptor execution is
required to call this function or test it with an injected converter.

## Guidelines to follow

1. **Cohesive modules and contracts.** Keep related argument types, operations,
   and meaningful helpers together. Do not create one file per operation or
   split request/result types into a generic global `contracts` module merely
   for symmetry. Introduce shared modules only for demonstrated reuse.
2. **Ordinary callable interfaces.** Prefer explicit signatures, native `Path`
   values, useful result objects, and injected dependencies. Use `Callable` for
   a function seam; introduce a `Protocol` only when callers need a real shared
   behavioral interface. Avoid universal `execute(context, **kwargs)` contracts,
   executable request classes, and bags of unrelated runtime dependencies.
3. **Appropriate validation.** Use Pydantic models at structured external
   boundaries and for meaningful invariants. Use TypedDict/TypeAdapter when a
   real authored mapping needs validation, and dataclasses or annotations for
   trusted internal values. Do not wrap every scalar or native SDK object.
   Configuration validation performs no I/O. Parsing structured settings and
   checking local files are explicit operation-boundary responsibilities.
4. **One source of argument definitions.** Reuse the operation's contract from
   Python and thin CLI adapters. Remove `RenderOptions`/`ConvertOptions` and
   generic artifact adapters with the deferred runtime. Keep
   `ConversionRequest`/`ConversionResult` as the starting interface; split
   operation-specific requests only if actual differing requirements justify it.
5. **Validate before consequential effects.** Validate argument relationships,
   file roles, destinations, and available tool configuration before invoking
   a subprocess or replacing outputs. Recheck actual generated results before
   replacement. Keep acquisition, interpretation, and publication decisions
   visible; helpers must not conceal meaningful effect ordering.
6. **Keep models and naming domain-specific.** Resource keys describe artifacts;
   catalog keys describe collections; functions use concrete action names.
   `sources` records provenance/acquisition locations and `targets` publication
   locations. Python parameters describe the inputs of the particular function.
   These are different roles; do not alias them or add processing phases to
   metadata. Lowercase snake_case is a recommended new identifier convention,
   not a reason to change existing names or tighten the current name grammar.
7. **Preserve compatibility floors.** Support Python 3.11 and the declared
   Pydantic floor of 2.10.6. PRS's newer typing/Pydantic conveniences are not a
   reason to increase those requirements. On Python 3.11, runtime-validated
   TypedDict contracts use `typing_extensions.TypedDict`; declare a direct
   dependency if such an import is actually introduced, rather than relying on
   a transitive installation. Retain public `__all__` declarations
   and document material behavior and design choices in docstrings and guides.
8. **Keep dependencies explicit.** Preserve lazy MarkItDown loading, actionable
   missing-extra errors, disabled converter plugins, and explicit LibreOffice
   discovery. Keep the existing Jinja base dependency in this pass. A future
   move to an optional extra must also remove eager imports from ordinary CLI
   and metadata paths and prove base-install behavior. Do not add more optional
   groups or a generic configuration loader without a consumer requirement.
9. **Prefer direct composition.** A project workflow can call pull, conversion,
   and push without a registry. If repeated data-authored dispatch later becomes
   necessary, start with a small explicit mapping at the application boundary.
   Do not derive persisted operation identities from arbitrary function renames.

These guidelines apply the previously selected Standard Python clean-architecture
review: cohesion, low coupling, explicit contracts, composition, and simplicity.
Do not mechanically impose the skill's FastAPI/CRUD layers on this library.

## Retain/remove implementation map

| Area | Planned change |
| --- | --- |
| `conversion.py`, `commands/conversion.py` | Retain typed standalone operations and thin root `convert`/`render` commands. Keep their behavior independent of descriptors. |
| `processing.py`, `commands/processing.py` | Remove the generic processor/port model, registration, graph planning/execution, Markdown declaration helpers, and `process` command. |
| `tasks.py`, `commands/steps.py` | Keep deleted. Do not restore an alias adapter, subprocess Poe worker, or compatibility execution engine. |
| `models.py`, package exports | Remove `Pipeline`, `Catalog.pipelines`, and associated validation/exports. Retain resource formats and all ordinary metadata/location models. Add narrow rejection of unsupported execution declarations. |
| `descriptor.py` | Remove `pipeline_resource` and pipeline-specific save bookkeeping/exports. Preserve traversal, selectors, origins, reference loading, and ordinary resource persistence. |
| `commands/descriptor.py`, `discovery.py` | Remove conversion-generating flags/arguments/helpers and their imports. Preserve inventory, reconciliation, observation metadata, and physical-owner writes for resources. |
| `diagram.py` | Remove pipeline-specific nodes, edges, binding lookup, and imports. Retain resource/catalog/location graphs and reports. |
| `transfer.py`, provider clients | Retain byte transfers, receipts, `--run-dir`, JSON output, identity/version observations, client ownership, and independent provider fixes. No processing callbacks. |
| `pyproject.toml`, `uv.lock` | Keep runtime Poe removed and developer task definitions intact. Regenerate the lock only through the dependency tooling if dependency declarations actually change. |
| Docs/schema generators and tests | Remove deferred execution registrations and coverage; retain or adapt coverage of observable standalone and core behavior. Regenerate maintained artifacts during implementation. |

Do not revert the feature commit wholesale. In particular, preserve stable
SharePoint folder-ID uploads, the Google move interface correction, provider
export filename handling, and transfer/discovery evidence improvements.

## Execution metadata and discovery cleanup

Catalog and location models allow extension metadata. Merely deleting executable
fields would leave configurations that load successfully but perform no work.

- Reject `pipelines` at its former catalog boundary, including nested and linked
  catalogs. Retain location rejection of `afterPull`, `after_pull`, `beforePush`,
  and `before_push`. Explain that processing is application-owned and name the
  standalone API/commands where relevant. Remove messages recommending the
  deferred pipeline schema.
- Mirror these exclusions in generated JSON Schemas. Keep other extensions
  working. Do not globally forbid `steps`, `sequence`, `params`, `args`, `type`,
  or `kwargs`, and do not inspect or rewrite their occurrences inside arbitrary
  metadata, templates, or parameter strings.
- Remove conversion-oriented `add --to markdown` and `discover --to markdown`,
  the Python `to`/`to_markdown` options that exist solely for conversion, and
  request-model fields carrying those flags. Unsupported CLI options should
  fail before descriptor mutation; removed Python parameters must not become
  silently accepted metadata via `**kwargs`.
- Remove `_declare_markdown`, `add_markdown_pipeline`, and pipeline persistence
  from `_owned_writes`. Keep `discover --write`, `--output`, reconciliation by
  stable provider identity, and writes to the owning linked descriptor.
- Inventory acquired files without creating derived resources, changing their
  targets, or substituting an `.md` filename for unconverted DOCX bytes.
  Preserve provider-native export behavior as acquisition behavior; it is not
  general document conversion. Existing registered paths are not silently
  rewritten by inventory refresh.
- Do not automatically delete existing raw/derived resources, restore inherited
  targets, or rewrite user descriptors when declarations are rejected. Users
  retain their artifacts and can produce derived files through explicit Python
  operations. Document that catalog entries alone do not execute them.

## Standalone operation behavior

Keep current public conversion contracts and function names unless a concrete
behavioral defect requires a bounded change. Avoid adding another serializable
workflow/config envelope around them.

- Distinguish raw input, processing copy where needed, and output. Preserve
  sources and previously valid outputs on failure. Protect the source, template,
  and mapping file from being selected as an output, including resolved aliases.
  Check these relationships before invoking tools, rather than only at the final
  write boundary. Inspect the existing source-only check for this concrete gap.
- Use action-specific validation: supported input requirements, valid mapping
  structure/pointers, required template context, and a positive finite subprocess
  timeout. Check without auth/provider calls. Do not build a generic preflight
  engine or treat format labels as proof of actual content validity.
- If declared-format dispatch is genuinely needed after deleting the pipeline
  adapter, express the hint in an operation-specific contract and adapt filenames
  only in disposable staging. Do not keep the private pipeline adapter merely
  to retain this capability or silently change registered resource paths.
- Preserve strict rendering, bounded LibreOffice execution and isolated profiles,
  checks that tools actually produced the expected file, unchanged-source
  verification, and atomic replacement of each completed local output.
- Keep literal configuration values literal: no shell evaluation, automatic
  environment interpolation, or dynamic imports. Do not interpret descriptor
  metadata or document contents as Python or shell commands. Explicit strict
  Jinja rendering remains the supported template operation.
- Keep errors actionable and retain meaningful causes. Catch exceptions at the
  operation/CLI boundary where context or translation is needed; do not return
  unchanged input as successful processing or swallow failures.
- Preserve useful source/output digests and actual output paths in results.
  Do not imply that conversion success proves approval or provider acceptance.
  Individual file replacement and separate provider calls are not a transaction.

The CLI validates options into the same request type, calls the function, and
formats its result. It contains no second implementation of transformation logic.
The application decides whether a completed result is eligible for publication.

## Implementation sequence and compatibility

1. Inventory the current diff and public surfaces before editing. Separate
   already released APIs from this branch's experimental additions; inspect
   actual consumers before removing or renaming an interface. Preserve unrelated
   work, `tasks_add.md`, the reference repositories, and existing user artifacts.
   Capture the current test/check baseline rather than assuming old counts.
2. Make the runtime removal and its callers consistent as one bounded change:
   CLI imports, models/exports, descriptor helpers, discovery, graphs, and docs
   generator registrations. Run collection and focused tests after this
   structural change so dangling imports are caught immediately.
3. Implement narrow runtime/schema rejection and conversion-option cleanup.
   Preserve linked-owner resource writes and all unrelated extension metadata.
   Add migration guidance in public docs before considering the change complete.
4. Keep and verify direct conversion/rendering. Correct the demonstrated input
   alias and timeout-validation gaps with focused behavior tests; do not use
   cleanup as a reason for a wider conversion rewrite or new packaging system.
5. Rewrite current processing guidance as explicit Python composition; update
   README, descriptors, transfers, schema guidance, contribution notes, navigation,
   and the repository Fileroute skill. Remove stale runtime symbols and imports.
   Regenerate schema, CLI/API docs, and saved examples with the named tasks.
6. Complete the validation below and report the final retained surface and any
   remaining compatibility or provider-acceptance limits.

Document removals of `process`, `Pipeline`, execution fields, and conversion
generation flags explicitly. The user approved implementation after reviewing
this plan. That approval does not authorize deleting user files or migrating
descriptors automatically. If inspection finds a released interface would be broken beyond
the already authorized scope, follow the repository's compatibility approval rule
with the exact impact. Do not infer another approval requirement merely from
routine internal refactoring. Do not manually bump the release version.

## Verification and acceptance

Reuse behavioral tests and preserve independent assertions for provider identity,
raw bytes, actual hashes, client cleanup, receipt outcomes, and linked-file
ownership. Remove tests whose sole subject is the deferred execution runtime;
retain useful fixtures/assertions under the surviving behavior.

Required coverage:

- Direct request/function behavior: invalid arguments and source/template/mapping
  output aliases fail before tool calls; failure leaves inputs and prior outputs
  unchanged; successful hashes match actual bytes; unavailable tools give useful
  errors; `.doc` normalization and strict rendering retain their current controls.
- Python and CLI conversion paths use the same validation and return truthful
  results. Exercise synthetic fixtures and injected dependencies; real optional
  DOCX/PDF tests run with the Markdown extra, separately from base-install checks.
- A base installation imports Fileroute and exposes metadata, transfer help, and
  standalone help without runtime Poe or MarkItDown. Missing MarkItDown is only
  an error when extraction is requested; no import-time provider calls occur.
- Runtime and generated schema both reject former execution declarations,
  including linked/nested catalogs and snake-case hook aliases. Unrelated
  extension metadata and literal parameter contents still round-trip.
- Unsupported conversion-generating flags cause no writes. Normal discovery
  still preserves authored identity, paths, targets, observations, and linked
  owner files; it does not manufacture Markdown resources.
- Pull and push remain byte-only. Preserve directory-child identities, provider
  export names, raw-download retention, and truthful failed/unknown publication
  receipts. No processing attempt is implied by a transfer dry run.

During implementation, use `poe test --collect-only -q` after structural changes
and appropriate focused tests before the full suite. Finish with:

```bash
poe schema-update
poe docs-update
poe release-check
git diff --check
```

When standalone conversion remains in scope, also run its optional integration
tests with `uv run --frozen --extra markdown pytest tests/test_conversion.py`.
Check the packaged artifact through the existing build task if dependency or
packaging declarations change. Distinguish synthetic/local acceptance from live
Microsoft/Google/S3 acceptance; this plan makes no new provider-support claim.

## When to revisit declarative processing

Reconsider only after multiple real workflows demonstrate the same need for
configuration-authored processing and explicit Python composition is repeating
substantial coordination. Record the users, workflows, actual repeated behavior,
and required failure/replay semantics first. Check that the core descriptor and
transfer APIs are stable enough for those consumers.

Choose the smallest demonstrated mechanism: direct calls first; a local mapping
for real authored dispatch if needed; a thin lifecycle adapter only if users
need it. Cross-resource scheduling, fan-in/fan-out, or distributed execution may
belong to an existing orchestrator. Do not predesign dormant hook fields, a
reserved plugin API, or a second schema in anticipation of that decision.

## Review outcome

Independent Standards and Spec reviews informed this plan. Their concrete
findings are incorporated: explicit rejection of otherwise ignored metadata,
removal of conversion-generating flags, preservation of physical-owner writes
and provider fixes, co-located contracts, optional import behavior, and local
file-safety checks. These are planning conclusions, not implementation acceptance.

## Implementation result

Completed the approved runtime removal and caller cleanup. Standalone
`ConversionRequest`/`ConversionResult`, local conversion/rendering, and their thin
CLI commands remain. Catalogs, discovery, graphs, and transfers contain no
processing executor or registry. Removed execution declarations fail explicitly;
ordinary extension metadata and existing artifact/target choices are preserved.

Conversion now checks all input/output aliases before tool calls, including
symlinks and hardlinks; mapping pointer syntax and finite timeout validation
fail early. Tests preserve independent raw-byte/provider identity and transfer
receipt coverage, exercise linked-owner inventory writes, and verify CLI/API
boundaries and real optional DOCX/PDF extraction. A fresh interpreter also blocks
both runtime Poe and MarkItDown imports while exercising metadata commands,
help, and structured rendering.

Validation completed with the repository's cached Poe 0.48.0:
`release-check` passed, including schema freshness, lint, **620 tests**, generated
CLI/API/example freshness, and strict documentation build. The separate optional
conversion/boundary selection passed **50 tests**. `git diff --check` passed.
The global Poe 0.49.0 and the pinned runner both hung after child completion in
the managed process sandbox; the pinned named release task completed successfully
outside that isolation. No installed tool was replaced.

No live provider operation was used for this validation, and no user descriptor
or reference-project checkout was migrated.

The named `build` task also produced an sdist and wheel. Wheel inspection found
no deleted execution modules or runtime Poe requirement. An offline isolated
installation of that wheel, outside the checkout and without Poe or MarkItDown,
passed CLI/help and structured-rendering checks; extraction reported its missing
optional extra as intended.

## Follow-up: opinionated DOCX/Markdown review profile

The user approved replacing generic extraction/templates with a bidirectional
DOCX ↔ Markdown review profile, including Markdown → DOCX. The function-first
boundary remains: adjacent `DocxToMarkdownRequest`/`MarkdownToDocxRequest`,
`ConversionResult`, and explicit functions; thin CLI commands; no execution
declarations in catalogs or transfers.

Use Pandoc's native document model for reading/writing instead of implementing
another document model. A versioned typed YAML profile stores comment payloads,
reviewer attributes, observed style names, retained metadata, and file hashes.
Empty body spans explicitly delimit comment ranges; do not reattach comments by
text matching. Preserve full style definitions in the pinned original/reference
DOCX. Retain images beside the Markdown in an immutable source-hash bundle.

Export validates marker identity/order, style availability, confined local asset
paths, pinned bytes, and the generated native comments. Known unsupported source
features are reported and block export even if their header entry is removed.
This is a declared semantic subset, not arbitrary Word layout or byte fidelity.
Keep per-file atomic output, unchanged-input checks, positive finite tool timeouts,
and actionable failures. Bundle publication followed by Markdown replacement can
leave an unreferenced bundle; it is not a multi-file transaction.

Remove the obsolete `convert`/`render` commands, generic `ConversionRequest`,
mapping/template helpers, MarkItDown extra/dependencies, and LibreOffice path.
Require externally installed Pandoc 3.1+ only when conversion is invoked. Test
both directions with real Pandoc, edited/overlapping/point/multi-paragraph ranges,
lost/ambiguous anchors, native reviewer attributes, styles, images, integrity
failures, and base installation behavior without optional conversion libraries.
The earlier implementation-result counts describe the preceding pass. After
this specialization, pinned Poe 0.48.0 `release-check` passed: schema freshness,
lint, **624 tests**, generated references/examples, and strict documentation build.
The named `build` task produced the sdist/wheel. An offline isolated base-wheel
installation outside the checkout passed both conversion CLI directions with
native comments/styles, metadata/help checks, and absence of retired libraries
and execution modules. `git diff --check` passed. Pandoc integration used 3.1.3;
no live provider operations or package publication were performed.

## References

Latest follow-up validation: the inferred transfer integration passed pinned Poe
`release-check`, including **633 tests**, schema/reference freshness, lint, and
the strict docs build. The named build succeeded; a fresh offline base-wheel
installation exercised normal CLI `pull`/`push` with real Pandoc and mocked remote
I/O, verified the automatic round trip, and confirmed the separate conversion
commands/modules and retired dependencies were absent. No live provider calls
or publication were performed. The implemented transfer layout is remote DOCX ↔
local Markdown; transporting remote Markdown review bundles remains separate.

- Repository `AGENTS.md` and the supplied
  `python-clean-architecture` skill (Standard depth).
- Local PRS design records: `docs/specs/cleaning-simplification-plan.md` and
  `docs/specs/cleaning-rule-dispatcher-simplification-plan.md`, plus the commits
  and current modules cited above.
- Local governance conventions and `docs/implementation-plan.md`, especially
  its removal of overlapping workflow runners.
- [Data Resource standard](https://datapackage.org/standard/data-resource/):
  artifact metadata/provenance vocabulary, not a processing language.
- [Data-engineering orchestration reference](https://github.com/vaquarkhan/data-engineering-agent-skills/blob/main/references/pipeline-orchestration-patterns.md),
  [file-ingestion skill](https://github.com/vaquarkhan/data-engineering-agent-skills/blob/main/skills/file-and-partner-feed-ingestion/SKILL.md),
  and [Python packaging skill](https://github.com/vaquarkhan/data-engineering-agent-skills/blob/main/skills/python-data-engineering-and-pipeline-packaging/SKILL.md):
  supporting guidance for explicit effects, processing ownership, and validation
  before publication; none prescribes Fileroute YAML syntax.
