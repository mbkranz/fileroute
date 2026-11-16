"""Exercise real local extraction and strict rendering without provider clients."""

from hashlib import sha256
from pathlib import Path
import subprocess
from zipfile import ZipFile

import pytest

from fileroute.conversion import (
    ConversionRequest,
    convert_document,
    render_document,
    render_template,
)


def _docx(path: Path) -> Path:
    with ZipFile(path, "w") as document:
        document.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        document.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>",
        )
        document.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>Source document evidence</w:t></w:r></w:p></w:body></w:document>",
        )
    return path


def _pdf(path: Path) -> Path:
    stream = b"BT /F1 12 Tf 50 700 Td (PDF source evidence) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(content)
    content.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode())
    content.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    "factory,phrase",
    [(_docx, "Source document evidence"), (_pdf, "PDF source evidence")],
)
def test_real_local_document_extraction_preserves_original(tmp_path, factory, phrase):
    pytest.importorskip("markitdown")
    source = factory(tmp_path / ("source.docx" if factory is _docx else "source.pdf"))
    original = source.read_bytes()
    output = tmp_path / "result.md"
    result = convert_document(ConversionRequest(source=source, output=output))
    assert phrase in output.read_text()
    assert source.read_bytes() == original
    assert result.source_sha256 == sha256(original).hexdigest()
    assert result.output_sha256 == sha256(output.read_bytes()).hexdigest()


def test_template_maps_json_pointers_and_keeps_literal_values(tmp_path):
    template = tmp_path / "document.md.jinja"
    template.write_text("# {{ mapped.title }}\n{{ mapped.value }}\n")
    result = render_template(
        template,
        {
            "conversion": {
                "title": "Unicode Δ",
                "rows": [{"a/b~": "literal $HOME | value"}],
            }
        },
        mappings={"title": "/conversion/title", "value": "/conversion/rows/0/a~1b~0"},
    )
    assert result == "# Unicode Δ\nliteral $HOME | value\n"


def test_template_failure_does_not_replace_previous_output(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("original")
    output = tmp_path / "output.md"
    output.write_text("previous")
    template = tmp_path / "broken.jinja"
    template.write_text("{{ missing_required_field }}")
    with pytest.raises(ValueError, match="Cannot render"):
        convert_document(
            ConversionRequest(source=source, output=output, template=template),
            converter=lambda path: {"markdown": path.read_text(), "title": None},
        )
    assert output.read_text() == "previous"
    assert source.read_text() == "original"


@pytest.mark.parametrize("index", ["-1", "01", "+1", " 1", "-"])
def test_mapping_rejects_noncanonical_array_indices(tmp_path, index):
    template = tmp_path / "template.jinja"
    template.write_text("{{ mapped.value }}")
    with pytest.raises(ValueError, match="Invalid array index"):
        render_template(
            template, {"rows": ["first", "last"]}, mappings={"value": f"/rows/{index}"}
        )


def test_json_render_and_mapping_file(tmp_path):
    source = tmp_path / "data.json"
    source.write_text('{"title":"A title"}')
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("title: /data/title\n")
    template = tmp_path / "template.jinja"
    template.write_text("# {{ mapped.title }}\n")
    output = tmp_path / "out.md"
    render_document(
        ConversionRequest(
            source=source, output=output, template=template, mapping_file=mapping
        )
    )
    assert output.read_text() == "# A title\n"


def test_missing_libreoffice_is_actionable_and_keeps_doc(tmp_path):
    source = tmp_path / "source.doc"
    source.write_bytes(b"original legacy bytes")
    output = tmp_path / "out.md"
    with pytest.raises(ValueError, match="requires an installed LibreOffice"):
        convert_document(
            ConversionRequest(source=source, output=output),
            find_executable=lambda name: None,
        )
    assert source.read_bytes() == b"original legacy bytes"
    assert not output.exists()


def test_doc_normalization_checks_output_even_after_zero_exit(tmp_path):
    source = tmp_path / "source.doc"
    source.write_bytes(b"legacy")
    with pytest.raises(ValueError, match="did not produce DOCX"):
        convert_document(
            ConversionRequest(source=source, output=tmp_path / "out.md"),
            office_runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                args[0], 0, "", ""
            ),
            find_executable=lambda name: "/fake/soffice",
        )


def test_conversion_refuses_to_overwrite_original(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("original")
    with pytest.raises(ValueError, match="must differ"):
        convert_document(
            ConversionRequest(source=source, output=source),
            converter=lambda path: {"markdown": "changed", "title": None},
        )
    assert source.read_text() == "original"


def test_default_native_sequence_runs_real_document_converter(tmp_path):
    pytest.importorskip("markitdown")
    from fileroute.tasks import StepsRequest, default_markdown_sequence, execute_steps

    source = _docx(tmp_path / "source.docx")
    work = tmp_path / "work"
    output = work / "document.md"
    result = execute_steps(
        StepsRequest(
            sequence=default_markdown_sequence(),
            input=source,
            output=output,
            root=tmp_path,
            catalog_dir=tmp_path,
            work_dir=work,
        )
    )
    assert result.exit_code == 0
    assert "Source document evidence" in output.read_text()
    assert not list(tmp_path.glob(".fileroute-steps-*.json"))


def test_resource_add_to_markdown_records_native_sequence(tmp_path):
    from fileroute.commands.descriptor import _add_resource_to_descriptor

    resource = _add_resource_to_descriptor(
        tmp_path / "catalog.yaml",
        name="document",
        create_if_missing=True,
        path="acquired/document.docx",
        source="https://drive.google.com/file/d/existing-id/view",
        to="markdown",
    )
    assert resource["path"] == "acquired/document.md"
    hook = resource["sources"][0]["afterPull"]
    assert hook["params"]["output"]["default"] == "${FILEROUTE_OUTPUT}"
    assert hook["steps"][0]["cmd"].startswith("fileroute steps convert")
