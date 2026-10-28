"""Generate versioned JSON Schemas directly from the public authored models.

Default output is the release bundle in ``dist/schemas``. ``--source`` updates
tracked editor schemas in ``schemas``; ``--check`` compares them without writes.
Release preparation refreshes those tracked files after uv changes the version.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import tomllib

from fileroute.auth.profiles import ProfileConfig
from fileroute.models import Catalog

SCHEMA_MODELS = (
    ("fileroute-catalog.schema.json", Catalog),
    ("fileroute-auth-profiles.schema.json", ProfileConfig),
)


def export_all(
    root: Path = Path("."), *, source: bool = False, check: bool = False
) -> tuple[Path, ...]:
    """Export every authored schema, or reject missing/stale tracked artifacts.

    Schema IDs always identify the checked-out package version's immutable
    release assets. Sorting the model-derived JSON makes source and release
    output byte-identical. Checking never creates or modifies files.
    """
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    directory = root / ("schemas" if source or check else "dist/schemas")
    destinations: list[Path] = []
    stale: list[Path] = []
    for filename, model in SCHEMA_MODELS:
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema.update({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"https://github.com/mbkranz/fileroute/releases/download/v{version}/{filename}",
            "$comment": "MIT licensed; see the fileroute LICENSE. Operational validation still requires fileroute.",
        })
        content = (json.dumps(schema, indent=2, sort_keys=True) + "\n").encode("utf-8")
        destination = directory / filename
        destinations.append(destination)
        if check:
            if not destination.is_file() or destination.read_bytes() != content:
                stale.append(destination)
        else:
            directory.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    if stale:
        names = ", ".join(str(path) for path in stale)
        raise ValueError(
            f"Missing or stale JSON Schemas: {names}. Run 'poe schema-update'."
        )
    return tuple(destinations)


def export(root: Path = Path(".")) -> Path:
    """Export the release schema bundle and return the catalog path for compatibility."""
    return export_all(root)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument(
        "--source", action="store_true", help="Update tracked editor schemas."
    )
    flags.add_argument(
        "--check", action="store_true", help="Check tracked schemas without writing."
    )
    args = parser.parse_args(argv)
    try:
        paths = export_all(source=args.source, check=args.check)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
