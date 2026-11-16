"""Local document extraction and Jinja rendering, independent of remote clients.

Poe owns execution order; these functions implement one file transformation.
Microsoft's MarkItDown supplies format conversion. See
https://github.com/microsoft/markitdown for supported converters and extras.
"""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import BadZipFile, ZipFile

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


class ConversionRequest(BaseModel):
    """One local input and output; paths are interpreted by the caller's cwd."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Path
    output: Path
    template: Path | None = None
    mapping_file: Path | None = None
    mappings: dict[str, str] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=120, gt=0)

    @model_validator(mode="after")
    def one_mapping_source(self) -> ConversionRequest:
        if self.mapping_file is not None and self.mappings:
            raise ValueError("Use either mapping_file or inline mappings")
        return self


class ConversionResult(BaseModel):
    """Confirmed local output, independent of remote availability or approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source: Path
    output: Path
    source_sha256: str
    output_sha256: str
    characters: int


def read_mapping(path: Path) -> dict[str, str]:
    """Read a YAML/JSON map of variable names to RFC 6901 JSON Pointers."""
    content = path.read_text(encoding="utf-8")
    data = _structured(content, path.suffix)
    if not isinstance(data, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in data.items()
    ):
        raise ValueError(
            "Mappings must associate variable names with JSON Pointer strings"
        )
    return data


def _structured(content: str, suffix: str) -> Any:
    try:
        return (
            json.loads(content) if suffix == ".json" else YAML(typ="safe").load(content)
        )
    except (ValueError, YAMLError) as error:
        raise ValueError(f"Invalid structured input: {error}") from error


def _run_office(command: list[str], **options: Any) -> subprocess.CompletedProcess:
    """Bound LibreOffice and terminate its process group on timeout."""
    timeout = options.pop("timeout")
    options.pop("check", None)
    options.pop("capture_output", None)
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                )
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _pointer(data: Any, pointer: str) -> Any:
    if pointer == "":
        return data
    if not pointer.startswith("/"):
        raise ValueError(f"Expected an RFC 6901 JSON Pointer, got {pointer!r}")
    value = data
    for token in pointer[1:].split("/"):
        if "~" in token.replace("~1", "").replace("~0", ""):
            raise ValueError(f"Invalid JSON Pointer escape in {pointer!r}")
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token) is None:
            raise ValueError(f"Invalid array index in mapping pointer {pointer!r}")
        try:
            value = value[int(token)] if isinstance(value, list) else value[token]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError(f"Mapping pointer {pointer!r} does not resolve") from error
    return value


def render_template(
    template: Path, context: dict[str, Any], *, mappings: dict[str, str] | None = None
) -> str:
    """Render a local template; missing inputs fail rather than disappearing."""
    mapped = {name: _pointer(context, path) for name, path in (mappings or {}).items()}
    environment = Environment(
        loader=FileSystemLoader(str(template.parent)),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        newline_sequence="\n",
    )
    try:
        return environment.get_template(template.name).render(**context, mapped=mapped)
    except TemplateError as error:
        raise ValueError(f"Cannot render {template}: {error}") from error


def _office_docx(
    source: Path,
    directory: Path,
    *,
    timeout: float,
    runner: Callable[..., subprocess.CompletedProcess] = _run_office,
    find_executable: Callable[[str], str | None] = shutil.which,
) -> Path:
    executable = next(
        (
            found
            for name in ("soffice.com", "libreoffice", "soffice")
            if (found := find_executable(name))
        ),
        None,
    )
    if executable is None:
        raise ValueError(
            "Legacy DOC conversion requires an installed LibreOffice executable"
        )
    result = runner(
        [
            executable,
            f"-env:UserInstallation={(directory / 'profile').as_uri()}",
            "--headless",
            "--norestore",
            "--convert-to",
            "docx",
            "--outdir",
            str(directory),
            str(source.resolve()),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    target = directory / (source.stem + ".docx")
    if result.returncode or not target.is_file() or not target.stat().st_size:
        raise ValueError(
            f"LibreOffice did not produce DOCX: {result.stderr or result.stdout}"
        )
    try:
        with ZipFile(target) as document:
            document.getinfo("word/document.xml")
    except (BadZipFile, KeyError) as error:
        raise ValueError("LibreOffice output is not a readable DOCX") from error
    return target


def _extract(source: Path) -> dict[str, str | None]:
    try:
        from markitdown import MarkItDown
        from markitdown._exceptions import MarkItDownException
    except ImportError as error:
        raise ValueError("Install fileroute[markdown] to convert documents") from error
    try:
        result = MarkItDown(enable_plugins=False).convert_local(str(source))
    except MarkItDownException as error:
        raise ValueError(f"Document conversion failed: {error}") from error
    if source.suffix.lower() == ".pdf" and not result.markdown.strip():
        raise ValueError(
            "No PDF text was extracted; scanned documents require explicit OCR"
        )
    return {"markdown": result.markdown, "title": result.title}


def _write_result(
    request: ConversionRequest, text: str, source_bytes: bytes
) -> ConversionResult:
    if request.output.resolve() == request.source.resolve():
        raise ValueError("Conversion output must differ from the immutable source")
    if request.source.read_bytes() != source_bytes:
        raise ValueError("Source changed during conversion; reacquire it")
    request.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=".fileroute-convert-", dir=request.output.parent
    ) as temporary:
        candidate = Path(temporary) / "output"
        candidate.write_text(text, encoding="utf-8", newline="\n")
        digest = sha256(candidate.read_bytes()).hexdigest()
        candidate.replace(request.output)
    return ConversionResult(
        source=request.source,
        output=request.output,
        source_sha256=sha256(source_bytes).hexdigest(),
        output_sha256=digest,
        characters=len(text),
    )


def convert_document(
    request: ConversionRequest,
    *,
    converter: Callable[[Path], dict[str, Any]] = _extract,
    office_runner: Callable[..., subprocess.CompletedProcess] = _run_office,
    find_executable: Callable[[str], str | None] = shutil.which,
) -> ConversionResult:
    """Extract a local document, optionally map/render it, then publish atomically."""
    source_bytes = request.source.read_bytes()
    with TemporaryDirectory(prefix="fileroute-office-") as temporary:
        source = request.source
        if source.suffix.lower() == ".doc":
            source = _office_docx(
                source,
                Path(temporary),
                timeout=request.timeout_seconds,
                runner=office_runner,
                find_executable=find_executable,
            )
        conversion = converter(source)
    context = {
        "conversion": conversion,
        "source": {
            "path": str(request.source),
            "sha256": sha256(source_bytes).hexdigest(),
        },
        "data": request.data,
    }
    mappings = (
        read_mapping(request.mapping_file) if request.mapping_file else request.mappings
    )
    text = (
        render_template(request.template, context, mappings=mappings)
        if request.template
        else str(conversion["markdown"])
    )
    return _write_result(request, text, source_bytes)


def render_document(request: ConversionRequest) -> ConversionResult:
    """Render JSON/YAML structured data without invoking a document converter."""
    if request.template is None:
        raise ValueError("Rendering requires --template")
    source_bytes = request.source.read_bytes()
    content = source_bytes.decode("utf-8")
    data = _structured(content, request.source.suffix)
    mappings = (
        read_mapping(request.mapping_file) if request.mapping_file else request.mappings
    )
    text = render_template(
        request.template, {"data": data, **request.data}, mappings=mappings
    )
    return _write_result(request, text, source_bytes)


__all__ = [
    "ConversionRequest",
    "ConversionResult",
    "read_mapping",
    "render_template",
    "convert_document",
    "render_document",
]
