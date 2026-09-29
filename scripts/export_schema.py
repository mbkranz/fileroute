"""Export the model-owned catalog profile for the checked-out package version."""

from pathlib import Path
import json
import tomllib

from fileroute.models import Catalog


def export(root: Path = Path(".")) -> Path:
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    schema = Catalog.model_json_schema(by_alias=True, mode="validation")
    schema.update({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://github.com/mbkranz/fileroute/releases/download/v{version}/fileroute-catalog.schema.json",
        "$comment": "MIT licensed; see the fileroute LICENSE. Operational validation still requires fileroute.",
    })
    destination = root / "dist/schemas/fileroute-catalog.schema.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return destination


if __name__ == "__main__":
    print(export())
