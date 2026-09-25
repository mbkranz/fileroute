"""End-to-end planning and dispatch without authentication or network I/O."""

from pathlib import Path
from types import SimpleNamespace
import pytest
from typer.testing import CliRunner
from fileroute.cli import app
from fileroute.descriptor import save
from fileroute.models import Catalog, Resource, Location
from fileroute.transfer import PushEntry, plan_push, push, plan_pull, pull

REMOTE = "https://tenant.sharepoint.com/sites/dev/Docs"


def descriptor(tmp_path, **kwargs):
    path = tmp_path / "config/catalog.yaml"
    save(Catalog(**kwargs), path)
    return path


def test_targets_inherit_override_and_opt_out(tmp_path):
    out = tmp_path / "out"
    (out / "sub").mkdir(parents=True)
    for name in ("one.docx", "sub/two.docx", "three.docx", "private.docx"):
        (out / name).write_text("artifact")
    path = descriptor(
        tmp_path,
        path="out",
        targets=[Location(path=REMOTE)],
        resources={
            "entry0": Resource(
                title="one", path="out/one.docx", sources=[Location(path="one.qmd")]
            ),
            "entry1": Resource(
                title="renamed",
                path="out/three.docx",
                targets=[Location(path=REMOTE + "/renamed.docx")],
            ),
            "entry2": Resource(title="private", path="out/private.docx", targets=[]),
        },
        catalogs={
            "nested": Catalog(
                title="nested", resources={"two": Resource(path="out/sub/two.docx")}
            )
        },
    )
    plan = plan_push(path, root=tmp_path)
    assert [entry.destination for entry in plan] == [
        REMOTE + "/one.docx",
        REMOTE + "/renamed.docx",
        REMOTE + "/sub/two.docx",
    ]


def test_multiple_targets_and_folder_target(tmp_path):
    (tmp_path / "guide.docx").write_text("artifact")
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="guide.docx",
                targets=[
                    Location(path=REMOTE, entityType="Directory"),
                    Location(path=REMOTE + "/copy.docx"),
                ],
            )
        },
    )
    assert [entry.destination for entry in plan_push(path, root=tmp_path)] == [
        REMOTE + "/guide.docx",
        REMOTE + "/copy.docx",
    ]


def test_push_never_uses_provenance_as_destination(tmp_path):
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(path="file", sources=[Location(path=REMOTE + "/source")])
        },
    )
    with pytest.raises(ValueError, match="targets"):
        plan_push(path, root=tmp_path)


@pytest.mark.parametrize(
    "kind",
    ["escape", "parent_symlink", "collision", "unsupported", "query", "oversized"],
)
def test_push_preflight_rejects_bad_plan(tmp_path, kind, monkeypatch):
    (tmp_path / "file").write_text("one")
    (tmp_path / "other").write_text("two")
    resource = Resource(path="file", targets=[Location(path=REMOTE + "/file")])
    resources = {"file": resource}
    if kind == "escape":
        resource.path = "../outside"
    elif kind == "parent_symlink":
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        resource.path = "link/file"
    elif kind == "collision":
        resources["other"] = Resource(
            path="other", targets=[Location(path=REMOTE + "/file")]
        )
    elif kind == "unsupported":
        resource.targets = [Location(path="s3://bucket/file")]
    elif kind == "query":
        resource.targets = [Location(path=REMOTE + "/file?download=1")]
    else:
        with (tmp_path / "file").open("wb") as stream:
            stream.truncate(250000001)
    path = descriptor(tmp_path, resources=resources)
    monkeypatch.setattr(
        "fileroute.clients.sharepoint.SharepointClient.build_default",
        lambda: pytest.fail("authenticated"),
    )
    with pytest.raises(ValueError):
        plan_push(path, root=tmp_path)


def test_push_dry_run_and_reference_targets(tmp_path, monkeypatch):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/guide #1.docx").write_text("doc")
    child = tmp_path / "child.yaml"
    save(
        Catalog(
            path="out",
            targets=[Location(path=REMOTE)],
            resources={"entry0": Resource(path="out/guide #1.docx")},
        ),
        child,
    )
    path = tmp_path / "root.yaml"
    path.write_text("catalogs:\n  entry0:\n    descriptor: child.yaml\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "fileroute.clients.sharepoint.SharepointClient.build_default",
        lambda: pytest.fail("authenticated"),
    )
    result = CliRunner().invoke(app, ["push", str(path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "guide%20%231.docx" in result.output


def test_pull_sources_not_targets_and_dispatch(tmp_path, monkeypatch):
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="download/file.csv",
                sources=[Location(path="s3://bucket/source.csv")],
                targets=[Location(path=REMOTE + "/file")],
            )
        },
    )
    calls = []
    item = SimpleNamespace(
        is_directory=False, download=lambda target: calls.append(target)
    )
    client = SimpleNamespace(get_from_weburl=lambda url: calls.append(url) or item)
    monkeypatch.setattr("fileroute.clients.s3.S3Client.build_default", lambda: client)
    entries = plan_pull(path, root=tmp_path)
    pull(entries)
    assert calls == ["s3://bucket/source.csv", tmp_path / "download/file.csv"]


def test_pull_refuses_multiple_sources(tmp_path):
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="output",
                sources=[
                    Location(path="s3://bucket/one"),
                    Location(path="s3://bucket/two"),
                ],
            )
        },
    )
    with pytest.raises(ValueError, match="exactly one source"):
        plan_pull(path, root=tmp_path)


def test_pull_dry_run_no_auth(tmp_path, monkeypatch):
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(path="file", sources=[Location(path="s3://bucket/file")])
        },
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "fileroute.clients.s3.S3Client.build_default",
        lambda: pytest.fail("authenticated"),
    )
    result = CliRunner().invoke(app, ["pull", str(path), "--dry-run"])
    assert result.exit_code == 0, result.output


def test_directory_pull_validates_all_remote_paths_before_writes(tmp_path, monkeypatch):
    path = descriptor(
        tmp_path,
        catalogs={
            "entry0": Catalog(
                path="downloads", sources=[Location(path="s3://bucket/root/")]
            )
        },
    )
    calls = []
    children = [
        SimpleNamespace(
            path="root/ok.csv", download=lambda target: calls.append(target)
        ),
        SimpleNamespace(
            path="root/../escape.csv", download=lambda target: calls.append(target)
        ),
    ]
    item = SimpleNamespace(
        path="root", is_directory=True, iter_files=lambda: iter(children)
    )
    monkeypatch.setattr(
        "fileroute.clients.s3.S3Client.build_default",
        lambda: SimpleNamespace(get_from_weburl=lambda url: item),
    )
    with pytest.raises(ValueError, match="outside"):
        pull(plan_pull(path, root=tmp_path))
    assert calls == []


def test_directional_planning_ignores_opposite_location_errors(tmp_path):
    (tmp_path / "file").write_text("data")
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="file",
                sources=[Location(path="s3://bucket/file")],
                targets=[Location(path="not-a-remote-target")],
            )
        },
    )
    assert plan_pull(path, root=tmp_path)[0].remote == "s3://bucket/file"
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="file",
                sources=[Location(path="s3://bucket/file", service_type="SharePoint")],
                targets=[Location(path=REMOTE + "/file")],
            )
        },
    )
    assert plan_push(path, root=tmp_path)[0].destination == REMOTE + "/file"


def test_planning_before_and_after_resolve_write_is_identical(tmp_path):
    from fileroute.descriptor import load, resolve, save

    (tmp_path / "out").mkdir()
    (tmp_path / "out/guide.docx").write_text("doc")
    path = descriptor(tmp_path, path="out", targets=[Location(path=REMOTE)])
    before = plan_push(path, root=tmp_path)
    save(resolve(load(path)), path)
    assert plan_push(path, root=tmp_path) == before


def test_pull_prefers_saved_id_to_url(tmp_path, monkeypatch):
    from fileroute.clients.s3 import S3Client

    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="download.csv",
                sources=[
                    Location(path="s3://bucket/file.csv", serviceId="bucket:file.csv")
                ],
            )
        },
    )
    calls = []
    item = SimpleNamespace(
        is_directory=False, download=lambda target: calls.append(target)
    )
    client = S3Client(client=object())
    monkeypatch.setattr(
        client, "get_from_id", lambda item_id: calls.append(item_id) or item
    )
    monkeypatch.setattr(
        client, "get_from_weburl", lambda url: pytest.fail("URL lookup")
    )
    monkeypatch.setattr(S3Client, "build_default", lambda: client)
    pull(plan_pull(path, root=tmp_path))
    assert calls == ["bucket:file.csv", tmp_path / "download.csv"]


def test_push_uses_saved_sharepoint_folder_id(tmp_path, monkeypatch):
    from fileroute.clients.sharepoint import SharepointClient

    (tmp_path / "file.csv").write_text("data")
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="file.csv",
                targets=[
                    Location(
                        path=REMOTE,
                        site="dev",
                        drive="Docs",
                        siteId="site-id",
                        driveId="drive-id",
                        serviceId="folder-id",
                        entityType="Directory",
                    )
                ],
            )
        },
    )
    client = SharepointClient(access_token="fake")
    calls = []
    monkeypatch.setattr(SharepointClient, "build_default", lambda: client)
    monkeypatch.setattr(client, "get_site_id", lambda *args: pytest.fail("site lookup"))
    monkeypatch.setattr(
        client, "get_drive_id", lambda *args, **kw: pytest.fail("drive lookup")
    )
    monkeypatch.setattr(
        client,
        "get_item_metadata",
        lambda drive, **kw: (
            calls.append((drive, kw)) or {"id": "folder-id", "folder": {}}
        ),
    )
    monkeypatch.setattr(
        client,
        "_put_file",
        lambda url, path: (
            calls.append(url) or {"id": "new", "name": "file.csv", "file": {}}
        ),
    )
    push(plan_push(path, root=tmp_path))
    assert calls[0] == ("drive-id", {"item_id": "folder-id"})


def _descriptor(root: Path) -> Path:
    descriptor = root / "config" / "fileroute.yaml"
    descriptor.parent.mkdir()
    descriptor.write_text(
        "profile: fileroute-catalog\ncatalogs:\n  documentation:\n    path: docs/_output\n    targets:\n    - path: https://contoso.sharepoint.com/sites/dev/Shared%20Documents/Docs\n      serviceType: SharePoint\n"
    )
    return descriptor


def test_plan_nested_and_dry_run_does_not_authenticate(tmp_path, monkeypatch):
    descriptor = _descriptor(tmp_path)
    directory = tmp_path / "docs" / "_output"
    (directory / "surveys").mkdir(parents=True)
    (directory / "index.docx").write_bytes(b"doc")
    (directory / "surveys" / "catalog.xlsx").write_bytes(b"xls")
    files = plan_push(descriptor, root=tmp_path)
    assert [f.relative.as_posix() for f in files] == [
        "index.docx",
        "surveys/catalog.xlsx",
    ]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "fileroute.clients.sharepoint.SharepointClient.build_default",
        lambda: pytest.fail("authenticated on dry run"),
    )
    result = CliRunner().invoke(app, ["push", str(descriptor), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "surveys/catalog.xlsx" in result.output


def test_missing_or_symlink_fails_before_transfer(tmp_path):
    descriptor = _descriptor(tmp_path)
    with pytest.raises(ValueError, match="missing"):
        plan_push(descriptor, root=tmp_path)
    directory = tmp_path / "docs" / "_output"
    directory.mkdir(parents=True)
    (directory / "ok.docx").write_bytes(b"ok")
    (directory / "escape").symlink_to(tmp_path / "docs", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        plan_push(descriptor, root=tmp_path)


def test_placeholder_cannot_trigger_authentication(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "fileroute.clients.sharepoint.SharepointClient.build_default",
        lambda: pytest.fail("authenticated"),
    )
    with pytest.raises(ValueError, match="placeholder"):
        push((
            PushEntry(
                tmp_path / "a",
                "https://your-tenant.sharepoint.com/sites/YOUR-SITE/Docs",
                Path("a"),
                "SharePoint",
            ),
        ))


def test_file_resource_uses_root_and_remote_filename(tmp_path, monkeypatch):
    (tmp_path / "local.docx").write_bytes(b"document")
    descriptor = tmp_path / "fileroute.yaml"
    descriptor.write_text(
        "profile: fileroute-catalog\nresources:\n  guide:\n    path: local.docx\n    targets:\n    - path: \n        https://example.sharepoint.com/sites/dev/Shared%20Documents/Docs/published.docx\n"
    )
    files = plan_push(descriptor, root=tmp_path)
    calls = []
    client = SimpleNamespace(upload_to_folder=lambda *args: calls.append(args))
    monkeypatch.setattr(
        "fileroute.clients.sharepoint.SharepointClient.build_default", lambda: client
    )
    push(files)
    assert calls == [
        (
            "https://example.sharepoint.com/sites/dev/Shared%20Documents/Docs",
            Path("published.docx"),
            tmp_path / "local.docx",
        )
    ]


def test_google_drive_folder_and_exact_file_targets(tmp_path, monkeypatch):
    (tmp_path / "report.pdf").write_bytes(b"pdf")
    folder = "https://drive.google.com/drive/folders/folder-id?usp=sharing"
    file = "https://drive.google.com/file/d/file-id/view?usp=sharing"
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="report.pdf", targets=[Location(path=folder), Location(path=file)]
            )
        },
    )
    calls = []
    client = SimpleNamespace(
        upload_to_location=lambda *args: calls.append(("folder", args)),
        upload_to_file=lambda *args: calls.append(("file", args)),
    )
    monkeypatch.setattr(
        "fileroute.clients.googledrive.GoogleDriveClient.build_default", lambda: client
    )
    planned = plan_push(path, root=tmp_path)
    assert [entry.direct_file for entry in planned] == [False, True]
    assert planned[1].location.service_id == "file-id"
    assert planned[0].destination == f"{folder} :: report.pdf"
    push(planned)
    assert calls[0][0] == "folder"
    assert calls[0][1][0].service_id == "folder-id"
    assert calls[0][1][1:] == (Path("report.pdf"), tmp_path / "report.pdf")
    assert calls[1][0] == "file"
    assert calls[1][1][0].service_id == "file-id"
    assert calls[1][1][1] == tmp_path / "report.pdf"


def test_google_drive_scoped_directory_and_mixed_targets_dry_run(tmp_path, monkeypatch):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "report.csv").write_bytes(b"data")
    path = descriptor(
        tmp_path,
        path="out",
        targets=[
            Location(
                path="reports",
                serviceType="GoogleDrive",
                drive="My Drive",
                entityType="Directory",
            ),
            Location(path=REMOTE),
        ],
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "fileroute.clients.googledrive.GoogleDriveClient.build_default",
        lambda: pytest.fail("Google authenticated during planning"),
    )
    monkeypatch.setattr(
        "fileroute.clients.sharepoint.SharepointClient.build_default",
        lambda: pytest.fail("SharePoint authenticated during planning"),
    )
    planned = plan_push(path, root=tmp_path)
    assert len(planned) == 2
    assert planned[0].destination == "reports :: report.csv"
    result = CliRunner().invoke(app, ["push", str(path), "--dry-run"])
    assert result.exit_code == 0, result.output


def test_google_drive_exact_file_collision_and_unresolved_path(tmp_path):
    (tmp_path / "one.csv").write_bytes(b"one")
    (tmp_path / "two.csv").write_bytes(b"two")
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="one.csv",
                targets=[Location(path="https://drive.google.com/file/d/same/view")],
            ),
            "entry1": Resource(
                path="two.csv",
                targets=[Location(path="https://drive.google.com/open?id=same")],
            ),
        },
    )
    with pytest.raises(ValueError, match="Multiple local files"):
        plan_push(path, root=tmp_path)
    path = descriptor(
        tmp_path,
        resources={
            "entry0": Resource(
                path="one.csv",
                targets=[
                    Location(
                        path="reports/one.csv",
                        serviceType="GoogleDrive",
                        drive="My Drive",
                    )
                ],
            )
        },
    )
    with pytest.raises(ValueError, match="needs an ID"):
        plan_push(path, root=tmp_path)
