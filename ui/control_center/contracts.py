"""Operation inputs for both the local API and the exported JSON Schema."""

from __future__ import annotations

import operator
from functools import reduce
from pathlib import Path
from typing import Annotated, Literal

from fileroute.auth.profiles import AuthProfile, ProfileName
from fileroute.auth.describe import AuthDescribeArgs
from fileroute.models import Catalog, CatalogLink, Location, Resource
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    create_model,
    with_config,
)
from typing_extensions import NotRequired, TypedDict

DRAFT = "https://json-schema.org/draft/2020-12/schema"
VERSION = "1.2.0"
SCHEMA_BASE = "https://fileroute-control-center.inwardchime4.chatgpt.site/schemas"
Text = Annotated[str, Field(min_length=1)]
Shell = Literal["bash", "zsh", "fish", "powershell", "pwsh"]
OutputFormat = Literal["text", "json"]
Strict = with_config(ConfigDict(extra="forbid"))


@Strict
class DescriptorArgs(TypedDict):
    descriptor: NotRequired[Path]


@Strict
class CatalogListArgs(DescriptorArgs):
    selector: NotRequired[Text]
    kind: NotRequired[
        Annotated[Literal["all", "catalog", "resource"], Field(default="all")]
    ]
    format: NotRequired[Annotated[OutputFormat, Field(default="text")]]


@Strict
class ActivateArgs(TypedDict):
    descriptor: Path


@Strict
class AddBaseArgs(DescriptorArgs):
    name: Text
    parent: NotRequired[Text]
    extensions: NotRequired[dict[str, JsonValue]]
    dry_run: NotRequired[Annotated[bool, Field(default=True)]]


@Strict
class AddResourceArgs(AddBaseArgs):
    kind: Literal["resource"]
    entry: Resource


@Strict
class AddCatalogArgs(AddBaseArgs):
    kind: Literal["catalog"]
    entry: Catalog


@Strict
class AddLinkArgs(AddBaseArgs):
    kind: Literal["link"]
    entry: CatalogLink


@Strict
class UpdateBaseArgs(DescriptorArgs):
    selector: NotRequired[Text]
    extensions: NotRequired[dict[str, JsonValue]]
    unset: NotRequired[list[Text]]
    dry_run: NotRequired[Annotated[bool, Field(default=True)]]
    revision: NotRequired[Text]


def _changes(model: type[BaseModel]) -> type[BaseModel]:
    # Retain the model's field aliases and constraints while making all edits
    # optional. Explicit null still goes through the field's original type.
    fields = {}
    for name, field in model.model_fields.items():
        spec = field.asdict()
        attributes = {
            k: v
            for k, v in spec["attributes"].items()
            if k not in {"default", "default_factory"}
        }
        annotation = Annotated[
            spec["annotation"], *spec["metadata"], Field(**attributes)
        ]
        fields[name] = (annotation, None)
    return create_model(
        model.__name__ + "Changes",
        __config__=ConfigDict(
            extra="forbid", validate_by_alias=True, validate_by_name=True
        ),
        **fields,
    )


ResourceChanges = _changes(Resource)
CatalogChanges = _changes(Catalog)
LinkChanges = _changes(CatalogLink)
LocationChanges = _changes(Location)


@Strict
class UpdateResourceArgs(UpdateBaseArgs):
    kind: Literal["resource"]
    fields: ResourceChanges


@Strict
class UpdateCatalogArgs(UpdateBaseArgs):
    kind: Literal["catalog"]
    fields: CatalogChanges


@Strict
class UpdateLinkArgs(UpdateBaseArgs):
    kind: Literal["link"]
    fields: LinkChanges


@Strict
class UpdateLocationArgs(UpdateBaseArgs):
    kind: Literal["location"]
    fields: LocationChanges


@Strict
class ResolveArgs(DescriptorArgs):
    selector: NotRequired[Text]
    online: NotRequired[Annotated[bool, Field(default=False)]]
    profile: NotRequired[ProfileName]
    write: NotRequired[Annotated[bool, Field(default=False)]]
    revision: NotRequired[Text]


@Strict
class DiscoveryPreviewArgs(DescriptorArgs):
    selector: NotRequired[Text]
    profile: NotRequired[ProfileName]


@Strict
class DiscoveryRunArgs(DiscoveryPreviewArgs):
    format: NotRequired[Annotated[OutputFormat, Field(default="text")]]
    output: NotRequired[Path]


@Strict
class DiscoverySaveArgs(TypedDict):
    result_id: Text
    output: Path


@Strict
class TransferPreviewArgs(DescriptorArgs):
    direction: Literal["pull", "push"]
    selector: NotRequired[Text]
    profile: NotRequired[ProfileName]


@Strict
class TransferRunArgs(TypedDict):
    preview_id: Text


@Strict
class DiagramArgs(DescriptorArgs):
    format: Literal["svg", "html", "markdown", "mermaid"]
    detail: NotRequired[Annotated[Literal["summary", "full"], Field(default="summary")]]
    output: NotRequired[Path]


@Strict
class CloneArgs(DescriptorArgs):
    target_path: Path
    dry_run: NotRequired[Annotated[bool, Field(default=True)]]
    force: NotRequired[Annotated[bool, Field(default=False)]]


@Strict
class MigrateArgs(TypedDict):
    descriptor: Path
    output: Path
    dry_run: NotRequired[Annotated[bool, Field(default=True)]]


@Strict
class ProfileFileArgs(TypedDict):
    config: NotRequired[Path]


@Strict
class ProfileArgs(ProfileFileArgs):
    name: ProfileName


@Strict
class SaveProfileArgs(ProfileArgs):
    settings: AuthProfile
    replace: NotRequired[Annotated[bool, Field(default=False)]]


@Strict
class CheckProfileArgs(ProfileFileArgs):
    profile: ProfileName
    url: NotRequired[Text]


@Strict
class SavedLoginArgs(TypedDict):
    method: Literal["saved"]
    profile: ProfileName
    config: NotRequired[Path]


@Strict
class GoogleLegacyLoginArgs(TypedDict):
    method: Literal["legacy"]
    oauth_client_secrets: NotRequired[Path]
    oauth_token_path: NotRequired[Path]
    scope: NotRequired[list[Text]]
    env_file: NotRequired[Path]
    no_local_server: NotRequired[Literal[False]]


@Strict
class MicrosoftLegacyLoginArgs(TypedDict):
    method: Literal["legacy"]
    auth_mode: NotRequired[Literal["app_only", "delegated"]]
    host_url: NotRequired[Text]
    scope: NotRequired[list[Text]]
    env_file: NotRequired[Path]


@Strict
class JobArgs(TypedDict):
    job_id: Text


@Strict
class HelpArgs(TypedDict):
    command: NotRequired[Text]


@Strict
class CompletionArgs(TypedDict):
    shell: Shell


@Strict
class ApprovalArgs(TypedDict):
    operation: Text
    arguments: dict[str, JsonValue]


OPERATIONS = {
    "catalog.open": DescriptorArgs,
    "list": CatalogListArgs,
    "activate": ActivateArgs,
    "add": AddResourceArgs | AddCatalogArgs | AddLinkArgs,
    "update": UpdateResourceArgs
    | UpdateCatalogArgs
    | UpdateLinkArgs
    | UpdateLocationArgs,
    "resolve": ResolveArgs,
    "discovery.preview": DiscoveryPreviewArgs,
    "discovery.run": DiscoveryRunArgs,
    "discovery.save": DiscoverySaveArgs,
    "transfer.preview": TransferPreviewArgs,
    "transfer.run": TransferRunArgs,
    "diagram": DiagramArgs,
    "clone.descriptor": CloneArgs,
    "migrate": MigrateArgs,
    "auth.describe": AuthDescribeArgs,
    "auth.profile.list": ProfileFileArgs,
    "auth.profile.show": ProfileArgs,
    "auth.profile.save": SaveProfileArgs,
    "auth.profile.remove": ProfileArgs,
    "auth.check": CheckProfileArgs,
    "auth.login.gdrive": SavedLoginArgs | GoogleLegacyLoginArgs,
    "auth.login.microsoft": SavedLoginArgs | MicrosoftLegacyLoginArgs,
    "auth.login.sharepoint": SavedLoginArgs | MicrosoftLegacyLoginArgs,
    "auth.login.s3": SavedLoginArgs,
    "jobs.get": JobArgs,
    "cli.help": HelpArgs,
    "cli.completion.show": CompletionArgs,
    "cli.completion.install": CompletionArgs,
    "approval.request": ApprovalArgs,
}


def _request(operation, arguments) -> type[BaseModel]:
    name = "".join(word.capitalize() for word in operation.split(".")) + "Request"
    return create_model(
        name,
        __config__=ConfigDict(extra="forbid"),
        operation=(Literal[operation], ...),
        arguments=(arguments, ...),
    )


REQUEST = TypeAdapter(
    reduce(operator.or_, (_request(*item) for item in OPERATIONS.items()))
)
ARGUMENTS = {key: TypeAdapter(contract) for key, contract in OPERATIONS.items()}
AUTHORED_MODELS = {
    "resource": Resource,
    "catalog": Catalog,
    "link": CatalogLink,
    "location": Location,
}


def operation_schema() -> dict:
    schema = REQUEST.json_schema(by_alias=True, mode="validation")
    schema.update(
        {
            "$schema": DRAFT,
            "$id": f"{SCHEMA_BASE}/v{VERSION}/fileroute-operations.schema.json",
            "title": "Fileroute UI operation requests",
            "x-fileroute-ui-profile": VERSION,
        }
    )
    return schema
