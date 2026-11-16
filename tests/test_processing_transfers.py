"""Endpoint processing preserves originals and prepares publication before writes."""

from hashlib import sha256
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from fileroute.descriptor import save
from fileroute.models import Catalog, Location, Resource, ServiceType
from fileroute.tasks import SequenceSpec, StepsResult, resolve_output
from fileroute.transfer import PullEntry, plan_pull, plan_push, pull, push


def hook(name: str | None = None, *, command: str = "validate") -> SequenceSpec:
    return SequenceSpec.model_validate({
        "steps": [{"cmd": command}],
        "params": {
            "output": {
                "default": (
                    "${FILEROUTE_WORK_DIR}/" + name if name else "${FILEROUTE_OUTPUT}"
                )
            }
        },
    })


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


def result(request, data=b"converted"):
    output = resolve_output(request)
    output.write_bytes(data)
    return StepsResult(output=output, exit_code=0, stdout="", stderr="")


def catalog(tmp_path, document):
    path = tmp_path / "config/catalog.yaml"
    path.parent.mkdir(exist_ok=True)
    save(document, path)
    return path


def receipts(directory):
    return [json.loads(path.read_text()) for path in directory.glob("**/receipt.json")]


def test_after_pull_keeps_raw_source_and_passes_only_working_input(
    tmp_path, monkeypatch
):
    target = tmp_path / "term.md"
    target.write_bytes(b"reviewed original")
    original = b"downloaded DOCX"
    client(monkeypatch, item=source(original))
    planned = PullEntry(
        target,
        "https://drive.google.com/file/d/source-id/view",
        ServiceType.GOOGLE_DRIVE,
        root=tmp_path,
        catalog_origin=tmp_path / "config/catalog.yaml",
        resource="terms.term",
        hook=hook(),
    )
    requests = []

    def convert(request):
        requests.append(request)
        assert request.input.read_bytes() == original
        assert request.input != target
        request.input.write_bytes(b"safe to mutate working copy")
        return result(request, b"# Converted term\n")

    (receipt,) = pull((planned,), run_dir=tmp_path / "runs", runner=convert)
    assert target.read_bytes() == b"# Converted term\n"
    assert receipt.raw_source.read_bytes() == original
    assert receipt.raw_source != requests[0].input
    assert receipt.source_sha256 == sha256(original).hexdigest()
    assert receipt.output_sha256 == sha256(target.read_bytes()).hexdigest()
    assert (
        receipt.source_id == "source-id" and receipt.source_version == "remote-version"
    )
    assert requests[0].catalog_dir == tmp_path / "config"
    assert requests[0].context["source"]["sha256"] == receipt.source_sha256
    assert receipts(tmp_path / "runs")[0]["outcome"] == "complete"


@pytest.mark.parametrize("failure", ["exception", "nonzero", "escape"])
def test_failed_after_pull_preserves_existing_target_and_raw_input(
    tmp_path, monkeypatch, failure
):
    target = tmp_path / "term.md"
    target.write_bytes(b"reviewed")
    client(monkeypatch, item=source())
    entry = PullEntry(
        target, "s3://bucket/doc.docx", ServiceType.S3, root=tmp_path, hook=hook()
    )

    def fail(request):
        if failure == "exception":
            raise ValueError("invalid form")
        if failure == "nonzero":
            return StepsResult(
                output=resolve_output(request), exit_code=7, stdout="", stderr="bad"
            )
        return StepsResult(output=target, exit_code=0, stdout="", stderr="")

    with pytest.raises(ValueError):
        pull((entry,), run_dir=tmp_path / "runs", runner=fail)
    assert target.read_bytes() == b"reviewed"
    (report,) = receipts(tmp_path / "runs")
    assert report["outcome"] == "failed" and report["stage"] == "afterPull"
    assert Path(report["raw_source"]).read_bytes() == b"original DOCX bytes"
    assert report["source_sha256"] and report["error"]


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


def test_plan_pull_keeps_linked_catalog_origin_and_rejects_directory_hook(tmp_path):
    child = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.md",
                    sources=[Location(path="s3://bucket/term.docx", afterPull=hook())],
                )
            }
        ),
    )
    root = tmp_path / "catalog.yaml"
    save(Catalog(catalogs={"linked": {"descriptor": "config/catalog.yaml"}}), root)
    (entry,) = plan_pull(root, root=tmp_path)
    assert entry.catalog_origin == child
    assert entry.resource == "linked.term" and entry.hook is not None
    bad = catalog(
        tmp_path,
        Catalog(
            path="input", sources=[Location(path="s3://bucket/docs/", afterPull=hook())]
        ),
    )
    with pytest.raises(ValueError, match="file resource"):
        plan_pull(bad, root=tmp_path)


def test_before_push_prepares_independent_target_copies_and_preserves_input(
    tmp_path, monkeypatch
):
    original = tmp_path / "out/sub/term.md"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"# Reviewed\n")
    descriptor = catalog(
        tmp_path,
        Catalog(
            path="out",
            targets=[
                Location(
                    path="s3://bucket/docx",
                    entityType="Directory",
                    beforePush=hook("term.docx"),
                ),
                Location(
                    path="s3://bucket/pdf",
                    entityType="Directory",
                    beforePush=hook("term.pdf"),
                ),
            ],
            resources={"term": Resource(path="out/sub/term.md")},
        ),
    )
    uploads = []
    client(monkeypatch, uploads=uploads)
    prepared = []

    def convert(request):
        assert uploads == []
        assert request.input.read_bytes() == b"# Reviewed\n"
        prepared.append(request.input)
        request.input.write_bytes(b"modified working copy")
        return result(request, resolve_output(request).suffix.encode())

    plan = plan_push(descriptor, root=tmp_path)
    assert [entry.relative for entry in plan] == [
        Path("sub/term.docx"),
        Path("sub/term.pdf"),
    ]
    reports = push(plan, runner=convert, run_dir=tmp_path / "runs")
    assert len(prepared) == 2 and prepared[0] != prepared[1]
    assert [item[1] for item in uploads] == [
        Path("sub/term.docx"),
        Path("sub/term.pdf"),
    ]
    assert [item[3] for item in uploads] == [b".docx", b".pdf"]
    assert original.read_bytes() == b"# Reviewed\n"
    assert all(report.outcome == "complete" for report in reports)


def test_before_push_failure_prevents_every_upload(tmp_path, monkeypatch):
    original = tmp_path / "term.md"
    original.write_text("reviewed")
    descriptor = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.md",
                    targets=[
                        Location(
                            path="s3://bucket/a",
                            entityType="Directory",
                            beforePush=hook("a.docx"),
                        ),
                        Location(
                            path="s3://bucket/b",
                            entityType="Directory",
                            beforePush=hook("b.docx"),
                        ),
                    ],
                )
            }
        ),
    )
    uploads = []
    client(monkeypatch, uploads=uploads)

    def convert(request):
        if resolve_output(request).name == "b.docx":
            raise ValueError("second output invalid")
        return result(request)

    with pytest.raises(ValueError, match="second output invalid"):
        push(
            plan_push(descriptor, root=tmp_path),
            runner=convert,
            run_dir=tmp_path / "runs",
        )
    assert uploads == [] and original.read_text() == "reviewed"
    reports = receipts(tmp_path / "runs")
    assert {report["stage"] for report in reports} == {"prepared", "prepare"}


def test_exact_drive_target_preserves_identity_while_uploading_transformed_file(
    tmp_path, monkeypatch
):
    (tmp_path / "term.md").write_text("reviewed")
    descriptor = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.md",
                    targets=[
                        Location(
                            path="https://drive.google.com/file/d/final-id/view",
                            beforePush=hook("term.docx"),
                        )
                    ],
                )
            }
        ),
    )
    uploads = []
    client(monkeypatch, uploads=uploads)
    push(plan_push(descriptor, root=tmp_path), runner=result)
    assert uploads[0][0].service_id == "final-id"
    assert uploads[0][1] is None and uploads[0][2].name == "term.docx"


def test_different_hooks_for_same_remote_identity_are_rejected_offline(tmp_path):
    (tmp_path / "term.md").write_text("reviewed")
    descriptor = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.md",
                    targets=[
                        Location(
                            path="https://tenant.sharepoint.com/sites/data/Docs/same.docx",
                            beforePush=hook("one.docx"),
                        ),
                        Location(
                            path="https://tenant.sharepoint.com/sites/data/Docs/same.docx",
                            beforePush=hook("two.docx"),
                        ),
                    ],
                )
            }
        ),
    )
    with pytest.raises(ValueError, match="processing sequences"):
        plan_push(descriptor, root=tmp_path)


def test_processing_directory_publication_requires_concrete_resources(tmp_path):
    (tmp_path / "out").mkdir()
    descriptor = catalog(
        tmp_path,
        Catalog(
            path="out", targets=[Location(path="s3://bucket/out", beforePush=hook())]
        ),
    )
    with pytest.raises(ValueError, match="concrete file resources"):
        plan_push(descriptor, root=tmp_path)


def test_transformed_sharepoint_size_is_checked_before_publication(
    tmp_path, monkeypatch
):
    (tmp_path / "term.md").write_text("small input")
    descriptor = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.md",
                    targets=[
                        Location(
                            path="https://tenant.sharepoint.com/sites/data/Docs/term.docx",
                            beforePush=hook("term.docx"),
                        )
                    ],
                )
            }
        ),
    )
    uploads = []
    client(monkeypatch, uploads=uploads)

    def oversized(request):
        output = resolve_output(request)
        with output.open("wb") as stream:
            stream.truncate(250_000_001)
        return StepsResult(output=output, exit_code=0, stdout="", stderr="")

    with pytest.raises(ValueError, match="250 MB"):
        push(plan_push(descriptor, root=tmp_path), runner=oversized)
    assert uploads == []


def test_lost_upload_response_records_unknown_and_preserves_completed_publication(
    tmp_path, monkeypatch
):
    (tmp_path / "term.md").write_text("reviewed")
    descriptor = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.md",
                    targets=[
                        Location(
                            path="s3://bucket/one.docx", beforePush=hook("one.docx")
                        ),
                        Location(
                            path="s3://bucket/two.docx", beforePush=hook("two.docx")
                        ),
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
        push(
            plan_push(descriptor, root=tmp_path),
            runner=result,
            run_dir=tmp_path / "runs",
        )
    reports = sorted(receipts(tmp_path / "runs"), key=lambda row: row["destination"])
    assert [report["outcome"] for report in reports] == ["complete", "unknown"]
    assert reports[1]["stage"] == "upload" and reports[1]["error"] == "response lost"


def test_injected_runner_cannot_change_planned_push_name(tmp_path, monkeypatch):
    (tmp_path / "term.md").write_text("reviewed")
    descriptor = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.md",
                    targets=[
                        Location(
                            path="s3://bucket/out",
                            entityType="Directory",
                            beforePush=hook("term.docx"),
                        )
                    ],
                )
            }
        ),
    )
    uploads = []
    client(monkeypatch, uploads=uploads)

    def rename(request):
        produced = result(request)
        other = produced.output.with_name("other.docx")
        produced.output.rename(other)
        return produced.model_copy(update={"output": other})

    with pytest.raises(ValueError, match="declared output"):
        push(plan_push(descriptor, root=tmp_path), runner=rename)
    assert uploads == []


def test_real_poe_after_pull_uses_declared_output_and_preserves_raw_download(
    tmp_path, monkeypatch
):
    native = hook().model_dump()
    native["steps"] = [
        {
            "cmd": (
                f'"{sys.executable}" -c '
                "'from pathlib import Path; import sys; Path(sys.argv[1]).write_text(\"native conversion\")' "
                '"${output}"'
            )
        }
    ]
    client(monkeypatch, item=source())
    from fileroute.tasks import SequenceSpec

    entry = PullEntry(
        tmp_path / "term.md",
        "s3://bucket/term.docx",
        ServiceType.S3,
        root=tmp_path,
        hook=SequenceSpec.model_validate(native),
    )
    (receipt,) = pull((entry,), run_dir=tmp_path / "runs")
    assert receipt.output.read_text() == "native conversion"
    assert receipt.raw_source.read_bytes() == b"original DOCX bytes"


def test_processing_plans_are_offline_and_create_no_working_directories(
    tmp_path, monkeypatch
):
    (tmp_path / "term.md").write_text("reviewed")
    descriptor = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.md",
                    sources=[Location(path="s3://bucket/input.docx", afterPull=hook())],
                    targets=[
                        Location(
                            path="https://tenant.sharepoint.com/sites/data/Docs",
                            entityType="Directory",
                            beforePush=hook("term.docx"),
                        )
                    ],
                )
            }
        ),
    )
    monkeypatch.setattr(
        "fileroute.tasks.subprocess.run",
        lambda *args, **kwargs: pytest.fail("processing executed during planning"),
    )
    monkeypatch.setattr(
        "fileroute.clients.sharepoint.SharepointClient.build_default",
        lambda: pytest.fail("authenticated during planning"),
    )
    assert plan_pull(descriptor, root=tmp_path)[0].hook is not None
    assert plan_push(descriptor, root=tmp_path)[0].relative.name == "term.docx"
    assert not (tmp_path / ".fileroute").exists()


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


def test_successful_after_pull_must_actually_produce_output(tmp_path, monkeypatch):
    target = tmp_path / "term.md"
    target.write_text("previously reviewed term")
    client(monkeypatch, item=source())
    entry = PullEntry(
        target, "s3://bucket/term.docx", ServiceType.S3, root=tmp_path, hook=hook()
    )
    requested_outputs = []

    def no_output(request):
        output = resolve_output(request)
        requested_outputs.append(output)
        assert not output.exists()
        return StepsResult(output=output, exit_code=0, stdout="", stderr="")

    with pytest.raises(ValueError, match="not a regular file"):
        pull((entry,), run_dir=tmp_path / "runs", runner=no_output)

    assert target.read_text() == "previously reviewed term"
    assert not requested_outputs[0].exists()
    (report,) = receipts(tmp_path / "runs")
    assert report["outcome"] == "failed" and report["stage"] == "afterPull"
    assert Path(report["raw_source"]).read_bytes() == b"original DOCX bytes"
    assert "not a regular file" in report["error"]


def test_successful_before_push_without_output_cannot_upload(tmp_path, monkeypatch):
    original = tmp_path / "term.md"
    original.write_text("reviewed canonical term")
    descriptor = catalog(
        tmp_path,
        Catalog(
            resources={
                "term": Resource(
                    path="term.md",
                    targets=[
                        Location(
                            path="s3://bucket/published",
                            entityType="Directory",
                            beforePush=hook("term.docx"),
                        )
                    ],
                )
            }
        ),
    )
    uploads = []
    client(monkeypatch, uploads=uploads)
    requested_outputs = []

    def no_output(request):
        output = resolve_output(request)
        requested_outputs.append(output)
        assert not output.exists()
        return StepsResult(output=output, exit_code=0, stdout="", stderr="")

    with pytest.raises(ValueError, match="not a regular file"):
        push(
            plan_push(descriptor, root=tmp_path),
            run_dir=tmp_path / "runs",
            runner=no_output,
        )

    assert uploads == []
    assert original.read_text() == "reviewed canonical term"
    assert not requested_outputs[0].exists()
    (report,) = receipts(tmp_path / "runs")
    assert report["outcome"] == "failed" and report["stage"] == "prepare"
    assert "not a regular file" in report["error"]
