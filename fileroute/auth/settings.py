from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Annotated

from dotenv import find_dotenv
from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from fileroute.auth.google import (
    DEFAULT_DRIVE_SCOPES,
    GoogleAuth,
)
from fileroute.auth.microsoft import (
    DEFAULT_MICROSOFT_GRAPH_SCOPES,
    MicrosoftAuth,
    normalize_microsoft_scopes,
)


class GoogleAuthMode(str, Enum):
    ADC = "adc"
    SERVICE_ACCOUNT = "service_account"
    USER_OAUTH = "user_oauth"


class MicrosoftAuthMode(str, Enum):
    APP_ONLY = "app_only"
    DELEGATED = "delegated"


class GoogleAuthConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=find_dotenv(".env", usecwd=True),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    auth_mode: GoogleAuthMode = Field(
        default=GoogleAuthMode.ADC,
        alias="GOOGLE_AUTH_MODE",
    )
    service_account_credentials: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_SERVICE_ACCOUNT_CREDENTIALS",
            "GOOGLE_APPLICATION_CREDENTIALS",
        ),
    )
    oauth_client_secrets: Path | None = Field(
        default=None,
        alias="GOOGLE_OAUTH_CREDENTIALS",
    )
    oauth_token_path: Path = Field(
        default=".google/token.json",
        alias="GOOGLE_OAUTH_TOKEN_PATH",
    )
    scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_DRIVE_SCOPES),
        alias="GOOGLE_SCOPES",
    )
    use_local_server: bool = Field(
        default=True,
        alias="GOOGLE_OAUTH_USE_LOCAL_SERVER",
    )

    @field_validator("scopes", mode="before")
    @classmethod
    def to_scope_list(cls, value: str | list[str] | tuple[str, ...] | None) -> list[str]:
        if value is None:
            return list(DEFAULT_DRIVE_SCOPES)
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            return [part for part in parts if part] or list(DEFAULT_DRIVE_SCOPES)
        return [scope for scope in value if scope]

    @model_validator(mode="after")
    def validate_for_mode(self) -> GoogleAuthConfig:
        if (
            self.auth_mode == GoogleAuthMode.SERVICE_ACCOUNT
            and self.service_account_credentials is None
        ):
            raise ValueError(
                "GOOGLE_APPLICATION_CREDENTIALS or GOOGLE_SERVICE_ACCOUNT_CREDENTIALS is required for service_account mode."
            )

        if (
            self.auth_mode == GoogleAuthMode.USER_OAUTH
            and self.oauth_client_secrets is None
        ):
            raise ValueError(
                "GOOGLE_OAUTH_CREDENTIALS is required for user_oauth mode."
            )

        return self

    def to_auth(self) -> GoogleAuth:
        """Return a :class:`~fileroute.auth.google.GoogleAuth` for this configuration."""
        if self.auth_mode == GoogleAuthMode.ADC:
            return GoogleAuth.from_adc(scopes=self.scopes)

        elif self.auth_mode == GoogleAuthMode.SERVICE_ACCOUNT:
            return GoogleAuth.from_service_account(
                credentials_path=self.service_account_credentials,
                scopes=self.scopes,
            )

        elif self.auth_mode == GoogleAuthMode.USER_OAUTH:
            return GoogleAuth.from_user_oauth(
                client_secrets_path=self.oauth_client_secrets,
                token_path=self.oauth_token_path,
                scopes=self.scopes
            )
        else:
            if self.auth_mode:
                raise ValueError(f"Unsupported Google auth mode: {self.auth_mode}")
            else:
                raise ValueError("GOOGLE_AUTH_MODE is required for Google authentication.")


class MicrosoftAuthConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=find_dotenv(".env", usecwd=True),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    auth_mode: MicrosoftAuthMode = Field(
        default=MicrosoftAuthMode.APP_ONLY,
        alias="SHAREPOINT_AUTH_MODE",
    )
    tenant_id: str | None = Field(default=None, alias="AZURE_TENANT_ID")
    client_id: str | None = Field(default=None, alias="AZURE_CLIENT_ID")
    client_secret: SecretStr | None = Field(
        default=None,
        alias="AZURE_CLIENT_SECRET",
        validate_default=True,
    )
    host_url: str = Field(
        default="norc.sharepoint.com",
        validation_alias=AliasChoices("SHAREPOINT_HOST_URL", "AZURE_HOST_URL"),
    )
    scopes: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_MICROSOFT_GRAPH_SCOPES),
        validation_alias=AliasChoices("SHAREPOINT_SCOPES", "AZURE_SCOPES"),
    )

    @field_validator("tenant_id", "client_id", mode="before")
    @classmethod
    def empty_string_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("host_url", mode="before")
    @classmethod
    def normalize_host_url(cls, value: str | None) -> str:
        if value is None:
            return "norc.sharepoint.com"
        normalized = value.strip()
        return normalized or "norc.sharepoint.com"

    @field_validator("scopes", mode="before")
    @classmethod
    def to_scope_list(
        cls,
        value: str | list[str] | tuple[str, ...] | None,
    ) -> list[str]:
        return normalize_microsoft_scopes(value)

    @model_validator(mode="after")
    def validate_for_mode(self) -> MicrosoftAuthConfig:
        if self.tenant_id is None:
            raise ValueError("AZURE_TENANT_ID is required for Microsoft authentication.")

        if self.client_id is None:
            raise ValueError("AZURE_CLIENT_ID is required for Microsoft authentication.")

        if (
            self.auth_mode == MicrosoftAuthMode.APP_ONLY
            and self.client_secret is None
        ):
            raise ValueError("AZURE_CLIENT_SECRET is required for Microsoft app_only mode.")

        return self

    def to_auth(self) -> MicrosoftAuth:
        """Return a :class:`~fileroute.auth.microsoft.MicrosoftAuth` for this configuration."""
        if self.auth_mode == MicrosoftAuthMode.DELEGATED:
            return MicrosoftAuth.from_delegated(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                scopes=self.scopes,
            )
        elif self.auth_mode == MicrosoftAuthMode.APP_ONLY:
            return MicrosoftAuth.from_app_only(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret.get_secret_value()
                if self.client_secret is not None
                else None,
                scopes=self.scopes,
            )
        else:
            if self.auth_mode:
                raise ValueError(f"Unsupported Microsoft auth mode: {self.auth_mode}")
            else:
                raise ValueError("SHAREPOINT_AUTH_MODE or AZURE_AUTH_MODE is required for Microsoft authentication.")


__all__ = [
    "GoogleAuthConfig",
    "GoogleAuthMode",
    "MicrosoftAuthConfig",
    "MicrosoftAuthMode",
]
