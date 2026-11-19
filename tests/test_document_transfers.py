"""Real document conversion through ordinary transfer plans and CLI commands."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import xml.etree.ElementTree as ET
from zipfile import ZipFile

import pytest
from typer.testing import CliRunner

from fileroute.cli import app
from fileroute.descriptor import save
from fileroute.models import Catalog, Location, Resource
from fileroute.transfer import plan_pull, plan_push, pull, push

REMOTE = "https://tenant.sharepoint.com/sites/review/Docs/report.docx"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture
def document(tmp_path):
    if shutil.which("pandoc") is None:
        pytest.skip("Document integration requires Pandoc 3.1+")
    path = tmp_path / "fixture.docx"
    subprocess.run(
        ["pandoc", "-f", "markdown", "-t", "docx", "-o", str(path)],
        input='# Title\n\n[Clarify scope.]{.comment-start id="7" author="Reviewer" initials="RV" date="2026-11-16T12:00:00Z"}[Original]{custom-style="Policy Term"}[]{.comment-end id="7"}\n',
        text=True,
        check=True,
    )
    return path.read_bytes()


def descriptor(tmp_path, *, source=REMOTE, targets=None, format=None):
    path = tmp_path / "catalog.yaml"
    save(
        Catalog(
            resources={
                "report": Resource(
                    path="review/report.md",
                    sources=[Location(path=source, format=format)],
                    targets=targets if targets is not None else [Location(path=REMOTE)],
                )
            }
        ),
        path,
    )
    return path


def provider(monkeypatch, data, *, name="report.docx", mime_type=None):
    downloads, uploads = [], []

    def download(path):
        downloads.append(path)
        path.write_bytes(data)

    item = SimpleNamespace(
        name=name,
        mime_type=mime_type,
        id="source-id",
        version="revision-7",
        is_directory=False,
        download=download,
    )
    value = SimpleNamespace(
        get_from_location=lambda location: item,
        get_from_weburl=lambda url: item,
        upload_to_location=lambda location, relative, local: uploads.append((
            location,
            relative,
            local.read_bytes(),
        )),
        upload_to_file=lambda location, local: uploads.append((
            location,
            None,
            local.read_bytes(),
        )),
        close=lambda: None,
    )
    monkeypatch.setattr(
        "fileroute.transfer.get_provider",
        lambda service: SimpleNamespace(
            capabilities=SimpleNamespace(supports_download=True, supports_upload=True),
            recognizes_url=lambda url: True,
            build_default=lambda: value,
        ),
    )
    return value, downloads, uploads


@pytest.mark.parametrize("retain_run", [False, True])
def test_pull_edit_push_converts_and_keeps_source_evidence(
    tmp_path, monkeypatch, document, retain_run
):
    _, downloads, uploads = provider(monkeypatch, document)
    catalog = descriptor(tmp_path)
    before_descriptor = catalog.read_bytes()
    entries = plan_pull(catalog, root=tmp_path)
    assert entries[0].conversion == "docx-to-md"
    options = {"run_dir": tmp_path / "runs"} if retain_run else {}
    (acquired,) = pull(entries, **options)
    assert acquired.source_id == "source-id" and acquired.source_version == "revision-7"
    assert acquired.source_sha256 == sha256(document).hexdigest()
    assert acquired.raw_source.read_bytes() == document
    assert acquired.output_sha256 == sha256(acquired.output.read_bytes()).hexdigest()
    assert downloads[0] != acquired.output
    assert "docx_profile: fileroute-review-v1" in acquired.output.read_text()
    acquired.output.write_text(
        acquired.output.read_text().replace("[Original]", "[Edited]")
    )
    before_review = acquired.output.read_bytes()
    planned = plan_push(catalog, root=tmp_path)
    assert planned[0].conversion == "md-to-docx"
    (published,) = push(planned, **options)
    assert published.input == acquired.output
    assert published.input_sha256 == sha256(before_review).hexdigest()
    assert published.output_sha256 == sha256(uploads[0][2]).hexdigest()
    assert published.conversion == "md-to-docx" and published.outcome == "complete"
    assert acquired.output.read_bytes() == before_review
    assert acquired.raw_source.read_bytes() == document
    assert catalog.read_bytes() == before_descriptor
    assert uploads[0][1] == Path("report.docx")
    if retain_run:
        assert published.output.read_bytes() == uploads[0][2]
        assert len(list((tmp_path / "runs").glob("**/receipt.json"))) == 2
    with ZipFile(BytesIO(uploads[0][2])) as archive:
        comment = ET.fromstring(archive.read("word/comments.xml")).find(W + "comment")
        assert comment.get(W + "author") == "Reviewer"
        assert comment.get(W + "initials") == "RV"
        assert "".join(comment.itertext()) == "Clarify scope."
        assert "Edited" in "".join(
            ET.fromstring(archive.read("word/document.xml")).itertext()
        )


def test_google_native_source_and_exact_binary_target_use_actual_metadata(
    tmp_path, monkeypatch, document
):
    value, _, uploads = provider(
        monkeypatch,
        document,
        name="report",
        mime_type="application/vnd.google-apps.document",
    )
    source = "https://docs.google.com/document/d/source-id/edit"
    target = "https://drive.google.com/file/d/target-id/view"
    catalog = descriptor(tmp_path, source=source, targets=[Location(path=target)])
    entries = plan_pull(catalog, root=tmp_path)
    assert entries[0].conversion is None  # The URL alone has no file extension.
    (acquired,) = pull(entries)
    assert acquired.conversion == "docx-to-md"
    value.get_from_location = lambda location: SimpleNamespace(name="published.docx")
    (published,) = push(plan_push(catalog, root=tmp_path))
    assert published.conversion == "md-to-docx"
    assert uploads[0][0].service_id == "target-id"
    assert uploads[0][1] is None


def test_format_hint_on_opaque_source_and_folder_target_is_visible_offline(
    tmp_path, monkeypatch, document
):
    _, _, uploads = provider(monkeypatch, document, name="opaque")
    catalog = descriptor(
        tmp_path,
        source="https://drive.google.com/file/d/source-id/view",
        format="docx",
        targets=[
            Location(
                path="https://drive.google.com/drive/folders/published", format="docx"
            )
        ],
    )
    assert plan_pull(catalog, root=tmp_path)[0].conversion == "docx-to-md"
    pull(plan_pull(catalog, root=tmp_path))
    planned = plan_push(catalog, root=tmp_path)
    assert planned[0].relative == Path("report.docx")
    assert planned[0].destination.endswith(" :: report.docx")
    push(planned)
    assert uploads[0][1] == Path("report.docx")


def test_transfer_dry_runs_show_conversion_without_pandoc_auth_or_writes(
    tmp_path, monkeypatch
):
    catalog = descriptor(tmp_path)
    local = tmp_path / "review/report.md"
    local.parent.mkdir()
    local.write_text("Existing review")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(
        "fileroute.transfer._transfer_clients",
        lambda *a, **kw: pytest.fail("Dry runs must not authenticate"),
    )
    for action, conversion in [("pull", "docx-to-md"), ("push", "md-to-docx")]:
        result = CliRunner().invoke(app, [action, str(catalog), "--dry-run"])
        assert result.exit_code == 0, result.output
        assert conversion in result.stdout
    assert local.read_text() == "Existing review"
    assert sorted(
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")
    ) == ["catalog.yaml", "review", "review/report.md"]


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="Pandoc not installed")
def test_pull_failure_preserves_previous_markdown_and_reports_downloaded_bytes(
    tmp_path, monkeypatch
):
    provider(monkeypatch, b"invalid DOCX")
    catalog = descriptor(tmp_path)
    local = tmp_path / "review/report.md"
    local.parent.mkdir()
    local.write_text("Previous review")
    with pytest.raises(ValueError, match="DOCX package"):
        pull(plan_pull(catalog, root=tmp_path), run_dir=tmp_path / "runs")
    assert local.read_text() == "Previous review"
    report = json.loads(next((tmp_path / "runs").glob("**/receipt.json")).read_text())
    assert report["stage"] == "convert" and report["outcome"] == "failed"
    assert report["source_sha256"] == sha256(b"invalid DOCX").hexdigest()


def test_all_conversions_prepare_before_first_upload(tmp_path, monkeypatch, document):
    _, _, uploads = provider(monkeypatch, document)
    catalog = descriptor(tmp_path)
    pull(plan_pull(catalog, root=tmp_path))
    document = Catalog(
        resources={
            "valid": Resource(path="review/report.md", targets=[Location(path=REMOTE)]),
            "invalid": Resource(
                path="plain.md",
                targets=[Location(path=REMOTE.replace("report.docx", "second.docx"))],
            ),
        }
    )
    (tmp_path / "plain.md").write_text("No retained profile")
    save(document, catalog)
    with pytest.raises(ValueError, match="YAML header"):
        push(plan_push(catalog, root=tmp_path))
    assert uploads == []


def test_matching_formats_keep_bytes_without_requiring_a_review_profile(
    tmp_path, monkeypatch
):
    _, _, uploads = provider(monkeypatch, b"plain Markdown", name="report.md")
    catalog = descriptor(
        tmp_path,
        source=REMOTE.replace(".docx", ".md"),
        targets=[Location(path=REMOTE.replace(".docx", ".md"))],
    )
    (acquired,) = pull(plan_pull(catalog, root=tmp_path))
    assert (
        acquired.conversion is None
        and acquired.output.read_bytes() == b"plain Markdown"
    )
    push(plan_push(catalog, root=tmp_path))
    assert uploads[0][2] == b"plain Markdown"


def test_remote_markdown_conversion_is_not_published_without_its_bundle(tmp_path):
    path = tmp_path / "catalog.yaml"
    local = tmp_path / "report.docx"
    local.write_bytes(b"original")
    save(
        Catalog(
            resources={
                "report": Resource(
                    path="report.docx",
                    sources=[Location(path=REMOTE.replace(".docx", ".md"))],
                    targets=[Location(path=REMOTE.replace(".docx", ".md"))],
                )
            }
        ),
        path,
    )
    for planner in (plan_pull, plan_push):
        with pytest.raises(ValueError, match="retained files"):
            planner(path, root=tmp_path)
    assert local.read_bytes() == b"original"
