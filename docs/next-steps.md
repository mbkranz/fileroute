# Next steps after the working release

## Simplify catalog models

After the traversal changes are merged into `dev`, the developer workflow is
validated, and a working version reaches `main`, evaluate replacing `dplib-py`
with Sharedrive-owned Pydantic descriptor models. This is a separate change;
the traversal work does not depend on it.

`sharedrive.models` currently subclasses `dplib` models while adding remote
resources and packages, catalogs and references, service and entity types,
selectors, basepaths, cache resolution, and catalog traversal. Owning the
smaller descriptor model could make loading, validation, and serialization
easier to understand. Preserve the existing serialized descriptor format and
public Sharedrive model contracts while evaluating the change. Compatibility
with Data Package concepts does not require Python inheritance from `dplib`.

Start with a focused inventory of the descriptors and behaviors actually used.
Prefer small local `Resource`, `Package`, `Catalog`, and `CatalogReference`
models with explicit loading, saving, traversal, reference, basepath, and path
validation helpers where needed. Avoid rebuilding all of `dplib`.

The descriptor clone and update failures were fixed in PR #11 without replacing
the model layer. Clone and update now preserve authored descriptor fields,
including `$schema`. Keep the broader model replacement separate from the
working release unless a new required workflow exposes a concrete blocker.

Until then, avoid adding direct `dplib` imports outside `sharedrive.models`;
`sharedrive.commands.descriptor` already imports its `Error` type, which the
migration should also remove. Move this
work ahead of the working release only if installation, descriptor round trips,
required model representation, or required workflows are concretely blocked by
`dplib` behavior. Cover existing descriptor fixtures and round trips before
removing the dependency.
