"""Named, user-local authentication configuration with explicit credential references.

Profiles follow DVC's useful separation of shared locations from local identity,
without storing raw secrets in authored configuration. Validation is pure;
credential files, environment references, native SDK chains and caches are read
only when an explicit authentication/client-building operation executes.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from io import StringIO
from importlib.metadata import PackageNotFoundError, version
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import boto3
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML

from fileroute.auth.google import DEFAULT_DRIVE_SCOPES, GoogleAuth
from fileroute.auth.microsoft import DEFAULT_MICROSOFT_GRAPH_SCOPES, MicrosoftAuth
from fileroute.auth.token_store import atomic_private_write, private_file_lock
from fileroute.clients.base import BaseClient
from fileroute.models import ServiceType

PROFILE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
ProfileName = Annotated[
    str, Field(strict=True, pattern=PROFILE_NAME_PATTERN, min_length=1, max_length=64)
]
ProfileMode = Literal[
    "adc", "service_account", "user_oauth", "app_only", "delegated", "default"
]


@dataclass(frozen=True)
class _ProfileModeRule:
    provider: ServiceType
    title: str
    fields: frozenset[str]
    required: frozenset[str] = frozenset()
    default: bool = False


# One compatibility matrix owns runtime validation and editor alternatives.
# Property types/descriptions still come from AuthProfile's Pydantic fields.
_MICROSOFT_FIELDS = frozenset({"tenant_id", "client_id", "host_url", "scopes"})
_MICROSOFT_REQUIRED = frozenset({"tenant_id", "client_id", "host_url"})
_PROFILE_MODE_RULES: dict[ProfileMode, _ProfileModeRule] = {
    "adc": _ProfileModeRule(
        ServiceType.GOOGLE_DRIVE,
        "Google Drive: Application Default Credentials",
        frozenset({"scopes"}),
        default=True,
    ),
    "service_account": _ProfileModeRule(
        ServiceType.GOOGLE_DRIVE,
        "Google Drive: service account",
        frozenset({"scopes", "credentials_file"}),
        frozenset({"credentials_file"}),
    ),
    "user_oauth": _ProfileModeRule(
        ServiceType.GOOGLE_DRIVE,
        "Google Drive: user OAuth",
        frozenset({"scopes", "oauth_client_secrets", "use_local_server"}),
        frozenset({"oauth_client_secrets"}),
    ),
    "app_only": _ProfileModeRule(
        ServiceType.SHAREPOINT,
        "SharePoint: application identity",
        _MICROSOFT_FIELDS | {"client_secret_env"},
        _MICROSOFT_REQUIRED | {"client_secret_env"},
        default=True,
    ),
    "delegated": _ProfileModeRule(
        ServiceType.SHAREPOINT,
        "SharePoint: delegated user identity",
        _MICROSOFT_FIELDS,
        _MICROSOFT_REQUIRED,
    ),
    "default": _ProfileModeRule(
        ServiceType.S3,
        "S3: native AWS credentials",
        frozenset({"aws_profile"}),
        default=True,
    ),
}
_PROVIDER_DEFAULT_MODES = {
    rule.provider: mode for mode, rule in _PROFILE_MODE_RULES.items() if rule.default
}


def _without_null(schema: dict[str, Any]) -> dict[str, Any]:
    """Narrow a generated nullable property without recreating its field schema."""
    result = deepcopy(schema)
    result.pop("default", None)
    if "anyOf" in result:
        alternatives = [
            option for option in result.pop("anyOf") if option != {"type": "null"}
        ]
        if len(alternatives) == 1:
            result.update(alternatives[0])
        else:
            result["anyOf"] = alternatives
    return result


def _auth_profile_schema(schema: dict[str, Any]) -> None:
    """Expose provider choices and conditional modes from the runtime rules.

    Mode enums sit above their required fields so editor branch ranking cannot
    hide a valid mode before the user supplies its credential references. Each
    conditional alternative reuses generated properties. Irrelevant fields are
    accepted only as explicit null, matching the runtime API, without offering
    them as normal completion properties.
    """
    generated = schema.pop("properties")
    # Keep provider value completion independent of incomplete mode settings.
    schema["properties"] = {"provider": deepcopy(generated["provider"])}
    schema.pop("required", None)
    schema.pop("additionalProperties", None)
    providers = []
    for provider, default_mode in _PROVIDER_DEFAULT_MODES.items():
        modes = {
            mode: rule
            for mode, rule in _PROFILE_MODE_RULES.items()
            if rule.provider == provider
        }
        mode_schema = _without_null(generated["mode"])
        mode_schema.pop("enum")
        mode_schema.pop("type", None)
        mode_schema.update({
            "anyOf": [{"enum": list(modes)}, {"type": "null"}],
            "default": default_mode,
            "examples": list(modes),
        })
        provider_field = deepcopy(generated["provider"])
        # YAML language services can merge a shared enum reference across const
        # branches; this branch already supplies its exact canonical value.
        provider_field.pop("$ref", None)
        provider_field.update({
            "type": "string",
            "const": provider.value,
            "default": provider.value,
            "examples": [provider.value],
        })
        provider_schema = {
            "title": f"{provider.value} authentication profile",
            "type": "object",
            "required": ["provider"],
            "properties": {
                "provider": provider_field,
                "mode": mode_schema,
            },
            "allOf": [],
            "examples": [],
        }
        for mode, rule in modes.items():
            properties: dict[str, Any] = {"provider": {}, "mode": {}}
            for name, field_schema in generated.items():
                if name not in rule.fields:
                    continue
                property_schema = (
                    _without_null(field_schema)
                    if name in rule.required
                    else deepcopy(field_schema)
                )
                if property_schema.get("default") is None:
                    property_schema.pop("default", None)
                if name == "scopes":
                    property_schema["default"] = list(
                        DEFAULT_DRIVE_SCOPES
                        if provider == ServiceType.GOOGLE_DRIVE
                        else DEFAULT_MICROSOFT_GRAPH_SCOPES
                    )
                    property_schema["examples"] = [property_schema["default"]]
                if name == "use_local_server":
                    property_schema["anyOf"] = [{"const": True}, {"type": "null"}]
                    property_schema["default"] = True
                properties[name] = property_schema
            irrelevant = sorted(set(generated) - {"provider", "mode"} - rule.fields)
            example = {
                "provider": provider.value,
                "mode": mode,
                **{
                    name: properties[name]["examples"][0]
                    for name in sorted(rule.required)
                },
            }
            condition = {
                "properties": {
                    "mode": {"enum": [mode, None]} if rule.default else {"const": mode}
                },
                **({} if rule.default else {"required": ["mode"]}),
            }
            provider_schema["allOf"].append({
                "if": condition,
                "then": {
                    "title": rule.title,
                    "properties": properties,
                    "required": sorted(rule.required),
                    "patternProperties": {
                        "^(?:" + "|".join(irrelevant) + ")$": {"type": "null"}
                    },
                    "additionalProperties": False,
                    "examples": [example],
                },
            })
            provider_schema["examples"].append(example)
        providers.append(provider_schema)
    schema["oneOf"] = providers


class ProfileError(ValueError):
    """Actionable profile failure whose message contains no credential payload."""


def _provider_alias(value: object) -> object:
    if isinstance(value, str):
        key = re.sub(r"[ _-]", "", value.strip().casefold())
        return {
            "google": ServiceType.GOOGLE_DRIVE,
            "drive": ServiceType.GOOGLE_DRIVE,
            "gdrive": ServiceType.GOOGLE_DRIVE,
            "googledrive": ServiceType.GOOGLE_DRIVE,
            "microsoft": ServiceType.SHAREPOINT,
            "sharepoint": ServiceType.SHAREPOINT,
            "s3": ServiceType.S3,
            "aws": ServiceType.S3,
        }.get(key, value)
    return value


class AuthProfile(BaseModel):
    """Provider-specific auth choices, never merged with legacy ``.env`` settings.

    Relative credential references resolve beside the profiles YAML file.
    ``client_secret_env`` names an environment variable; it never holds a secret.
    Google ADC and AWS retain their SDK-native credential discovery.
    """

    model_config = ConfigDict(
        title="Authentication profile",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        json_schema_extra=_auth_profile_schema,
    )

    provider: Annotated[ServiceType, BeforeValidator(_provider_alias)] = Field(
        description="Provider whose identity this profile selects. Use the canonical spelling shown by completion.",
        examples=["GoogleDrive", "SharePoint", "S3"],
    )
    mode: ProfileMode | None = Field(
        default=None,
        description="Authentication method. Omitted or null uses adc for GoogleDrive, app_only for SharePoint, or default for S3.",
    )
    tenant_id: str | None = Field(
        default=None,
        description="Microsoft Entra tenant ID or tenant domain; required for SharePoint.",
        examples=["example.onmicrosoft.com"],
        json_schema_extra={"pattern": r"^\s*[A-Za-z0-9.-]+\s*$"},
    )
    client_id: str | None = Field(
        default=None,
        description="Registered Microsoft application client ID; this is an identifier, not a secret.",
        examples=["00000000-0000-0000-0000-000000000000"],
        json_schema_extra={"pattern": r"\S"},
    )
    host_url: str | None = Field(
        default=None,
        description="SharePoint hostname, optionally prefixed with https://; paths, ports, credentials, queries and fragments are not allowed.",
        examples=["contoso.sharepoint.com"],
        json_schema_extra={"pattern": r"\S"},
    )
    scopes: list[str] | None = Field(
        default=None,
        description="OAuth scopes granted to this application. Google and Microsoft defaults are shown for their selected provider; changing scopes can require new consent.",
        examples=[["https://www.googleapis.com/auth/drive.readonly"]],
        json_schema_extra={
            "minItems": 1,
            "items": {"type": "string", "pattern": r"\S"},
        },
    )
    credentials_file: Path | None = Field(
        default=None,
        description="Google service-account JSON file reference. Relative paths resolve beside profiles.yaml; credential contents are never embedded here.",
        examples=["credentials/google-service-account.json"],
    )
    oauth_client_secrets: Path | None = Field(
        default=None,
        description="Google Desktop-app OAuth client JSON file reference, required for user_oauth. Relative paths resolve beside profiles.yaml.",
        examples=["credentials/google-desktop.json"],
    )
    client_secret_env: str | None = Field(
        default=None,
        description="Name of the environment variable supplying a Microsoft app-only client secret. Store the variable name here, never its secret value.",
        examples=["WORK_AZURE_CLIENT_SECRET"],
        json_schema_extra={"pattern": r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*$"},
    )
    aws_profile: str | None = Field(
        default=None,
        description="Existing AWS profile name for Boto3. Omit to use its default credential chain; AWS keys and SSO sessions stay with native AWS tooling.",
        examples=["team-dev"],
        json_schema_extra={"pattern": r"\S"},
    )
    use_local_server: bool | None = Field(
        default=None,
        description="Google installed-app OAuth uses a local callback server. user_oauth accepts true or null (effective true); false is unsupported.",
        examples=[True],
    )

    @field_validator(
        "tenant_id", "client_id", "host_url", "client_secret_env", "aws_profile"
    )
    @classmethod
    def nonempty_text(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("Configured values must not be empty.")
        return value

    @field_validator("scopes")
    @classmethod
    def nonempty_scopes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if not value or any(not scope.strip() for scope in value):
            raise ValueError("scopes must contain nonempty scope names.")
        return list(dict.fromkeys(scope.strip() for scope in value))

    @field_validator("client_secret_env")
    @classmethod
    def environment_reference(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is None:
            raise ValueError("client_secret_env must name an environment variable.")
        return value

    @field_validator("tenant_id")
    @classmethod
    def tenant_reference(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[A-Za-z0-9.-]+", value) is None:
            raise ValueError("tenant_id must be a tenant identifier or domain.")
        return value

    @field_validator("host_url")
    @classmethod
    def normalize_host(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value if "://" in value else f"https://{value}")
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "host_url must be a SharePoint HTTPS hostname without a path."
            )
        return parsed.hostname

    @model_validator(mode="after")
    def provider_configuration(self) -> AuthProfile:
        mode = self.mode or _PROVIDER_DEFAULT_MODES[self.provider]
        rule = _PROFILE_MODE_RULES[mode]
        if rule.provider != self.provider:
            raise ValueError("Auth mode is unsupported for this provider.")
        for field in type(self).model_fields:
            if (
                field not in {"provider", "mode"}
                and getattr(self, field) is not None
                and field not in rule.fields
            ):
                raise ValueError(
                    f"{field} is unsupported in {mode} mode for this provider."
                )
        missing = sorted(
            field for field in rule.required if getattr(self, field) is None
        )
        if missing:
            raise ValueError(f"{mode} mode requires {', '.join(missing)}.")
        if self.provider == ServiceType.GOOGLE_DRIVE:
            if self.use_local_server is False:
                raise ValueError(
                    "Google installed-app OAuth requires use_local_server=true."
                )
            object.__setattr__(
                self, "scopes", self.scopes or list(DEFAULT_DRIVE_SCOPES)
            )
            if mode == "user_oauth":
                object.__setattr__(self, "use_local_server", True)
        elif self.provider == ServiceType.SHAREPOINT:
            object.__setattr__(
                self, "scopes", self.scopes or list(DEFAULT_MICROSOFT_GRAPH_SCOPES)
            )
        object.__setattr__(self, "mode", mode)
        return self


class ProfileConfig(BaseModel):
    """Validated contents of a profiles YAML file; parsing performs no auth I/O."""

    model_config = ConfigDict(
        title="Fileroute authentication profiles",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )
    profiles: dict[ProfileName, AuthProfile] = Field(
        default_factory=dict,
        description="Map local identity names to provider authentication settings. Catalog authProfile references use these case-sensitive names. Never store raw secrets or session tokens here.",
        examples=[{"archive-reader": {"provider": "S3", "aws_profile": "team-dev"}}],
        json_schema_extra={"propertyNames": {"pattern": PROFILE_NAME_PATTERN}},
    )


class ProfileReceipt(BaseModel):
    """Safe, serializable evidence of a completed profile operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: ProfileName
    provider: ServiceType
    mode: ProfileMode
    action: Literal["saved", "removed", "authenticated"]
    config_path: Path


def _config_path(config_path: Path | None) -> Path:
    if config_path is not None:
        return Path(config_path).expanduser()
    override = os.environ.get("FILEROUTE_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / "profiles.yaml"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base.expanduser() / "fileroute" / "profiles.yaml"


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or re.fullmatch(PROFILE_NAME_PATTERN, name) is None:
        raise ProfileError(
            "Profile names must be 1-64 letters, digits, dots, underscores or hyphens, starting with a letter or digit."
        )


def load_profiles(*, config_path: Path | None = None) -> ProfileConfig:
    """Read named profiles; a missing config is an empty validated configuration."""
    path = _config_path(config_path)
    if not path.exists():
        return ProfileConfig()
    try:
        contents = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
        return ProfileConfig.model_validate(contents if contents is not None else {})
    except (OSError, ValueError, TypeError):
        raise ProfileError(
            f"Cannot read a valid auth profile configuration at {path}. Check its schema and permissions."
        ) from None
    except Exception:
        # YAML parser diagnostics can reproduce raw input, including pasted secrets.
        raise ProfileError(
            f"Cannot parse auth profile configuration at {path}. Check its YAML syntax."
        ) from None


def _receipt(
    name: str,
    profile: AuthProfile,
    action: Literal["saved", "removed", "authenticated"],
    path: Path,
) -> ProfileReceipt:
    return ProfileReceipt(
        name=name,
        provider=profile.provider,
        mode=profile.mode or "default",
        action=action,
        config_path=path,
    )


def _write_config(path: Path, profiles: dict[str, AuthProfile]) -> None:
    config = ProfileConfig(profiles=profiles)
    payload = config.model_dump(mode="json", exclude_none=True)
    if path.suffix.lower() == ".json":
        # JSON editor schemas are associated through editor settings; adding
        # YAML comments or an instance $schema field would change its format.
        atomic_private_write(path, json.dumps(payload, indent=2) + "\n")
        return
    stream = StringIO()
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    yaml.dump(payload, stream)
    source_root = Path(__file__).resolve().parents[2]
    local_schema = source_root / "schemas/fileroute-auth-profiles.schema.json"
    if local_schema.is_file() and (source_root / "pyproject.toml").is_file():
        # Source users can edit private configs before the next stable release,
        # without fetching a schema that has not been published yet.
        schema_url = local_schema.as_uri()
    else:
        try:
            installed_version = version("fileroute")
        except PackageNotFoundError:
            installed_version = ""
        # Only stable releases publish GitHub assets. Prerelease or unpackaged
        # installs use the latest stable schema until supplied a local schema.
        release = (
            f"download/v{installed_version}"
            if re.fullmatch(r"\d+\.\d+\.\d+", installed_version)
            else "latest/download"
        )
        schema_url = (
            f"https://github.com/mbkranz/fileroute/releases/{release}/"
            "fileroute-auth-profiles.schema.json"
        )
    atomic_private_write(
        path, f"# yaml-language-server: $schema={schema_url}\n" + stream.getvalue()
    )


def _clear_tokens(path: Path, name: str) -> None:
    directory = _profile_cache_root(path, name)
    if directory.is_symlink():
        directory.unlink()
    elif directory.exists():
        shutil.rmtree(directory)


def _profile_cache_root(path: Path, name: str) -> Path:
    namespace = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
    return path.parent / "tokens" / namespace / name


def _profile_cache_directory(path: Path, name: str, profile: AuthProfile) -> Path:
    """Isolate config files and authentication generations, including credential changes.

    A login already in flight may finish after a replacement. Changed settings
    use a different generation. Hashing file contents also detects a changed
    OAuth client at an unchanged reference path. Replacing identical settings
    does not cancel an in-flight login.
    """
    identity = profile.model_dump(mode="json", exclude_none=True)
    for field in ("credentials_file", "oauth_client_secrets"):
        reference = getattr(profile, field)
        if reference is not None:
            credential = _credential_path(reference, path).resolve()
            identity[field] = {
                "path": str(credential),
                "sha256": hashlib.sha256(credential.read_bytes()).hexdigest(),
            }
    generation = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()[:24]
    return _profile_cache_root(path, name) / generation


def save_profile(
    name: str,
    profile: AuthProfile,
    *,
    config_path: Path | None = None,
    replace: bool = False,
) -> ProfileReceipt:
    """Save a profile privately, refusing accidental replacement and concurrent lost updates."""
    _validate_name(name)
    profile = AuthProfile.model_validate(profile.model_dump())
    path = _config_path(config_path)
    with private_file_lock(path):
        profiles = dict(load_profiles(config_path=path).profiles)
        if name in profiles and not replace:
            raise ProfileError(
                f"Auth profile {name!r} already exists; use replace explicitly."
            )
        replacing = name in profiles
        profiles[name] = profile
        _write_config(path, profiles)
        if replacing:
            _clear_tokens(path, name)
    return _receipt(name, profile, "saved", path)


def get_profile(name: str, *, config_path: Path | None = None) -> AuthProfile:
    """Find one profile without resolving credentials or creating a client."""
    _validate_name(name)
    profile = load_profiles(config_path=config_path).profiles.get(name)
    if profile is None:
        raise ProfileError(
            f"Auth profile {name!r} does not exist; create it with 'fileroute auth configure {name} --provider PROVIDER'."
        )
    return profile


def remove_profile(name: str, *, config_path: Path | None = None) -> ProfileReceipt:
    """Remove the profile and its private token cache; external credentials are untouched."""
    _validate_name(name)
    path = _config_path(config_path)
    with private_file_lock(path):
        profiles = dict(load_profiles(config_path=path).profiles)
        if name not in profiles:
            raise ProfileError(f"Auth profile {name!r} does not exist.")
        profile = profiles.pop(name)
        _write_config(path, profiles)
        _clear_tokens(path, name)
    return _receipt(name, profile, "removed", path)


def _credential_path(reference: Path, config_path: Path) -> Path:
    path = reference.expanduser()
    resolved = path if path.is_absolute() else config_path.parent / path
    if not resolved.is_file():
        raise ProfileError(
            "A referenced credential file does not exist or is not a regular file; check the profile's credential path."
        )
    return resolved


def _build_client(
    name: str, profile: AuthProfile, path: Path, interactive: bool
) -> BaseClient:
    cache_directory = _profile_cache_directory(path, name, profile)
    if profile.provider == ServiceType.GOOGLE_DRIVE:
        from fileroute.clients.googledrive import GoogleDriveClient

        if profile.mode == "adc":
            auth = GoogleAuth.from_adc(scopes=profile.scopes)
        elif profile.mode == "service_account":
            assert profile.credentials_file is not None
            auth = GoogleAuth.from_service_account(
                _credential_path(profile.credentials_file, path), scopes=profile.scopes
            )
        else:
            assert profile.oauth_client_secrets is not None
            auth = GoogleAuth.from_user_oauth(
                scopes=profile.scopes,
                client_secrets_path=_credential_path(
                    profile.oauth_client_secrets, path
                ),
                token_path=cache_directory / "google.json",
                interactive=interactive,
                use_local_server=profile.use_local_server is not False,
            )
        # Transfers prepare every named client before any destination writes.
        # ADC and service-account constructors only load credentials, so force
        # the first refresh here rather than deferring a failure to a transfer.
        auth.ensure_valid()
        return GoogleDriveClient(auth=auth)
    if profile.provider == ServiceType.SHAREPOINT:
        from fileroute.clients.sharepoint import SharepointClient

        assert profile.tenant_id is not None and profile.client_id is not None
        if profile.mode == "app_only":
            assert profile.client_secret_env is not None
            secret = os.environ.get(profile.client_secret_env)
            if not secret:
                raise ProfileError(
                    f"Set environment variable {profile.client_secret_env} for this profile's client secret."
                )
            auth = MicrosoftAuth.from_app_only(
                profile.tenant_id,
                profile.client_id,
                secret,
                scopes=profile.scopes,
                cache_path=cache_directory / "microsoft.json",
            )
        else:
            auth = MicrosoftAuth.from_delegated(
                profile.tenant_id,
                profile.client_id,
                scopes=profile.scopes,
                cache_path=cache_directory / "microsoft.json",
                interactive=interactive,
            )
        return SharepointClient(auth=auth, host_url=profile.host_url)
    from fileroute.clients.s3 import S3Client

    session = (
        boto3.Session(profile_name=profile.aws_profile)
        if profile.aws_profile
        else boto3.Session()
    )
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials are available for this profile.")
    frozen = credentials.get_frozen_credentials()
    if not frozen.access_key or not frozen.secret_key:
        raise RuntimeError("AWS credentials are incomplete for this profile.")
    return S3Client(client=session.client("s3"))


def _authentication_hint(name: str, profile: AuthProfile, path: Path) -> str:
    if profile.provider == ServiceType.S3:
        if profile.aws_profile:
            return (
                "Check the native AWS profile; for IAM Identity Center run "
                f"'aws sso login --profile {shlex.quote(profile.aws_profile)}'."
            )
        return "Check the native AWS credential chain or the runtime's attached role."
    if profile.provider == ServiceType.GOOGLE_DRIVE and profile.mode != "user_oauth":
        return "Check Google ADC or the service-account credential file and its scopes."
    if profile.provider == ServiceType.SHAREPOINT and profile.mode == "app_only":
        return (
            "Check the secret environment reference and Entra application permissions."
        )
    provider = (
        "gdrive" if profile.provider == ServiceType.GOOGLE_DRIVE else "sharepoint"
    )
    return (
        f"Run 'fileroute auth login {provider} --profile {shlex.quote(name)} "
        f"--config {shlex.quote(str(path))}'."
    )


def build_profile_client(
    name: str,
    service_type: ServiceType,
    *,
    config_path: Path | None = None,
    interactive: bool = False,
) -> BaseClient:
    """Build a matching provider client; named profiles never load legacy settings."""
    path = _config_path(config_path)
    profile = get_profile(name, config_path=path)
    if profile.provider != service_type:
        raise ProfileError(
            f"Auth profile {name!r} uses {profile.provider.value}, but the location requires {service_type.value}."
        )
    try:
        return _build_client(name, profile, path, interactive)
    except ProfileError:
        raise
    except Exception:
        raise ProfileError(
            f"Could not authenticate profile {name!r}. Check its credential references and permissions; "
            + _authentication_hint(name, profile, path)
        ) from None


def authenticate_profile(
    name: str, *, config_path: Path | None = None, interactive: bool = False
) -> ProfileReceipt:
    """Acquire or refresh credentials, with user consent only when explicitly allowed."""
    path = _config_path(config_path)
    profile = get_profile(name, config_path=path)
    try:
        build_profile_client(
            name, profile.provider, config_path=path, interactive=interactive
        )
        if profile.provider == ServiceType.S3:
            session = (
                boto3.Session(profile_name=profile.aws_profile)
                if profile.aws_profile
                else boto3.Session()
            )
            session.client("sts").get_caller_identity()
    except ProfileError:
        raise
    except Exception:
        raise ProfileError(
            f"Could not validate credentials for profile {name!r}. "
            + _authentication_hint(name, profile, path)
        ) from None
    return _receipt(name, profile, "authenticated", path)


__all__ = [
    "AuthProfile",
    "ProfileConfig",
    "ProfileError",
    "ProfileReceipt",
    "PROFILE_NAME_PATTERN",
    "load_profiles",
    "save_profile",
    "get_profile",
    "remove_profile",
    "build_profile_client",
    "authenticate_profile",
]
