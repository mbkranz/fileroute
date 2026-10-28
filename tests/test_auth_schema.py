"""Auth-profile editor schemas enforce the same provider/mode contracts."""

from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from fileroute.auth.profiles import AuthProfile, ProfileConfig
from fileroute.models import Catalog


MODES = {
    "adc": {"provider": "GoogleDrive", "mode": "adc"},
    "service_account": {
        "provider": "GoogleDrive",
        "mode": "service_account",
        "credentials_file": "credentials/service-account.json",
    },
    "user_oauth": {
        "provider": "GoogleDrive",
        "mode": "user_oauth",
        "oauth_client_secrets": "credentials/desktop.json",
    },
    "app_only": {
        "provider": "SharePoint",
        "mode": "app_only",
        "tenant_id": "example.onmicrosoft.com",
        "client_id": "example-client",
        "host_url": "contoso.sharepoint.com",
        "client_secret_env": "WORK_SECRET",
    },
    "delegated": {
        "provider": "SharePoint",
        "mode": "delegated",
        "tenant_id": "example.onmicrosoft.com",
        "client_id": "example-client",
        "host_url": "contoso.sharepoint.com",
    },
    "default": {"provider": "S3", "mode": "default", "aws_profile": "team-dev"},
}


def _validator() -> Draft202012Validator:
    schema = ProfileConfig.model_json_schema(by_alias=True, mode="validation")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("options", list(MODES.values()), ids=list(MODES))
def test_all_six_modes_validate_in_runtime_and_schema(options):
    model = ProfileConfig.model_validate({"profiles": {"example": options}})
    validator = _validator()
    validator.validate({"profiles": {"example": options}})
    # Public serialization includes explicit nulls for irrelevant fields.
    validator.validate(model.model_dump(mode="json"))


@pytest.mark.parametrize("mode", ["adc", "app_only", "default"])
@pytest.mark.parametrize("authored_mode", ["omitted", None])
def test_default_modes_allow_omission_and_explicit_null(mode, authored_mode):
    options = {**MODES[mode]}
    options.pop("mode")
    if authored_mode is None:
        options["mode"] = None
    config = {"profiles": {"example": options}}
    assert ProfileConfig.model_validate(config).profiles["example"].mode == mode
    _validator().validate(config)


@pytest.mark.parametrize(
    ("mode", "field", "value"),
    [
        ("adc", "mode", "delegated"),
        ("default", "mode", "adc"),
        ("adc", "credentials_file", "credentials/account.json"),
        ("service_account", "oauth_client_secrets", "credentials/desktop.json"),
        ("service_account", "credentials_file", None),
        ("user_oauth", "oauth_client_secrets", None),
        ("user_oauth", "use_local_server", False),
        ("delegated", "client_secret_env", "WORK_SECRET"),
        ("delegated", "tenant_id", None),
        ("app_only", "client_id", None),
        ("app_only", "host_url", None),
        ("app_only", "client_secret_env", None),
        ("app_only", "client_secret_env", "a-pasted-secret-value"),
        ("app_only", "tenant_id", "bad/tenant"),
        ("app_only", "client_id", "  "),
        ("default", "scopes", ["unexpected"]),
        ("default", "aws_profile", "  "),
        ("adc", "scopes", []),
        ("adc", "scopes", ["  "]),
        ("adc", "client_secret", "never-embed-a-secret"),
    ],
)
def test_invalid_provider_mode_fields_fail_in_runtime_and_schema(mode, field, value):
    config = {"profiles": {"example": {**MODES[mode], field: value}}}
    with pytest.raises(ValueError):
        ProfileConfig.model_validate(config)
    assert list(_validator().iter_errors(config))


@pytest.mark.parametrize("options", list(MODES.values()), ids=list(MODES))
def test_irrelevant_explicit_null_fields_remain_runtime_compatible(options):
    authored = {
        name: None
        for name in AuthProfile.model_fields
        if name not in {"provider", "mode"}
    }
    authored.update(options)
    config = {"profiles": {"example": authored}}
    ProfileConfig.model_validate(config)
    _validator().validate(config)


@pytest.mark.parametrize("name", ["../name", ".hidden", "", "a" * 65])
def test_profile_key_constraints_reject_unsafe_names(name):
    config = {"profiles": {name: MODES["adc"]}}
    with pytest.raises(ValueError):
        ProfileConfig.model_validate(config)
    assert list(_validator().iter_errors(config))


def test_schema_branches_offer_only_relevant_fields_and_valid_examples():
    schema = ProfileConfig.model_json_schema()
    profile_schema = schema["$defs"]["AuthProfile"]
    assert profile_schema["properties"]["provider"]["$ref"] == "#/$defs/ServiceType"
    branches = profile_schema["oneOf"]
    assert len(branches) == 3
    validator = _validator()
    for branch in branches:
        assert branch["title"]
        selector_fields = branch["properties"]
        provider = selector_fields["provider"]["const"]
        assert "$ref" not in selector_fields["provider"]
        assert selector_fields["provider"]["type"] == "string"
        assert selector_fields["mode"]["default"] in MODES
        assert set(selector_fields["mode"]["anyOf"][0]["enum"]) == {
            mode for mode, options in MODES.items() if options["provider"] == provider
        }
        for field in selector_fields.values():
            assert field.get("description")
        for example in branch["examples"]:
            ProfileConfig.model_validate({"profiles": {"example": example}})
            validator.validate({"profiles": {"example": example}})
        for condition in branch["allOf"]:
            mode_schema = condition["then"]
            properties = mode_schema["properties"]
            mode = mode_schema["examples"][0]["mode"]
            for name, field in properties.items():
                if name not in {"provider", "mode"}:
                    assert field.get("description")
            assert "client_secret" not in properties
            assert "access_token" not in properties
            if mode == "default":
                assert set(properties) == {"provider", "mode", "aws_profile"}
            if mode == "delegated":
                assert "client_secret_env" not in properties
            if mode == "service_account":
                assert "oauth_client_secrets" not in properties
            if mode == "user_oauth":
                assert properties["use_local_server"]["default"] is True


def test_schema_generation_is_deterministic_and_has_no_config_or_credential_io(
    monkeypatch,
):
    def fail(*args, **kwargs):
        pytest.fail(
            "schema generation must not access files, private profiles, or SDKs"
        )

    monkeypatch.setattr(Path, "read_text", fail)
    monkeypatch.setattr(Path, "is_file", fail)
    monkeypatch.setattr("fileroute.auth.profiles.load_profiles", fail)
    monkeypatch.setattr("fileroute.auth.profiles.boto3.Session", fail)
    monkeypatch.setattr("fileroute.auth.profiles._build_client", fail)
    first = ProfileConfig.model_json_schema()
    assert ProfileConfig.model_json_schema() == first
    _validator().validate({"profiles": {"example": MODES["adc"]}})


def test_catalog_schema_gives_static_location_and_inheritance_help():
    schema = Catalog.model_json_schema(by_alias=True)
    catalog = schema["$defs"]["Catalog"] if "$ref" in schema else schema
    assert catalog["title"] == "Fileroute catalog"
    for name in ("resources", "catalogs", "sources", "targets", "profile"):
        assert catalog["properties"][name]["description"]
        assert catalog["properties"][name]["examples"]
    location = schema["$defs"]["Location"]
    for name in (
        "authProfile",
        "serviceType",
        "serviceId",
        "remotePath",
        "site",
        "siteId",
        "drive",
        "driveId",
        "bucket",
    ):
        assert location["properties"][name]["description"]
    profile = location["properties"]["authProfile"]
    assert "private profile names" in profile["description"]
    assert "enum" not in profile
    assert "inherit" in catalog["properties"]["targets"]["description"]
    # Generating one model's schema must not mutate another model's field schema.
    before = deepcopy(schema)
    ProfileConfig.model_json_schema()
    assert Catalog.model_json_schema(by_alias=True) == before
