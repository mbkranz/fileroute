from __future__ import annotations

import sys
from collections.abc import Sequence

import click

from fileroute.cli import app


def main(argv: Sequence[str] | None = None) -> int:
    """Run fileroute CLI commands in-process for VS Code debugging.

    Examples:
      python scripts/dev_test.py list --help
      python scripts/dev_test.py auth --help

    In VS Code launch args, pass only the fileroute subcommand arguments,
    e.g. ["list", "--help"].
    """
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args:
        args = ["--help"]

    try:
        app(args=args, prog_name="fileroute", standalone_mode=False)
        return 0
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
