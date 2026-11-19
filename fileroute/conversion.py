"""Opinionated DOCX ↔ Markdown review documents, using Pandoc's native model.

The versioned YAML profile records comments and pinned retained files. Explicit
body markers preserve comment ranges through edits; style assignments stay on
Pandoc Markdown spans/divs, with definitions in the original reference DOCX.
This preserves a declared semantic subset, not arbitrary Word layout or bytes.
See https://pandoc.org/MANUAL.html#custom-styles and #option--track-changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from hashlib import sha256
from io import BytesIO, StringIO
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Literal
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

_CONFIG = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MARKDOWN = "markdown+bracketed_spans+fenced_divs-raw_html-raw_tex-yaml_metadata_block"
_MAX_PACKAGE = 256 * 1024 * 1024


class DocxToMarkdownRequest(BaseModel):
    """Import a local DOCX into a review-profile Markdown file and retained assets."""

    model_config = _CONFIG
    source: Path
    output: Path
    timeout_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)


class MarkdownToDocxRequest(BaseModel):
    """Export a review-profile Markdown document using its pinned style reference."""

    model_config = _CONFIG
    source: Path
    output: Path
    timeout_seconds: float = Field(default=120, gt=0, allow_inf_nan=False)


def _relative_path(value: str) -> str:
    if (
        not value
        or "\\" in value
        or "\0" in value
        or urlsplit(value).scheme
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or ".." in PurePosixPath(value).parts
    ):
        raise ValueError(f"Expected a confined relative asset path: {value!r}")
    return value


class RetainedFile(BaseModel):
    """An unmodified local input, identified by a relative path and SHA-256."""

    model_config = _CONFIG
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        return _relative_path(value)


class ReviewComment(BaseModel):
    """Comment Markdown and original reviewer attributes, keyed by stable body ID."""

    model_config = _CONFIG
    text: str
    author: str = ""
    date: str | None = None
    initials: str | None = None


class ReviewProfile(BaseModel):
    """The review-v1 YAML header; assets are relative to the Markdown's directory.

    styles maps observed Word style IDs to names for inspection; body attributes
    select styles and reference_docx carries their actual definitions. Unsupported
    source features remain in the retained original and block DOCX export.
    """

    model_config = _CONFIG
    docx_profile: Literal["fileroute-review-v1"]
    schema_version: Literal[1]
    source: RetainedFile
    reference_docx: RetainedFile
    converter: str
    styles: dict[str, str] = Field(default_factory=dict)
    comments: dict[str, ReviewComment] = Field(default_factory=dict)
    assets: dict[str, str] = Field(default_factory=dict)
    unsupported: list[str] = Field(default_factory=list)
    properties: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("comments")
    @classmethod
    def comment_ids(cls, value: dict[str, ReviewComment]) -> dict[str, ReviewComment]:
        if any(re.fullmatch(r"c[0-9]+", key) is None for key in value):
            raise ValueError("Comment IDs must use c followed by decimal digits")
        return value

    @field_validator("assets")
    @classmethod
    def asset_files(cls, value: dict[str, str]) -> dict[str, str]:
        for path, digest in value.items():
            _relative_path(path)
            if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError(f"Invalid asset SHA-256: {path}")
        return value


class ConversionResult(BaseModel):
    """Actual local output and integrity evidence, independent of publication."""

    model_config = _CONFIG
    source: Path
    output: Path
    source_sha256: str
    output_sha256: str
    assets: Path | None = None
    unsupported: list[str] = Field(default_factory=list)


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _paths(
    source: Path, output: Path, source_suffixes: set[str], output_suffixes: set[str]
) -> None:
    if (
        source.suffix.lower() not in source_suffixes
        or output.suffix.lower() not in output_suffixes
    ):
        raise ValueError("Use .docx input and .md/.markdown output, or the reverse")
    if not source.is_file():
        raise ValueError(f"Input is not a regular file: {source}")
    if source.resolve() == output.resolve() or (
        output.exists() and output.samefile(source)
    ):
        raise ValueError("Output must differ from the immutable source")
    if output.exists() and not output.is_file():
        raise ValueError(f"Output is not a file: {output}")
    if any(parent.exists() and not parent.is_dir() for parent in output.parents):
        raise ValueError(f"Output parent is not a directory: {output}")


def _run(
    pandoc: str,
    arguments: list[str],
    timeout: float,
    *,
    text: str | None = None,
    cwd: Path | None = None,
) -> str:
    try:
        result = subprocess.run(
            [pandoc, *arguments],
            input=text,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Pandoc exceeded the {timeout:g} second timeout") from error
    if result.returncode:
        raise ValueError(
            f"Pandoc failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    if result.stderr.strip():
        # A reader/writer warning can signal a lossy conversion; do not hide it.
        raise ValueError(
            f"Pandoc reported a conversion warning: {result.stderr.strip()}"
        )
    return result.stdout


def _pandoc(timeout: float) -> tuple[str, str]:
    executable = shutil.which("pandoc")
    if executable is None:
        raise ValueError(
            "Install Pandoc 3.1 or newer and put pandoc on PATH for DOCX/Markdown conversion"
        )
    version = _run(executable, ["--version"], timeout).splitlines()[0]
    match = re.search(r"pandoc (\d+)\.(\d+)", version)
    if match is None or tuple(map(int, match.groups())) < (3, 1):
        raise ValueError(f"Pandoc 3.1 or newer is required; found {version}")
    return executable, version


def _xml(data: bytes) -> ET.Element:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Document XML must not contain DTD or entity declarations")
    try:
        return ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError(f"Invalid DOCX XML: {error}") from error


def _package(data: bytes) -> dict[str, bytes]:
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if sum(entry.file_size for entry in entries) > _MAX_PACKAGE:
                raise ValueError("DOCX uncompressed contents exceed 256 MiB")
            names = [entry.filename for entry in entries]
            if len(set(names)) != len(names):
                raise ValueError("DOCX contains duplicate package entries")
            for name in names:
                _relative_path(name)
            files = {
                entry.filename: archive.read(entry)
                for entry in entries
                if not entry.is_dir()
            }
    except BadZipFile as error:
        raise ValueError("Input is not a readable DOCX package") from error
    if not {"[Content_Types].xml", "word/document.xml"} <= files.keys():
        raise ValueError("DOCX lacks its content types or document body")
    return files


def _source_features(files: dict[str, bytes]) -> tuple[dict[str, str], list[str]]:
    body = _xml(files["word/document.xml"])
    all_styles = {}
    if "word/styles.xml" in files:
        for style in _xml(files["word/styles.xml"]).findall(_W + "style"):
            name = style.find(_W + "name")
            if name is not None:
                all_styles[style.get(_W + "styleId", "")] = name.get(_W + "val", "")
    used = {
        element.get(_W + "val", "")
        for tag in ("pStyle", "rStyle")
        for element in body.iter(_W + tag)
    }
    styles = {key: all_styles.get(key, key) for key in sorted(used)}
    unsupported = set()
    features = {
        "ins": "tracked changes",
        "del": "tracked changes",
        "moveFrom": "tracked changes",
        "moveTo": "tracked changes",
        "rPrChange": "tracked formatting changes",
        "pPrChange": "tracked formatting changes",
        "tblPrChange": "tracked formatting changes",
        "sdt": "content controls",
        "txbxContent": "text boxes",
        "altChunk": "embedded content",
        "object": "embedded objects",
        "fldSimple": "document fields",
        "instrText": "document fields",
        "gridSpan": "merged table cells",
        "vMerge": "merged table cells",
        "tab": "literal tabs",
    }
    for element in body.iter():
        if element.tag.startswith(_W) and element.tag[len(_W) :] in features:
            unsupported.add(features[element.tag[len(_W) :]])
        if element.tag.endswith("}anchor"):
            unsupported.add("floating drawings")
    for properties in body.iter(_W + "rPr"):
        if any(
            child.tag
            not in {
                _W + tag
                for tag in ("rStyle", "b", "bCs", "i", "iCs", "strike", "vertAlign")
            }
            for child in properties
        ):
            unsupported.add("direct character formatting outside the profile")
    cell_properties = {
        id(properties)
        for cell in body.iter(_W + "tc")
        for properties in cell.iter(_W + "pPr")
    }
    for properties in body.iter(_W + "pPr"):
        allowed = {_W + tag for tag in ("pStyle", "numPr")}
        if id(properties) in cell_properties:
            # Pandoc represents ordinary table alignment in column specifications.
            allowed.add(_W + "jc")
        if any(child.tag not in allowed for child in properties):
            unsupported.add("direct paragraph formatting outside the profile")
    if len(list(body.iter(_W + "sectPr"))) > 1:
        unsupported.add("multiple document sections")
    if "word/comments.xml" in files:
        for comment in _xml(files["word/comments.xml"]):
            if len(comment.findall(_W + "p")) != 1 or any(
                element.tag
                in {_W + name for name in ("tbl", "drawing", "object", "ins", "del")}
                for element in comment.iter()
            ):
                unsupported.add("complex or multi-paragraph comments")
    for path in files:
        if path.startswith(("word/charts/", "word/diagrams/", "word/embeddings/")):
            unsupported.add("charts, diagrams, or embedded files")
        if path.startswith((
            "word/commentsExtended",
            "word/commentsIds",
            "word/people",
        )):
            unsupported.add("threaded or resolved comments")
        if path.startswith("word/media/") and Path(path).suffix.lower() not in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
        }:
            unsupported.add("media formats outside PNG/JPEG/GIF")
        if path.endswith(".rels"):
            for relation in _xml(files[path]):
                if relation.get("TargetMode") == "External" and not relation.get(
                    "Type", ""
                ).endswith("/hyperlink"):
                    unsupported.add("externally linked resources")
    return styles, sorted(unsupported)


def _nodes(value: Any) -> Iterator[dict[str, Any]]:
    """Walk Pandoc's internal structural data without nested model reconstruction."""
    if isinstance(value, dict):
        if "t" in value:
            yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _attributes(node: dict[str, Any]) -> list[Any] | None:
    if node["t"] in {"Span", "Div", "Code", "CodeBlock", "Image", "Link"}:
        return node["c"][0]
    if node["t"] == "Header":
        return node["c"][1]
    return None


def _check_style(node: dict[str, Any], names: set[str | None]) -> None:
    attr = _attributes(node)
    style = dict(attr[2]).get("custom-style") if attr is not None else None
    if style and style not in names:
        raise ValueError(
            f"Style is not defined in the retained reference DOCX: {style}"
        )


def _markdown(pandoc: str, ast: dict[str, Any], timeout: float) -> str:
    return _run(
        pandoc,
        ["--from=json", "--to=" + _MARKDOWN, "--wrap=none"],
        timeout,
        text=json.dumps(ast),
    )


def _parse_markdown(pandoc: str, body: str, timeout: float) -> dict[str, Any]:
    return json.loads(
        _run(pandoc, ["--from=" + _MARKDOWN, "--to=json"], timeout, text=body)
    )


def _encode_comments(
    ast: dict[str, Any], files: dict[str, bytes], pandoc: str, timeout: float
) -> dict[str, ReviewComment]:
    records = {}
    if "word/comments.xml" in files:
        records = {
            element.get(_W + "id"): element
            for element in _xml(files["word/comments.xml"])
        }
    comments = {}
    for node in list(_nodes(ast["blocks"])):
        if node["t"] != "Span":
            continue
        attr, content = node["c"]
        kind = next(
            (name for name in ("comment-start", "comment-end") if name in attr[1]), None
        )
        if kind is None:
            continue
        attributes = dict(attr[2])
        identifier = attributes.get("id", "")
        if re.fullmatch(r"[0-9]+", identifier) is None:
            raise ValueError(f"DOCX comment has an invalid ID: {identifier!r}")
        key = "c" + identifier
        if kind == "comment-start":
            if key in comments:
                raise ValueError(f"DOCX has a duplicated comment range: {key}")
            original = records.get(identifier)
            small_ast = {**ast, "meta": {}, "blocks": [{"t": "Para", "c": content}]}
            comments[key] = ReviewComment(
                text=_markdown(pandoc, small_ast, timeout).strip(),
                author=attributes.get("author", ""),
                date=attributes.get("date"),
                initials=original.get(_W + "initials")
                if original is not None
                else None,
            )
        endpoint = "start" if kind == "comment-start" else "end"
        node["c"] = [[f"docx-{key}-{endpoint}", [f"docx-comment-{endpoint}"], []], []]
    if set(records) != {key[1:] for key in comments}:
        raise ValueError(
            "Some Word comments have no representable body anchor; retain the original DOCX"
        )
    return comments


def _anchors(ast: dict[str, Any], comments: dict[str, ReviewComment]) -> list[str]:
    seen: dict[tuple[str, str], int] = {}
    active = set()
    order = []
    for node in _nodes(ast["blocks"]):
        attr = _attributes(node)
        if attr is None:
            continue
        if {"comment-start", "comment-end"} & set(attr[1]):
            raise ValueError(
                "Use profile comment markers, not native Pandoc comment spans"
            )
        kinds = {"start", "end"}.intersection(
            name.removeprefix("docx-comment-") for name in attr[1]
        )
        if not kinds and not str(attr[0]).startswith("docx-c"):
            continue
        match = re.fullmatch(r"docx-(c[0-9]+)-(start|end)", attr[0])
        if node["t"] != "Span" or match is None or kinds != {match[2]} or node["c"][1]:
            raise ValueError(f"Malformed comment marker: {attr[0]!r}")
        key, endpoint = match.groups()
        if key not in comments or (key, endpoint) in seen:
            raise ValueError(f"Unknown or duplicated comment anchor: {key} {endpoint}")
        seen[key, endpoint] = 1
        if endpoint == "start":
            active.add(key)
            order.append(key)
        elif key not in active:
            raise ValueError(f"Comment end precedes its start: {key}")
        else:
            active.remove(key)
    missing = {
        (key, endpoint) for key in comments for endpoint in ("start", "end")
    } - seen.keys()
    if missing or active:
        raise ValueError(
            f"Missing comment anchors: {sorted(missing)}; restore them or explicitly remove the comment and both markers"
        )
    return order


def _asset_path(root: Path, relative: str) -> Path:
    path = root / _relative_path(relative)
    if not path.resolve().is_relative_to(root.resolve()) or any(
        part.is_symlink() for part in (path, *path.parents)
    ):
        raise ValueError(
            f"Asset path escapes its directory or uses a symlink: {relative}"
        )
    return path


def _verify(root: Path, reference: RetainedFile) -> Path:
    path = _asset_path(root, reference.path)
    if not path.is_file() or _digest(path.read_bytes()) != reference.sha256:
        raise ValueError(f"Retained file is missing or changed: {reference.path}")
    return path


def _atomic_write(output: Path, data: bytes) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            dir=output.parent, prefix=".fileroute-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _header(profile: ReviewProfile, body: str) -> bytes:
    stream = StringIO()
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 4096
    yaml.allow_unicode = True
    yaml.dump(profile.model_dump(mode="json"), stream)
    return ("---\n" + stream.getvalue() + "---\n\n" + body).encode("utf-8")


def _read_profile(data: bytes) -> tuple[ReviewProfile, str]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("Review Markdown must be UTF-8") from error
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(
            "Markdown requires a fileroute-review-v1 YAML header; import a DOCX first"
        )
    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"), None
    )
    if end is None:
        raise ValueError("Review Markdown YAML header has no closing ---")
    try:
        metadata = YAML(typ="safe").load("".join(lines[1:end]))
    except YAMLError as error:
        raise ValueError(f"Invalid review YAML header: {error}") from error
    return ReviewProfile.model_validate(metadata), "".join(lines[end + 1 :])


def docx_to_markdown(request: DocxToMarkdownRequest) -> ConversionResult:
    """Import comments, named styles, media, and source evidence into review-v1.

    Original/media files live in an immutable content-addressed sibling bundle.
    Its publication precedes atomic Markdown replacement; a failed final write
    may leave an unreferenced bundle, never a partially replaced Markdown file.
    Known unsupported features are reported in YAML and block later export.
    """
    _paths(request.source, request.output, {".docx"}, {".md", ".markdown"})
    pandoc, version = _pandoc(request.timeout_seconds)
    source_bytes = request.source.read_bytes()
    digest = _digest(source_bytes)
    files = _package(source_bytes)
    styles, unsupported = _source_features(files)
    if "externally linked resources" in unsupported:
        raise ValueError(
            "DOCX contains externally linked resources; embed them before importing"
        )
    relative_bundle = f"{request.output.stem}.assets/{digest}"
    with TemporaryDirectory(prefix="fileroute-docx-") as temporary:
        work = Path(temporary)
        original = work / "original.docx"
        original.write_bytes(source_bytes)
        ast = json.loads(
            _run(
                pandoc,
                [
                    "--sandbox",
                    "--from=docx+styles",
                    "--track-changes=all",
                    "--to=json",
                    "--extract-media=media",
                    str(original),
                ],
                request.timeout_seconds,
                cwd=work,
            )
        )
        comments = _encode_comments(ast, files, pandoc, request.timeout_seconds)
        _anchors(ast, comments)
        assets = {}
        for node in _nodes(ast["blocks"]):
            if node["t"] == "Image":
                local = unquote(node["c"][2][0])
                path = _asset_path(work, local)
                if not path.is_file():
                    raise ValueError(
                        f"Pandoc did not extract the referenced image: {local}"
                    )
                relative = f"{relative_bundle}/{local}"
                assets[relative] = _digest(path.read_bytes())
                node["c"][2][0] = relative
        retained = RetainedFile(path=relative_bundle + "/original.docx", sha256=digest)
        profile = ReviewProfile(
            docx_profile="fileroute-review-v1",
            schema_version=1,
            source=retained,
            reference_docx=retained,
            converter=version,
            styles=styles,
            comments=comments,
            assets=assets,
            unsupported=unsupported,
            properties=ast["meta"],
        )
        body = _markdown(pandoc, {**ast, "meta": {}}, request.timeout_seconds)
        _anchors(_parse_markdown(pandoc, body, request.timeout_seconds), comments)
        markdown = _header(profile, body)
        if request.source.read_bytes() != source_bytes:
            raise ValueError("Source changed during import; reacquire the DOCX")
        _paths(request.source, request.output, {".docx"}, {".md", ".markdown"})
        request.output.parent.mkdir(parents=True, exist_ok=True)
        bundle = _asset_path(request.output.parent.resolve(), relative_bundle)
        expected = {
            "original.docx": digest,
            **{
                key.removeprefix(relative_bundle + "/"): value
                for key, value in assets.items()
            },
        }
        if bundle.exists():
            for name, expected_digest in expected.items():
                _verify(bundle, RetainedFile(path=name, sha256=expected_digest))
        else:
            bundle.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(
                prefix=".fileroute-bundle-", dir=bundle.parent
            ) as staged:
                target = Path(staged) / "bundle"
                target.mkdir()
                for name in expected:
                    destination = target / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(work / name, destination)
                target.rename(bundle)
        _atomic_write(request.output, markdown)
    return ConversionResult(
        source=request.source,
        output=request.output,
        source_sha256=digest,
        output_sha256=_digest(markdown),
        assets=bundle,
        unsupported=unsupported,
    )


def markdown_to_docx(request: MarkdownToDocxRequest) -> ConversionResult:
    """Export review-v1 with native Word comments and the retained named styles.

    Missing/duplicated/reversed anchors, modified assets, external images, and
    reported unsupported features fail before replacing any existing output.
    Comment text is never used to guess a new attachment location.
    """
    _paths(request.source, request.output, {".md", ".markdown"}, {".docx"})
    data = request.source.read_bytes()
    profile, body = _read_profile(data)
    root = request.source.parent.resolve()
    original = _verify(root, profile.source)
    reference = _verify(root, profile.reference_docx)
    protected = [original, reference]
    for path, digest in profile.assets.items():
        protected.append(_verify(root, RetainedFile(path=path, sha256=digest)))
    for path in protected:
        if request.output.resolve() == path.resolve() or (
            request.output.exists() and request.output.samefile(path)
        ):
            raise ValueError(f"Output must not overwrite a retained input: {path}")
    _, source_unsupported = _source_features(_package(original.read_bytes()))
    _, reference_unsupported = _source_features(_package(reference.read_bytes()))
    unsupported = sorted(
        set(profile.unsupported + source_unsupported + reference_unsupported)
    )
    if unsupported:
        raise ValueError(
            "Features outside review-v1 block export: "
            + "; ".join(unsupported)
            + ". Resolve them in Word and import again; the original is retained."
        )
    pandoc, _ = _pandoc(request.timeout_seconds)
    ast = _parse_markdown(pandoc, body, request.timeout_seconds)
    order = _anchors(ast, profile.comments)
    ast["meta"] = profile.properties
    # The YAML boundary preserves native Pandoc metadata. Let its JSON reader
    # validate that structure before walking attributes, rather than rebuilding
    # Pandoc's AST as a second hierarchy of Python models.
    ast = json.loads(
        _run(
            pandoc,
            ["--from=json", "--to=json"],
            request.timeout_seconds,
            text=json.dumps(ast),
        )
    )
    _anchors({"blocks": profile.properties}, {})
    styles = _xml(_package(reference.read_bytes()).get("word/styles.xml", b"<styles/>"))
    known_styles = {element.get(_W + "val") for element in styles.iter(_W + "name")}
    comment_inlines = {}
    for key, comment in profile.comments.items():
        parsed = _parse_markdown(pandoc, comment.text, request.timeout_seconds)
        if len(parsed["blocks"]) > 1 or (
            parsed["blocks"] and parsed["blocks"][0]["t"] not in {"Para", "Plain"}
        ):
            raise ValueError(f"Comment {key} must be one inline Markdown paragraph")
        _anchors(parsed, {})
        for node in _nodes(parsed["blocks"]):
            _check_style(node, known_styles)
        if any(
            node["t"] in {"Note", "Image", "RawInline", "RawBlock"}
            for node in _nodes(parsed["blocks"])
        ):
            raise ValueError(
                f"Comment {key} contains content outside the inline review profile"
            )
        comment_inlines[key] = parsed["blocks"][0]["c"] if parsed["blocks"] else []
    for node in list(_nodes(ast)):
        if node["t"] in {"RawInline", "RawBlock"}:
            raise ValueError("Raw document content is outside review-v1")
        attr = _attributes(node)
        if attr is not None:
            if {
                "insertion",
                "deletion",
                "paragraph-insertion",
                "paragraph-deletion",
            } & set(attr[1]):
                raise ValueError(
                    "Tracked-change markup is outside review-v1; resolve changes explicitly first"
                )
            _check_style(node, known_styles)
            match = re.fullmatch(r"docx-(c[0-9]+)-(start|end)", attr[0])
            if match:
                key, endpoint = match.groups()
                comment = profile.comments[key]
                attributes = [["id", key[1:]]]
                if endpoint == "start":
                    attributes.append(["author", comment.author])
                    for name in ("date", "initials"):
                        if (value := getattr(comment, name)) is not None:
                            attributes.append([name, value])
                node["c"] = [
                    ["", ["comment-" + endpoint], attributes],
                    comment_inlines[key] if endpoint == "start" else [],
                ]
        if node["t"] == "Image":
            relative = unquote(node["c"][2][0])
            if relative not in profile.assets:
                raise ValueError(f"Image is not a declared retained asset: {relative}")
            node["c"][2][0] = str(
                _verify(
                    root, RetainedFile(path=relative, sha256=profile.assets[relative])
                )
            )
    with TemporaryDirectory(prefix="fileroute-docx-") as temporary:
        candidate = Path(temporary) / "output.docx"
        _run(
            pandoc,
            [
                "--from=json",
                "--to=docx",
                "--reference-doc=" + str(reference),
                "--output=" + str(candidate),
            ],
            request.timeout_seconds,
            text=json.dumps(ast),
        )
        if not candidate.is_file():
            raise ValueError("Pandoc did not produce the DOCX output")
        output = candidate.read_bytes()
        generated = _package(output)
        actual_comments = list(
            _xml(generated.get("word/comments.xml", b"<comments/>")).iter(
                _W + "comment"
            )
        )
        if len(actual_comments) != len(order):
            raise ValueError("Pandoc output did not preserve every declared comment")
        for element, key in zip(actual_comments, order):
            comment = profile.comments[key]
            expected_text = "".join(
                node["c"]
                if node["t"] == "Str"
                else " "
                if node["t"] in {"Space", "SoftBreak"}
                else "\n"
                if node["t"] == "LineBreak"
                else node["c"][1]
                if node["t"] in {"Code", "Math"}
                else ""
                for node in _nodes(comment_inlines[key])
            )
            actual_text = "".join(
                node.text or ""
                if node.tag == _W + "t"
                else "\n"
                if node.tag == _W + "br"
                else ""
                for node in element.iter()
            )
            if actual_text != expected_text:
                raise ValueError(f"Pandoc changed comment {key} text")
            for name in ("author", "date", "initials"):
                expected = getattr(comment, name)
                if expected is not None and element.get(_W + name, "") != expected:
                    raise ValueError(f"Pandoc changed comment {key} {name}")
        if request.source.read_bytes() != data:
            raise ValueError(
                "Markdown changed during export; retry with a stable source"
            )
        for path in protected:
            # Recheck pinned content after tools have read the reference/assets.
            if path == original:
                _verify(root, profile.source)
            if path == reference:
                _verify(root, profile.reference_docx)
        for path, digest in profile.assets.items():
            _verify(root, RetainedFile(path=path, sha256=digest))
        _paths(request.source, request.output, {".md", ".markdown"}, {".docx"})
        _atomic_write(request.output, output)
    return ConversionResult(
        source=request.source,
        output=request.output,
        source_sha256=_digest(data),
        output_sha256=_digest(output),
    )


__all__ = [
    "DocxToMarkdownRequest",
    "MarkdownToDocxRequest",
    "ConversionResult",
    "ReviewProfile",
    "ReviewComment",
    "RetainedFile",
    "docx_to_markdown",
    "markdown_to_docx",
]
