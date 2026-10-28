# JSON Schemas and editor assistance

Fileroute publishes two JSON Schema documents: one for catalogs and one for
authentication profile configuration. They include the nested resource,
location, catalog-link, and authentication-profile definitions. Field help,
examples, defaults, provider choices, and authentication-mode constraints help
editors suggest valid configuration as you type.

| Document | Schema file | Starter |
| --- | --- | --- |
| Catalog, resources, sources, and targets | `fileroute-catalog.schema.json` | [YAML catalog](https://github.com/mbkranz/fileroute/blob/main/examples/use-cases/profile-identities.yaml), [JSON catalog](https://github.com/mbkranz/fileroute/blob/main/examples/catalog.json) |
| Named authentication profiles | `fileroute-auth-profiles.schema.json` | [YAML profiles](https://github.com/mbkranz/fileroute/blob/main/examples/auth/profiles.yaml), [JSON profiles](https://github.com/mbkranz/fileroute/blob/main/examples/auth/profiles.json) |

The authoritative inputs are the Python models, including their shared
provider/mode rules. Exported schemas use JSON Schema Draft 2020-12 and contain
their definitions locally; validation does not require credentials or fetch
provider data. OAuth client files, downloaded service-account files, and token
caches remain provider-owned formats and are not Fileroute configuration.

## Available schema locations

In a checkout, `schemas/` contains generated schemas for the current package
version. The included VS Code settings and YAML templates use these local files,
so source editing works before a release and without downloading a schema.

Each stable release attaches both schema files to its GitHub Release. Use an
exact version for reproducible editor validation:

```text
https://github.com/mbkranz/fileroute/releases/download/vVERSION/fileroute-catalog.schema.json
https://github.com/mbkranz/fileroute/releases/download/vVERSION/fileroute-auth-profiles.schema.json
```

Replace `VERSION` with the installed Fileroute version. Convenient latest-stable
links are also available after a release containing those assets:

- [Latest catalog schema](https://github.com/mbkranz/fileroute/releases/latest/download/fileroute-catalog.schema.json)
- [Latest authentication profile schema](https://github.com/mbkranz/fileroute/releases/latest/download/fileroute-auth-profiles.schema.json)

A draft branch's new schemas become release assets when that version is
published. Use the repository's local schemas for unreleased changes. A schema's
`$id` identifies the exact versioned release asset; it is not a command to
publish a schema to a separate registry.

## YAML completion and validation

Use an editor with YAML Language Server support, such as VS Code with the
recommended Red Hat YAML extension. A comment associates one YAML document
with a schema without changing its configuration data:

```yaml
# yaml-language-server: $schema=https://github.com/mbkranz/fileroute/releases/latest/download/fileroute-auth-profiles.schema.json
profiles:
  archive-reader:
    provider: S3
    aws_profile: team-dev
```

For a catalog, use the catalog schema URL:

```yaml
# yaml-language-server: $schema=https://github.com/mbkranz/fileroute/releases/latest/download/fileroute-catalog.schema.json
profile: fileroute-catalog
resources:
  report:
    path: artifacts/report.csv
    sources:
      - path: s3://example-reports/report.csv
        authProfile: archive-reader
```

For stable installed packages, Fileroute writes a version-pinned schema comment
when `auth configure` saves a YAML profile file. A source checkout uses its
local schema file URI, so private profiles get editor assistance before release.
An installed prerelease or unpackaged installation without local schemas uses
the latest stable schema; point its modeline to a matching checkout schema for
unreleased fields. Source templates use relative paths to `schemas/`; when
copying a template to a different directory, update that relative path or use
a released schema URL. Relative modelines resolve beside the YAML document.
See [YAML Language Server associations](https://github.com/redhat-developer/yaml-language-server#associating-schemas).

Trigger your editor's suggestions while entering a key or value. Profile
definitions offer provider and mode choices and describe the fields needed by
each of the six supported provider/mode combinations. Use canonical provider
values `SharePoint`, `GoogleDrive`, and `S3` in schema-guided authoring; the
runtime also accepts its documented provider aliases. Defaults in a schema are
suggestions; schema validation does not insert them into your file.

## JSON completion and validation

The repository's `.vscode/settings.json` associates catalog and profile
filenames with local schemas. The JSON starters contain ordinary configuration
data. Fileroute does not accept `$schema` as a catalog/profile configuration
field, so use editor associations for JSON.

For files outside this checkout, add appropriate mappings to your editor's
user or workspace settings. For example:

```json
{
  "json.schemas": [
    {
      "fileMatch": ["**/fileroute/profiles.json"],
      "url": "https://github.com/mbkranz/fileroute/releases/latest/download/fileroute-auth-profiles.schema.json"
    },
    {
      "fileMatch": ["**/catalog.json", "**/fileroute.json"],
      "url": "https://github.com/mbkranz/fileroute/releases/latest/download/fileroute-catalog.schema.json"
    }
  ]
}
```

Adjust file patterns for your configuration directory and catalog names, and
pin the release URLs when using a fixed package version. `.json` profiles saved
by `auth configure --config PATH` are written as JSON. VS Code offers field and
value completion, hover descriptions, and structural validation from the schema.
See [VS Code JSON schema settings](https://code.visualstudio.com/docs/languages/json#json-schemas-and-settings).

## What editor validation covers

The catalog schema checks nested shapes, required paths, registered-name
patterns, provider spellings, and authentication-profile name syntax. The
profile schema checks supported modes, required settings, and incompatible
provider/mode fields. Use it to catch missing service-account file references,
app-only secret references, and misplaced provider options before running a
command.

Private profile names cannot be enumerated by a public static schema, and a
credential reference in a schema does not prove the file exists or the account
has access. Runtime checks still establish profile lookup, provider matching,
case-insensitive catalog-name collisions, linked-file boundaries/cycles, local
filesystem state, and remote permissions. Editor completion details depend on
the language service; schema validation is the shared contract.

## Maintain and publish schemas

After changing an authored configuration model or its schema metadata:

```bash
poe schema-update
poe schema-check
poe docs-update
poe release-check
```

`schema-update` regenerates the two checked-in files. `schema-check` verifies
their exact model-derived contents without changing files and runs in PR and
release checks. `schema-export` writes the complete bundle under `dist/schemas/`
for release artifacts.

Release preparation bumps the package version, regenerates the checked-in and
distribution schemas, and commits the exact version's source schemas with the
version update. Stable publication requires both assets; retries reuse the saved
bundle and compare existing assets byte for byte before uploading missing files.
See [Contributing and releases](contributing.md).
