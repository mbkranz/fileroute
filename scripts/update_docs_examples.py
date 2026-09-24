"""Regenerate the use-case YAML and Mermaid blocks from saved descriptors."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from fileroute.diagram import load_graph, render_mermaid

ROOT = Path(__file__).resolve().parents[1]
NAMES = (
    "sharepoint-two-targets",
    "sharepoint-google-drive",
    "sharepoint-mixed-targets",
    "local-three-targets",
)


def replace_block(content: str, *, kind: str, name: str, body: str) -> str:
    start = f"<!-- {kind}:{name}:start -->"
    end = f"<!-- {kind}:{name}:end -->"
    updated, count = re.subn(
        re.escape(start) + r".*?" + re.escape(end),
        lambda _: f"{start}\n{body}\n{end}",
        content,
        flags=re.DOTALL,
    )
    if count != 1:
        raise ValueError(f"Expected exactly one {kind} block for {name}")
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify generated examples")
    args = parser.parse_args()
    page = ROOT / "docs" / "use-cases.md"
    original = page.read_text(encoding="utf-8")
    updated = original
    stale_diagrams = []
    with TemporaryDirectory() as directory:
        for name in NAMES:
            descriptor = ROOT / "examples" / "use-cases" / f"{name}.yaml"
            yaml_source = descriptor.read_text(encoding="utf-8").rstrip()
            updated = replace_block(
                updated,
                kind="example",
                name=name,
                body=f"```yaml\n{yaml_source}\n```",
            )
            mermaid = render_mermaid(
                load_graph(descriptor), Path(directory) / f"{name}.mmd"
            ).read_text(encoding="utf-8")
            output = descriptor.with_suffix(".mmd")
            if args.check:
                if not output.exists() or output.read_text(encoding="utf-8") != mermaid:
                    stale_diagrams.append(output)
            else:
                output.write_text(mermaid, encoding="utf-8")
            updated = replace_block(
                updated,
                kind="diagram",
                name=name,
                body=f"```mermaid\n{mermaid.rstrip()}\n```",
            )
    if args.check:
        if updated != original or stale_diagrams:
            raise SystemExit(
                "Generated examples are outdated. Run poe docs-update. "
                + ", ".join(map(str, stale_diagrams))
            )
    else:
        page.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
