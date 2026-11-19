"""Inferred DOCX/Markdown transfers retain acquisition/publication evidence."""

import json
from types import SimpleNamespace

import pytest

from fileroute.descriptor import save
from fileroute.models import Catalog, Location, Resource, ServiceType
from fileroute.transfer import PullEntry, plan_push, pull, push


def client(monkeypatch, *, item=None, uploads=None):
    uploads = uploads if uploads is not None else []
    value = SimpleNamespace(
        get_from_weburl=lambda url: item,
        upload_to_location=lambda location, relative, local: uploads.append((
            location,
            relative,
            local,
            local.read_bytes(),
        )),
        upload_to_file=lambda location, local: uploads.append((
            location,
            None,
            local,
            local.read_bytes(),
        )),
        close=lambda: None,
    )
    monkeypatch.setattr(
        "fileroute.transfer.get_provider",
        lambda service: SimpleNamespace(
            capabilities=SimpleNamespace(supports_download=True, supports_upload=True),
            build_default=lambda: value,
        ),
    )
    return value


def source(data: bytes = b"original DOCX bytes", *, mime_type=None, name="source.docx"):
    return SimpleNamespace(
        id="source-id",
        version="remote-version",
        name=name,
        source_url="https://drive.google.com/file/d/source-id/view",
        mime_type=mime_type,
        is_directory=False,
        download=lambda target: target.write_bytes(data),
    )


@pytest.mark.parametrize("name", ["Term.doc", "Term.md", "Term", "Term.docx"])
def test_google_native_document_uses_actual_export_extension(tmp_path, name):
    from fileroute.transfer import _source_name

    entry = PullEntry(tmp_path / "term.md", "remote", ServiceType.GOOGLE_DRIVE)
    actual = _source_name(
        source(mime_type="application/vnd.google-apps.document", name=name), entry
    )
    assert actual.endswith(".docx")
    assert not actual.endswith(".docx.docx")


def test_google_slides_uses_provider_default_pdf_export(tmp_path):
    from fileroute.transfer import _source_name

    entry = PullEntry(tmp_path / "slides.md", "remote", ServiceType.GOOGLE_DRIVE)
    assert _source_name(
        source(
            mime_type="application/vnd.google-apps.presentation", name="Slides.pptx"
        ),
        entry,
    ).endswith(".pdf")


def catalog(tmp_path, document):
    path = tmp_path / "config/catalog.yaml"
    path.parent.mkdir(exist_ok=True)
    save(document, path)
    return path


def receipts(directory):
    return [json.loads(path.read_text()) for path in directory.glob("**/receipt.json")]


def test_explicit_run_directory_retains_plain_download_and_workspace_export_extension(
    tmp_path, monkeypatch
):
    client(
        monkeypatch,
        item=source(mime_type="application/vnd.google-apps.document", name="Term"),
    )
    entry = PullEntry(
        tmp_path / "document.docx",
        "s3://bucket/document",
        ServiceType.S3,
        root=tmp_path,
    )
    (receipt,) = pull((entry,), run_dir=tmp_path / "runs")
    assert receipt.raw_source.name == "Term.docx"
    assert receipt.raw_source.read_bytes() == receipt.output.read_bytes()


def test_lost_upload_response_records_unknown_and_preserves_completed_publication(
    tmp_path, monkeypatch
):
    (tmp_path / "term.csv").write_text("reviewed")
    descriptor = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.csv",
                    targets=[
                        Location(path="s3://bucket/one.csv"),
                        Location(path="s3://bucket/two.csv"),
                    ],
                )
            }
        ),
    )
    calls = []
    value = client(monkeypatch)

    def upload(location, relative, local):
        calls.append(local)
        if len(calls) == 2:
            raise TimeoutError("response lost")

    value.upload_to_location = upload
    with pytest.raises(TimeoutError, match="response lost"):
        push(plan_push(descriptor, root=tmp_path), run_dir=tmp_path / "runs")
    reports = sorted(receipts(tmp_path / "runs"), key=lambda row: row["destination"])
    assert [report["outcome"] for report in reports] == ["complete", "unknown"]
    assert reports[1]["stage"] == "upload" and reports[1]["error"] == "response lost"


def test_directory_child_never_borrows_parent_source_identity(tmp_path, monkeypatch):
    child = SimpleNamespace(
        name="child.docx",
        path="root/child.docx",
        download=lambda target: target.write_bytes(b"child bytes"),
    )
    item = SimpleNamespace(
        is_directory=True, path="root", iter_files=lambda: iter([child])
    )
    client(monkeypatch, item=item)
    entry = PullEntry(
        tmp_path / "input",
        "s3://bucket/root",
        ServiceType.S3,
        directory=True,
        root=tmp_path,
        location=Location(path="s3://bucket/root", serviceId="parent-id"),
    )
    (receipt,) = pull((entry,), run_dir=tmp_path / "runs")
    assert receipt.source_id is None
    assert receipt.source_version is None
    assert receipt.output.read_bytes() == b"child bytes"
