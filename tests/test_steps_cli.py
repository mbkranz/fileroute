"""Endpoint edits follow physical descriptor origins; runs stay explicit/local."""

import json
from pathlib import Path
import shlex
import sys

from typer import Typer
from typer.testing import CliRunner

from fileroute.commands.steps import register_steps_commands
from fileroute.descriptor import load


def application():
    app = Typer()
    steps = Typer()
    app.add_typer(steps, name="steps")
    register_steps_commands(steps)
    return app


def catalog(tmp_path):
    child = tmp_path / "child.yaml"
    child.write_text(
        "# Retain catalog comments\nresources:\n  term:\n    path: candidates/term.md\n"
        "    sources:\n      - path: source.docx\n"
        "    targets:\n      - path: s3://bucket/term.docx\n"
    )
    root = tmp_path / "catalog.yaml"
    root.write_text("catalogs:\n  terms:\n    descriptor: child.yaml\n")
    return root, child


def test_add_auto_updates_only_physical_endpoint_origin(tmp_path):
    root, child = catalog(tmp_path)
    before = root.read_bytes()
    result = CliRunner().invoke(
        application(),
        [
            "steps",
            "add",
            str(root),
            "--select",
            "$.catalogs.terms.resources.term.sources[0]",
            "--auto",
        ],
    )
    assert result.exit_code == 0, result.output
    assert root.read_bytes() == before
    assert child.read_text().startswith("# Retain catalog comments")
    source = load(child).resources["term"].sources[0]
    assert source.after_pull.root["sequence"][0]["cmd"].startswith(
        "fileroute steps convert"
    )
    assert load(child).resources["term"].targets[0].before_push is None
    payload = json.loads(result.output)
    assert payload["descriptor"] == str(child) and payload["hook"] == "afterPull"


def test_add_requires_endpoint_and_explicit_replacement(tmp_path):
    root, child = catalog(tmp_path)
    runner = CliRunner()
    args = [
        "steps",
        "add",
        str(root),
        "--select",
        "$.catalogs.terms.resources.term.sources[0]",
        "--cmd",
        "echo validate",
    ]
    assert runner.invoke(application(), args).exit_code == 0
    repeated = runner.invoke(application(), args)
    assert repeated.exit_code == 1 and "--replace" in repeated.output
    assert runner.invoke(application(), [*args, "--replace"]).exit_code == 0
    selected_artifact = runner.invoke(
        application(), ["steps", "add", str(root), "--select", "terms.term", "--auto"]
    )
    assert selected_artifact.exit_code == 1 and "endpoint" in selected_artifact.output
    target = runner.invoke(
        application(),
        [
            "steps",
            "add",
            str(root),
            "--select",
            "$.catalogs.terms.resources.term.targets[0]",
            "--auto",
        ],
    )
    assert target.exit_code == 1 and "source" in target.output


def test_add_native_definition_file_and_reject_invalid_options(tmp_path):
    root, child = catalog(tmp_path)
    definition = tmp_path / "steps.json"
    definition.write_text(
        json.dumps({
            "args": {"output": {"default": "${FILEROUTE_WORK_DIR}/publication.docx"}},
            "sequence": [{"cmd": "echo render"}],
        })
    )
    args = [
        "steps",
        "add",
        str(root),
        "--select",
        "$.catalogs.terms.resources.term.targets[0]",
        "--file",
        str(definition),
    ]
    result = CliRunner().invoke(application(), args)
    assert result.exit_code == 0, result.output
    assert load(child).resources["term"].targets[0].before_push is not None
    before = child.read_bytes()
    definition.write_text(
        '{"steps":[{"cmd":"echo no","params":{}}],"params":{"output":{"default":"x"}}}'
    )
    invalid = CliRunner().invoke(application(), [*args, "--replace"])
    assert invalid.exit_code == 1 and "Unsupported option" in invalid.output
    assert child.read_bytes() == before


def test_run_dry_run_resolves_without_writes_or_commands(tmp_path):
    root, child = catalog(tmp_path)
    app = application()
    runner = CliRunner()
    assert (
        runner.invoke(
            app,
            [
                "steps",
                "add",
                str(root),
                "--select",
                "$.catalogs.terms.resources.term.sources[0]",
                "--cmd",
                "not-a-real-command",
            ],
        ).exit_code
        == 0
    )
    before = child.read_bytes()
    work = tmp_path / "dry-staging"
    result = runner.invoke(
        app,
        [
            "steps",
            "run",
            str(root),
            "--select",
            "$.catalogs.terms.resources.term.sources[0]",
            "--input",
            str(tmp_path / "not-yet-acquired.docx"),
            "--root",
            str(tmp_path),
            "--work-dir",
            str(work),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "output": str(work / "term.md"),
        "executed": False,
    }
    assert not work.exists() and child.read_bytes() == before


def test_run_executes_native_sequence_and_preserves_catalog(tmp_path):
    root, child = catalog(tmp_path)
    source = tmp_path / "input.txt"
    source.write_text("input")
    code = 'import os; from pathlib import Path; Path(os.environ["output"]).write_text("candidate")'
    app = application()
    runner = CliRunner()
    added = runner.invoke(
        app,
        [
            "steps",
            "add",
            str(root),
            "--select",
            "$.catalogs.terms.resources.term.sources[0]",
            "--cmd",
            shlex.join([sys.executable, "-c", code]),
        ],
    )
    assert added.exit_code == 0, added.output
    before = child.read_bytes()
    work = tmp_path / "staging"
    result = runner.invoke(
        app,
        [
            "steps",
            "run",
            str(root),
            "--select",
            "$.catalogs.terms.resources.term.sources[0]",
            "--input",
            str(source),
            "--root",
            str(tmp_path),
            "--work-dir",
            str(work),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["exit_code"] == 0
    assert Path(payload["output"]).read_text() == "candidate"
    assert child.read_bytes() == before
    assert not (tmp_path / "candidates/term.md").exists()
