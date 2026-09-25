---
name: fileroute
description: Use Fileroute to describe, inspect, resolve, transfer, and visualize relationships between local artifacts and SharePoint, Google Drive, or S3. Use when working with Fileroute descriptors, file provenance, sources and targets, pull/push workflows, provider URLs, or file-flow diagrams. Prefer Fileroute over ad-hoc transfer or metadata scripts when its descriptor model can express the task.
---

# Fileroute

Use Fileroute as the source of truth for file provenance and supported transfer workflows. Prefer its descriptor, CLI, and Python API over parallel metadata or one-off provider scripts.

## Choose the invocation

- In a project that declares Fileroute as a dependency, use `uv run fileroute ...` so the locked project version is used.
- For standalone or occasional CLI use, use `uvx fileroute ...`.
- When developing Fileroute itself, use the repository environment and the commands in `AGENTS.md` / `pyproject.toml`.

Do not add Fileroute to a project merely to run a one-off command when `uvx fileroute` is sufficient.

## Read the existing state first

Before changing a workflow:

1. Find and read the existing Fileroute descriptor if one exists.
2. Inspect `fileroute --help` or the repository's `docs/cli.md` before assuming a command or option exists.
3. For descriptor semantics, use `docs/descriptors.md` as the reference.
4. For transfer support and inheritance behavior, use `docs/transfers.md` as the reference.
5. Prefer the installed/current code and generated CLI help if documentation and assumptions disagree.

Do not invent descriptor fields, provider aliases, or CLI options.

## Descriptor mental model

A descriptor is local YAML or JSON metadata describing artifacts and their relationships.

- `path`: the artifact's local file or directory used for transfers.
- `sources`: upstream inputs or provenance. These may be remote download locations or local authoring inputs such as a `.qmd` file.
- `targets`: downstream publication destinations.
- `serviceType`: optional provider metadata. Canonical saved values are `GoogleDrive`, `SharePoint`, and `S3`.
- `serviceId`: optional provider-native identifier.
- `catalogs` and `resources`: keyed maps whose keys are registered names. Do not repeat a child `name` field.
- `descriptor`: a cross-file catalog link, relative to its containing descriptor. Keep it distinct from an artifact's `path`.

```yaml
catalogs:
  shared:
    descriptor: catalogs/shared.yaml
resources:
  report:
    path: artifacts/report.csv
    sources:
      - path: s3://example-bucket/report.csv
        serviceType: S3
```

Use registered names or qualified names with `--select`. Use `fileroute list`
to discover names, exact JSONPath/Pointer addresses, and physical origin files.
The positional argument remains a descriptor file. `activate` selects a file.
Names match `[A-Za-z_][A-Za-z0-9_-]*`; put display text in `title`.
For old named lists and `$ref` links, run `fileroute migrate INPUT OUTPUT_DIR --dry-run`, then repeat without `--dry-run` to convert the linked descriptor graph into a new output directory. Do not silently accept or rewrite legacy descriptors while performing another task.

Descriptor fields are camelCase. Python attributes are snake_case.

Preserve authored URLs because they are useful, clickable provenance even when provider metadata can be inferred. Use `resolve` rather than replacing URLs with opaque IDs.

## Current transfer capabilities

Treat this table as a guardrail and verify `docs/transfers.md` when current behavior matters.

| Operation | SharePoint | Google Drive | S3 |
| --- | --- | --- | --- |
| `pull` remote source | supported | supported | supported |
| `push` publication target | supported | supported (binary files) | not implemented |

Google Drive push supports folder targets and exact existing binary file IDs; reject ambiguous names and Google-native document updates. Descriptors and diagrams may describe S3 targets even though push cannot publish to them yet. `push --dry-run` fails if any target is unsupported; it does not partially publish supported targets.

Fileroute does not stream directly from one cloud provider to another and does not transform files. A remote-to-remote workflow is conceptually `pull` to the local artifact, perform any external transformation if needed, then `push`.

## Default workflow

For an existing or newly authored descriptor:

```bash
uv run fileroute resolve config/fileroute.yaml
uv run fileroute diagram config/fileroute.yaml
uv run fileroute pull config/fileroute.yaml --dry-run
uv run fileroute push config/fileroute.yaml --dry-run
```

Substitute `uvx fileroute` for `uv run fileroute` when Fileroute is not a project dependency.

Use only the steps relevant to the task. `resolve`, `diagram`, and transfer dry runs should be preferred before authenticated remote operations.

For an actual transfer:

```bash
uv run fileroute pull config/fileroute.yaml
uv run fileroute push config/fileroute.yaml
```

Run a dry run before an agent initiates a remote write unless the user explicitly asks to skip it.

## Pick the command from the intent

| Intent | Command |
| --- | --- |
| Preview/infer provider metadata without network access | `fileroute resolve [descriptor]` |
| Persist inferred provider metadata | `fileroute resolve [descriptor] --write` |
| Select a descriptor for later commands | `fileroute activate <descriptor>` |
| Inspect registered names, origins, and paths | `fileroute list [descriptor]` |
| Explain one entry, including inherited targets | `fileroute resolve [descriptor] --select NAME` |
| Verify provider metadata online | `fileroute resolve [descriptor] --online` |
| Add a resource or catalog | `fileroute add ...` |
| Edit descriptor/root properties | `fileroute update ...` |
| Preview or perform retrieval | `fileroute pull [descriptor] --dry-run` / `pull` |
| Preview or perform publication | `fileroute push [descriptor] --dry-run` / `push` |
| Visualize provenance and destinations | `fileroute diagram [descriptor]` |
| Convert previous named lists and links | `fileroute migrate INPUT OUTPUT_DIR` |
| Create a local descriptor variant | `fileroute clone descriptor <target>` |
| Establish provider authentication | `fileroute auth login ...` |

For exact flags, use `fileroute <command> --help` rather than guessing.

## Editing rules

- Prefer updating an existing descriptor over creating parallel metadata.
- Prefer `add`, `update`, and `resolve --write` for ordinary descriptor edits when they express the requested change clearly.
- Direct YAML/JSON edits are acceptable for larger structural changes; preserve existing comments, style, `descriptor` links, and unknown metadata where practical.
- `update` and `add --parent` edit one physical file. Use the origin descriptor shown by `list` when an entry belongs to a linked child.
- Whole-descriptor `resolve --write` changes only the selected file; `resolve --select NAME --write` changes the entry's reported origin file. Preserve deeper links and never copy inherited targets onto a child.
- Use explicit `targets: []` when an artifact must opt out of inherited publication targets.
- Do not reinterpret a local provenance source as a download operation. For example, a `.qmd` source usually means "build this artifact from the Quarto source," not `pull` it.
- Multiple sources can describe transformation/provenance. Do not arbitrarily choose one for `pull`; Fileroute intentionally refuses ambiguous retrieval.
- Keep credentials and tenant secrets out of descriptors and committed files.

## Diagram guidance

Use diagrams when the user needs to understand or document the workflow rather than execute it.

```bash
uvx fileroute diagram config/fileroute.yaml
uvx fileroute diagram config/fileroute.yaml -o workflow.html --detail full
uvx fileroute diagram config/fileroute.yaml -o workflow.md
uvx fileroute diagram config/fileroute.yaml -o workflow.mmd
```

Supported outputs include SVG, HTML, Markdown, and Mermaid. A diagram expresses intended relationships; it is not evidence that a transfer completed.

## When to use Python instead of the CLI

Use the CLI for human-facing repository workflows, small automation steps, inspection, and transfers. Use the Python API when Fileroute is embedded in a larger application or ETL workflow and the caller needs to compose catalogs, graphs, or transfer plans programmatically.

Do not replace a simple CLI workflow with custom Python merely because Python is available.

## Avoid these anti-patterns

- Writing a new SharePoint/Google Drive/S3 transfer script before checking whether Fileroute already covers the operation.
- Creating a second provenance manifest instead of extending the existing descriptor.
- Treating `sources` and `targets` as interchangeable.
- Assuming a described target is currently writable.
- Assuming offline `resolve` verifies remote state; online verification requires `--online`.
- Assuming Fileroute transforms files or copies directly between remote services.
- Hard-coding credentials or secrets into descriptors, scripts, or commands committed to the repository.

## Repository references

When this skill is used inside the Fileroute repository, consult these files as needed rather than loading all of them up front:

- `README.md` — concise usage and current capability summary.
- `docs/descriptors.md` — descriptor model, paths, references, and URL resolution.
- `docs/transfers.md` — pull/push semantics and provider support.
- `docs/diagram.md` — diagram outputs and graph behavior.
- `docs/cli.md` — generated CLI reference.
- `docs/api.md` — generated Python API reference.
- `docs/use-cases.md` — complete examples.
- `docs/authentication.md` — provider authentication.
