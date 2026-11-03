# Authentication and client lifecycle implementation plan

Base: `main` at `5e2e25c06e1409ef831e902718f878ae98841c25`.

## Scope

Preserve catalog `authProfile` selection, private YAML/JSON profiles, provider
SDKs, environment precedence, and existing constructors. Address the observed
Microsoft token snapshot and missing HTTP session cleanup. Clarify credential
loading without introducing another settings system.

## Implementation sequence

1. Keep named profiles as pure Pydantic models and legacy configuration as
   Pydantic Settings. Document that named secret references read the process
   environment. Python callers explicitly use `load_dotenv(path, override=False)`;
   the CLI retains its existing automatic discovery and explicit-file override.
   Improve the missing-variable message and cover both paths with tests.
2. Retain the MSAL application and its in-memory token cache in Microsoft auth
   constructors. Add explicit `get_access_token()` acquisition: app-only uses
   `acquire_token_for_client`; delegated uses account selection and
   `acquire_token_silent`. MSAL owns expiry and refresh decisions. Reload and
   persist the existing private cache under its existing lock. Only the initial
   explicitly permitted delegated login can open a browser. Raw-token auth stays
   static, and `access_token` remains the most recently acquired token.
3. Keep the Microsoft auth object in SharePoint clients and acquire a usable token
   before each authenticated attempt. Preserve public token/header attributes,
   custom headers, safe retry policy, and unauthenticated presigned downloads.
   Do not replay a failed mutation in response to an authentication error.
4. Add a nonabstract `BaseClient.close()` and context manager support without
   imposing a new abstract requirement on providers. SharePoint and Google close
   only sessions they create; injected sessions remain caller-owned. Allocate
   sessions only after validating constructor arguments. Close internally
   constructed HTTP clients after transfers, online resolution, and profile
   checks, including partial setup and failure paths, using `ExitStack`.
5. Update README, authentication guidance, `.env-sample`, and generated API docs.
   Add regression tests for renewal, noninteractive failure, cache persistence,
   secret-source behavior, and cleanup on success/failure. Run repository checks,
   schema freshness checks, generated-doc checks, and the strict docs build.

## Design review before implementation

- **Explicit behavior:** token acquisition is a method because it may perform I/O;
  reading `access_token` does not unexpectedly contact an identity provider.
- **Single responsibility:** MSAL handles tokens, profile models describe
  identities, provider clients handle requests, and operation scopes own cleanup.
- **Small surface:** retain Requests and YAML/JSON; add no package, registry,
  generic transport framework, settings-source layer, or mandatory dependency.
- **Compatibility:** existing constructors, CLI commands, profile schema, and raw
  token callers remain supported. Session ownership is explicit and documented.
- **Failure behavior:** refresh failures stop before a request is sent; runtime
  delegated acquisition never opens a browser. Existing locks and atomic writes
  protect persistent credentials.
- **Scope decision:** optional keyring storage is deferred. Private cache files
  retain their current protections; no demonstrated desktop requirement justifies
  another credential backend. S3 transport ownership is outside this HTTP fix.

Review outcome: proceed with these bounded, additive changes.

## References

- [Zen of Python](https://peps.python.org/pep-0020/).
- [MSAL token acquisition](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens).
- [MSAL cache serialization](https://learn.microsoft.com/en-us/entra/msal/python/advanced/msal-python-token-cache-serialization).
- [Requests sessions and context managers](https://requests.readthedocs.io/en/latest/user/advanced/#session-objects).
- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).
- [Microsoft Graph drive API](https://learn.microsoft.com/en-us/graph/api/resources/onedrive?view=graph-rest-1.0).
- [Google Drive v3 API](https://developers.google.com/workspace/drive/api/reference/rest/v3).

## Final review

Completed after implementation and a corrective pass.

| Principle | Review result |
| --- | --- |
| Explicit I/O | `get_access_token()` can acquire credentials; `access_token` remains a read of the last acquired value. Python dotenv loading stays an explicit application decision. |
| Single responsibility | MSAL owns expiry and refresh. Existing profile models describe configuration. Provider clients build request headers and own their internally created HTTP sessions. |
| Resource ownership | `close()` and context managers preserve caller ownership of injected sessions. `ExitStack` closes clients on success, transfer/lookup errors, and partial profile setup. |
| Small abstractions | One private cache-acquisition helper serves both Microsoft modes. Existing transfer-client preparation becomes a context manager. No production module, package, registry, transport framework, or dependency was added. |
| Compatibility | Existing constructor parameters, CLI commands, profile schemas, environment precedence, and raw-token behavior are retained. `BaseClient.close()` is nonabstract. |
| Failure clarity | Runtime delegated renewal cannot open a browser. Token errors avoid raw provider payloads. A missing named secret explains explicit dotenv loading. Authentication setup errors retain their login guidance in URL checks. |
| Scope | Keyring and S3 transport ownership remain deferred. Requests, Pydantic model/settings separation, and YAML/JSON remain in use. |

The final review corrected two implementation details: URL checks now keep
authentication setup outside the metadata-error handler, and HTTP session
ownership remains fixed across repeated cleanup. It also strengthened exception
propagation assertions and removed formatting churn from unchanged definitions.
The subsequent review found no remaining blocking issue in the changed scope.

### Complexity check

- Eight existing production modules changed: 266 lines added and 164 removed
  (net 102 lines). Transfer and resolution diffs include indentation needed for
  their cleanup scopes.
- No production module or dependency was added. One documentation-generation
  module now includes the base client API. Public additions are
  `MicrosoftAuth.get_access_token()`, client `close()`, and client context support.
- Twenty-two additional regression cases bring the suite from 486 to 508 tests.
  Existing client doubles now implement the lifecycle methods they exercise.

### Validation

`poe release-check` passed, including schema freshness, Ruff, all **508 tests**,
generated API/CLI and example freshness, and the strict documentation build.
`git diff --check` passed. Documentation was regenerated with `poe docs-update`.
Provider/token tests use controlled SDK/HTTP doubles and temporary credential
caches; live Microsoft or Google authentication was not exercised.
