from pathlib import Path

from typer.testing import CliRunner

from sharedrive.cli import app

RUNNER = CliRunner()


def test_activate_selects_descriptor(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    descriptor = tmp_path / "descriptor.yaml"
    descriptor.write_text("resources: []\n", encoding="utf-8")

    result = RUNNER.invoke(app, ["activate", "descriptor.yaml"], prog_name="sharedrive")

    assert result.exit_code == 0
    assert "Activated descriptor:" in result.output
    selected = (tmp_path / ".sharedrive" / "descriptor").read_text(encoding="utf-8").strip()
    assert selected == str(descriptor.resolve())


def test_checkout_command_is_removed() -> None:
    result = RUNNER.invoke(app, ["checkout", "descriptor.yaml"], prog_name="sharedrive")

    assert result.exit_code != 0
    assert "checkout" in result.output
