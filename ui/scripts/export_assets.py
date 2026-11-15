"""Generate the hosted fixtures and UI schemas from Fileroute and Pydantic.

Run this with the pinned Fileroute Python environment. The fixture replay contains
only synthetic data. It never calls authentication, provider, or transfer execution.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from importlib.metadata import version
from pathlib import Path

from fileroute.auth.profiles import ProfileConfig
from fileroute.models import Catalog

from control_center.contracts import (
    ARGUMENTS,
    DRAFT,
    SCHEMA_BASE,
    VERSION,
    operation_schema,
)
from control_center.workspace import Workspace

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
SCHEMAS = PUBLIC / "schemas"
FIXTURES = PUBLIC / "fixtures"
MODEL_SCHEMAS = {
    "fileroute-catalog.schema.json": Catalog,
    "fileroute-auth-profiles.schema.json": ProfileConfig,
}
CLI = {
    "pull": "transfer.preview",
    "push": "transfer.preview",
    "discover": "discovery.preview",
    "resolve": "resolve",
    "migrate": "migrate",
    "update": "update",
    "activate": "activate",
    "list": "list",
    "add": "add",
    "diagram": "diagram",
    "clone descriptor": "clone.descriptor",
    "auth configure": "auth.profile.save",
    "auth describe": "auth.describe",
    "auth list": "auth.profile.list",
    "auth show": "auth.profile.show",
    "auth remove": "auth.profile.remove",
    "auth check": "auth.check",
    "auth login gdrive": "auth.login.gdrive",
    "auth login microsoft": "auth.login.microsoft",
    "auth login sharepoint": "auth.login.sharepoint",
    "auth login s3": "auth.login.s3",
}
DEMO_ROOT = "/fixtures/survey-governance"


def put(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(contents, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def fixture_path(value, root):
    return json.loads(json.dumps(value, default=str).replace(str(root), DEMO_ROOT))


def main():
    catalog_file = "catalog.yaml"
    for filename, model in MODEL_SCHEMAS.items():
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema.update(
            {
                "$schema": DRAFT,
                "$id": f"https://github.com/mbkranz/fileroute/releases/download/v{version('fileroute')}/{filename}",
                "$comment": "MIT licensed; see the fileroute LICENSE. Operational validation still requires fileroute.",
            }
        )
        put(SCHEMAS / filename, schema)
    put(
        SCHEMAS / f"v{VERSION}" / "fileroute-operations.schema.json", operation_schema()
    )
    manifest = json.loads((PUBLIC / "operation-views.json").read_text())
    if set(ARGUMENTS) != {item["id"] for item in manifest}:
        raise ValueError("Operations and the interface manifest must agree.")
    profile = {
        "$schema": DRAFT,
        "$id": f"{SCHEMA_BASE}/v{VERSION}/ui-profile.json",
        "version": VERSION,
        "dialect": DRAFT,
        "operations": f"/schemas/v{VERSION}/fileroute-operations.schema.json",
        "catalog": "/schemas/fileroute-catalog.schema.json",
        "auth": "/schemas/fileroute-auth-profiles.schema.json",
        "cli": CLI,
        "baseline": "57a55c1a88bbaf15fd2898f770d0597309ab4b59",
    }
    put(SCHEMAS / "ui-profile.json", profile)
    put(SCHEMAS / f"v{VERSION}" / "ui-profile.json", profile)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for item in (
            "catalog.yaml",
            "catalogs",
            "profiles.yaml",
            "staging",
            "legacy.yaml",
        ):
            source = FIXTURES / item
            if source.is_dir():
                shutil.copytree(source, root / item)
            else:
                shutil.copyfile(source, root / item)
        os.chdir(root)
        workspace = Workspace(root)
        catalog = workspace.catalog(catalog_file)
        selectors = {
            row["qualifiedName"]: row["selector"] for row in catalog["entries"]
        }
        fixture_selectors = {
            selectors["mapping"]: "pull",
            selectors["outputs.union-schema"]: "push",
            selectors["outputs"]: "push",
        }
        transfers = {}
        for selector, direction in fixture_selectors.items():
            _, result = workspace._transfer(
                direction, descriptor=catalog_file, selector=selector
            )
            transfers[f"{direction}:{selector}"] = result
        _, result = workspace._transfer("push", descriptor=catalog_file)
        transfers["push:"] = result
        resolves = {
            row["selector"]: workspace.resolve(
                descriptor=catalog_file, selector=row["selector"]
            )
            for node in catalog["entries"]
            for row in (node, *node["locations"])
        }
        previews = {
            fmt: {
                detail: workspace.diagram(fmt, catalog_file, detail)
                for detail in ("summary", "full")
            }
            for fmt in ("svg", "html", "markdown", "mermaid")
        }
        profiles = workspace.profile_list("profiles.yaml")
        auth_descriptions = {
                "overview": workspace.auth_describe(),
                "scopes": {
                    f"{direction}:{row['selector']}": workspace.auth_describe(
                        descriptor=catalog_file, selector=row["selector"], operation=direction,
                        config_path=root / "profiles.yaml",
                    )
                    for row in catalog["entries"]
                    for direction in ("pull", "push", "discover")
                    if direction != "discover" or row["kind"] == "catalog"
                },
        }
        auth_descriptions["urls"] = {
            url: workspace.auth_describe(url=url)
            for url in sorted({
                route["url"]
                for scope in auth_descriptions["scopes"].values()
                for route in scope["routes"]
                if route["provider"] and "://" in route["url"]
            })
        }
        fixture = {
            "catalog": catalog,
            "authDescriptions": auth_descriptions,
            "yaml": {
                name: workspace.path(name).read_text(encoding="utf-8")
                for name in sorted({row["originDescriptor"] for row in catalog["entries"]})
            },
            "transfers": transfers,
            "resolutions": resolves,
            "discovery": workspace.discovery_preview(
                catalog_file, selectors["releases"]
            ),
            "diagrams": previews,
            "profiles": profiles,
            "profileDetails": {
                item["name"]: workspace.profile_show(item["name"], "profiles.yaml")
                for item in profiles["rows"]
            },
            "migration": workspace.migrate("legacy.yaml", "converted", True),
            "completion": {
                shell: workspace.completion(shell)
                for shell in ("bash", "zsh", "fish", "powershell", "pwsh")
            },
            "help": {
                command: workspace.help(command)
                for command in ("", *CLI, "auth", "auth login", "clone")
            },
        }
        put(FIXTURES / "workspace.json", fixture_path(fixture, root))


if __name__ == "__main__":
    main()
