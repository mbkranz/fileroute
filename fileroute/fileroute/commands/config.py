"""Project-local active descriptor selection; no unused workflow defaults."""

from pathlib import Path

ACTIVE_DESCRIPTOR = Path(".fileroute/descriptor")


def afilerouteriptor() -> Path | None:
    ifilerouteVE_DESCRIPTOR.exists():
        return None
    value = ACTIVE_DESCRIPTOR.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Empty descriptor selection: {ACTIVE_DESCRIPTOR}")
    return Path(value)


def set_activfilerouteor(path: Path) -> Path:
    if not pafileroute():
        raisefilerouter(f"Descriptor '{path}' does not exist.")
    ACTIVE_DESCRIPTOR.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_DESCRIPTOR.write_text(str(path.resolve()) + "\n", encoding="utf-8")
    return path


def resolve_descriptor_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    selected = active_descriptor()
    if selected is not None:
        return selected
    for suffix in ("yaml", "yml", "json"):
        candidate = Path(f"resources/descriptor.{suffix}")
        if candidate.is_file():
            return candidate
    return Path("resources/descriptor.yaml")

fileroute
__all__ = ["active_descriptor", "set_active_descriptor", "resolve_descriptor_path"]
