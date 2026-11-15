# Fileroute Control Center

The static workbench demonstrates Fileroute workflows against synthetic data. The bundled previews are generated with the Fileroute core from this checkout. Project updates and sample drafts are stored in the current browser. Export a workflow snapshot to move changes to another browser, or use the JSON to update the defaults in `public/workflow.json`.

The local workbench uses FastAPI and the same Vite frontend. It delegates to Fileroute for descriptor loading, resolution, transfer planning and execution, discovery, profile configuration, diagrams, and migration. The CLI supplies help, clone behavior, and provider login. The upstream `fileroute ui` entry point, Python package release, and hosted CI are tracked on the Implementation plan and Verification pages.

## Run locally

From the repository's `ui/` directory, install the local server and the Fileroute core from this checkout. Build the browser assets once.

```bash
uv venv
uv pip install -r requirements-local.txt
npm ci
npm run build
uv run --no-project python -m control_center.app catalog.yaml --root public/fixtures --no-open
```

Open the printed loopback URL. To work with your own catalog, pass the descriptor and the project working root. Omit the descriptor to use Fileroute's active and standard-path lookup. To use the bundled synthetic profiles, enter `profiles.yaml` as the profile configuration file. Keep provider consent, offline previews, and project mutations in the local session.

In **Catalog**, switch between **List** and **Graph**. Select an entry to edit its authored YAML, review the unified diff, or preview a scoped command. Use **How do I connect?** on a remote location to see its profile, declaration, and equivalent `auth describe` command. **Connections** opens the same setup guide. **Activity** opens the local jobs started for the selected entry. Sample route previews come from the generated fixture. Open edited descriptors locally to plan changed routes.

## Regenerate and verify

```bash
uv pip install -r requirements-test.txt
uv run --no-project python -m scripts.export_assets
npm ci
npm run build
uv run --no-project python -m pytest -q
```

- `control_center/contracts.py` declares each operation with Pydantic-backed typed dictionaries and reused authored models. The current profile in `public/schemas/ui-profile.json` points to the versioned tagged operation schema. The catalog and auth profile artifacts come from the installed Fileroute models.
- `public/operation-views.json` contains tab labels and operation metadata. A test compares its IDs and the generated schema with the registered Typer leaf commands.
- Vite emits `fileroute/ui/static/`. The Site build copies those assets to `dist/`. The Site has no credentialed backend.
- Review a transfer or provider request explicitly in the local browser. FastAPI binds approvals to the specific validated request and stores jobs in memory for the session. Before dispatch, the transfer adapter compares a fresh plan with the reviewed route and local file state.
- `public/workflow.json` holds published parity, evidence, gates, and design decisions. The editor supports a browser-local overlay, JSON import/export, and restoring the published snapshot. Save new defaults in source when they should be shared across browsers.
