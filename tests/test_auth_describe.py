"""Offline CLI coverage for auth guidance and route provenance."""

import json

from typer.testing import CliRunner

from fileroute.cli import app


def test_auth_describe_linked_route_and_profile_precedence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FILEROUTE_CONFIG_DIR", str(tmp_path / "auth"))
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "profiles.yaml").write_text(
        "profiles:\n  publisher:\n    provider: s3\n    aws_profile: team-dev\n"
    )
    (tmp_path / "catalog.yaml").write_text(
        "catalogs:\n  outputs:\n    descriptor: outputs.yaml\n"
    )
    (tmp_path / "outputs.yaml").write_text(
        "targets:\n"
        "  - path: s3://example-bucket/published/schema.json\n"
        "    authProfile: publisher\n"
        "resources:\n"
        "  schema:\n"
        "    path: staged/schema.json\n"
        "  disabled:\n"
        "    path: staged/disabled.json\n"
        "    targets: []\n"
    )

    def provider_auth_must_not_run(*args, **kwargs):
        raise AssertionError("describe accessed provider authentication")

    monkeypatch.setattr(
        "fileroute.auth.profiles.build_profile_client", provider_auth_must_not_run
    )
    monkeypatch.setattr(
        "fileroute.auth.profiles.authenticate_profile", provider_auth_must_not_run
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "auth",
            "describe",
            "--operation",
            "push",
            "--select",
            "outputs",
            "--profile",
            "missing-default",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    routes = json.loads(result.output)["routes"]
    assert len(routes) == 1
    route = routes[0]
    assert route["consumer_selector"] == "$.catalogs.outputs.resources.schema"
    assert route["declaration_selector"] == "$.catalogs.outputs.targets[0]"
    assert route["origin_selector"] == "$.targets[0]"
    assert route["origin_descriptor"] == str(tmp_path / "outputs.yaml")
    assert route["inherited"] and route["profile"] == "publisher"
    assert route["profile_source"] == "location" and route["mode"] == "default"

    result = runner.invoke(
        app,
        [
            "auth",
            "describe",
            "--url",
            "s3://example-bucket/published/schema.json",
            "--profile",
            "missing-default",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["routes"][0]["error"].startswith("Auth profile")


def test_auth_describe_provider_and_mode():
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["auth", "describe", "sharepoint", "--mode", "delegated", "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    assert [guide["mode"] for guide in json.loads(result.output)["guides"]] == [
        "delegated"
    ]
    assert (
        runner.invoke(app, ["auth", "describe", "s3", "--mode", "delegated"]).exit_code
        != 0
    )
