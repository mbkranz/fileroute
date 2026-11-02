# TODO

## Python API documentation

- [ ] Evaluate replacing the custom AST/docstring API renderer in
  `scripts/update_docs_markdown.py` with [pdoc](https://pdoc.dev/).
  Generate the public API declared by `__all__`, including exported aliases,
  signatures, type annotations, and complete docstrings. Compare pdoc's standalone
  HTML integration with MkDocs-native
  [mkdocstrings](https://mkdocstrings.github.io/) before choosing the simpler
  implementation for the existing site. Keep generation/build/check orchestration
  in Poe and preserve non-mutating CI checks. Update documentation dependencies,
  `uv.lock`, navigation, and contributor instructions together.

## CLI documentation

- [x] Use [Typer's built-in Markdown generator](https://typer.tiangolo.com/tutorial/typer-command/#generate-docs).
  `scripts/update_docs_markdown.py` already invokes
  `python -m typer fileroute.cli utils docs` and documents registered commands
  recursively. Retain this implementation; do not add a custom CLI renderer.

Keep workflow guides and practical examples authored separately from generated
API and CLI references.
