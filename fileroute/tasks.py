"""Native Poe sequences attached to transfer locations.

``steps`` and ``params`` are catalog spellings of Poe's ``sequence`` and
``args`` options. Poe owns validation and execution; this module only adapts
those spellings and passes concrete transfer paths to an isolated Poe process.
See https://poethepoet.natn.io/tasks/task_types/sequence.html.
"""

from __future__ import annotations

from contextlib import redirect_stderr
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable

from poethepoet.config import PoeConfig
from poethepoet.env.cache import EnvFileCache
from poethepoet.env.task_env import TaskEnv
from poethepoet.exceptions import PoeException
from poethepoet.io import PoeIO
from poethepoet.schema import build_schema
from poethepoet.task.args import ArgSpec, PoeTaskArgs
from poethepoet.task.base import TaskSpecFactory
from poethepoet.task.sequence import SequenceTask
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    model_serializer,
    model_validator,
)


def _task_aliases(value: Any, *, authored: bool = False) -> Any:
    """Translate aliases only at native task positions, never inside parameters."""
    if isinstance(value, list):
        return [_task_aliases(item, authored=authored) for item in value]
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    for alias, native in (("steps", "sequence"), ("params", "args")):
        if alias in result and native in result:
            raise ValueError(f"Provide only one of {alias!r} and {native!r}")
        source, target = (native, alias) if authored else (alias, native)
        if source in result:
            result[target] = result.pop(source)
    for key in ("steps" if authored else "sequence", "parallel", "switch"):
        if isinstance(result.get(key), list):
            result[key] = [
                _task_aliases(item, authored=authored) for item in result[key]
            ]
    if isinstance(result.get("control"), dict):
        result["control"] = _task_aliases(result["control"], authored=authored)
    return result


def _quiet_io() -> PoeIO:
    return PoeIO(
        output=StringIO(),
        error=StringIO(),
        input=StringIO(),
        ansi=False,
        make_default=False,
    )


def _native_spec(value: dict[str, Any]) -> SequenceTask.TaskSpec:
    """Build specs from an in-memory table; never invoke Poe's config loader."""
    config = PoeConfig(
        cwd=Path("."), table={"tasks": {"fileroute_steps": value}}, io=_quiet_io()
    )
    factory = TaskSpecFactory(config).load_all()
    for spec in factory:
        spec.validate(config, factory)
    spec = factory.get("fileroute_steps")
    if not isinstance(spec, SequenceTask.TaskSpec):
        raise ValueError("Transfer hooks must be a Poe sequence with steps")
    return spec


def _output_argument(value: dict[str, Any]) -> ArgSpec:
    arguments = tuple(ArgSpec.parse(value.get("args", {})))
    output = next(
        (argument for argument in arguments if argument.name == "output"), None
    )
    if output is None:
        raise ValueError("Transfer hooks must declare an outer params.output argument")
    if output.type != "string" or output.multiple or output.positional:
        raise ValueError(
            "params.output must be a single string option, not positional or multiple"
        )
    if not output.options or any(
        not option.startswith("-")
        or option in {"-", "--"}
        or any(character.isspace() for character in option)
        for option in output.options
    ):
        raise ValueError(
            "params.output needs at least one usable option such as --output"
        )
    return output


def _sequence_schema() -> dict[str, Any]:
    """Compose Poe's schema as an embedded, offline JSON Schema resource.

    Absolute references address the embedded resource, so Pydantic does not
    mistake Poe's draft-07 definitions for its own generated model references.
    """
    native = build_schema()
    identifier = "https://fileroute.invalid/schemas/poethepoet-sequence-0.49.json"

    def aliases(node: Any) -> Any:
        if isinstance(node, list):
            return [aliases(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {key: aliases(item) for key, item in node.items()}
        reference = result.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/definitions/"):
            result["$ref"] = identifier + reference
        properties = result.get("properties")
        if isinstance(properties, dict):
            for alias, source in (("steps", "sequence"), ("params", "args")):
                if source not in properties:
                    continue
                properties[alias] = deepcopy(properties[source])
                result.setdefault("allOf", []).append({
                    "not": {"required": [alias, source]}
                })
                if source in result.get("required", []):
                    result["required"].remove(source)
                    result["allOf"].append({
                        "anyOf": [{"required": [alias]}, {"required": [source]}]
                    })
        return result

    definitions = aliases(native["definitions"])
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": identifier,
        "title": "Transfer steps (native Poe sequence)",
        "definitions": definitions,
        **deepcopy(definitions["sequence_task"]),
    }
    single_output = {
        "type": "object",
        "properties": {
            "type": {"const": "string"},
            "multiple": {"const": False},
            "positional": {"const": False},
            "options": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "string",
                    "pattern": r"^-\S+$",
                    "not": {"enum": ["-", "--"]},
                },
            },
        },
    }
    output = {
        "anyOf": [
            {
                "type": "object",
                "required": ["output"],
                "properties": {"output": single_output},
            },
            {
                "type": "array",
                "contains": {
                    "anyOf": [
                        {"const": "output"},
                        {
                            "type": "object",
                            "required": ["name"],
                            "allOf": [single_output],
                            "properties": {"name": {"const": "output"}},
                        },
                    ]
                },
            },
        ]
    }
    schema.setdefault("allOf", []).append({
        "anyOf": [
            {"required": [key], "properties": {key: output}}
            for key in ("args", "params")
        ]
    })
    return schema


class SequenceSpec(RootModel[dict[str, Any]]):
    """Serializable native sequence, with catalog aliases and pure validation."""

    @model_validator(mode="before")
    @classmethod
    def _validate_native(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value.root
        if not isinstance(value, dict):
            raise ValueError("Transfer steps must be a sequence options mapping")
        normalized = _task_aliases(value)
        if "sequence" not in normalized:
            raise ValueError("Transfer hooks require steps (native alias: sequence)")
        try:
            _native_spec(normalized)
            _output_argument(normalized)
        except (PoeException, TypeError, IndexError) as error:
            raise ValueError(f"Invalid native Poe sequence: {error}") from error
        return normalized

    @model_serializer
    def _serialize(self) -> dict[str, Any]:
        return _task_aliases(self.root, authored=True)

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: Any
    ) -> dict[str, Any]:
        return _sequence_schema()


class StepsRequest(BaseModel):
    """Concrete local inputs and output candidate for one endpoint hook."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sequence: SequenceSpec
    input: Path
    output: Path
    root: Path
    catalog_dir: Path
    work_dir: Path
    context: dict[str, Any] = Field(default_factory=dict)
    arguments: list[str] = Field(default_factory=list)


class StepsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    output: Path
    exit_code: int
    stdout: str
    stderr: str


class StepExecutionError(ValueError):
    """A native task or its required output failed; diagnostics remain available."""

    def __init__(self, message: str, *, result: StepsResult | None = None):
        super().__init__(message)
        self.result = result


def _absolute(path: Path, root: Path) -> Path:
    if not root.is_absolute():
        raise ValueError("StepsRequest.root must be an absolute path")
    return Path(os.path.normpath(path if path.is_absolute() else root / path))


def _runtime_values(request: StepsRequest) -> dict[str, str]:
    root = _absolute(request.root, request.root)
    catalog_dir = _absolute(request.catalog_dir, root)
    work_dir = _absolute(request.work_dir, root)
    return {
        "FILEROUTE_INPUT": str(_absolute(request.input, root)),
        "FILEROUTE_OUTPUT": str(_absolute(request.output, root)),
        "FILEROUTE_ROOT": str(root),
        "FILEROUTE_CATALOG_DIR": str(catalog_dir),
        "FILEROUTE_DESCRIPTOR_DIR": str(catalog_dir),
        "FILEROUTE_WORK_DIR": str(work_dir),
        "FILEROUTE_CONTEXT": str(work_dir / "context.json"),
        "POE_ROOT": str(root),
        "POE_CONF_DIR": str(root),
    }


def _argument_values(request: StepsRequest) -> tuple[dict[str, Any], ArgSpec]:
    """Resolve the output using Poe's parser without reading envfiles or config."""
    values = _runtime_values(request)
    io = _quiet_io()
    environment = TaskEnv(
        env=values,
        args={},
        envfiles=EnvFileCache(request.root, io),
        lazy_vars={},
        private_vars=set(),
    )
    # Inline native env configuration is pure; envfile reads remain with execution.
    environment.apply_env_config(
        envfile_option=(),
        config_env=request.sequence.root.get("env", {}),
        config_dir=request.root,
        config_working_dir=request.root,
    )
    output = _output_argument(request.sequence.root)
    # Other parameters may have typed defaults supplied by an envfile. They
    # belong to native execution, not pure output-path planning. Poe constructs
    # the parser for this one native option; argparse leaves other tokens alone.
    arguments = PoeTaskArgs([vars(output)], "fileroute_steps", io)
    parser = arguments.build_parser(environment, "fileroute steps")
    try:
        with redirect_stderr(io.error_output):
            parsed, _ = parser.parse_known_args(request.arguments)
    except SystemExit as error:
        raise ValueError("Invalid native output argument") from error
    return vars(parsed), output


def resolve_output(request: StepsRequest) -> Path:
    """Resolve the native output argument lexically, without filesystem access.

    Output defaults may reference the runtime FILEROUTE variables or inline task
    env. Envfiles are intentionally not read during planning; use an explicit
    argument when the output otherwise depends on an envfile.
    """
    context_path = _absolute(request.work_dir, request.root) / "context.json"
    if _absolute(request.input, request.root) == context_path:
        raise ValueError("Step input cannot use the reserved context.json path")
    try:
        values, _ = _argument_values(request)
    except PoeException as error:
        raise ValueError(f"Cannot resolve transfer output: {error}") from error
    output = values.get("output")
    if not isinstance(output, str) or not output.strip():
        raise ValueError("params.output must resolve to a non-empty staging path")
    path = _absolute(Path(output), request.root)
    work_dir = _absolute(request.work_dir, request.root)
    if path == context_path:
        raise ValueError("params.output cannot use the reserved context.json path")
    if path == work_dir or not path.is_relative_to(work_dir):
        raise ValueError("params.output must stay inside the transfer work directory")
    return path


def execute_steps(
    request: StepsRequest,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> StepsResult:
    """Run actual Poe in a child process and require its exact staged output.

    The temporary config lives in the transfer root so native POE_ROOT, envfile,
    script and capture paths retain Poe's root-relative semantics. Isolation also
    protects the host from native use_exec and nested event-loop behavior.
    """
    output = resolve_output(request)
    values = _runtime_values(request)
    source = Path(values["FILEROUTE_INPUT"])
    work_dir = Path(values["FILEROUTE_WORK_DIR"])
    if not source.is_file():
        raise StepExecutionError(f"Step input is not a local file: {source}")
    if output.is_symlink() or not output.resolve().is_relative_to(work_dir.resolve()):
        raise StepExecutionError(
            "Step output escapes the transfer work directory through a symlink"
        )
    context_path = Path(values["FILEROUTE_CONTEXT"])
    if context_path.is_symlink() or (
        context_path.exists() and os.path.samefile(context_path, source)
    ):
        raise StepExecutionError("Reserved context.json must not alias the input file")
    work_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    context = {
        **request.context,
        "input": str(source),
        "output": str(output),
        "root": values["FILEROUTE_ROOT"],
        "catalog_dir": values["FILEROUTE_CATALOG_DIR"],
        "work_dir": str(work_dir),
    }
    context_path.write_text(
        json.dumps(context, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    _, output_arg = _argument_values(request)
    arguments = list(request.arguments)
    separator = arguments.index("--") if "--" in arguments else len(arguments)
    arguments[separator:separator] = [output_arg.options[0], str(output)]
    definition = {
        "executor": "simple",
        "tasks": {"fileroute_steps": request.sequence.root},
    }
    config_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".fileroute-steps-",
            suffix=".json",
            dir=request.root,
            delete=False,
        ) as stream:
            json.dump(definition, stream, ensure_ascii=False)
            config_path = Path(stream.name)
        completed = (runner or subprocess.run)(
            [
                sys.executable,
                "-m",
                "poethepoet",
                "--directory",
                str(config_path),
                "fileroute_steps",
                *arguments,
            ],
            cwd=request.root,
            env={**os.environ, **values},
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise StepExecutionError(
            f"Could not execute native Poe steps: {error}"
        ) from error
    finally:
        if config_path is not None:
            config_path.unlink(missing_ok=True)
    result = StepsResult(
        output=output,
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if result.exit_code:
        raise StepExecutionError(
            f"Native Poe steps exited with status {result.exit_code}: {result.stderr or result.stdout}",
            result=result,
        )
    if (
        output.is_symlink()
        or not output.is_file()
        or not output.resolve().is_relative_to(work_dir.resolve())
    ):
        raise StepExecutionError(
            f"Native Poe steps did not produce the staged output: {output}",
            result=result,
        )
    return result


def default_markdown_sequence() -> SequenceSpec:
    """An explicit local MarkItDown hook; source format checks occur at execution."""
    return SequenceSpec.model_validate({
        "params": {
            "input": {"default": "${FILEROUTE_INPUT}"},
            "output": {"default": "${FILEROUTE_OUTPUT}"},
        },
        "steps": [
            {"cmd": 'fileroute steps convert --input "${input}" --output "${output}"'}
        ],
    })


__all__ = [
    "SequenceSpec",
    "StepsRequest",
    "StepsResult",
    "StepExecutionError",
    "resolve_output",
    "execute_steps",
    "default_markdown_sequence",
]
