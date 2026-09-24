# AGENTS.md

Instructions in this file apply to the full repository.

## Goal

Build and evolve reusable shared-drive adapters (SharePoint, Google Drive, S3-oriented retrieval workflows).

Goal is to create a quick and efficient system like git and uv both for quick development (so retrieve one or a group of files) with CLI, for more complex workflows (ETL etc) that will use python API, and emphasis on standardized metadata to build a catalog of resources that can be easily searched and retrieved by users and agents. Currently, the DataPackage standard is followed for metdata (https://github.com/frictionlessdata/datapackage/tree/main/profiles) but this may evolve as we iterate on the metadata structure and retrieval patterns (for example: see Issue #4 for OpenMetadata standard)

## Fast context

- Read the code! The best way to understand the current state and patterns is to read through the existing code, especially the clients, auth strategies, and CLI.

- Also equally important! Study the REST APIs for the clients and authorizations:
    - Drive:
        - /fetch https://learn.microsoft.com/en-us/graph/api/resources/onedrive?view=graph-rest-1.0
    - Sharepoint:
        -
    - Google drive:
        - /fetch https://developers.google.com/workspace/drive/api/reference/rest/v3
        - /fetch https://developers.google.com/workspace/drive/api/reference/rest/v3/files
        - /fetch https://developers.google.com/workspace/drive/api/reference/rest/v3/drives

- And equally important is to use metadata standards. Study these:
    - standard data package (/fetch https://github.com/frictionlessdata/datapackage/tree/main/profiles)
    - and see recipes for using (/fetch https://github.com/frictionlessdata/datapackage/tree/main/content/docs/recipes)
    - And OpenMetadata for drive services and where else applicable:
        - data assets and storage: /fetch https://github.com/open-metadata/OpenMetadataStandards/tree/main/docs/data-assets/storage
        - and see docs overall here: /fetch https://github.com/open-metadata/OpenMetadataStandards/tree/main/docs

## Preferred commands

Repository task orchestration lives in `pyproject.toml` under `[tool.poe.tasks]`.
Bootstrap Poe once with `uv tool install poethepoet==0.48.0`, then use the same
named tasks locally and in CI. Run `poe` with no task to list the available
commands.

- Install (CLI tool): `poe install`
- Sync project/dev dependencies: `poe sync`
- Sync explicit dev/test groups: `poe sync-dev`
- Run scripts: `uv run python <script-path>`
- Lint: `poe lint`
- Format: `poe format`
- Tests: `poe test`
- Lint and tests: `poe check`
- Release validation: `poe release-check`
- Safe local transfer plan: `poe dry-run pull <descriptor>` or `poe dry-run push <descriptor>`
- One-off Poe without installation: `uvx --from poethepoet==0.48.0 poe <task>`

## Release model

- `.github/workflows/publish-to-pypi.yml` coordinates prepare, publish, and stable GitHub Release jobs. `scripts/release.py` owns local version/build/tag preparation through `poe release`.
- A push to `dev` creates or advances the next patch development series (`X.Y.Z.devN`) using `uv version`.
- A push to `main` promotes a development version to stable; a direct stable-version push to `main` advances the patch version.
- The workflow commits the `pyproject.toml` and `uv.lock` version change as `chore(release): vX.Y.Z...` and tags that release commit.
- Do not manually edit the project version for normal releases. Run `poe release-check` before merging when validating release readiness locally.

- Save distributions before atomically pushing the release branch and tag. Never force-push release refs.
- On retries, identify the release by its source SHA and branch before calculating a new version; publish the saved artifact from the original run without rebuilding.
- Keep release helpers outside the installed package and retain the Trusted Publisher workflow filename.
- See README's Package releases section for task usage, setup, and artifact-expiry recovery.

## Development guardrails

- Do not hard-code secrets or tenant-specific values.
- Keep `.env-sample` synchronized with variables read in code.
- Adhere to the [Zen of python](https://peps.python.org/pep-0020/) even if refactoring and reorganization is needed but ensure breaking changes in the cli function commands are noted.
- Always check and analyze the current codebase and the associated API documentation (for example, see Sharepoint and Google URLs in "Fast Context") for services when thinking about answers in terms of better solution
- Don't be afraid to highlight any concerns with assumptions made in prompts.
- **Type Strictness**: Prefer explicit strict typings (e.g., precise property definitions and `typing.cast`) over deep nested Pydantic validation when simple internal structural typing suffices and performance/clarity is prioritized.
- **Enforcing Interfaces**: Strictly uphold `abstractmethod` signatures across all foundational components (e.g., `BaseClient` and `ServiceItem`). Constantly ensure any mock or dummy classes within the test suite (like `_Item`) are fully updated with stub implementations to prevent abstract instantation failures.
- **Safe Patching (For Agents and Scripts)**: When automatically refactoring Python files—especially Pydantic models—avoid destructive `sed` or naive line-counting patches. Rely on precise string replacements and validate immediately via `poe test` to catch collection or `ImportError` regressions early.

## When updating code

- Give warnings and ask for approval before breaking backward compatibility.
- Raise explicit exceptions with actionable error messages.
- Keep network calls and auth behavior obvious and testable.
- Assume the code is what is desired and update tests to test the behavior (not the other way around).

## Documentation rule

- Document any non-obvious behavior in docstrings and `README.md`.
- Update generated CLI/API docs with: `poe docs-update`.
- Check generated docs without changing files with: `poe docs-check`.
- Build all documentation with: `poe docs`.
- the __all__ variable is used to explicitly declare public API for each module. When adding new functions or classes that are intended to be part of the public API, make sure to include them in the __all__ list at the end of the module. This helps with clarity and maintainability of the codebase.
- Document design choices through doc strings. For example, the logic and source (like url and name of standard(s) or existing software like uv/git etc that provided either motivation of why property names were used)
