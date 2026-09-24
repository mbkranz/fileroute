# Google Auth Credentials

This page explains how to set up Google Drive credentials for `fileroute`.

`fileroute` supports three Google auth modes:

- `adc`: Application Default Credentials.
- `service_account`: non-interactive automation via service account JSON.
- `user_oauth`: installed-app OAuth with a user browser login.

## Choose the right mode

- Use `user_oauth` when you need access as a specific human user.
- Use `service_account` when you need fully automated, non-interactive jobs.
- Use `adc` when your runtime environment already provides Google credentials.

## Manual setup (user OAuth)

This is the most common setup for local development.

1. In Google Cloud Console, create or select a project.
2. Enable the Google Drive API.
3. Configure OAuth consent screen.
4. Add your account as a test user if the app is in testing mode.
5. Create an OAuth Client ID of type **Desktop app**.
6. Download the client JSON.
7. Save it at `.google/oauth-credentials.json` in your repo root (or name you wish to specify in oauth client secret variable).

Example `.env` values:

```env
GOOGLE_AUTH_MODE=user_oauth
GOOGLE_OAUTH_CREDENTIALS=.google/oauth-credentials.json
GOOGLE_OAUTH_TOKEN_PATH=.google/oauth-token.json
GOOGLE_SCOPES=https://www.googleapis.com/auth/drive
GOOGLE_OAUTH_USE_LOCAL_SERVER=true
```

Run interactive login from a project that includes Fileroute:

```bash
uv run fileroute auth login gdrive \
  --oauth-client-secrets .google/oauth-credentials.json \
  --oauth-token-path .google/oauth-token.json
```

For headless/SSH terminals:

```bash
uv run fileroute auth login gdrive \
  --no-local-server \
  --oauth-client-secrets .google/oauth-credentials.json \
  --oauth-token-path .google/oauth-token.json
```

For standalone use, substitute `uvx --from
git+https://github.com/mbkranz/fileroute.git@dev fileroute` for
`uv run fileroute`. See the README's [usage options](https://github.com/mbkranz/fileroute#choose-how-to-run-it)
for the difference between the project environment and a separate tool environment.

## Programmatic and CI setup (service account)

If you need fully automated jobs with no browser step, use `service_account` mode.

1. Create a service account in Google Cloud.
2. Grant it access to required Drive resources (share folders/files with the service account email, or use domain-wide delegation if appropriate).
3. Download service account JSON and store it securely.
4. Configure env vars:

```env
GOOGLE_AUTH_MODE=service_account
GOOGLE_SERVICE_ACCOUNT_CREDENTIALS=.google/service-account.json
GOOGLE_SCOPES=https://www.googleapis.com/auth/drive.readonly
```

You can also use `GOOGLE_APPLICATION_CREDENTIALS` instead of `GOOGLE_SERVICE_ACCOUNT_CREDENTIALS`.

## What can and cannot be fully automated

- Creating local files and `.env` values can be scripted.
- Calling `fileroute auth login gdrive` can be scripted.
- Reusing saved OAuth token JSON is automatic.
- The first user consent grant for `user_oauth` is interactive by Google design.

## Common path issue

If you see `No such file or directory: '.google/oauth-credentials.json'`, your current working directory is likely not the repo root.

- Run commands from the project root.
- Or set an absolute path in `GOOGLE_OAUTH_CREDENTIALS`.
