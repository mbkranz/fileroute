import json
import re
import shutil
from difflib import unified_diff
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.main import get_command

from control_center.app import ROOT, create_app
from control_center.contracts import ARGUMENTS, REQUEST, VERSION, operation_schema
from fileroute.cli import app as typer_app

ORIGIN = "http://127.0.0.1:8765"
MAPPING = "$.resources.mapping"
UNION = '$.catalogs.outputs.resources["union-schema"]'


def read_json(filename):
    return json.loads((ROOT / "public" / filename).read_text())


def leaves(command, parts=()):
    children = getattr(command, "commands", None)
    if not children:
        return {" ".join(parts)}
    return {
        path
        for name, child in children.items()
        for path in leaves(child, (*parts, name))
    }


EXAMPLES = {
    "catalog.open": {},
    "list": {"kind": "resource"},
    "activate": {"descriptor": "catalog.yaml"},
    "add": {"name": "mapping", "kind": "resource", "entry": {"path": "mapping.xlsx"}},
    "update": {"kind": "resource", "fields": {"targets": None}},
    "resolve": {"selector": MAPPING},
    "discovery.preview": {"selector": "$.catalogs.releases"},
    "discovery.run": {"format": "json"},
    "discovery.save": {"result_id": "example", "output": "discovered.yaml"},
    "transfer.preview": {"direction": "push", "selector": UNION},
    "transfer.run": {"preview_id": "example"},
    "diagram": {"format": "markdown", "detail": "full"},
    "clone.descriptor": {"target_path": "clone.yaml"},
    "migrate": {"descriptor": "legacy.yaml", "output": "converted"},
    "auth.profile.list": {},
    "auth.describe": {"provider": "s3"},
    "auth.profile.show": {"name": "example"},
    "auth.profile.save": {
        "name": "example",
        "settings": {
            "provider": "GoogleDrive",
            "mode": "user_oauth",
            "oauth_client_secrets": "oauth.json",
        },
    },
    "auth.profile.remove": {"name": "example"},
    "auth.check": {"profile": "example"},
    "auth.login.gdrive": {"method": "legacy", "scope": ["drive.readonly"]},
    "auth.login.microsoft": {"method": "legacy", "auth_mode": "delegated"},
    "auth.login.sharepoint": {
        "method": "saved",
        "profile": "example",
        "config": "profiles.yaml",
    },
    "auth.login.s3": {"method": "saved", "profile": "example"},
    "jobs.get": {"job_id": "example"},
    "cli.help": {"command": "auth login s3"},
    "cli.completion.show": {"shell": "fish"},
    "cli.completion.install": {"shell": "bash"},
    "approval.request": {
        "operation": "transfer.run",
        "arguments": {"preview_id": "example"},
    },
}


def test_schema_freshness_and_registered_cli():
    generated = read_json(f"schemas/v{VERSION}/fileroute-operations.schema.json")
    Draft202012Validator.check_schema(generated)
    assert generated == operation_schema()
    index = read_json("schemas/ui-profile.json")
    assert index == read_json(f"schemas/v{VERSION}/ui-profile.json")
    assert set(index["cli"]) == leaves(get_command(typer_app))
    assert set(index["cli"].values()) <= ARGUMENTS.keys()
    assert (
        {op["id"] for op in read_json("operation-views.json")}
        == ARGUMENTS.keys()
        == EXAMPLES.keys()
    )


@pytest.mark.parametrize(("operation", "args"), EXAMPLES.items(), ids=EXAMPLES.keys())
def test_each_operation_with_python_and_draft_2020_12(operation, args):
    validator = Draft202012Validator(operation_schema())
    payload = {"operation": operation, "arguments": args}
    REQUEST.validate_python(payload)
    validator.validate(payload)
    bad = {"operation": operation, "arguments": {**args, "extra_argument": True}}
    assert not validator.is_valid(bad)
    with pytest.raises(ValidationError):
        REQUEST.validate_python(bad)


@pytest.mark.parametrize(
    "settings",
    [
        {"provider": "GoogleDrive", "mode": "adc"},
        {
            "provider": "GoogleDrive",
            "mode": "service_account",
            "credentials_file": "service.json",
        },
        {
            "provider": "GoogleDrive",
            "mode": "user_oauth",
            "oauth_client_secrets": "oauth.json",
        },
        {
            "provider": "SharePoint",
            "mode": "delegated",
            "tenant_id": "example.onmicrosoft.com",
            "client_id": "app",
            "host_url": "example.sharepoint.com",
        },
        {
            "provider": "SharePoint",
            "mode": "app_only",
            "tenant_id": "example.onmicrosoft.com",
            "client_id": "app",
            "host_url": "example.sharepoint.com",
            "client_secret_env": "APP_SECRET",
        },
        {"provider": "S3", "mode": "default", "aws_profile": "dev"},
    ],
)
def test_auth_configuration_modes(settings):
    payload = {
        "operation": "auth.profile.save",
        "arguments": {"name": "profile", "settings": settings},
    }
    Draft202012Validator(operation_schema()).validate(payload)
    REQUEST.validate_python(payload)


@pytest.fixture
def local(tmp_path, monkeypatch):
    work = tmp_path / "work"
    shutil.copytree(
        ROOT / "public" / "fixtures",
        work,
        ignore=shutil.ignore_patterns("workspace.json"),
    )
    monkeypatch.chdir(work)
    monkeypatch.setenv("FILEROUTE_CONFIG_DIR", str(work / "config"))
    application = create_app(work)
    with TestClient(application, base_url=ORIGIN) as client:
        index = client.get("/")
        assert (
            index.status_code == 200
            and 'name="fileroute-runtime" content="local"' in index.text
        )
        token = re.search(
            r'name="fileroute-session" content="([\w-]+)"', index.text
        ).group(1)
        session = {"origin": ORIGIN, "x-fileroute-session": token}

        def call(operation, arguments=None, approval=None):
            headers = (
                session
                if approval is None
                else {**session, "x-fileroute-approval": approval}
            )
            return client.post(
                "/api/operations",
                headers=headers,
                json={"operation": operation, "arguments": arguments or {}},
            )

        yield work, client, call, session


def test_loopback_boundary_and_bundled_assets(local):
    _, client, call, session = local
    assert client.get("/", headers={"host": "example.test"}).status_code == 400
    assert client.get("/api/state").status_code == 403
    assert (
        client.post(
            "/api/operations",
            headers={**session, "origin": "https://example.test"},
            json={"operation": "catalog.open", "arguments": {}},
        ).status_code
        == 403
    )
    assert client.get("/api/state", headers=session).status_code == 200
    assert client.get("/schemas/v1.0.0/ui-profile.json").status_code == 200
    assert call("auth.check", {"profile": "archive-reader"}).status_code == 403


def prohibit(*args, **kwargs):
    pytest.fail("Offline action attempted to use provider credentials or the network")


def test_offline_previews_match_cli_without_authentication(local, monkeypatch):
    work, _, call, _ = local
    from fileroute.auth import profiles
    from fileroute.clients import get_provider
    from fileroute.models import ServiceType
    from typer.testing import CliRunner

    no_provider = monkeypatch.setattr
    no_provider(profiles, "authenticate_profile", prohibit)
    no_provider(profiles, "build_profile_client", prohibit)
    for service in ServiceType:
        no_provider(get_provider(service), "build_default", prohibit)
    no_provider("socket.create_connection", prohibit)
    before = {
        path.relative_to(work): path.read_bytes()
        for path in work.rglob("*")
        if path.is_file()
    }
    catalog = call("catalog.open").json()
    union = next(row for row in catalog["entries"] if row["selector"] == UNION)
    assert union["originDescriptor"] == "catalogs/outputs.yaml"
    assert union["targetOrigin"]["selector"] == "$.catalogs.outputs"

    for direction, selector, adjective in (
        ("pull", MAPPING, "download"),
        ("push", UNION, "upload"),
    ):
        response = call(
            "transfer.preview", {"direction": direction, "selector": selector}
        )
        assert response.status_code == 200, response.text
        (row,) = response.json()["rows"]
        source = str(work / row["from"]) if direction == "push" else row["from"]
        target = str(work / row["to"]) if direction == "pull" else row["to"]
        cli = CliRunner().invoke(
            typer_app, [direction, "catalog.yaml", "--select", selector, "--dry-run"]
        )
        assert cli.exit_code == 0, cli.output
        assert f"Would {adjective} {source} -> {target}" in cli.output
    assert (
        call("resolve", {"selector": MAPPING}).json()["verification"]
        == "Offline inference"
    )
    scope = call("discovery.preview", {"selector": "$.catalogs.releases"}).json()
    assert scope["rows"][0]["template"] == "{survey}/v{revision:d}.json"
    after = {
        path.relative_to(work): path.read_bytes()
        for path in work.rglob("*")
        if path.is_file()
    }
    assert before == after


@pytest.mark.parametrize(
    "targets", [None, [], [{"path": "s3://example-bucket/key.json"}]]
)
def test_linked_edit_preserves_authored_target_states(local, targets):
    work, _, call, _ = local
    before = (work / "catalog.yaml").read_bytes()
    inputs = {
        "selector": UNION,
        "kind": "resource",
        "fields": {"targets": targets},
        "dry_run": True,
    }
    preview = call("update", inputs)
    assert preview.status_code == 200, preview.text
    assert "targets" not in preview.json()["before"]
    assert preview.json()["after"]["targets"] == targets
    assert preview.json()["origin"] == "catalogs/outputs.yaml"
    assert (
        call("update", {**inputs, "dry_run": False, "revision": "stale"}).status_code
        == 422
    )
    saved = call(
        "update", {**inputs, "dry_run": False, "revision": preview.json()["revision"]}
    )
    assert saved.status_code == 200, saved.text
    assert (work / "catalog.yaml").read_bytes() == before
    row = next(
        row
        for row in call("catalog.open").json()["entries"]
        if row["selector"] == UNION
    )
    assert row["entity"]["targets"] == targets
    assert row["effectiveTargets"] == (
        row["entity"]["targets"] if targets is not None else union_targets()
    )
    assert row["entity"]["sources"]


def union_targets():
    return [
        {
            "authProfile": "governance-publisher",
            "entityType": "Directory",
            "path": "https://example.sharepoint.com/sites/governance/Shared%20Documents/Published%20Schemas",
        }
    ]


def test_graph_edit_preview_matches_the_file_written(local):
    work, _, call, _ = local
    path = work / "catalogs" / "outputs.yaml"
    original = "# The linked output catalog\n" + path.read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")
    graph = call("catalog.open").json()
    target = {
        "from": UNION, "to": "$.catalogs.outputs.targets[0]",
        "kind": "target", "inherited": True,
    }
    assert target in graph["relationships"]
    assert {
        "from": UNION + ".sources[0]", "to": UNION,
        "kind": "source", "inherited": False,
    } in graph["relationships"]

    inputs = {"selector": UNION, "kind": "resource", "fields": {"title": "Reviewed union schema"}}
    result = call("update", inputs)
    assert result.status_code == 200, result.text
    preview = result.json()
    assert preview["origin"] == "catalogs/outputs.yaml"
    assert "fileroute update --descriptor catalogs/outputs.yaml" in preview["command"]
    assert "-    title: Survey union schema" in preview["patch"]
    assert "+    title: Reviewed union schema" in preview["patch"]
    assert path.read_text(encoding="utf-8") == original

    save = call("update", {**inputs, "dry_run": False, "revision": preview["revision"]})
    assert save.status_code == 200, save.text
    written = path.read_text(encoding="utf-8")
    assert written.startswith("# The linked output catalog\n")
    assert preview["patch"] == "".join(unified_diff(
        original.splitlines(keepends=True), written.splitlines(keepends=True),
        fromfile="a/catalogs/outputs.yaml", tofile="b/catalogs/outputs.yaml",
    ))
    assert "descriptor: catalogs/outputs.yaml" in (work / "catalog.yaml").read_text()
    repeated = call("update", inputs).json()
    assert repeated["patch"] == ""

    disable = {**inputs, "fields": {"targets": []}, "dry_run": False, "revision": repeated["revision"]}
    disabled = call("update", disable)
    assert disabled.status_code == 200, disabled.text
    assert target not in call("catalog.open").json()["relationships"]
    restore = call("update", {**disable, "fields": {}, "unset": ["targets"], "revision": disabled.json()["revision"]})
    assert restore.status_code == 200, restore.text
    assert target in call("catalog.open").json()["relationships"]


def test_local_authoring_and_exports(local):
    work, _, call, _ = local
    assert (
        call(
            "add", {"name": "new", "kind": "resource", "entry": {"path": "new.txt"}}
        ).status_code
        == 200
    )
    assert "new" not in (work / "catalog.yaml").read_text()
    added = call(
        "add",
        {
            "name": "new",
            "kind": "resource",
            "entry": {"path": "new.txt"},
            "dry_run": False,
        },
    )
    assert added.status_code == 200, added.text
    assert call("activate", {"descriptor": "catalog.yaml"}).status_code == 200
    assert call("catalog.open").json()["activeDescriptor"] == "catalog.yaml"
    clone = call("clone.descriptor", {"target_path": "clone.yaml", "dry_run": False})
    assert clone.status_code == 200, clone.text
    assert (work / "clone.yaml").is_file()
    converted = call(
        "migrate",
        {"descriptor": "legacy.yaml", "output": "converted", "dry_run": False},
    )
    assert converted.status_code == 200, converted.text
    assert (work / "converted" / "legacy.yaml").is_file()
    for format in ["svg", "html", "markdown", "mermaid"]:
        response = call("diagram", {"format": format, "detail": "full"})
        assert response.status_code == 200, response.text
        assert response.json()["content"]
        if format == "markdown":
            assert "<svg" in response.json()["companion"]
    assert (
        "complete"
        in call("cli.completion.show", {"shell": "bash"}).json()["content"].lower()
    )
    assert (
        "read access" in call("cli.help", {"command": "auth check"}).json()["content"]
    )


def grant(call, operation, args):
    return call("approval.request", {"operation": operation, "arguments": args}).json()[
        "approval_id"
    ]


def wait_job(call, job_id):
    limit = monotonic() + 5
    while monotonic() < limit:
        job = call("jobs.get", {"job_id": job_id}).json()
        if job["status"] in {"succeeded", "failed"}:
            return job
        sleep(0.02)
    pytest.fail("Local job did not finish")


def test_approval_binds_scope_and_repeated_transfer_is_idempotent(local, monkeypatch):
    _, _, call, _ = local
    invoked = []
    running, finish = Event(), Event()

    def fake_push(entries, *, profile=None):
        invoked.append((entries, profile))
        running.set()
        assert finish.wait(3)

    monkeypatch.setattr("control_center.workspace.push", fake_push)
    preview = call("transfer.preview", {"direction": "push", "selector": UNION}).json()
    args = {"preview_id": preview["preview_id"]}
    assert call("transfer.run", args).status_code == 403
    wrong = grant(call, "transfer.run", {"preview_id": "different"})
    assert call("transfer.run", args, wrong).status_code == 403
    assert not invoked
    result = call("transfer.run", args, grant(call, "transfer.run", args))
    job_id = result.json()["job_id"]
    assert result.json()["status"] == "queued"
    assert result.json()["catalogScope"] == {"descriptor": "catalog.yaml", "selector": UNION}
    assert running.wait(2)
    assert call("jobs.get", {"job_id": job_id}).json()["status"] == "running"
    try:
        again = call("transfer.run", args, grant(call, "transfer.run", args))
        assert again.json()["job_id"] == job_id
    finally:
        finish.set()
    assert wait_job(call, job_id)["status"] == "succeeded"
    assert len(invoked) == 1


def test_changed_local_state_requires_new_preview(local, monkeypatch):
    work, _, call, _ = local
    monkeypatch.setattr("control_center.workspace.push", prohibit)
    preview = call("transfer.preview", {"direction": "push", "selector": UNION}).json()
    (work / "staging/schemas/1001/v8.json").write_text('{"changed": true}')
    args = {"preview_id": preview["preview_id"]}
    job = call("transfer.run", args, grant(call, "transfer.run", args)).json()
    checked = wait_job(call, job["job_id"])
    assert checked["status"] == "failed" and "changed" in checked["error"]


def test_credential_checks_use_double_only_after_approval(local, monkeypatch):
    _, _, call, _ = local
    calls = []

    from fileroute.auth import profiles

    def check(name, **args):
        calls.append((name, args))
        return profiles.ProfileReceipt(
            name=name,
            provider="S3",
            mode="default",
            action="authenticated",
            config_path=local[0] / "profiles.yaml",
        )

    monkeypatch.setattr(profiles, "authenticate_profile", check)
    args = {"profile": "archive-reader", "config": "profiles.yaml"}
    assert call("auth.check", args).status_code == 403 and not calls
    job = call("auth.check", args, grant(call, "auth.check", args)).json()
    assert wait_job(call, job["job_id"])["result"]["title"] == "Credentials checked"
    assert len(calls) == 1


def test_discovery_result_saved_with_original_scope(local, monkeypatch):
    work, _, call, _ = local
    from fileroute.models import Catalog, Resource

    def discover(path, *, root, selector, profile):
        assert path == work / "catalog.yaml" and root == work
        assert selector == "$.catalogs.releases"
        return Catalog(
            resources={
                "release": Resource(
                    path="releases/1001/v8.json",
                    sources=[
                        {"path": "s3://example-fileroute-fixtures/surveys/1001/v8.json"}
                    ],
                    pathProperties={"revision": 8},
                )
            }
        )

    monkeypatch.setattr("control_center.workspace.discover_resources", discover)
    args = {"selector": "$.catalogs.releases", "format": "json"}
    job = call("discovery.run", args, grant(call, "discovery.run", args)).json()
    result = wait_job(call, job["job_id"])["result"]
    assert result["rows"][0]["captures"]["revision"] == 8
    result_id = result["result_id"]
    assert (
        call(
            "discovery.save", {"result_id": result_id, "output": "catalog.yaml"}
        ).status_code
        == 422
    )
    assert (
        call(
            "discovery.save",
            {"result_id": result_id, "output": "releases/1001/v8.json"},
        ).status_code
        == 422
    )
    assert (
        call(
            "discovery.save", {"result_id": result_id, "output": "discovered.yaml"}
        ).status_code
        == 200
    )
    assert (work / "discovered.yaml").is_file()


def test_profile_save_defaults_and_remove_follow_fileroute(local):
    work, _, call, _ = local
    args = {"name": "test-s3", "settings": {"provider": "S3", "aws_profile": "example"}}
    saved = call("auth.profile.save", args)
    assert saved.status_code == 200, saved.text
    assert (work / "config/profiles.yaml").is_file()
    (profile,) = call("auth.profile.list").json()["rows"]
    assert profile == {"name": "test-s3", "provider": "S3", "mode": "default"}
    assert call("auth.profile.save", args).status_code == 422
    assert (
        call("auth.profile.show", {"name": "test-s3"}).json()["settings"]["aws_profile"]
        == "example"
    )
    assert call("auth.profile.remove", {"name": "test-s3"}).status_code == 200
    assert not call("auth.profile.list").json()["rows"]


def test_transfer_keeps_descriptor_when_cli_selection_changes(local, monkeypatch):
    work, _, call, _ = local
    calls = []

    def fake_push(entries, **kwargs):
        calls.extend(entries)

    monkeypatch.setattr("control_center.workspace.push", fake_push)
    plan = call("transfer.preview", {"direction": "push", "selector": UNION}).json()
    (work / "other.yaml").write_text("title: Other scope\n")
    assert call("activate", {"descriptor": "other.yaml"}).status_code == 200
    args = {"preview_id": plan["preview_id"]}
    job = call("transfer.run", args, grant(call, "transfer.run", args)).json()
    assert wait_job(call, job["job_id"])["status"] == "succeeded"
    assert calls[0].local == work / "staging/schemas/1001/v8.json"


def test_resource_changes_emit_only_supplied_fields():
    # Updating a description must not serialize default changes to other fields.
    from control_center.workspace import authored

    contract = ARGUMENTS["update"]
    args = contract.validate_python(
        {"kind": "resource", "fields": {"description": "Revised"}}
    )
    assert authored(args["fields"]) == {"description": "Revised"}


def test_auth_describe_inspector_matches_cli(local):
    from typer.testing import CliRunner

    work, _, call, _ = local
    monkeypatch_profile = work / "config"
    monkeypatch_profile.mkdir(exist_ok=True)
    shutil.copyfile(work / "profiles.yaml", monkeypatch_profile / "profiles.yaml")
    args = {
        "descriptor": "catalog.yaml",
        "operation": "push",
        "selector": UNION,
        "location": "$.catalogs.outputs.targets[0]",
    }
    response = call("auth.describe", args)
    assert response.status_code == 200, response.text
    result = CliRunner().invoke(typer_app, [
        "auth", "describe", "--descriptor", "catalog.yaml", "--operation", "push",
        "--select", UNION, "--location", args["location"], "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    cli = json.loads(result.output)
    route = cli["routes"][0]
    assert route["profile"] == "governance-publisher" and route["inherited"]
    route["origin_descriptor"] = "catalogs/outputs.yaml"
    assert response.json()["routes"] == cli["routes"]
    assert response.json()["guides"] == cli["guides"]
