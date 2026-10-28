# Authentication

Configure a named profile once, sign in when required, and select it for
transfers. A profile describes a provider identity and authentication method.
Session tokens are cached privately per profile. Source and target locations
remain in the catalog.

Offline `list`, `resolve`, diagrams, and transfer dry runs do not read profiles
or require provider credentials. Online resolution reads metadata; actual
transfers download or upload content.

## Configure and use a profile

```bash
uv run fileroute auth configure work --provider sharepoint
uv run fileroute auth login sharepoint --profile work
uv run fileroute auth show work
uv run fileroute auth check --profile work
uv run fileroute pull config/fileroute.yaml --profile work
```

`configure` prompts for missing settings in an interactive terminal. Complete
flag-based configuration works in scripts. With no `--mode`, SharePoint uses
`app_only`, Google Drive uses `adc`, and S3 uses `default`. Choose `delegated`
for an interactive Microsoft account or `user_oauth` for a Google user.
An existing profile requires `--replace` to update it.

```bash
uv run fileroute auth configure work-reader --provider sharepoint \
  --mode delegated --tenant-id YOUR_TENANT_ID --client-id YOUR_CLIENT_ID \
  --host-url contoso.sharepoint.com
uv run fileroute auth login sharepoint --profile work-reader
uv run fileroute auth check --profile work-reader \
  --url 'https://contoso.sharepoint.com/sites/data/Shared%20Documents/report.csv'
uv run fileroute auth list
```

Configuration does not register cloud applications, grant permissions, or
contact the provider. A successful authentication check establishes that
credentials can be acquired. Adding `--url` also checks read access to that
location. It does not write a test file or prove publication permission.

Use `auth remove NAME` to remove a local profile and its Fileroute token cache.
This is not provider-side credential revocation; revoke grants or credentials in
the provider when needed. Finish active login commands before replacing or
removing a profile; configuration changes do not cancel a login already running.
See the [CLI reference](cli.md#fileroute-auth) for exact flags.

## Storage and selection

The default configuration is `$XDG_CONFIG_HOME/fileroute/profiles.yaml`, or
`~/.config/fileroute/profiles.yaml` when `XDG_CONFIG_HOME` is unset.
`FILEROUTE_CONFIG_DIR` selects a different directory. Auth-management commands
also accept `--config PATH`; set `FILEROUTE_CONFIG_DIR` for subsequent transfers
to find that configuration. Relative credential paths resolve beside the
configuration file. Credential paths passed to `auth configure` are anchored to
the invocation's working directory. Guided setup records credential references,
not raw secrets.

Profile names are case-sensitive and use 1–64 ASCII letters, digits, `.`, `_` or
`-`, starting with a letter or digit. See the synthetic
[profile configuration template](https://github.com/mbkranz/fileroute/blob/main/examples/auth/profiles.yaml).
Do not commit live configuration, downloaded credential files, or token caches.
Local file protection is not encryption or a replacement for a secret manager.

Selection order is:

1. The source or target location's `authProfile`.
2. The invocation's `--profile` default, or `profile=` in the Python API.
3. Existing provider environment/settings when neither selects a profile.

```yaml
resources:
  report:
    path: artifacts/report.csv
    sources:
      - path: https://contoso.sharepoint.com/sites/data/Shared%20Documents/report.csv
        authProfile: work-reader
    targets:
      - path: https://drive.google.com/drive/folders/FOLDER_ID
        authProfile: partner-writer
```

The two locations can use different providers or different accounts of the
same provider. Inherited targets retain their profile. An explicitly selected
profile must exist and match the provider; failure never silently selects
another identity. A single `--profile` applies to all locations without their
own `authProfile`, so mixed-provider catalogs should select profiles per location.
The catalog's top-level `profile: fileroute-catalog` is unrelated metadata.

Named profile settings are authoritative. Legacy `GOOGLE_*`, `AZURE_*`, and
`SHAREPOINT_*` settings do not override their fields. Explicit secret environment
references, Google ADC, and the native AWS credential chain still resolve through
their corresponding environment/provider mechanisms. Named-profile transfers
and checks never open a browser; run explicit login when a cached session
cannot be used. Offline commands preserve profile references without looking
them up, so offline success is not an authentication check.

## SharePoint and Microsoft Graph

Register an Entra application and grant the Microsoft Graph permissions needed
for the intended sites. For local delegated use, the registration must permit
interactive public-client sign-in. Configure the tenant, client, host, and
appropriate scopes. `auth login sharepoint --profile NAME` persists its MSAL
session so later commands can acquire tokens silently. `auth login microsoft`
is an alias for the same provider.

For app-only automation, name the environment variable that supplies the secret:

```bash
uv run fileroute auth configure work-automation --provider sharepoint \
  --mode app_only --tenant-id YOUR_TENANT_ID --client-id YOUR_CLIENT_ID \
  --host-url contoso.sharepoint.com --client-secret-env WORK_AZURE_CLIENT_SECRET
uv run fileroute auth check --profile work-automation
```

Supply `WORK_AZURE_CLIENT_SECRET` through your runtime or secret manager before
the check. Configuration stores its name only. Repeat `--scope` to specify
multiple Graph scopes; the default is `https://graph.microsoft.com/.default`.
Token acquisition does not prove access to a particular site, library, or file.
Use `auth check --url` or online resolution to verify the location's read access.

## Google Drive

Choose `adc` for Application Default Credentials, `service_account` for a
credential-file reference, or `user_oauth` for an interactive user account.

```bash
uv run fileroute auth configure partner-writer --provider gdrive \
  --mode user_oauth --oauth-client-secrets /secure/google-desktop.json \
  --scope https://www.googleapis.com/auth/drive
uv run fileroute auth login gdrive --profile partner-writer
```

OAuth tokens are saved per profile and refreshed when possible. Changing scopes
can require another consent grant; editing configuration cannot grant access.
For service accounts use `--mode service_account --credentials-file PATH` and
share the relevant resources with that account. For ADC use `--mode adc` and
prepare credentials with your Google runtime tooling. See
[Google Drive setup](google-auth.md) for provider prerequisites and scope limits.

## S3 and AWS profiles

Reuse an existing AWS profile, including its IAM Identity Center session:

```bash
aws configure sso --profile team-dev
aws sso login --profile team-dev
uv run fileroute auth configure archive-reader --provider s3 --aws-profile team-dev
uv run fileroute auth check --profile archive-reader
uv run fileroute pull config/fileroute.yaml --profile archive-reader
```

Fileroute selects a Boto3 session bound to `team-dev`; it does not copy AWS keys
or manage AWS SSO login. `auth login s3 --profile archive-reader` checks the
existing credential chain and points to native AWS login when needed. Omitting
`--aws-profile` uses Boto3's default credential chain, suitable for an attached
role in automation. A credentials-only check does not prove bucket permissions;
add a concrete S3 URL for a location read check.

AWS SSO profiles refer to an `sso-session` and an account/role. Fileroute adds a
provider-independent identity name above that existing AWS configuration. See
[AWS configuration](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).

## Existing environment workflows and CI

Profiles are optional. Existing `.env`, runtime environment, provider login,
and direct Python auth-object workflows remain available when no profile is
selected. Start from the repository's
[sample environment file](https://github.com/mbkranz/fileroute/blob/main/.env-sample).

For example, legacy Microsoft app-only setup still uses
`SHAREPOINT_AUTH_MODE`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`,
`AZURE_CLIENT_SECRET`, `SHAREPOINT_HOST_URL`, and `SHAREPOINT_SCOPES`.
Legacy S3 use can select `AWS_PROFILE=team-dev`. Existing `--env-file` behavior
is unchanged: an explicitly selected file overrides the process environment.

For CI, generate private profile configuration in the runner's configuration
directory and inject secrets or credential files through the runner's existing
secret mechanism. Choose noninteractive ADC, service-account, app-only, or
AWS role/profile authentication. Avoid depending on an interactive user's
cached tokens. Configuration, offline plans, and synthetic tests do not
establish that a deployed account has the required cloud permissions.
