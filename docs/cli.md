# CLI Reference

Shared drive utilities for SharePoint, Google Drive, and S3.

**Usage**:

```console
$ sharedrive [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--install-completion`: Install completion for the current shell.
* `--show-completion`: Show completion for the current shell, to copy it or customize the installation.
* `--help`: Show this message and exit.

**Commands**:

* `push`: Publish artifact paths to targets; create...
* `pull`: Materialize a single remote source into...
* `resolve`: Preview inferred provider metadata,...
* `migrate`: Convert a legacy descriptor to...
* `update`: Update descriptor-root or resource...
* `activate`: Activate a descriptor for later commands.
* `list`: List local descriptor entities, paths, and...
* `add`: Add a standards-aligned resource or...
* `diagram`: Render descriptor sources, artifacts, and...
* `auth`: Authentication helpers.
* `clone`: Clone descriptor state for new local...

## `sharedrive push`

Publish artifact paths to targets; create or replace, never delete.

**Usage**:

```console
$ sharedrive push [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `--dry-run`: List files without authenticating or writing.
* `--help`: Show this message and exit.

## `sharedrive pull`

Materialize a single remote source into each artifact's path.

**Usage**:

```console
$ sharedrive pull [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `--dry-run`: Plan without authenticating or writing.
* `--help`: Show this message and exit.

## `sharedrive resolve`

Preview inferred provider metadata, preserving URLs; no network access.

**Usage**:

```console
$ sharedrive resolve [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `--write`: Save resolved metadata back to this descriptor.
* `--help`: Show this message and exit.

## `sharedrive migrate`

Convert a legacy descriptor to path/sources/targets in a new file.

**Usage**:

```console
$ sharedrive migrate [OPTIONS] DESCRIPTOR OUTPUT
```

**Arguments**:

* `DESCRIPTOR`: Legacy descriptor to read.  [required]
* `OUTPUT`: New canonical descriptor to write.  [required]

**Options**:

* `--direction TEXT`: Interpret legacy remote URLs as pull sources or push targets.  [default: pull]
* `--help`: Show this message and exit.

**Examples**

```bash
sharedrive migrate old.yaml new.yaml --direction pull
```

```bash
sharedrive migrate old.yaml new.yaml --direction push
```

## `sharedrive update`

Update descriptor-root or resource properties using flag-style field edits.

**Usage**:

```console
$ sharedrive update [OPTIONS]
```

**Options**:

* `--descriptor PATH`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.
* `--name TEXT`: Exact entity name or dot-path to update.
* `--dry-run`: Show what would be updated without writing files.
* `--help`: Show this message and exit.

**Examples**

```bash
sharedrive update --title "Hello" --description "hello"
```

```bash
sharedrive update --name file1 --title "Hello" --description "hello"
```

```bash
sharedrive update --descriptor resources/descriptor.yaml --name file1 --title "Hello"
```

## `sharedrive activate`

Activate a descriptor for later commands.

**Usage**:

```console
$ sharedrive activate [OPTIONS] DESCRIPTOR
```

**Arguments**:

* `DESCRIPTOR`: Descriptor path to activate for later commands.  [required]

**Options**:

* `--help`: Show this message and exit.

**Examples**

```bash
sharedrive activate resources/descriptor.yaml
```

## `sharedrive list`

List local descriptor entities, paths, and source metadata.

**Usage**:

```console
$ sharedrive list [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `--format [text|json]`: Output format.  [default: text]
* `--help`: Show this message and exit.

**Examples**

```bash
sharedrive list
```

```bash
sharedrive list resources/descriptor.yaml
```

```bash
sharedrive list resources/descriptor.yaml --format json
```

## `sharedrive add`

Add a standards-aligned resource or catalog entry to a descriptor.

**Usage**:

```console
$ sharedrive add [OPTIONS] NAME
```

**Arguments**:

* `NAME`: Resource name to store in the descriptor.  [required]

**Options**:

* `--catalog`: Treat as a catalog of resources or a directory.
* `--descriptor PATH`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.
* `--help`: Show this message and exit.

**Examples**

```bash
sharedrive add my-resource --path downloads/file.csv --source https://drive.google.com/file/d/123...
```

```bash
sharedrive add my-folder --catalog --path docs/_output --target https://tenant.sharepoint.com/sites/docs/Published
```

## `sharedrive diagram`

Render descriptor sources, artifacts, and targets as an SVG workflow.

**Usage**:

```console
$ sharedrive diagram [OPTIONS] [DESCRIPTOR]
```

**Arguments**:

* `[DESCRIPTOR]`: Descriptor file path. Defaults to the saved descriptor or the first standard descriptor path.

**Options**:

* `-o, --output PATH`: SVG file to write.  [default: sharedrive-diagram.svg]
* `--help`: Show this message and exit.

## `sharedrive auth`

Authentication helpers.

**Usage**:

```console
$ sharedrive auth [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `login`: Interactive login commands.

### `sharedrive auth login`

Interactive login commands.

**Usage**:

```console
$ sharedrive auth login [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `gdrive`: Run the Google installed-app OAuth flow...
* `microsoft`: Validate Microsoft authentication used by...
* `sharepoint`: Validate SharePoint authentication using...

#### `sharedrive auth login gdrive`

Run the Google installed-app OAuth flow and optionally persist a token.

**Usage**:

```console
$ sharedrive auth login gdrive [OPTIONS]
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
sharedrive auth login gdrive --oauth-client-secrets .google/oauth-credentials.json --oauth-token-path .google/oauth-token.json
```

```bash
sharedrive auth login gdrive --scope https://www.googleapis.com/auth/drive.readonly
```

#### `sharedrive auth login microsoft`

Validate Microsoft authentication used by SharePoint workflows.

**Usage**:

```console
$ sharedrive auth login microsoft [OPTIONS]
```

**Options**:

* `--auth-mode TEXT`: Microsoft auth mode: app_only or delegated.
* `--host-url TEXT`: SharePoint host for validating Graph-backed access, for example norc.sharepoint.com.
* `--scope TEXT`: Microsoft Graph scope. Repeat for multiple scopes.
* `--env-file PATH`: Path to .env file for credentials. Defaults to .env in the current directory.
* `--help`: Show this message and exit.

**Examples**

```bash
sharedrive auth login microsoft
```

```bash
sharedrive auth login microsoft --auth-mode delegated
```

```bash
sharedrive auth login microsoft --host-url norc.sharepoint.com
```

#### `sharedrive auth login sharepoint`

Validate SharePoint authentication using the configured auth mode.

**Usage**:

```console
$ sharedrive auth login sharepoint [OPTIONS]
```

**Options**:

* `--auth-mode TEXT`: Microsoft auth mode for SharePoint: app_only or delegated.
* `--host-url TEXT`: SharePoint host, for example norc.sharepoint.com.
* `--scope TEXT`: Microsoft Graph scope. Repeat for multiple scopes.
* `--env-file PATH`: Path to .env file for credentials. Defaults to .env in the current directory.
* `--help`: Show this message and exit.

**Examples**

```bash
sharedrive auth login sharepoint
```

```bash
sharedrive auth login sharepoint --auth-mode delegated
```

```bash
sharedrive auth login sharepoint --host-url norc.sharepoint.com
```

## `sharedrive clone`

Clone descriptor state for new local variants.

**Usage**:

```console
$ sharedrive clone [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `--help`: Show this message and exit.

**Commands**:

* `descriptor`: Clone one descriptor file to a new local...

### `sharedrive clone descriptor`

Clone one descriptor file to a new local path.

**Usage**:

```console
$ sharedrive clone descriptor [OPTIONS] TARGET_PATH
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
sharedrive clone descriptor resources/descriptor-copy.yaml --descriptor resources/descriptor.yaml
```

```bash
sharedrive clone descriptor resources/descriptor-copy.json --descriptor resources/descriptor.yaml --dry-run
```
