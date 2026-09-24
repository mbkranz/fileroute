"""Regenerate the four README Mermaid examples from their saved descriptors."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify generated examples"
    )
    args = parser.parse_args()
    readme_path = ROOT / "README.md"
    original = readme_path.read_text(encoding="utf-8")
    updated = original
    with TemporaryDirectory() as directory:
        for name in NAMES:
            descriptor = ROOT / "examples" / "use-cases" / f"{name}.yaml"
            generated = render_mermaid(
                load_graph(descriptor), Path(directory) / f"{name}.mmd"
            ).read_text(encoding="utf-8")
            output = descriptor.with_suffix(".mmd")
            if args.check:
                if (
                    not output.exists()
                    or output.read_text(encoding="utf-8") != generated
                ):
                    raise SystemExit(f"Outdated diagram: {output}")
            else:
                output.write_text(generated, encoding="utf-8")
            start = f"<!-- diagram:{name}:start -->"
            end = f"<!-- diagram:{name}:end -->"
            replacement = f"{start}\n```mermaid\n{generated}```\n{end}"
            updated, count = re.subn(
                re.escape(start) + r".*?" + re.escape(end),
                lambda _: replacement,
                updated,
                flags=re.DOTALL,
            )
            if count != 1:
                raise SystemExit(f"Expected exactly one README block for {name}")
    if args.check:
        if updated != original:
            raise SystemExit("README Mermaid diagrams are outdated")
    else:
        readme_path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
