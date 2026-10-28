"""Named profiles select accounts without affecting offline descriptor work."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.descriptor import load, resolve, resolve_selection, save
from fileroute.models import Catalog, Location, Resource, ServiceType
from fileroute.resolution import ResolvedLocation
from fileroute.transfer import plan_pull, plan_push, pull, push


def _descriptor(tmp_path: Path, catalog: Catalog) -> Path:
    path = tmp_path / "catalog.yaml"
    save(catalog, path)
    return path


def test_pull_uses_two_accounts_and_reuses_each_client(tmp_path, monkeypatch):
    catalog = Catalog(
        resources={
            name: Resource(
                path=f"downloads/{name}.csv",
                sources=[Location(path="s3://bucket/same.csv", authProfile=profile)],
            )
            for name, profile in (("one", "alpha"), ("two", "beta"), ("three", "alpha"))
        }
    )
    catalog.resources["four"] = Resource(
        path="downloads/four.csv", sources=[Location(path="s3://bucket/same.csv")]
    )
    built = []

    def build(name, service_type):
        built.append((name, service_type))
        return SimpleNamespace(
            get_from_location=lambda location: SimpleNamespace(
                is_directory=False, download=lambda target: target.write_text(name)
            )
        )

    path = _descriptor(tmp_path, catalog)
    entries = plan_pull(path, root=tmp_path)
    monkeypatch.setattr("fileroute.auth.profiles.build_profile_client", build)
    monkeypatch.setattr(
        "fileroute.transfer.get_provider",
        lambda _: pytest.fail("named profiles must not use legacy credentials"),
    )
    pull(entries, profile="beta")
    assert built == [("alpha", ServiceType.S3), ("beta", ServiceType.S3)]
    assert [entry.local.read_text() for entry in entries] == [
        "alpha",
        "beta",
        "alpha",
        "beta",
    ]


def test_push_inherits_profile_and_keeps_two_account_destinations(
    tmp_path, monkeypatch
):
    for name in ("one", "two", "three"):
        (tmp_path / f"{name}.csv").write_text(name)
    folder = "https://drive.google.com/drive/folders/same-folder"
    path = _descriptor(
        tmp_path,
        Catalog(
            targets=[Location(path=folder, authProfile="alpha")],
            resources={
                "one": Resource(path="one.csv"),
                "two": Resource(
                    path="two.csv", targets=[Location(path=folder, authProfile="beta")]
                ),
                "three": Resource(path="three.csv"),
            },
        ),
    )
    entries = plan_push(path, root=tmp_path)
    built, uploaded = [], []

    def build(name, service_type):
        built.append((name, service_type))
        return SimpleNamespace(
            upload_to_location=lambda location, relative, local: uploaded.append((
                name,
                location.auth_profile,
                relative,
                local.read_text(),
            ))
        )

    monkeypatch.setattr("fileroute.auth.profiles.build_profile_client", build)
    push(entries, profile="unused")
    assert built == [
        ("alpha", ServiceType.GOOGLE_DRIVE),
        ("beta", ServiceType.GOOGLE_DRIVE),
    ]
    assert uploaded == [
        ("alpha", "alpha", Path("one.csv"), "one"),
        ("beta", "beta", Path("two.csv"), "two"),
        ("alpha", "alpha", Path("three.csv"), "three"),
    ]
    # A profile cannot make the same globally identified destination distinct.
    catalog = load(path)
    catalog.resources["two"].targets = [
        Location(
            path="https://drive.google.com/file/d/same-file/view", authProfile="alpha"
        )
    ]
    catalog.resources["one"].targets = [
        Location(path="https://drive.google.com/open?id=same-file", authProfile="beta")
    ]
    save(catalog, path)
    with pytest.raises(ValueError, match="Multiple local files"):
        plan_push(path, root=tmp_path)


def test_online_resolve_scopes_cache_by_profile_and_provider(monkeypatch):
    locations = [
        Location(path="s3://bucket/one", authProfile="alpha"),
        Location(path="s3://bucket/two", authProfile="beta"),
        Location(path="s3://bucket/three", authProfile="alpha"),
        Location(path="s3://bucket/four"),
        Location(
            path="https://drive.google.com/file/d/google/view", authProfile="alpha"
        ),
    ]
    built, looked_up = [], []

    def build(name, service_type):
        built.append((name, service_type))

        def lookup(location):
            looked_up.append((name, location.path))
            return ResolvedLocation(service_type, entity_type="File")

        return SimpleNamespace(resolve_location=lookup)

    monkeypatch.setattr("fileroute.auth.profiles.build_profile_client", build)
    result = resolve(Catalog(sources=locations), online=True, profile="beta")
    assert built == [
        ("alpha", ServiceType.S3),
        ("beta", ServiceType.S3),
        ("alpha", ServiceType.GOOGLE_DRIVE),
    ]
    assert [name for name, _ in looked_up] == [
        "alpha",
        "beta",
        "alpha",
        "beta",
        "alpha",
    ]
    assert all(location.entity_type == "File" for location in result.sources)
    assert all(location.entity_type is None for location in locations)


def test_selected_online_resolve_reuses_profile_for_inherited_targets(
    tmp_path, monkeypatch
):
    path = _descriptor(
        tmp_path,
        Catalog(
            targets=[Location(path="s3://bucket/destination/", authProfile="alpha")],
            resources={
                "file": Resource(
                    path="file.csv", sources=[Location(path="s3://bucket/source")]
                )
            },
        ),
    )
    built = []

    def build(name, service_type):
        built.append((name, service_type))
        return SimpleNamespace(
            resolve_location=lambda location: ResolvedLocation(service_type)
        )

    monkeypatch.setattr("fileroute.auth.profiles.build_profile_client", build)
    selection = resolve_selection(path, "file", online=True, profile="alpha")
    assert built == [("alpha", ServiceType.S3)]
    assert selection.effective_targets[0].auth_profile == "alpha"
    assert selection.model.targets is None


@pytest.mark.parametrize("direction", ["pull", "push"])
def test_invalid_later_profile_fails_before_any_bytes(tmp_path, monkeypatch, direction):
    (tmp_path / "one.csv").write_text("one")
    (tmp_path / "two.csv").write_text("two")
    service = (
        "s3://bucket"
        if direction == "pull"
        else "https://drive.google.com/drive/folders"
    )
    field = "sources" if direction == "pull" else "targets"
    path = _descriptor(
        tmp_path,
        Catalog(
            resources={
                name: Resource(
                    path=f"{name}.csv",
                    **{
                        field: [Location(path=f"{service}/{name}", authProfile=profile)]
                    },
                )
                for name, profile in (("one", "alpha"), ("two", "missing"))
            }
        ),
    )
    entries = (plan_pull if direction == "pull" else plan_push)(path, root=tmp_path)
    events = []

    def build(name, service_type):
        if name == "missing":
            raise ValueError("Profile 'missing' was not found; run fileroute auth list")
        return SimpleNamespace(
            get_from_location=lambda location: events.append("lookup"),
            upload_to_location=lambda *args: events.append("upload"),
        )

    monkeypatch.setattr("fileroute.auth.profiles.build_profile_client", build)
    with pytest.raises(ValueError, match="missing.*fileroute auth list"):
        (pull if direction == "pull" else push)(entries)
    assert events == []


def test_offline_work_never_reads_profiles(tmp_path, monkeypatch):
    (tmp_path / "file.csv").write_text("data")
    path = _descriptor(
        tmp_path,
        Catalog(
            resources={
                "file": Resource(
                    path="file.csv",
                    sources=[
                        Location(
                            path="s3://bucket/source", authProfile="does-not-exist"
                        )
                    ],
                    targets=[
                        Location(
                            path="https://drive.google.com/drive/folders/folder",
                            authProfile="does-not-exist",
                        )
                    ],
                )
            }
        ),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "fileroute.auth.profiles.build_profile_client",
        lambda *args, **kwargs: pytest.fail("offline profile configuration access"),
    )
    monkeypatch.setattr(
        "fileroute.auth.profiles.load_profiles",
        lambda *args, **kwargs: pytest.fail("offline profile configuration read"),
    )
    resolve(load(path), profile="also-missing")
    resolve_selection(path, "file", profile="also-missing")
    runner = CliRunner()
    for command in (
        ["list", str(path), "--format", "json"],
        ["resolve", str(path), "--profile", "also-missing"],
        ["pull", str(path), "--profile", "also-missing", "--dry-run"],
        ["push", str(path), "--profile", "also-missing", "--dry-run"],
        ["diagram", str(path), "--output", str(tmp_path / "diagram.svg")],
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output


@pytest.mark.parametrize("command", ["pull", "push", "resolve"])
@pytest.mark.parametrize("profile", ["missing", "wrong-provider"])
def test_cli_profile_errors_are_actionable_before_auth_or_transfer(
    tmp_path, monkeypatch, command, profile
):
    (tmp_path / "file.csv").write_text("data")
    config_dir = tmp_path / "auth"
    config_dir.mkdir()
    (config_dir / "profiles.yaml").write_text(
        "profiles:\n  wrong-provider:\n    provider: GoogleDrive\n    mode: adc\n"
    )
    monkeypatch.setenv("FILEROUTE_CONFIG_DIR", str(config_dir))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "fileroute.auth.profiles._build_client",
        lambda *args, **kwargs: pytest.fail("invalid profile reached authentication"),
    )
    path = _descriptor(
        tmp_path,
        Catalog(
            resources={
                "file": Resource(
                    path="file.csv",
                    sources=[Location(path="s3://bucket/source")],
                    targets=[
                        Location(
                            path="https://tenant.sharepoint.com/sites/dev/Docs/file.csv"
                        )
                    ],
                )
            }
        ),
    )
    arguments = [command, str(path), "--profile", profile]
    if command == "resolve":
        arguments.append("--online")
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 1, result.output
    assert (
        "does not exist" in result.output
        if profile == "missing"
        else "location requires" in result.output
    )
    assert (tmp_path / "file.csv").read_text() == "data"


@pytest.mark.parametrize("name", ["alpha", "team.prod-1", "A" * 64, None])
def test_auth_profile_validates_and_preserves_location_extensions(name):
    data = {"path": "s3://bucket/key", "authProfile": name, "owner": {"team": "data"}}
    location = Location.model_validate(data)
    assert location.auth_profile == name
    assert location.model_dump(by_alias=True)["owner"] == {"team": "data"}
    Draft202012Validator(Location.model_json_schema(by_alias=True)).validate(data)


@pytest.mark.parametrize(
    "name", ["", " ", "a/b", "../name", ".hidden", "A" * 65, 123, True]
)
def test_auth_profile_rejects_invalid_names_in_model_and_schema(name):
    data = {"path": "s3://bucket/key", "authProfile": name}
    with pytest.raises(ValueError):
        Location.model_validate(data)
    assert list(
        Draft202012Validator(Location.model_json_schema(by_alias=True)).iter_errors(
            data
        )
    )


def test_location_profile_alias_roundtrips_and_catalog_profile_stays_independent(
    tmp_path,
):
    path = _descriptor(
        tmp_path,
        Catalog(
            profile="custom-catalog",
            sources=[Location(path="s3://bucket/key", auth_profile="alpha")],
        ),
    )
    catalog = load(path)
    assert catalog.profile == "custom-catalog"
    assert catalog.sources[0].auth_profile == "alpha"
    assert "authProfile: alpha" in path.read_text()


@pytest.mark.parametrize("command", ["pull", "push"])
def test_cli_passes_run_profile_to_transfers(tmp_path, monkeypatch, command):
    (tmp_path / "file.csv").write_text("data")
    path = _descriptor(
        tmp_path,
        Catalog(
            resources={
                "file": Resource(
                    path="file.csv",
                    sources=[Location(path="s3://bucket/source")],
                    targets=[
                        Location(path="https://drive.google.com/drive/folders/folder")
                    ],
                )
            }
        ),
    )
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(
        f"fileroute.transfer.{command}",
        lambda entries, *, profile: calls.append(profile),
    )
    result = CliRunner().invoke(app, [command, str(path), "--profile", "alpha"])
    assert result.exit_code == 0, result.output
    assert calls == ["alpha"]
