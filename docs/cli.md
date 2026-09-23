# CLI Reference

Auto-generated from live `sharedrive --help` output.

## `sharedrive --help`

```text
Usage: sharedrive [OPTIONS] COMMAND [ARGS]...

  Shared drive utilities for SharePoint, Google Drive, and S3.

Options:
  --install-completion  Install completion for the current shell.
  --show-completion     Show completion for the current shell, to copy it or
                        customize the installation.
  --help                Show this message and exit.

Commands:
  update    Update descriptor-root or resource properties using flag-style...
  checkout  Activate a descriptor and optionally an entity within it for...
  list      List local descriptor entities, paths, and source metadata.
  add       Add a standards-aligned resource or catalog entry to a descriptor.
  set       Set reusable key/value parameters for sharedrive descriptor...
  auth      Authentication helpers.
  clone     Clone descriptor state for new local variants.
```

## `sharedrive auth --help`

```text
Usage: sharedrive auth [OPTIONS] COMMAND [ARGS]...

  Authentication helpers.

Options:
  --help  Show this message and exit.

Commands:
  login  Interactive login commands.
```

## `sharedrive auth login --help`

```text
Usage: sharedrive auth login [OPTIONS] COMMAND [ARGS]...

  Interactive login commands.

Options:
  --help  Show this message and exit.

Commands:
  gdrive      Run the Google installed-app OAuth flow and optionally...
  microsoft   Validate Microsoft authentication used by SharePoint workflows.
  sharepoint  Validate SharePoint authentication using the configured auth...
```

## `sharedrive auth login gdrive --help`

```text
Usage: sharedrive auth login gdrive [OPTIONS]

  Run the Google installed-app OAuth flow and optionally persist a token.

Options:
  --oauth-client-secrets PATH  Path to Google OAuth client secrets JSON.
  --oauth-token-path PATH      Path to persist the authorized-user token JSON.
  --scope TEXT                 OAuth scope. Repeat for multiple scopes.
  --no-local-server            Use the console flow instead of a local callback
                               server.
  --env-file PATH              Path to .env file for credentials. Defaults to
                               .env in the current directory.
  --help                       Show this message and exit.

  **Examples**

  ```bash

  sharedrive auth login gdrive --oauth-client-secrets .google/oauth-
  credentials.json --oauth-token-path .google/oauth-token.json

  ```

  ```bash

  sharedrive auth login gdrive --scope
  https://www.googleapis.com/auth/drive.readonly

  ```
```

## `sharedrive auth login microsoft --help`

```text
Usage: sharedrive auth login microsoft [OPTIONS]

  Validate Microsoft authentication used by SharePoint workflows.

Options:
  --auth-mode TEXT  Microsoft auth mode: app_only or delegated.
  --host-url TEXT   SharePoint host for validating Graph-backed access, for
                    example norc.sharepoint.com.
  --scope TEXT      Microsoft Graph scope. Repeat for multiple scopes.
  --env-file PATH   Path to .env file for credentials. Defaults to .env in the
                    current directory.
  --help            Show this message and exit.

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
```

## `sharedrive auth login sharepoint --help`

```text
Usage: sharedrive auth login sharepoint [OPTIONS]

  Validate SharePoint authentication using the configured auth mode.

Options:
  --auth-mode TEXT  Microsoft auth mode for SharePoint: app_only or delegated.
  --host-url TEXT   SharePoint host, for example norc.sharepoint.com.
  --scope TEXT      Microsoft Graph scope. Repeat for multiple scopes.
  --env-file PATH   Path to .env file for credentials. Defaults to .env in the
                    current directory.
  --help            Show this message and exit.

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
```

## `sharedrive checkout --help`

```text
Usage: sharedrive checkout [OPTIONS] DESCRIPTOR [ENTITY]

  Activate a descriptor and optionally an entity within it for later commands.

Arguments:
  DESCRIPTOR  Descriptor path to activate for later commands.  [required]
  [ENTITY]    Entity dot-path within the descriptor to set as the active scope
              for fetch/download commands.

Options:
  --help  Show this message and exit.

  **Examples**

  ```bash

  sharedrive checkout resources/descriptor.yaml

  ```

  ```bash

  sharedrive checkout resources/descriptor.yaml research

  ```

  ```bash

  sharedrive checkout resources/descriptor.yaml research.archive

  ```
```

## `sharedrive set --help`

```text
Usage: sharedrive set [OPTIONS] [DESCRIPTOR_SCOPE]

  Set reusable key/value parameters for sharedrive descriptor workflows.

Arguments:
  [DESCRIPTOR_SCOPE]  Descriptor path to save defaults for.

Options:
  --global           Save params as global defaults for all descriptors.
  --descriptor TEXT  Default descriptor path to save.
  --output-dir TEXT  Default output directory to save.
  --help             Show this message and exit.

  **Examples**

  ```bash

  sharedrive set --global --descriptor resources/descriptor.yaml

  ```

  ```bash

  sharedrive set --global --output-dir resources

  ```

  ```bash

  sharedrive set resources/descriptor.yaml --output-dir exports

  ```
```

## `sharedrive add --help`

```text
Usage: sharedrive add [OPTIONS] NAME

  Add a standards-aligned resource or catalog entry to a descriptor.

Arguments:
  NAME  Resource name to store in the descriptor.  [required]

Options:
  --catalog          Treat as a catalog with accessURL.
  --descriptor PATH  Descriptor file path. Defaults to the saved descriptor or
                     the first standard descriptor path.
  --help             Show this message and exit.

  **Examples**

  ```bash

  sharedrive add my-resource --path https://drive.google.com/file/d/123...
  --cache downloads/file.csv

  ```

  ```bash

  sharedrive add my-folder --catalog --accessURL
  https://drive.google.com/drive/folders/abc...

  ```
```

## `sharedrive update --help`

```text
Usage: sharedrive update [OPTIONS]

  Update descriptor-root or resource properties using flag-style field edits.

Options:
  --descriptor PATH  Descriptor file path. Defaults to the saved descriptor or
                     the first standard descriptor path.
  --name TEXT        Exact entity name or dot-path to update.
  --dry-run          Show what would be updated without writing files.
  --help             Show this message and exit.

  **Examples**

  ```bash

  sharedrive update --title "Hello" --description "hello"

  ```

  ```bash

  sharedrive update --name file1 --title "Hello" --description "hello"

  ```

  ```bash

  sharedrive update --descriptor resources/descriptor.yaml --name file1 --title
  "Hello"

  ```
```
