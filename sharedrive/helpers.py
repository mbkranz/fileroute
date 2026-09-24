"""Configuration and defaults management for descriptor files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DESCRIPTOR_DEFAULTS_FILE = Path(".sharedrive/sharedrive_set.json")


def load_descriptor_defaults_store() -> dict[str, Any]:
    """Load persisted descriptor defaults for global and descriptor scopes."""
    if not DESCRIPTOR_DEFAULTS_FILE.exists():
        return {"global": {}, "descriptors": {}}
    data = json.loads(DESCRIPTOR_DEFAULTS_FILE.read_text(encoding="utf-8"))
    return data


def save_descriptor_defaults_store(data: dict[str, Any]) -> None:
    """Persist descriptor defaults store to disk."""
    DESCRIPTOR_DEFAULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DESCRIPTOR_DEFAULTS_FILE.write_text(
        json.dumps(data, indent=4) + "\n", encoding="utf-8"
    )


def descriptor_scope_key(descriptor: Path | str) -> str:
    """Return the stable key used for descriptor-scoped defaults."""
    return str(Path(descriptor))


def save_params_for_scope(
    parsed: dict[str, Any], descriptor: Path | str | None, *, global_scope: bool
) -> str:
    """Save reusable CLI params to the global or descriptor-specific scope."""
    if global_scope and descriptor is not None:
        raise ValueError("Use either <descriptor> or --global, not both.")

    store = load_descriptor_defaults_store()
    if global_scope:
        target = "global"
        scope = store.setdefault("global", {})
    else:
        if descriptor is None:
            raise ValueError("Provide <descriptor> or use --global.")
        target = str(descriptor)
        descriptors = store.setdefault("descriptors", {})
        scope = descriptors.setdefault(target, {})

    if not isinstance(scope, dict):
        scope = {}
        if global_scope:
            store["global"] = scope
        else:
            store.setdefault("descriptors", {})[target] = scope

    scope.update(parsed)
    save_descriptor_defaults_store(store)
    return target


def has_saved_global_descriptor() -> bool:
    """Return whether the global defaults include a descriptor path."""
    store = load_descriptor_defaults_store()
    global_scope = store.get("global")
    if not isinstance(global_scope, dict):
        return False

    descriptor_value = global_scope.get("descriptor")
    return isinstance(descriptor_value, str) and bool(descriptor_value.strip())


def set_active_descriptor(descriptor_path: Path) -> Path:
    """Persist the active descriptor."""
    if not descriptor_path.exists():
        raise ValueError(f"Descriptor '{descriptor_path}' does not exist.")

    store = load_descriptor_defaults_store()
    global_scope = store.setdefault("global", {})
    if not isinstance(global_scope, dict):
        global_scope = {}
        store["global"] = global_scope

    global_scope["descriptor"] = descriptor_path.as_posix()
    global_scope.pop("entity", None)
    save_descriptor_defaults_store(store)
    return descriptor_path


def get_saved_params_for_descriptor(
    descriptor: Path | str | None = None,
) -> dict[str, Any]:
    """Return merged global and descriptor-scoped saved params."""
    store = load_descriptor_defaults_store()
    merged: dict[str, Any] = {}

    global_params = store.get("global", {})
    if isinstance(global_params, dict):
        merged.update(global_params)

    if descriptor is None:
        return merged

    descriptor_params = store.get("descriptors", {}).get(
        descriptor_scope_key(descriptor), {}
    )
    if isinstance(descriptor_params, dict):
        merged.update(descriptor_params)
    return merged


def resolve_descriptor_path(descriptor: Path | str | None = None) -> Path:
    """Resolve descriptor path from explicit input, saved defaults, or standard locations."""
    if descriptor is not None:
        return Path(descriptor)

    saved_descriptor = get_saved_params_for_descriptor().get("descriptor")
    if isinstance(saved_descriptor, str) and saved_descriptor.strip():
        return Path(saved_descriptor.strip())

    return resolve_default_descriptor()


def resolve_default_descriptor() -> Path:
    """Return the first existing default descriptor path."""
    for candidate in (
        Path("resources/descriptor.yaml"),
        Path("resources/descriptor.yml"),
        Path("resources/descriptor.json"),
    ):
        if candidate.exists():
            return candidate
    return Path("resources/descriptor.yaml")


__all__ = [
    "DESCRIPTOR_DEFAULTS_FILE",
    "descriptor_scope_key",
    "get_saved_params_for_descriptor",
    "has_saved_global_descriptor",
    "load_descriptor_defaults_store",
    "resolve_default_descriptor",
    "resolve_descriptor_path",
    "save_params_for_scope",
    "save_descriptor_defaults_store",
    "set_active_descriptor",
]
