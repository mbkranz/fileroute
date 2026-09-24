# Authentication

You can inspect descriptors, resolve provider URLs, render diagrams, and plan
`pull` or `push` with `--dry-run` without provider credentials. Actual
transfers authenticate using the provider's settings in the current project.
Copy the relevant variables from the repository's
[sample environment file](https://github.com/mbkranz/fileroute/blob/main/.env-sample)
into a local `.env` or your runtime's environment; never commit credentials.

| Provider | Authentication for transfers | CLI helper |
| --- | --- | --- |
| SharePoint | Microsoft Graph app-only or delegated token | `fileroute auth login sharepoint` |
| Google Drive | ADC, service account, or user OAuth | `fileroute auth login gdrive` for user OAuth |
| S3 | Boto3's AWS credential chain | No Fileroute login command |

## SharePoint and Microsoft Graph

For unattended transfers, configure an Entra application with the Microsoft
Graph permissions your SharePoint locations require. Set the tenant ID,
client ID, and client secret in the runtime environment:

```env
SHAREPOINT_AUTH_MODE=app_only
AZURE_TENANT_ID=your-tenant-id
AZURE_CLIENT_ID=your-client-id
AZURE_CLIENT_SECRET=your-client-secret
SHAREPOINT_HOST_URL=your-tenant.sharepoint.com
SHAREPOINT_SCOPES=https://graph.microsoft.com/.default
```

For a local, interactive account, set `SHAREPOINT_AUTH_MODE=delegated` and
use a client registration that permits delegated sign-in. This mode needs
`AZURE_TENANT_ID` and `AZURE_CLIENT_ID`; it does not require
`AZURE_CLIENT_SECRET`. Use the appropriate Graph scopes for that app.

```bash
uv run fileroute auth login sharepoint
uv run fileroute auth login sharepoint --auth-mode delegated
```

`auth login microsoft` and `auth login sharepoint` call the same Microsoft
credential helper. They acquire a Graph token; a successful message does not
prove access to a particular site, library, or file. Verify that with an
actual transfer against a development location.

## Google Drive

Set `GOOGLE_AUTH_MODE` to `adc` when Application Default Credentials are
already available, `service_account` for unattended access to files shared
with that identity, or `user_oauth` for an interactive user account.
The `auth login gdrive` helper runs the installed-app OAuth flow for
`user_oauth`; ADC and service accounts use their configured credentials
directly during transfers.

See [Google Drive OAuth and service account setup](google-auth.md) for
credential files, scopes, saved tokens, and local/headless examples.

## S3

S3 uses the standard AWS credential chain through Boto3. Set up credentials
in the environment where the transfer runs, such as a configured AWS profile
or an attached role. For a named local profile:

```bash
AWS_PROFILE=my-profile uv run fileroute pull config/fileroute.yaml
```

Fileroute checks that AWS credentials are available when it creates the S3
client. That check does not establish permissions for a particular bucket
or object. There is no `fileroute auth login s3` command.

For exact authentication flags, see the
[CLI reference](cli.md#fileroute-auth).
