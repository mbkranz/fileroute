"""Small YAML helpers for test fixture serialization and assertions."""

from io import StringIO

from ruamel.yaml import YAML


def safe_load(content: str):
    return YAML(typ="safe").load(content)


def safe_dump(value, *, sort_keys: bool = False):
    output = StringIO()
    YAML(typ="safe").dump(value, output)
    return output.getvalue()
