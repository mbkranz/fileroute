# Fileroute descriptor specification

This page is the **normative contract** for Fileroute catalog descriptors.
MUST, MUST NOT, and MAY describe requirements. The [descriptor guide](descriptors.md)
provides practical examples; the [Python API](api.md) documents implementation APIs.

## Documents and identity

A descriptor MUST be a YAML or JSON object representing a Catalog. Custom YAML
tags are not supported. `profile` defaults to `fileroute-catalog`; it is a label,
not a schema URL. `resources` and `catalogs` MUST be keyed objects. Keys MUST match
`[A-Za-z_][A-Za-z0-9_-]*` and MUST be unique case-insensitively across both maps
within one catalog. Use `title` for display names. Names have no reserved-word list.

| Model | Fields | Meaning |
| --- | --- | --- |
| Catalog | `basePath`, `resourcePathTemplate`, `resources`, `catalogs`, `profile` | Optional directory base, application naming pattern, and child entries |
| Resource | Required `path`; optional `format` | One concrete local artifact |
| CatalogLink | Required `descriptor` only | Another local Catalog document |
| Location | Required `path`; optional provider metadata | Provenance or destination, not a catalog child |

Catalog and Resource MAY contain `title`, `description`, `entityType`, `sources`,
`targets`, and extension metadata. Locations MAY contain `serviceType`, `serviceId`,
`entityType`, `site`, `siteId`, `drive`, `driveId`, `bucket`, and `remotePath`.
Unknown metadata is preserved except for explicitly rejected obsolete fields.
CatalogLink MUST NOT combine `descriptor` with inline catalog fields.

## Path composition

* Catalog `basePath` is relative to its parent's effective base. Omission inherits
  that base. With no authored base anywhere, resources are working-root-relative;
  a grouping catalog itself has no materialized directory.
* Resource `path` is relative to its containing catalog's effective base.
* Local artifact paths MUST be relative POSIX paths without `..`, backslashes,
  URI schemes, or drive prefixes. `.` explicitly selects the current base.
* `resourcePathTemplate` is relative to the effective catalog base. Applications
  interpret placeholders; Fileroute MUST NOT expand them during transfers.
* CatalogLink `descriptor` is resolved relative to its containing **descriptor
  file**, independently of artifact bases. The linked root's `basePath` then
  composes under the parent catalog, exactly like an inline child. Cycles fail;
  independent references to the same file are allowed.
* Location `path` MUST NOT be prefixed with a catalog base.
* Transfer paths resolve under the explicit working root, defaulting to cwd,
  never automatically under the descriptor's directory.

```yaml
catalogs:
  validation_resources:
    basePath: build/resources
    resources:
      gx_configuration:
        path: gx/great_expectations.yml
    catalogs:
      spark_schemas:
        basePath: schemas/surveys
        resourcePathTemplate: "{surveyid}/{env}/v{version}/spark_schema.json"
```

The schema catalog's effective base is `build/resources/schemas/surveys`.
The GX resource's effective path is `build/resources/gx/great_expectations.yml`.
Python `walk()` and `select()` expose `effective_path`; authored model fields
remain unchanged. JSON selection output includes `effectivePath`.

## Transfers and inheritance

`sources` describe provenance; a pull requires exactly one supported remote
source. `targets` describe publication only. Missing/null targets inherit;
an explicit list replaces inherited targets, and `[]` disables publication.
A catalog with children transfers those children; a leaf catalog transfers its
directory. An unbased grouping catalog cannot transfer a directory implicitly.
Selection retains ancestor bases and targets, but checks only the selected
transfer scope's providers. Transfers validate concrete paths before writing.

## Machine-readable schema

Each stable [GitHub release](https://github.com/mbkranz/fileroute/releases) includes
`fileroute-catalog.schema.json`, generated from the same Pydantic models used by
the loader. Pin the asset from the version you use in your editor's YAML schema
association. Generate locally with `poe schema-export`; the raw schema is written
to `dist/schemas/fileroute-catalog.schema.json`.

JSON Schema validates structure. Runtime validation additionally checks name
collisions, safe composed paths, reference cycles, filesystem state, and provider
requirements. Legacy named lists, `$ref`, `$schema`, `pathTemplate`, and Catalog
`path` are not supported. There is no compatibility loader or migration command.
