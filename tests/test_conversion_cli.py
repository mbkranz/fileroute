"""Document conversion is inferred by pull/push, without separate commands."""

import pytest
from typer.testing import CliRunner

from fileroute.cli import app


@pytest.mark.parametrize("command", ["convert", "render", "docx-to-md", "md-to-docx"])
def test_separate_conversion_commands_are_absent(command):
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code != 0 and "No such command" in result.output
