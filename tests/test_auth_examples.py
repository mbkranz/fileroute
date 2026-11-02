"""Saved auth examples validate and remain usable without live credentials."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fileroute.auth.profiles import ProfileConfig, load_profiles
from fileroute.cli import app
from fileroute.clients import get_provider
from fileroute.descriptor import load, resolve, save, walk
from fileroute.diagram import load_graph, render_mermaid, render_svg
from fileroute.models import Catalog, Resource, ServiceType
from fileroute.transfer import plan_pull, plan_push


EXAMPLES = Path(__file__).parents[1] / "examples"
PROFILE_TEMPLATE = EXAMPLES / "auth" / "profiles.yaml"
CATALOG_EXAMPLE = EXAMPLES / "use-cases" / "profile-identities.yaml"


def _forbid_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("synthetic examples must not read credentials or authenticate")

    monkeypatch.setattr("fileroute.auth.profiles.build_profile_client", fail)
    monkeypatch.setattr("fileroute.auth.profiles.load_profiles", fail)
    for provider in ServiceType:
        monkeypatch.setattr(get_provider(provider), "build_default", fail)


def test_profile_template_validates_without_resolving_credential_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("reading a configuration template must not authenticate")

    monkeypatch.setattr("fileroute.auth.profiles._build_client", fail)
    config = load_profiles(config_path=PROFILE_TEMPLATE)
    assert isinstance(config, ProfileConfig)
    assert {
        (profile.provider, profile.mode) for profile in config.profiles.values()
    } == {
        (ServiceType.SHAREPOINT, "delegated"),
        (ServiceType.SHAREPOINT, "app_only"),
        (ServiceType.GOOGLE_DRIVE, "user_oauth"),
        (ServiceType.GOOGLE_DRIVE, "service_account"),
        (ServiceType.GOOGLE_DRIVE, "adc"),
        (ServiceType.S3, "default"),
    }
    assert (
        config.profiles["work-automation"].client_secret_env
        == "WORK_AZURE_CLIENT_SECRET"
    )
    assert config.profiles["partner-writer"].oauth_client_secrets == Path(
        "credentials/google-desktop.json"
    )
    assert config.profiles["google-automation"].credentials_file == Path(
        "credentials/google-service-account.json"
    )


def test_catalog_example_profile_references_match_template_and_survive_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = load_profiles(config_path=PROFILE_TEMPLATE).profiles
    catalog = load(CATALOG_EXAMPLE)
    before = catalog.model_dump(mode="json", by_alias=True, exclude_unset=True)
    _forbid_credentials(monkeypatch)
    resolved = resolve(catalog)
    selected = set()
    for row in walk(resolved, include_self=True):
        if not isinstance(row.model, (Catalog, Resource)):
            continue
        for location in [*row.model.sources, *(row.model.targets or [])]:
            name = location.auth_profile
            assert name is not None
            assert name in profiles, f"Unknown example authProfile: {name}"
            assert profiles[name].provider == location.service_type
            selected.add(name)
    assert selected == {"work-reader", "partner-writer", "archive-reader"}
    destination = tmp_path / "catalog.yaml"
    save(catalog, destination)
    assert (
        load(destination).model_dump(mode="json", by_alias=True, exclude_unset=True)
        == before
    )


def test_catalog_example_offline_plans_and_diagrams_need_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_credentials(monkeypatch)
    monkeypatch.chdir(tmp_path)
    catalog = load(CATALOG_EXAMPLE)
    for row in walk(catalog):
        if isinstance(row.model, Resource):
            local = tmp_path / row.model.path
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_text("synthetic example artifact")
    downloads = plan_pull(CATALOG_EXAMPLE, root=tmp_path)
    uploads = plan_push(CATALOG_EXAMPLE, root=tmp_path)
    assert {entry.location.auth_profile for entry in downloads} == {
        "work-reader",
        "archive-reader",
    }
    assert [entry.location.auth_profile for entry in uploads] == ["partner-writer"]
    graph = load_graph(CATALOG_EXAMPLE)
    assert render_svg(graph, tmp_path / "example.svg").read_text().startswith("<svg")
    assert "flowchart" in render_mermaid(graph, tmp_path / "example.mmd").read_text()
    runner = CliRunner()
    for arguments in (
        ["list", str(CATALOG_EXAMPLE), "--format", "json"],
        ["resolve", str(CATALOG_EXAMPLE)],
        ["pull", str(CATALOG_EXAMPLE), "--dry-run"],
        ["push", str(CATALOG_EXAMPLE), "--dry-run"],
    ):
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0, result.output
