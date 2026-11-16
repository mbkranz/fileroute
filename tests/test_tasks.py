"""Native Poe contracts, pure model validation, and concrete local execution."""

from copy import deepcopy
import json
from pathlib import Path
import shlex
import subprocess
import sys

from jsonschema import Draft202012Validator
import pytest
from pydantic import ValidationError

from fileroute.models import Catalog, Location, Resource
from fileroute.tasks import (
    SequenceSpec,
    StepExecutionError,
    StepsRequest,
    default_markdown_sequence,
    execute_steps,
    resolve_output,
)


def sequence(*commands, output="${FILEROUTE_OUTPUT}"):
    return SequenceSpec.model_validate({
        "params": {"output": {"default": output}},
        "steps": [{"cmd": command} for command in commands],
    })


def python_command(code):
    return shlex.join([sys.executable, "-c", code])


def request(tmp_path, definition):
    source = tmp_path / "source.txt"
    source.write_text("source")
    return StepsRequest(
        sequence=definition,
        input=source,
        output=tmp_path / "work/output.txt",
        root=tmp_path,
        catalog_dir=tmp_path / "catalogs",
        work_dir=tmp_path / "work",
    )


def test_aliases_and_native_sequence_validate_without_config_or_filesystem(monkeypatch):
    from poethepoet.config import PoeConfig

    def forbidden(*args, **kwargs):
        pytest.fail("Model construction attempted filesystem/config/subprocess I/O")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(PoeConfig, "load", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    spec = default_markdown_sequence()
    native = SequenceSpec.model_validate(spec.root)
    assert native.root == spec.root
    assert set(spec.model_dump()) == {"steps", "params"}
    assert "args" not in spec.model_dump()
    source = Location(path="source.docx", afterPull=spec)
    target = Location(path="target.docx", beforePush=spec)
    resource = Resource(path="term.md", sources=[source], targets=[target])
    assert resource.sources[0] is source and resource.targets[0] is target


@pytest.mark.parametrize(
    "definition, match",
    [
        ({"steps": [], "sequence": []}, "only one"),
        ({"steps": [], "params": {}, "args": {}}, "only one"),
        (
            {
                "steps": [{"cmd": "true", "params": {}}],
                "params": {"output": {"default": "x"}},
            },
            "Unsupported option 'args'",
        ),
        ({"steps": [{"cmd": "true", "args": {}, "params": {}}]}, "only one"),
        (
            {"steps": [{"ref": "missing"}], "params": {"output": {"default": "x"}}},
            "unknown task",
        ),
        ({"steps": [{"cmd": "true"}]}, "params.output"),
        (
            {
                "steps": [{"cmd": "true"}],
                "params": {"output": {"default": {"path": "x"}}},
            },
            "type",
        ),
        (
            {"steps": [{"cmd": "true"}], "params": {"output": {"positional": True}}},
            "single string option",
        ),
    ],
)
def test_native_and_alias_errors_are_explicit(definition, match):
    with pytest.raises(ValidationError, match=match):
        SequenceSpec.model_validate(definition)


def test_aliases_do_not_rewrite_parameter_names_or_values():
    spec = SequenceSpec.model_validate({
        "steps": [{"cmd": "echo ok"}],
        "params": {"output": {"default": "out"}, "steps": {"default": "literal"}},
    })
    assert "steps" in spec.root["args"]
    assert "sequence" not in spec.root["args"]


@pytest.mark.parametrize("options", [[], ["output"], ["--"], ["--bad option"]])
def test_output_requires_a_usable_native_option(options):
    value = {
        "params": {"output": {"default": "x", "options": options}},
        "steps": [{"cmd": "echo ok"}],
    }
    with pytest.raises(ValidationError, match="option"):
        SequenceSpec.model_validate(value)
    assert list(
        Draft202012Validator(SequenceSpec.model_json_schema()).iter_errors(value)
    )


def test_catalog_schema_embeds_native_poe_rules_and_endpoint_roles():
    value = {
        "resources": {
            "term": {
                "path": "term.md",
                "sources": [
                    {
                        "path": "source.docx",
                        "afterPull": default_markdown_sequence().model_dump(),
                    }
                ],
            }
        }
    }
    schema = Catalog.model_json_schema(by_alias=True)
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    validator.validate(value)
    native = deepcopy(value)
    native["resources"]["term"]["sources"][0]["afterPull"] = (
        default_markdown_sequence().root
    )
    validator.validate(native)
    invalid = deepcopy(value)
    invalid["resources"]["term"]["sources"][0]["beforePush"] = (
        default_markdown_sequence().model_dump()
    )
    assert list(validator.iter_errors(invalid))
    with pytest.raises(ValidationError, match="Source locations"):
        Catalog.model_validate(invalid)
    invalid = deepcopy(value)
    invalid["resources"]["term"]["sources"][0]["afterPull"]["steps"][0]["params"] = {}
    assert list(validator.iter_errors(invalid))


def test_wrong_endpoint_hooks_fail_for_preconstructed_locations():
    hook = default_markdown_sequence()
    with pytest.raises(ValidationError, match="Source locations"):
        Resource(path="term.md", sources=[Location(path="x", beforePush=hook)])
    with pytest.raises(ValidationError, match="Target locations"):
        Resource(path="term.md", targets=[Location(path="x", afterPull=hook)])


def test_default_null_endpoint_fields_survive_full_model_roundtrip():
    resource = Resource(
        path="term.md",
        sources=[Location(path="input.docx")],
        targets=[Location(path="output.docx")],
    )
    restored = Resource.model_validate(resource.model_dump())
    assert restored.sources[0].after_pull is None
    assert restored.targets[0].before_push is None
    schema = Resource.model_json_schema(by_alias=True)
    Draft202012Validator(schema).validate(resource.model_dump(by_alias=True))


def test_output_is_resolved_natively_without_filesystem_access(tmp_path, monkeypatch):
    candidate = request(
        tmp_path, sequence("true", output="${FILEROUTE_WORK_DIR}/a file.docx")
    )
    monkeypatch.setattr(
        Path, "resolve", lambda *_: pytest.fail("filesystem resolution")
    )
    monkeypatch.setattr(Path, "is_file", lambda *_: pytest.fail("filesystem check"))
    assert resolve_output(candidate) == tmp_path / "work/a file.docx"
    overridden = candidate.model_copy(
        update={"arguments": ["--output", str(tmp_path / "work/other.docx")]}
    )
    assert resolve_output(overridden) == tmp_path / "work/other.docx"
    with pytest.raises(ValueError, match="work directory"):
        resolve_output(
            candidate.model_copy(
                update={"arguments": ["--output", str(tmp_path / "escape.docx")]}
            )
        )


def test_native_execution_is_sequential_and_ignores_ambient_project(
    tmp_path, monkeypatch
):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poe.tasks]\nfileroute_steps = "exit 42"\n'
    )
    monkeypatch.setenv("POE_PROJECT_DIR", str(tmp_path / "missing"))
    definition = sequence(
        python_command(
            'import os; from pathlib import Path; Path(os.environ["FILEROUTE_WORK_DIR"], "first").write_text("one")'
        ),
        python_command(
            'import os; from pathlib import Path; Path(os.environ["output"]).write_text(Path(os.environ["FILEROUTE_WORK_DIR"], "first").read_text() + " two")'
        ),
    )
    candidate = request(tmp_path, definition)
    result = execute_steps(candidate)
    assert result.exit_code == 0 and result.output.read_text() == "one two"
    context = json.loads((tmp_path / "work/context.json").read_text())
    assert context["output"] == str(result.output)
    assert not list(tmp_path.glob(".fileroute-steps-*.json"))


def test_native_failure_stops_sequence_and_retains_diagnostics(tmp_path):
    candidate = request(
        tmp_path,
        sequence(
            python_command(
                'import sys; print("failed step", file=sys.stderr); sys.exit(7)'
            ),
            python_command(
                'import os; from pathlib import Path; Path(os.environ["output"]).write_text("must not run")'
            ),
        ),
    )
    with pytest.raises(StepExecutionError) as caught:
        execute_steps(candidate)
    assert caught.value.result.exit_code != 0
    assert "failed step" in caught.value.result.stderr
    assert not candidate.output.exists()
    assert not list(tmp_path.glob(".fileroute-steps-*.json"))


def test_unrelated_typed_envfile_defaults_are_deferred_to_native_execution(tmp_path):
    (tmp_path / "count.env").write_text("COUNT=3\n")
    definition = SequenceSpec.model_validate({
        "params": {
            "output": {"default": "${FILEROUTE_OUTPUT}"},
            "count": {"type": "integer", "default": "${COUNT}"},
        },
        "envfile": "count.env",
        "steps": [
            {
                "cmd": python_command(
                    'import os; from pathlib import Path; Path(os.environ["output"]).write_text(os.environ["count"])'
                )
            }
        ],
    })
    candidate = request(tmp_path, definition)
    assert resolve_output(candidate) == candidate.output
    assert execute_steps(candidate).output.read_text() == "3"


def test_success_without_output_is_a_visible_failure(tmp_path):
    candidate = request(tmp_path, sequence(python_command("pass")))
    with pytest.raises(StepExecutionError, match="did not produce") as caught:
        execute_steps(candidate)
    assert caught.value.result.exit_code == 0


def test_staged_validation_only_sequence_can_keep_prepared_candidate(tmp_path):
    candidate = request(tmp_path, sequence(python_command("pass")))
    candidate.output.parent.mkdir()
    candidate.output.write_text("preserved")
    assert execute_steps(candidate).output.read_text() == "preserved"


def test_runtime_rejects_symlink_output(tmp_path):
    candidate = request(tmp_path, sequence(python_command("pass")))
    candidate.output.parent.mkdir()
    try:
        candidate.output.symlink_to(candidate.input)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises(StepExecutionError, match="symlink"):
        execute_steps(candidate)


def test_reserved_context_path_cannot_fulfill_output_contract(tmp_path):
    candidate = request(
        tmp_path,
        sequence(python_command("pass"), output="${FILEROUTE_WORK_DIR}/context.json"),
    )
    with pytest.raises(ValueError, match="reserved context"):
        execute_steps(candidate)
    assert not candidate.work_dir.exists()
    assert not list(tmp_path.glob(".fileroute-steps-*.json"))


def test_reserved_context_input_is_preserved_before_writes(tmp_path):
    candidate = request(tmp_path, sequence(python_command("pass")))
    candidate.work_dir.mkdir()
    source = candidate.work_dir / "context.json"
    source.write_bytes(b"original input bytes")
    with pytest.raises(ValueError, match="reserved context"):
        execute_steps(candidate.model_copy(update={"input": source}))
    assert source.read_bytes() == b"original input bytes"
    assert not candidate.output.exists()
