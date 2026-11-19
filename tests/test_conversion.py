"""Real DOCX ↔ review-Markdown behavior, including edits and ambiguous anchors."""

from base64 import b64decode
from hashlib import sha256
from io import StringIO
import shutil
import subprocess
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from pydantic import ValidationError
import pytest
from ruamel.yaml import YAML

from fileroute.conversion import (
    DocxToMarkdownRequest,
    MarkdownToDocxRequest,
    ReviewProfile,
    docx_to_markdown,
    markdown_to_docx,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
ET.register_namespace("w", W[1:-1])
ET.register_namespace(
    "r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
ET.register_namespace(
    "wp", "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
)
ET.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
ET.register_namespace("pic", "http://schemas.openxmlformats.org/drawingml/2006/picture")


def package(path):
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def write_package(path, files):
    with ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def profile(path):
    _, header, body = path.read_text().split("---", 2)
    return YAML(typ="safe").load(header), body


def edit_profile(path, metadata, body):
    stream = StringIO()
    YAML().dump(metadata, stream)
    path.write_text("---\n" + stream.getvalue() + "---\n" + body)


@pytest.fixture
def original(tmp_path):
    if shutil.which("pandoc") is None:
        pytest.skip("Real DOCX integration requires Pandoc 3.1+")
    path = tmp_path / "source.docx"
    markdown = '# Title\n\n::: {custom-style="Policy Block"}\nA [special]{custom-style="Policy Term"} sentence.\n:::\n\n- One\n- Two\n\n| A | B |\n|---|---|\n| 1 | 2 |\n'
    subprocess.run(
        ["pandoc", "-f", "markdown", "-t", "docx", "-o", str(path)],
        input=markdown,
        text=True,
        check=True,
    )
    files = package(path)
    body = ET.fromstring(files["word/document.xml"])
    paragraph = next(
        p
        for p in body.iter(W + "p")
        if any(node.text == "special" for node in p.iter(W + "t"))
    )
    run = next(
        run
        for run in paragraph.findall(W + "r")
        if any(node.text == "special" for node in run.iter(W + "t"))
    )
    index = list(paragraph).index(run)
    paragraph.insert(index, ET.Element(W + "commentRangeStart", {W + "id": "7"}))
    paragraph.insert(index + 2, ET.Element(W + "commentRangeEnd", {W + "id": "7"}))
    reference = ET.Element(W + "r")
    ET.SubElement(reference, W + "commentReference", {W + "id": "7"})
    paragraph.insert(index + 3, reference)
    files["word/document.xml"] = ET.tostring(body)
    comments = ET.Element(W + "comments")
    comment = ET.SubElement(
        comments,
        W + "comment",
        {
            W + "id": "7",
            W + "author": "Reviewer Δ",
            W + "initials": "RV",
            W + "date": "2026-11-16T12:00:00Z",
        },
    )
    p = ET.SubElement(comment, W + "p")
    ET.SubElement(ET.SubElement(p, W + "r"), W + "t").text = "Clarify scope."
    files["word/comments.xml"] = ET.tostring(comments)
    rels = ET.fromstring(files["word/_rels/document.xml.rels"])
    ET.SubElement(
        rels,
        R + "Relationship",
        {
            "Id": "review-comments",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
            "Target": "comments.xml",
        },
    )
    files["word/_rels/document.xml.rels"] = ET.tostring(rels)
    content = ET.fromstring(files["[Content_Types].xml"])
    ET.SubElement(
        content,
        CT + "Override",
        {
            "PartName": "/word/comments.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
        },
    )
    files["[Content_Types].xml"] = ET.tostring(content)
    styles = ET.fromstring(files["word/styles.xml"])
    term_style = next(
        style
        for style in styles.findall(W + "style")
        if style.get(W + "styleId") == "PolicyTerm"
    )
    props = term_style.find(W + "rPr")
    if props is None:
        props = ET.SubElement(term_style, W + "rPr")
    ET.SubElement(props, W + "color", {W + "val": "445566"})
    files["word/styles.xml"] = ET.tostring(styles)
    write_package(path, files)
    return path


def import_review(original):
    output = original.parent / "review.md"
    result = docx_to_markdown(DocxToMarkdownRequest(source=original, output=output))
    return output, result


def commented_text(path):
    document = ET.fromstring(package(path)["word/document.xml"])
    active, text = set(), {}
    for element in document.iter():
        key = element.get(W + "id")
        if element.tag == W + "commentRangeStart":
            active.add(key)
            text[key] = ""
        elif element.tag == W + "commentRangeEnd":
            active.remove(key)
        elif element.tag == W + "t":
            for key in active:
                text[key] += element.text or ""
    return text


def test_semantic_roundtrip_preserves_comments_styles_and_source(original):
    before = original.read_bytes()
    markdown, imported = import_review(original)
    metadata, body = profile(markdown)
    parsed = ReviewProfile.model_validate(metadata)
    assert imported.unsupported == []
    assert parsed.comments["c7"].author == "Reviewer Δ"
    assert parsed.comments["c7"].initials == "RV"
    assert parsed.styles["PolicyTerm"] == "Policy Term"
    assert 'custom-style="Policy Term"' in body
    retained = markdown.parent / parsed.source.path
    assert retained.read_bytes() == before
    output = original.parent / "again.docx"
    result = markdown_to_docx(MarkdownToDocxRequest(source=markdown, output=output))
    assert original.read_bytes() == before
    assert result.output_sha256 == sha256(output.read_bytes()).hexdigest()
    assert imported.output_sha256 == sha256(markdown.read_bytes()).hexdigest()
    assert list(commented_text(output).values()) == ["special"]
    files = package(output)
    comment = ET.fromstring(files["word/comments.xml"]).find(W + "comment")
    assert comment.get(W + "author") == "Reviewer Δ"
    assert comment.get(W + "initials") == "RV"
    assert comment.get(W + "date") == "2026-11-16T12:00:00Z"
    assert "".join(comment.itertext()) == "Clarify scope."
    styles = ET.fromstring(files["word/styles.xml"])
    term = next(
        style
        for style in styles.findall(W + "style")
        if style.get(W + "styleId") == "PolicyTerm"
    )
    assert term.find(W + "rPr/" + W + "color").get(W + "val") == "445566"
    body_xml = ET.fromstring(files["word/document.xml"])
    assert any(
        node.get(W + "val") == "PolicyBlock" for node in body_xml.iter(W + "pStyle")
    )
    assert len(list(body_xml.iter(W + "tbl"))) == 1
    assert len(list(body_xml.iter(W + "numPr"))) == 2
    second = original.parent / "second.md"
    docx_to_markdown(DocxToMarkdownRequest(source=output, output=second))
    second_metadata, second_body = profile(second)
    assert (
        next(iter(second_metadata["comments"].values())) == metadata["comments"]["c7"]
    )
    assert 'custom-style="Policy Term"' in second_body


def test_comments_follow_explicit_edited_anchors_not_matching_text(original):
    markdown, _ = import_review(original)
    metadata, body = profile(markdown)
    body = (
        body.replace("[special]", "[revised wording]")
        + "\n\nspecial special revised wording\n"
    )
    metadata["comments"]["c7"]["text"] = "Revised reviewer comment."
    edit_profile(markdown, metadata, body)
    output = original.parent / "edited.docx"
    markdown_to_docx(MarkdownToDocxRequest(source=markdown, output=output))
    assert list(commented_text(output).values()) == ["revised wording"]
    assert "Revised reviewer comment." in package(output)["word/comments.xml"].decode()


def test_overlapping_point_and_cross_paragraph_comment_ranges(original):
    markdown, _ = import_review(original)
    metadata, _ = profile(markdown)
    for key in ("c8", "c9", "c10"):
        metadata["comments"][key] = {"text": f"Comment {key}.", "author": "Reviewer"}

    def marker(key, end):
        return f"[]{{#docx-{key}-{end} .docx-comment-{end}}}"

    body = (
        marker("c7", "start")
        + "alpha "
        + marker("c8", "start")
        + "beta"
        + marker("c7", "end")
        + " gamma"
        + marker("c8", "end")
        + marker("c9", "start")
        + marker("c9", "end")
        + "\n\n"
        + marker("c10", "start")
        + "first\n\nsecond"
        + marker("c10", "end")
    )
    edit_profile(markdown, metadata, body)
    output = original.parent / "ranges.docx"
    markdown_to_docx(MarkdownToDocxRequest(source=markdown, output=output))
    assert list(commented_text(output).values()) == [
        "alpha beta",
        "beta gamma",
        "",
        "firstsecond",
    ]


@pytest.mark.parametrize("damage", ["missing", "duplicate", "reversed", "unknown"])
def test_ambiguous_or_lost_anchors_preserve_existing_output(original, damage):
    markdown, _ = import_review(original)
    metadata, body = profile(markdown)
    start = "[]{#docx-c7-start .docx-comment-start}"
    end = "[]{#docx-c7-end .docx-comment-end}"
    if damage == "missing":
        body = body.replace(start, "")
    elif damage == "duplicate":
        body += "\n" + start
    elif damage == "reversed":
        body = body.replace(start, "TOKEN").replace(end, start).replace("TOKEN", end)
    else:
        body = body.replace("docx-c7-start", "docx-c99-start")
    edit_profile(markdown, metadata, body)
    output = original.parent / "edited.docx"
    output.write_bytes(b"reviewed")
    with pytest.raises(ValueError, match="anchor|Comment end"):
        markdown_to_docx(MarkdownToDocxRequest(source=markdown, output=output))
    assert output.read_bytes() == b"reviewed"


def test_explicit_comment_removal_needs_metadata_and_both_markers(original):
    markdown, _ = import_review(original)
    metadata, body = profile(markdown)
    metadata["comments"] = {}
    body = body.replace("[]{#docx-c7-start .docx-comment-start}", "").replace(
        "[]{#docx-c7-end .docx-comment-end}", ""
    )
    edit_profile(markdown, metadata, body)
    output = original.parent / "removed.docx"
    markdown_to_docx(MarkdownToDocxRequest(source=markdown, output=output))
    assert commented_text(output) == {}


def test_integrity_and_retained_input_aliases_are_enforced(original):
    markdown, _ = import_review(original)
    metadata, _ = profile(markdown)
    retained = markdown.parent / metadata["source"]["path"]
    with pytest.raises(ValueError, match="overwrite a retained input"):
        markdown_to_docx(MarkdownToDocxRequest(source=markdown, output=retained))
    retained.write_bytes(b"modified")
    with pytest.raises(ValueError, match="missing or changed"):
        markdown_to_docx(
            MarkdownToDocxRequest(source=markdown, output=original.parent / "out.docx")
        )


def test_unknown_style_fails_instead_of_creating_a_default_style(original):
    markdown, _ = import_review(original)
    metadata, body = profile(markdown)
    edit_profile(
        markdown,
        metadata,
        body.replace('custom-style="Policy Term"', 'custom-style="Unknown Style"'),
    )
    with pytest.raises(ValueError, match="not defined"):
        markdown_to_docx(
            MarkdownToDocxRequest(source=markdown, output=original.parent / "out.docx")
        )


@pytest.mark.parametrize("feature", ["tracked", "field", "formatting", "threaded"])
def test_unsupported_features_are_reported_and_cannot_be_hidden_in_yaml(
    original, feature
):
    files = package(original)
    body = ET.fromstring(files["word/document.xml"])
    if feature == "tracked":
        paragraph = list(body.iter(W + "p"))[0]
        insertion = ET.SubElement(
            paragraph,
            W + "ins",
            {W + "id": "8", W + "author": "Writer", W + "date": "2026-11-16T12:00:00Z"},
        )
        ET.SubElement(ET.SubElement(insertion, W + "r"), W + "t").text = "New words"
    elif feature == "field":
        ET.SubElement(
            list(body.iter(W + "p"))[0], W + "fldSimple", {W + "instr": "DATE"}
        )
    elif feature == "formatting":
        run = next(body.iter(W + "r"))
        properties = ET.SubElement(run, W + "rPr")
        ET.SubElement(properties, W + "color", {W + "val": "123456"})
    else:
        files["word/commentsExtended.xml"] = b"<extended/>"
    files["word/document.xml"] = ET.tostring(body)
    write_package(original, files)
    markdown, result = import_review(original)
    metadata, body_text = profile(markdown)
    assert result.unsupported and metadata["unsupported"]
    metadata["unsupported"] = []
    edit_profile(markdown, metadata, body_text)
    output = original.parent / "out.docx"
    output.write_bytes(b"previous")
    with pytest.raises(ValueError, match="outside review-v1"):
        markdown_to_docx(MarkdownToDocxRequest(source=markdown, output=output))
    assert output.read_bytes() == b"previous"


def test_images_are_retained_and_pinned_in_both_directions(tmp_path):
    if shutil.which("pandoc") is None:
        pytest.skip("Pandoc not installed")
    image = tmp_path / "pixel.png"
    image.write_bytes(
        b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        )
    )
    original = tmp_path / "picture.docx"
    subprocess.run(
        ["pandoc", "-f", "markdown", "-t", "docx", "-o", str(original)],
        input=f"Image ![pixel]({image})\n",
        text=True,
        check=True,
    )
    markdown, _ = import_review(original)
    metadata, _ = profile(markdown)
    assert len(metadata["assets"]) == 1
    output = tmp_path / "picture-again.docx"
    markdown_to_docx(MarkdownToDocxRequest(source=markdown, output=output))
    assert image.read_bytes() in package(output).values()
    asset = markdown.parent / next(iter(metadata["assets"]))
    asset.write_bytes(b"changed")
    with pytest.raises(ValueError, match="missing or changed"):
        markdown_to_docx(MarkdownToDocxRequest(source=markdown, output=output))


def test_import_reuses_verified_immutable_bundle_and_failure_preserves_markdown(
    original, monkeypatch
):
    import fileroute.conversion as conversion

    markdown, first = import_review(original)
    _, second = import_review(original)
    assert first.assets == second.assets
    before = markdown.read_bytes()

    def fail(*args, **kwargs):
        raise ValueError("converter failure")

    monkeypatch.setattr(conversion, "_run", fail)
    with pytest.raises(ValueError, match="converter failure"):
        import_review(original)
    assert markdown.read_bytes() == before


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_contracts_validate_without_io(tmp_path, timeout):
    for request in (DocxToMarkdownRequest, MarkdownToDocxRequest):
        with pytest.raises(ValidationError):
            request(
                source=tmp_path / "input",
                output=tmp_path / "output",
                timeout_seconds=timeout,
            )
    assert list(tmp_path.iterdir()) == []


def test_pandoc_missing_error_is_actionable_and_no_outputs_are_created(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.docx"
    source.write_bytes(b"source")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="Install Pandoc"):
        docx_to_markdown(
            DocxToMarkdownRequest(source=source, output=tmp_path / "review.md")
        )
    assert list(tmp_path.iterdir()) == [source]


def test_profile_rejects_unknown_fields_versions_and_escaping_paths(original):
    markdown, _ = import_review(original)
    metadata, body = profile(markdown)
    metadata["source"]["path"] = "../source.docx"
    edit_profile(markdown, metadata, body)
    with pytest.raises(ValidationError, match="relative asset"):
        markdown_to_docx(
            MarkdownToDocxRequest(source=markdown, output=original.parent / "out.docx")
        )


@pytest.mark.parametrize(
    "change", ["version", "profile", "extra", "missing_profile", "missing_version"]
)
def test_profile_version_and_fields_are_explicit(original, change):
    markdown, _ = import_review(original)
    metadata, body = profile(markdown)
    if change == "version":
        metadata["schema_version"] = 99
    elif change == "profile":
        metadata["docx_profile"] = "unknown"
    elif change.startswith("missing_"):
        del metadata[
            "docx_profile" if change == "missing_profile" else "schema_version"
        ]
    else:
        metadata["execute"] = "not a supported field"
    edit_profile(markdown, metadata, body)
    with pytest.raises(ValidationError):
        markdown_to_docx(
            MarkdownToDocxRequest(source=markdown, output=original.parent / "out.docx")
        )
    assert not (original.parent / "out.docx").exists()


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_import_cannot_overwrite_its_source_through_an_alias(original, alias):
    output = original.parent / "alias.md"
    try:
        output.symlink_to(original) if alias == "symlink" else output.hardlink_to(
            original
        )
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"Filesystem lacks {alias}: {error}")
    before = original.read_bytes()
    with pytest.raises(ValueError, match="immutable source"):
        docx_to_markdown(DocxToMarkdownRequest(source=original, output=output))
    assert original.read_bytes() == before


def test_metadata_cannot_bypass_declared_image_assets(original, monkeypatch):
    import fileroute.conversion as conversion

    markdown, _ = import_review(original)
    metadata, body = profile(markdown)
    metadata["properties"] = {
        "title": {
            "t": "MetaInlines",
            "c": [
                {
                    "t": "Image",
                    "c": [["", [], []], [], ["https://example.invalid/image.png", ""]],
                }
            ],
        }
    }
    edit_profile(markdown, metadata, body)
    real_run = conversion._run

    def guarded(pandoc, arguments, *args, **kwargs):
        assert "--to=docx" not in arguments, "Writer must not fetch undeclared assets"
        return real_run(pandoc, arguments, *args, **kwargs)

    monkeypatch.setattr(conversion, "_run", guarded)
    with pytest.raises(ValueError, match="declared retained asset"):
        markdown_to_docx(
            MarkdownToDocxRequest(source=markdown, output=original.parent / "out.docx")
        )


def test_malformed_native_metadata_has_an_actionable_error(original):
    markdown, _ = import_review(original)
    metadata, body = profile(markdown)
    metadata["properties"] = {"title": {"t": "Image", "c": []}}
    edit_profile(markdown, metadata, body)
    with pytest.raises(ValueError, match="Pandoc failed"):
        markdown_to_docx(
            MarkdownToDocxRequest(source=markdown, output=original.parent / "out.docx")
        )


def test_timeout_is_actionable_and_preserves_existing_output(tmp_path, monkeypatch):
    import fileroute.conversion as conversion

    source, output = tmp_path / "source.docx", tmp_path / "review.md"
    source.write_bytes(b"original")
    output.write_text("reviewed")
    monkeypatch.setattr(conversion.shutil, "which", lambda name: "/pandoc")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(conversion.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="exceeded"):
        docx_to_markdown(DocxToMarkdownRequest(source=source, output=output))
    assert output.read_text() == "reviewed"
    assert set(tmp_path.iterdir()) == {source, output}
