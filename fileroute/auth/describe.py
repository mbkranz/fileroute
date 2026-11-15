"""Offline setup guidance and authentication selection for descriptor routes."""

from collections.abc import Iterator
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, model_validator

from fileroute.auth.profiles import (
    _PROFILE_MODE_RULES,
    AuthProfile,
    ProfileMode,
    ProfileName,
    _provider_alias,
    load_profiles,
)
from fileroute.descriptor import EntityPath, _pointer_json_path, load, select, walk
from fileroute.models import Catalog, Location, Resource, ServiceType
from fileroute.resolution import parse_location

Operation = Literal["pull", "push", "discover"]

_PROVIDER_COMMAND = {
    ServiceType.GOOGLE_DRIVE: "gdrive",
    ServiceType.SHAREPOINT: "sharepoint",
    ServiceType.S3: "s3",
}
_GUIDANCE: dict[ProfileMode, tuple[str, ...]] = {
    "adc": (
        "Use Google Application Default Credentials with the needed Drive scopes.",
    ),
    "service_account": (
        "Provide a Google service-account credential file and share the Drive resource with that account.",
    ),
    "user_oauth": (
        "Provide a Google Desktop OAuth client JSON file.",
        "fileroute auth login gdrive --profile NAME",
    ),
    "app_only": (
        "Register an Entra app, grant the required Graph permissions, and supply the secret through the configured environment variable.",
    ),
    "delegated": (
        "Register an Entra public client with the required delegated Graph permissions.",
        "fileroute auth login sharepoint --profile NAME",
    ),
    "default": (
        "Use the native AWS credential chain or reference a named AWS profile with --aws-profile.",
        "For an AWS SSO profile, run aws sso login --profile AWS_PROFILE.",
    ),
}


class AuthDescribeArgs(BaseModel):
    """Common CLI and API request for authentication guidance."""

    model_config = ConfigDict(extra="forbid")
    provider: str | None = None
    mode: ProfileMode | None = None
    url: str | None = None
    descriptor: Path | None = None
    selector: str | None = None
    operation: Operation | None = None
    location: str | None = None
    profile: ProfileName | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        scoped = self.operation is not None
        if sum((self.provider is not None, self.url is not None, scoped)) > 1:
            raise ValueError("Choose one of a provider, --url, or --operation.")
        if self.mode and not self.provider:
            raise ValueError("--mode requires a provider.")
        if not scoped and any(
            value is not None
            for value in (self.descriptor, self.selector, self.location)
        ):
            raise ValueError("Descriptor, selector, and location require --operation.")
        if self.profile and not (scoped or self.url):
            raise ValueError("--profile requires --url or --operation.")
        return self


class AuthModeGuide(BaseModel):
    provider: ServiceType
    mode: ProfileMode
    title: str
    default: bool
    required_settings: list[str]
    next_steps: list[str]


class AuthRoute(BaseModel):
    url: str
    check_url: str | None = None
    provider: ServiceType | None = None
    mode: ProfileMode | None = None
    profile: str | None = None
    profile_source: Literal["location", "invocation", "provider_default"]
    consumer_selector: str | None = None
    declaration_selector: str | None = None
    origin_descriptor: Path | None = None
    origin_selector: str | None = None
    inherited: bool = False
    error: str | None = None


class AuthDescription(BaseModel):
    guides: list[AuthModeGuide]
    routes: list[AuthRoute]


def _safe_url(path: str) -> str:
    """Omit query, fragment, and user info from displayed locators."""
    parts = urlsplit(path)
    return urlunsplit((
        parts.scheme,
        parts.netloc.rsplit("@", 1)[-1],
        parts.path,
        "",
        "",
    ))


def _locations(
    document: Catalog, args: AuthDescribeArgs
) -> Iterator[tuple[Location, EntityPath, EntityPath, str, int]]:
    scope = select(document, args.selector or "$")
    if not isinstance(scope.model, (Catalog, Resource)):
        raise ValueError("Select the resource or catalog that uses this location.")
    if args.operation == "discover" and not isinstance(scope.model, Catalog):
        raise ValueError("Discovery must select a catalog.")
    pointer = scope.entry.json_pointer
    owners: dict[str, EntityPath | None] = {}
    for row in walk(document, include_self=True):
        entity = row.model
        if not isinstance(entity, (Catalog, Resource)):
            continue
        parent = row.json_pointer.rsplit("/", 2)[0]
        owner = row if entity.targets is not None else owners.get(parent)
        owners[row.json_pointer] = owner
        if row.json_pointer != pointer and not row.json_pointer.startswith(
            pointer + "/"
        ):
            continue
        has_children = isinstance(entity, Catalog) and bool(
            entity.catalogs or entity.resources
        )
        if args.operation == "push":
            if owner is not None and not has_children:
                for index, location in enumerate(owner.model.targets or []):
                    yield location, row, owner, "targets", index
        elif args.operation == "discover":
            if (
                isinstance(entity, Catalog)
                and len(entity.sources) == 1
                and entity.sources[0].resource_path_template is not None
            ):
                yield entity.sources[0], row, row, "sources", 0
        elif (
            not has_children
            and len(entity.sources) == 1
            and entity.sources[0].resource_path_template is None
        ):
            yield entity.sources[0], row, row, "sources", 0


def _route(
    location: Location, profile: str | None, profiles: dict[str, AuthProfile]
) -> AuthRoute:
    selected = location.auth_profile or profile
    route = AuthRoute(
        url=_safe_url(location.path),
        profile=selected,
        profile_source="location"
        if location.auth_profile
        else "invocation"
        if profile
        else "provider_default",
    )
    try:
        route.provider = parse_location(
            location.model_copy(), required=True
        ).service_type
    except ValueError:
        route.error = "Use a supported remote URL or configure the location's provider and namespace."
        return route
    if route.url == location.path and "://" in route.url:
        route.check_url = route.url
    if selected:
        saved = profiles.get(selected)
        if saved is None:
            route.error = f"Auth profile {selected!r} does not exist. Use fileroute auth configure."
        elif saved.provider != route.provider:
            route.error = f"Auth profile {selected!r} uses {saved.provider}, but this location requires {route.provider}."
        else:
            route.mode = saved.mode
    return route


def describe_auth(
    args: AuthDescribeArgs, *, config_path: Path | None = None
) -> AuthDescription:
    """Explain possible or selected authentication without accessing credentials."""
    provider = ServiceType(_provider_alias(args.provider)) if args.provider else None
    routes: list[AuthRoute] = []
    if args.url is not None:
        routes.append(
            _route(
                Location(path=args.url),
                args.profile,
                load_profiles(config_path=config_path).profiles if args.profile else {},
            )
        )
    elif args.operation:
        from fileroute.commands.config import resolve_descriptor_path

        document = load(
            resolve_descriptor_path(args.descriptor), resolve_references=True
        )
        locations = list(_locations(document, args))
        profiles = (
            load_profiles(config_path=config_path).profiles
            if args.profile or any(loc.auth_profile for loc, *_ in locations)
            else {}
        )
        for location, consumer, owner, field, index in locations:
            suffix = f"/{field}/{index}"
            declaration = _pointer_json_path(owner.json_pointer + suffix)
            if args.location is not None and args.location != declaration:
                continue
            if (
                args.operation == "pull"
                and not location.service_type
                and "://" not in location.path
            ):
                continue
            route = _route(location, args.profile, profiles)
            route.consumer_selector = consumer.json_path
            route.declaration_selector = declaration
            route.origin_descriptor = owner.origin_descriptor
            route.origin_selector = _pointer_json_path(owner.origin_pointer + suffix)
            route.inherited = consumer is not owner
            routes.append(route)

    providers = {route.provider for route in routes if route.provider is not None}
    if provider:
        providers.add(provider)
    guides = [
        AuthModeGuide(
            provider=rule.provider,
            mode=mode,
            title=rule.title,
            default=rule.default,
            required_settings=sorted(rule.required),
            next_steps=[
                _GUIDANCE[mode][0],
                f"fileroute auth configure NAME --provider {_PROVIDER_COMMAND[rule.provider]} --mode {mode}",
                *_GUIDANCE[mode][1:],
                "fileroute auth check --profile NAME",
            ],
        )
        for mode, rule in _PROFILE_MODE_RULES.items()
        if (
            not providers
            and not args.operation
            and args.url is None
            or rule.provider in providers
        )
        and (args.mode is None or args.mode == mode)
    ]
    if args.mode and not guides:
        raise ValueError("That authentication mode is unavailable for this provider.")
    return AuthDescription(guides=guides, routes=routes)


__all__ = ["AuthDescribeArgs", "AuthDescription", "describe_auth"]
