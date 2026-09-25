# CLI Reference

Shared drive utilities for SharePoint, Google Drive, and S3.

**Usage**:

```console
$ fileroute [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `push`: Publish artifact paths to targets; create...
* `pull`: Materialize a single remote source into...
* `resolve`: Resolve locators offline by default;...
* `migrate-format`: Convert named lists and $ref links to...
* `migrate`: Convert a legacy descriptor to...
* `update`: Update descriptor-root or resource...
* `activate`: Activate a descriptor for later commands.
* `list`: List registered names, origins, exact...
* `add`: Add a standards-aligned resource or...
* `diagram`: Render descriptor sources, artifacts, and...
* `auth`: Authentication helpers.
* `clone`: Clone descriptor state for new local...

## `fileroute push`

Publish artifact paths to targets; create or replace, never delete.

**Usage**:

```console
$ fileroute push [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `--dry-run`: List files without authenticating or writing.
* `--help`: Show this message and exit.

## `fileroute pull`

Materialize a single remote source into each artifact's path.

**Usage**:

```console
$ fileroute pull [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `--dry-run`: Plan without authenticating or writing.
* `--help`: Show this message and exit.

## `fileroute resolve`

Resolve locators offline by default; --online verifies IDs and types.

**Usage**:

```console
$ fileroute resolve [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `--select TEXT`: Registered name or exact address. Use fileroute list for names and selectors.
* `--write`: Save resolved metadata back to this descriptor.
* `--online`: Verify and enrich remote locations using provider credentials.
* `--help`: Show this message and exit.

## `fileroute migrate-format`

Convert named lists and $ref links to keyed descriptors atomically.

**Usage**:

```console
$ fileroute migrate-format [OPTIONS] DESCRIPTOR OUTPUT
```

**Arguments**:

* `DESCRIPTOR`: Legacy list-shaped descriptor.  [required]
* `OUTPUT`: New directory for the converted file graph.  [required]

**Options**:

* `--dry-run`: Validate and report all outputs without writing.
* `--help`: Show this message and exit.

## `fileroute migrate`

Convert a legacy descriptor to path/sources/targets in a new file.

**Usage**:

```console
$ fileroute migrate [OPTIONS] DESCRIPTOR OUTPUT
```

**Arguments**:

* `DESCRIPTOR`: Legacy descriptor to read.  [required]
* `OUTPUT`: New canonical descriptor to write.  [required]

**Options**:

* `--direction TEXT`: Interpret legacy remote URLs as pull sources or push targets.  [default: pull]
* `--help`: Show this message and exit.

**Examples**

```bash
fileroute migrate old.yaml new.yaml --direction pull
```

```bash
fileroute migrate old.yaml new.yaml --direction push
```

## `fileroute update`

Update descriptor-root or resource properties using flag-style field edits.

**Usage**:

```console
$ fileroute update [OPTIONS]
```

**Options**:

* `--descriptor PATH`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.
* `--name, --select TEXT`: Entity or location by name, dot-path, JSON Pointer, or exact JSONPath (optional $). Defaults to the descriptor root. Use 'fileroute list' to see exact JSONPaths.
* `--dry-run`: Show what would be updated without writing files.
* `--help`: Show this message and exit.

**Examples**

```bash
fileroute update --title "Hello" --description "hello"
```

```bash
fileroute update --name file1 --title "Hello" --description "hello"
```

```bash
fileroute update --select '$.catalogs.docs.resources.guide' --title 'Hello'
```

## `fileroute activate`

Activate a descriptor for later commands.

**Usage**:

```console
$ fileroute activate [OPTIONS] DESCRIPTOR
```

**Arguments**:

* `DESCRIPTOR`: Descriptor path to activate for later commands.  [required]

**Options**:

* `--help`: Show this message and exit.

**Examples**

```bash
fileroute activate resources/descriptor.yaml
```

## `fileroute list`

List registered names, origins, exact selectors and local metadata.

**Usage**:

```console
$ fileroute list [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `--format [text|json]`: Output format.  [default: text]
* `--kind TEXT`: Filter catalogs or resources: all, catalog, resource.  [default: all]
* `--select TEXT`: Show one entity, location, or catalog subtree by name, JSON Pointer, or exact JSONPath (optional $). Use 'fileroute list' to see exact JSONPaths.
* `--help`: Show this message and exit.

**Examples**

```bash
fileroute list
```

```bash
fileroute list resources/descriptor.yaml
```

```bash
fileroute list resources/descriptor.yaml --format json
```

```bash
fileroute list --select '$.catalogs.docs.resources.guide'
```

## `fileroute add`

Add a standards-aligned resource or catalog entry to a descriptor.

**Usage**:

```console
$ fileroute add [OPTIONS] NAME
```

**Arguments**:

* `NAME`: Resource name to store in the descriptor.  [required]

**Options**:

* `--catalog`: Treat as a catalog of resources or a directory.
* `--descriptor PATH`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.
* `--parent TEXT`: Catalog parent by name, dot-path, JSON Pointer, or exact JSONPath (optional $); defaults to root. Use 'fileroute list' to see exact JSONPaths.
* `--help`: Show this message and exit.

**Examples**

```bash
fileroute add my-resource --path downloads/file.csv --source https://drive.google.com/file/d/123...
```

```bash
fileroute add my-folder --catalog --path docs/_output --parent '$.catalogs.docs'
```

## `fileroute diagram`

Render descriptor sources, artifacts, and targets as SVG, HTML, Markdown, or Mermaid.

**Usage**:

```console
$ fileroute diagram [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `-o, --output PATH`: Output .svg, .html, .md, or .mmd file (Markdown also writes a companion SVG).  [default: fileroute-diagram.svg]
* `--detail [summary|full]`: Metadata exported in HTML/Markdown; SVG remains compact.  [default: summary]
* `--help`: Show this message and exit.

## `fileroute auth`

Authentication helpers.

**Usage**:

```console
$ fileroute auth [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `login`: Interactive login commands.

### `fileroute auth login`

Interactive login commands.

**Usage**:

```console
$ fileroute auth login [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `gdrive`: Run the Google installed-app OAuth flow...
* `microsoft`: Validate Microsoft authentication used by...
* `sharepoint`: Validate SharePoint authentication using...

#### `fileroute auth login gdrive`

Run the Google installed-app OAuth flow and optionally persist a token.

**Usage**:

```console
$ fileroute auth login gdrive [OPTIONS]
```

**Options**:

* `--oauth-client-secrets PATH`: Path to Google OAuth client secrets JSON.
* `--oauth-token-path PATH`: Path to persist the authorized-user token JSON.
* `--scope TEXT`: OAuth scope. Repeat for multiple scopes.
* `--no-local-server`: Use the console flow instead of a local callback server.
* `--env-file PATH`: Path to .env file for credentials. Defaults to .env in the current directory.
* `--help`: Show this message and exit.

**Examples**

```bash
fileroute auth login gdrive --oauth-client-secrets .google/oauth-credentials.json --oauth-token-path .google/oauth-token.json
```

```bash
fileroute auth login gdrive --scope https://www.googleapis.com/auth/drive.readonly
```

#### `fileroute auth login microsoft`

Validate Microsoft authentication used by SharePoint workflows.

**Usage**:

```console
$ fileroute auth login microsoft [OPTIONS]
```

**Options**:

* `--auth-mode TEXT`: Microsoft auth mode: app_only or delegated.
* `--host-url TEXT`: SharePoint host for validating Graph-backed access, for example norc.sharepoint.com.
* `--scope TEXT`: Microsoft Graph scope. Repeat for multiple scopes.
* `--env-file PATH`: Path to .env file for credentials. Defaults to .env in the current directory.
* `--help`: Show this message and exit.

**Examples**

```bash
fileroute auth login microsoft
```

```bash
fileroute auth login microsoft --auth-mode delegated
```

```bash
fileroute auth login microsoft --host-url norc.sharepoint.com
```

#### `fileroute auth login sharepoint`

Validate SharePoint authentication using the configured auth mode.

**Usage**:

```console
$ fileroute auth login sharepoint [OPTIONS]
```

**Options**:

* `--auth-mode TEXT`: Microsoft auth mode for SharePoint: app_only or delegated.
* `--host-url TEXT`: SharePoint host, for example norc.sharepoint.com.
* `--scope TEXT`: Microsoft Graph scope. Repeat for multiple scopes.
* `--env-file PATH`: Path to .env file for credentials. Defaults to .env in the current directory.
* `--help`: Show this message and exit.

**Examples**

```bash
fileroute auth login sharepoint
```

```bash
fileroute auth login sharepoint --auth-mode delegated
```

```bash
fileroute auth login sharepoint --host-url norc.sharepoint.com
```

## `fileroute clone`

Clone descriptor state for new local variants.

**Usage**:

```console
$ fileroute clone [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `descriptor`: Clone one descriptor file to a new local...

### `fileroute clone descriptor`

Clone one descriptor file to a new local path.

**Usage**:

```console
$ fileroute clone descriptor [OPTIONS] TARGET_PATH
```

**Arguments**:

* `TARGET_PATH`: Target descriptor path for the clone.  [required]

**Options**:

* `--descriptor PATH`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.
* `--dry-run`: Show what would be cloned without writing files.
* `--force`: Overwrite an existing target descriptor.
* `--help`: Show this message and exit.

**Examples**

```bash
fileroute clone descriptor resources/descriptor-copy.yaml --descriptor resources/descriptor.yaml
```

```bash
fileroute clone descriptor resources/descriptor-copy.json --descriptor resources/descriptor.yaml --dry-run
```
