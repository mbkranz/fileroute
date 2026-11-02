"""Schema associations and saved configurations work as authoring inputs."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest
from ruamel.yaml import YAML

from fileroute.auth import profiles
from fileroute.auth.profiles import (
    AuthProfile,
    ProfileConfig,
    load_profiles,
    save_profile,
)
from fileroute.models import Catalog


ROOT = Path(__file__).parents[1]
YAML_TEMPLATES = [
    *sorted((ROOT / "examples/use-cases").glob("*.yaml")),
    ROOT / "examples/auth/profiles.yaml",
    ROOT / "resources/descriptor.yaml",
]


@pytest.mark.parametrize("path", YAML_TEMPLATES, ids=lambda path: path.name)
def test_yaml_templates_reference_valid_local_schemas(path: Path) -> None:
    source = path.read_text()
    first_line = source.splitlines()[0]
    prefix = "# yaml-language-server: $schema="
    assert first_line.startswith(prefix)
    schema_path = (path.parent / first_line.removeprefix(prefix)).resolve()
    assert schema_path.is_relative_to(ROOT)
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    value = YAML(typ="safe").load(source)
    Draft202012Validator(schema).validate(value)
    if path.parent.name == "auth":
        ProfileConfig.model_validate(value)
    else:
        Catalog.model_validate(value)


@pytest.mark.parametrize(
    ("example", "schema_name", "model"),
    [
        (
            "examples/auth/profiles.json",
            "fileroute-auth-profiles.schema.json",
            ProfileConfig,
        ),
        ("examples/catalog.json", "fileroute-catalog.schema.json", Catalog),
    ],
)
def test_json_starters_validate_without_schema_data_fields(example, schema_name, model):
    value = json.loads((ROOT / example).read_text())
    assert "$schema" not in value
    schema = json.loads((ROOT / "schemas" / schema_name).read_text())
    Draft202012Validator(schema).validate(value)
    model.model_validate(value)


def test_saved_yaml_profiles_have_a_pinned_editor_hint_without_changing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(profiles, "version", lambda _package: "1.2.3")
    monkeypatch.setattr(
        profiles, "__file__", str(tmp_path / "site-packages/fileroute/auth/profiles.py")
    )
    path = tmp_path / "profiles.yaml"
    profile = AuthProfile(provider="s3", aws_profile="team-dev")
    save_profile("archive-reader", profile, config_path=path)
    assert path.read_text().splitlines()[0] == (
        "# yaml-language-server: $schema=https://github.com/mbkranz/fileroute/"
        "releases/download/v1.2.3/fileroute-auth-profiles.schema.json"
    )
    assert load_profiles(config_path=path).profiles["archive-reader"] == profile


def test_source_checkout_profiles_use_the_local_schema_before_release(tmp_path):
    path = tmp_path / "profiles.yaml"
    save_profile("archive-reader", AuthProfile(provider="s3"), config_path=path)
    assert path.read_text().splitlines()[0] == (
        "# yaml-language-server: $schema="
        + (ROOT / "schemas/fileroute-auth-profiles.schema.json").as_uri()
    )


@pytest.mark.parametrize("installed_version", ["1.2.3.dev1", None])
def test_unpublished_installs_use_latest_schema_without_inventing_a_release(
    installed_version, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        profiles, "__file__", str(tmp_path / "site-packages/fileroute/auth/profiles.py")
    )

    def read_version(_package):
        if installed_version is None:
            raise profiles.PackageNotFoundError("fileroute")
        return installed_version

    monkeypatch.setattr(profiles, "version", read_version)
    path = tmp_path / "profiles.yaml"
    save_profile("archive-reader", AuthProfile(provider="s3"), config_path=path)
    assert "/releases/latest/download/" in path.read_text().splitlines()[0]


def test_saved_json_profiles_are_json_and_use_external_editor_association(tmp_path):
    path = tmp_path / "profiles.json"
    profile = AuthProfile(provider="s3")
    save_profile("archive-reader", profile, config_path=path)
    value = json.loads(path.read_text())
    assert "$schema" not in value
    assert ProfileConfig.model_validate(value).profiles["archive-reader"] == profile
    assert load_profiles(config_path=path).profiles["archive-reader"] == profile


def test_workspace_schema_urls_resolve_and_recommend_yaml_support():
    settings = json.loads((ROOT / ".vscode/settings.json").read_text())
    urls = {entry["url"] for entry in settings["json.schemas"]}
    urls.update(settings["yaml.schemas"])
    assert len(urls) == 2
    for url in urls:
        schema = json.loads((ROOT / url).read_text())
        Draft202012Validator.check_schema(schema)
    extensions = json.loads((ROOT / ".vscode/extensions.json").read_text())
    assert "redhat.vscode-yaml" in extensions["recommendations"]
