from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import typer
from dotenv import load_dotenv

from fileroute.commands.config import resolve_descriptor_path


class OutputFormat(str, Enum):
    TEXT = "text"
    JSON = "json"


DESCRIPTOR_DEFAULT_HELP = (
    "Descriptor file path. Defaults to the saved descriptor or the first "
    "standard descriptor path."
)


def examples_epilog(*lines: str) -> str:
    codeblocks = "\n\n".join(f"```bash\n\n\n{line.strip()}\n\n\n```" for line in lines)
    return f"\n\n**Examples**\n\n\n{codeblocks}"


def echo_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


def load_env_file(env_file: Optional[Path]) -> None:
    if env_file is not None:
        load_dotenv(str(env_file), override=True)


def prepare_descriptor_path(
    descriptor: Path | str | None = None,
    *,
    env_file: Optional[Path] = None,
    require_exists: bool = True,
) -> Path:
    load_env_file(env_file)
    descriptor_path = resolve_descriptor_path(descriptor)
    if require_exists:
        exit_if_descriptor_missing(descriptor_path)
    return descriptor_path


def coerce_field_value(raw: str) -> Any:
    value = raw.strip()
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"null", "none"}:
        return None
    if (value.startswith("{") and value.endswith("}")) or (
        value.startswith("[") and value.endswith("]")
    ):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return raw
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return raw


def parse_field_args(args: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    if not args:
        return parsed
    idx = 0
    while idx < len(args):
        token = args[idx]
        if not token.startswith("--"):
            raise typer.BadParameter(
                f"Invalid token '{token}'. Use --key value or --key=value."
            )

        key_token = token[2:]
        if not key_token:
            raise typer.BadParameter("Invalid empty parameter name.")

        if "=" in key_token:
            key, raw_value = key_token.split("=", 1)
            if not key:
                raise typer.BadParameter("Invalid empty parameter name.")
            parsed[key] = coerce_field_value(raw_value)
            idx += 1
            continue

        key = key_token
        idx += 1
        if idx >= len(args):
            raise typer.BadParameter(f"Missing value for --{key}.")

        raw_value = args[idx]
        if raw_value.startswith("--"):
            raise typer.BadParameter(f"Missing value for --{key}.")
        parsed[key] = coerce_field_value(raw_value)
        idx += 1

    return parsed


def exit_if_descriptor_missing(descriptor_path: Path) -> None:
    if descriptor_path.exists():
        return

    typer.echo(f"Descriptor '{descriptor_path}' does not exist.", err=True)
    raise typer.Exit(code=1)


__all__ = [
    "DESCRIPTOR_DEFAULT_HELP",
    "OutputFormat",
    "coerce_field_value",
    "echo_json",
    "examples_epilog",
    "exit_if_descriptor_missing",
    "load_env_file",
    "parse_field_args",
    "prepare_descriptor_path",
]
