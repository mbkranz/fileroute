"""End-to-end planning and dispatch without authentication or network I/O."""

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from sharedrive.cli import app
from sharedrive.models import Catalog, Resource, Location
from sharedrive.upload import plan_upload
from sharedrive.download import plan_download, download

REMOTE = "https://tenant.sharepoint.com/sites/dev/Docs"


def descriptor(tmp_path, **kwargs):
    path = tmp_path / "config/catalog.yaml"
    Catalog(**kwargs).to_path(path)
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
        resources=[
            Resource(
                name="one", path="out/one.docx", sources=[Location(path="one.qmd")]
            ),
            Resource(
                name="renamed",
                path="out/three.docx",
                targets=[Location(path=REMOTE + "/renamed.docx")],
            ),
            Resource(name="private", path="out/private.docx", targets=[]),
        ],
        catalogs=[
            Catalog(
                name="nested", resources=[Resource(name="two", path="out/sub/two.docx")]
            )
        ],
    )
    plan = plan_upload(path, root=tmp_path)
    assert [entry.destination for entry in plan] == [
        REMOTE + "/one.docx",
        REMOTE + "/renamed.docx",
        REMOTE + "/sub/two.docx",
    ]


def test_multiple_targets_and_folder_target(tmp_path):
    (tmp_path / "guide.docx").write_text("artifact")
    path = descriptor(
        tmp_path,
        resources=[
            Resource(
                path="guide.docx",
                targets=[
                    Location(path=REMOTE, entityType="Directory"),
                    Location(path=REMOTE + "/copy.docx"),
                ],
            )
        ],
    )
    assert [entry.destination for entry in plan_upload(path, root=tmp_path)] == [
        REMOTE + "/guide.docx",
        REMOTE + "/copy.docx",
    ]


def test_push_never_uses_provenance_as_destination(tmp_path):
    path = descriptor(
        tmp_path,
        resources=[Resource(path="file", sources=[Location(path=REMOTE + "/source")])],
    )
    with pytest.raises(ValueError, match="targets"):
        plan_upload(path, root=tmp_path)


@pytest.mark.parametrize(
    "kind",
    ["escape", "parent_symlink", "collision", "unsupported", "query", "oversized"],
)
def test_push_preflight_rejects_bad_plan(tmp_path, kind, monkeypatch):
    (tmp_path / "file").write_text("one")
    (tmp_path / "other").write_text("two")
    resource = Resource(path="file", targets=[Location(path=REMOTE + "/file")])
    resources = [resource]
    if kind == "escape":
        resource.path = "../outside"
    elif kind == "parent_symlink":
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        resource.path = "link/file"
    elif kind == "collision":
        resources.append(
            Resource(path="other", targets=[Location(path=REMOTE + "/file")])
        )
    elif kind == "unsupported":
        resource.targets = [Location(path="s3://bucket/file")]
    elif kind == "query":
        resource.targets = [Location(path=REMOTE + "/file?download=1")]
    else:
        with (tmp_path / "file").open("wb") as stream:
            stream.truncate(250_000_001)
    path = descriptor(tmp_path, resources=resources)
    monkeypatch.setattr(
        "sharedrive.upload.get_client", lambda _: pytest.fail("authenticated")
    )
    with pytest.raises(ValueError):
        plan_upload(path, root=tmp_path)


def test_push_dry_run_and_reference_targets(tmp_path, monkeypatch):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/guide #1.docx").write_text("doc")
    child = tmp_path / "child.yaml"
    Catalog(
        path="out",
        targets=[Location(path=REMOTE)],
        resources=[Resource(path="out/guide #1.docx")],
    ).to_path(child)
    path = tmp_path / "root.yaml"
    path.write_text("catalogs:\n - $ref: child.yaml\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sharedrive.upload.get_client", lambda _: pytest.fail("authenticated")
    )
    result = CliRunner().invoke(app, ["push", str(path), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "guide%20%231.docx" in result.output


def test_pull_sources_not_targets_and_dispatch(tmp_path, monkeypatch):
    path = descriptor(
        tmp_path,
        resources=[
            Resource(
                path="download/file.csv",
                sources=[Location(path="s3://bucket/source.csv")],
                targets=[Location(path=REMOTE + "/file")],
            )
        ],
    )
    calls = []
    item = SimpleNamespace(
        is_directory=False, download=lambda target: calls.append(target)
    )
    client = SimpleNamespace(get_from_weburl=lambda url: calls.append(url) or item)
    monkeypatch.setattr("sharedrive.download.get_client", lambda _: client)
    entries = plan_download(path, root=tmp_path)
    download(entries)
    assert calls == ["s3://bucket/source.csv", tmp_path / "download/file.csv"]


def test_pull_refuses_multiple_sources(tmp_path):
    path = descriptor(
        tmp_path,
        resources=[
            Resource(
                path="output",
                sources=[
                    Location(path="s3://bucket/one"),
                    Location(path="s3://bucket/two"),
                ],
            )
        ],
    )
    with pytest.raises(ValueError, match="exactly one source"):
        plan_download(path, root=tmp_path)


def test_pull_dry_run_no_auth(tmp_path, monkeypatch):
    path = descriptor(
        tmp_path,
        resources=[Resource(path="file", sources=[Location(path="s3://bucket/file")])],
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sharedrive.download.get_client", lambda _: pytest.fail("authenticated")
    )
    result = CliRunner().invoke(app, ["pull", str(path), "--dry-run"])
    assert result.exit_code == 0, result.output


def test_directory_pull_validates_all_remote_paths_before_writes(tmp_path, monkeypatch):
    path = descriptor(
        tmp_path,
        catalogs=[
            Catalog(path="downloads", sources=[Location(path="s3://bucket/root/")])
        ],
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
        "sharedrive.download.get_client",
        lambda _: SimpleNamespace(get_from_weburl=lambda url: item),
    )
    with pytest.raises(ValueError, match="outside"):
        download(plan_download(path, root=tmp_path))
    assert calls == []
