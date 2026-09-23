from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import typer.rich_utils as rich_utils  # noqa: E402
from sharedrive.cli import app  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CLI_WIDTH = 200
RUNNER = CliRunner()

CLI_COMMANDS: list[tuple[str, list[str]]] = [
    ("sharedrive --help", ["--help"]),
    ("sharedrive auth --help", ["auth", "--help"]),
    ("sharedrive auth login --help", ["auth", "login", "--help"]),
    ("sharedrive auth login gdrive --help", ["auth", "login", "gdrive", "--help"]),
    (
        "sharedrive auth login microsoft --help",
        ["auth", "login", "microsoft", "--help"],
    ),
    (
        "sharedrive auth login sharepoint --help",
        ["auth", "login", "sharepoint", "--help"],
    ),
    ("sharedrive checkout --help", ["checkout", "--help"]),
    ("sharedrive set --help", ["set", "--help"]),
    ("sharedrive add --help", ["add", "--help"]),
    ("sharedrive update --help", ["update", "--help"]),
]

API_MODULES = [
    {"module": "sharedrive.helpers", "path": ROOT / "sharedrive" / "helpers.py"},
    {"module": "sharedrive.models", "path": ROOT / "sharedrive" / "models.py"},
    {"module": "sharedrive.item", "path": ROOT / "sharedrive" / "item.py"},
    {
        "module": "sharedrive.clients.aws",
        "path": ROOT / "sharedrive" / "clients" / "aws.py",
    },
    {
        "module": "sharedrive.clients.googledrive",
        "path": ROOT / "sharedrive" / "clients" / "googledrive.py",
    },
    {
        "module": "sharedrive.auth.google",
        "path": ROOT / "sharedrive" / "auth" / "google.py",
    },
    {
        "module": "sharedrive.auth.microsoft",
        "path": ROOT / "sharedrive" / "auth" / "microsoft.py",
    },
    {
        "module": "sharedrive.auth.token_store",
        "path": ROOT / "sharedrive" / "auth" / "token_store.py",
    },
    {
        "module": "sharedrive.auth.settings",
        "path": ROOT / "sharedrive" / "auth" / "settings.py",
    },
    {
        "module": "sharedrive.clients.sharepoint",
        "path": ROOT / "sharedrive" / "clients" / "sharepoint.py",
    },
]


def _normalize_help_text(text: str) -> str:
    normalized = ANSI_PATTERN.sub("", text).replace("\r\n", "\n")
    normalized = normalized.encode("ascii", "ignore").decode()
    lines = [line.rstrip() for line in normalized.splitlines()]
    return "\n".join(lines).strip() + "\n"


def _run_help(args: list[str]) -> str:
    app.rich_markup_mode = None
    rich_utils.FORCE_TERMINAL = False
    rich_utils.COLOR_SYSTEM = None
    rich_utils.MAX_WIDTH = CLI_WIDTH
    result = RUNNER.invoke(app, args, prog_name="sharedrive", color=False)
    if result.exit_code != 0:
        raise RuntimeError(
            f"Failed: {' '.join(['sharedrive', *args])}\n{result.stdout}"
        )
    return _normalize_help_text(result.stdout)


def _render_cli_markdown() -> str:
    lines = ["# CLI Reference", ""]
    lines.append("Auto-generated from live `sharedrive --help` output.")
    lines.append("")
    for title, args in CLI_COMMANDS:
        lines.append(f"## `{title}`")
        lines.append("")
        lines.append("```text")
        lines.append(_run_help(args).rstrip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _top_level_maps(
    module_ast: ast.Module,
) -> tuple[
    dict[str, ast.FunctionDef],
    dict[str, ast.ClassDef],
    dict[str, ast.Assign | ast.AnnAssign],
    dict[str, ast.expr],
    dict[str, str],
]:
    top_functions = {
        item.name: item for item in module_ast.body if isinstance(item, ast.FunctionDef)
    }
    top_classes = {
        item.name: item for item in module_ast.body if isinstance(item, ast.ClassDef)
    }
    top_assignments: dict[str, ast.Assign | ast.AnnAssign] = {}
    aliases: dict[str, ast.expr] = {}
    imported_modules: dict[str, str] = {}

    for item in module_ast.body:
        if isinstance(item, ast.Import):
            for import_alias in item.names:
                bind_name = import_alias.asname or import_alias.name
                imported_modules[bind_name] = import_alias.name
        elif isinstance(item, ast.ImportFrom):
            if item.module is None:
                continue
            for import_alias in item.names:
                bind_name = import_alias.asname or import_alias.name
                imported_modules[bind_name] = f"{item.module}.{import_alias.name}"
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    top_assignments[target.id] = item
                    aliases[target.id] = item.value
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            top_assignments[item.target.id] = item
            if item.value is not None:
                aliases[item.target.id] = item.value

    return top_functions, top_classes, top_assignments, aliases, imported_modules


def _resolve_module_alias_target(
    expr: ast.expr, imported_modules: dict[str, str]
) -> tuple[str, str] | None:
    if isinstance(expr, ast.Name):
        target_module = imported_modules.get(expr.id)
        if target_module:
            return target_module, expr.id
        return None

    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        target_module = imported_modules.get(expr.value.id)
        if target_module:
            return target_module, expr.attr
    return None


def _resolve_exported_name(
    exported_name: str,
    *,
    top_functions: dict[str, ast.FunctionDef],
    top_classes: dict[str, ast.ClassDef],
    top_assignments: dict[str, ast.Assign | ast.AnnAssign],
    aliases: dict[str, ast.expr],
    imported_modules: dict[str, str],
    module_map: dict[str, Path],
) -> tuple[str, str, ast.AST] | None:
    if exported_name in top_functions:
        return ("function", exported_name, top_functions[exported_name])
    if exported_name in top_classes:
        return ("class", exported_name, top_classes[exported_name])

    alias_expr = aliases.get(exported_name)
    if alias_expr is None:
        if exported_name in top_assignments:
            return ("constant", exported_name, top_assignments[exported_name])
        return None

    if isinstance(alias_expr, ast.Name):
        target_name = alias_expr.id
        if target_name in top_functions:
            return ("function", exported_name, top_functions[target_name])
        if target_name in top_classes:
            return ("class", exported_name, top_classes[target_name])
        if target_name in top_assignments:
            return ("constant", exported_name, top_assignments[exported_name])

    resolved_module_target = _resolve_module_alias_target(alias_expr, imported_modules)
    if resolved_module_target is not None:
        target_module, target_name = resolved_module_target
        target_path = module_map.get(target_module)
        if target_path is not None:
            target_ast = _parse_module(target_path)
            (
                target_functions,
                target_classes,
                target_assignments,
                target_aliases,
                target_imports,
            ) = _top_level_maps(target_ast)
            resolved = _resolve_exported_name(
                target_name,
                top_functions=target_functions,
                top_classes=target_classes,
                top_assignments=target_assignments,
                aliases=target_aliases,
                imported_modules=target_imports,
                module_map=module_map,
            )
            if resolved is not None:
                resolved_kind, _resolved_name, resolved_node = resolved
                return (resolved_kind, exported_name, resolved_node)

    if exported_name in top_assignments:
        return ("constant", exported_name, top_assignments[exported_name])
    return None


def _parse_dunder_all(module_ast: ast.Module) -> list[str]:
    for item in module_ast.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if not isinstance(target, ast.Name) or target.id != "__all__":
                continue
            try:
                value = ast.literal_eval(item.value)
            except Exception as exc:
                raise ValueError("Could not statically evaluate __all__") from exc
            if not isinstance(value, (list, tuple)):
                raise ValueError("__all__ must be a list or tuple of strings")
            if not all(isinstance(entry, str) for entry in value):
                raise ValueError("__all__ entries must all be strings")
            return list(value)
    raise ValueError("Module is missing __all__")


def _doc_first_line(node: ast.AST) -> str:
    doc = ast.get_docstring(node)
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def _ann_to_str(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _format_signature(node: ast.FunctionDef, *, name: str | None = None) -> str:
    args = node.args
    parts: list[str] = []

    pos_args = args.posonlyargs + args.args
    defaults = args.defaults
    default_start = len(pos_args) - len(defaults)

    for idx, arg in enumerate(pos_args):
        piece = arg.arg
        ann = _ann_to_str(arg.annotation)
        if ann:
            piece += f": {ann}"
        if idx >= default_start and default_start >= 0:
            piece += f" = {_ann_to_str(defaults[idx - default_start])}"
        parts.append(piece)

    if args.posonlyargs:
        parts.insert(len(args.posonlyargs), "/")

    if args.vararg:
        var_piece = f"*{args.vararg.arg}"
        ann = _ann_to_str(args.vararg.annotation)
        if ann:
            var_piece += f": {ann}"
        parts.append(var_piece)
    elif args.kwonlyargs:
        parts.append("*")

    for kwarg, kw_default in zip(args.kwonlyargs, args.kw_defaults):
        piece = kwarg.arg
        ann = _ann_to_str(kwarg.annotation)
        if ann:
            piece += f": {ann}"
        if kw_default is not None:
            piece += f" = {_ann_to_str(kw_default)}"
        parts.append(piece)

    if args.kwarg:
        kw_piece = f"**{args.kwarg.arg}"
        ann = _ann_to_str(args.kwarg.annotation)
        if ann:
            kw_piece += f": {ann}"
        parts.append(kw_piece)

    sig = f"def {name or node.name}({', '.join(parts)})"
    ret = _ann_to_str(node.returns)
    if ret:
        sig += f" -> {ret}"
    return sig


def _format_assignment_signature(name: str, node: ast.Assign | ast.AnnAssign) -> str:
    annotation = None
    value = None
    if isinstance(node, ast.AnnAssign):
        annotation = _ann_to_str(node.annotation)
        value = node.value
    else:
        value = node.value

    signature = name
    if annotation:
        signature += f": {annotation}"
    if value is not None:
        rendered_value = _ann_to_str(value)
        if rendered_value:
            signature += f" = {rendered_value}"
    return signature


def _class_fields(node: ast.ClassDef) -> list[str]:
    fields: list[str] = []
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            ann = _ann_to_str(item.annotation)
            if ann:
                fields.append(f"{item.target.id}: {ann}")
    return fields


def _public_class_methods(node: ast.ClassDef) -> list[ast.FunctionDef]:
    methods: list[ast.FunctionDef] = []
    for item in node.body:
        if not isinstance(item, ast.FunctionDef):
            continue
        if item.name == "__init__":
            continue
        if item.name.startswith("__") and item.name.endswith("__"):
            continue
        if item.name.startswith("_"):
            continue
        methods.append(item)
    return methods


def _render_api_markdown() -> str:
    lines = ["# Python API", ""]
    lines.append("Auto-generated from source signatures and docstrings.")
    lines.append("")

    for module_spec in API_MODULES:
        module_name = module_spec["module"]
        module_ast = _parse_module(module_spec["path"])
        top_functions, top_classes, top_assignments, aliases, imported_modules = (
            _top_level_maps(module_ast)
        )
        exported_names = _parse_dunder_all(module_ast)
        module_map = {spec["module"]: spec["path"] for spec in API_MODULES}

        constants: list[tuple[str, ast.Assign | ast.AnnAssign]] = []
        functions: list[tuple[str, ast.FunctionDef]] = []
        classes: list[tuple[str, ast.ClassDef]] = []

        for exported_name in exported_names:
            resolved = _resolve_exported_name(
                exported_name,
                top_functions=top_functions,
                top_classes=top_classes,
                top_assignments=top_assignments,
                aliases=aliases,
                imported_modules=imported_modules,
                module_map=module_map,
            )
            if resolved is None:
                continue
            resolved_kind, resolved_name, resolved_node = resolved
            if resolved_kind == "function" and isinstance(
                resolved_node, ast.FunctionDef
            ):
                functions.append((resolved_name, resolved_node))
            elif resolved_kind == "class" and isinstance(resolved_node, ast.ClassDef):
                classes.append((resolved_name, resolved_node))
            elif resolved_kind == "constant" and isinstance(
                resolved_node, (ast.Assign, ast.AnnAssign)
            ):
                constants.append((resolved_name, resolved_node))

        lines.append(f"## `{module_name}`")
        lines.append("")

        if constants:
            lines.append("### Constants")
            lines.append("")
            for name, node in constants:
                lines.append(f"- `{_format_assignment_signature(name, node)}`")
            lines.append("")

        if functions:
            lines.append("### Functions")
            lines.append("")
            for name, node in functions:
                lines.append(f"- `{_format_signature(node, name=name)}`")
                doc = _doc_first_line(node)
                if doc:
                    lines.append(f"  - {doc}")
            lines.append("")

        if classes:
            lines.append("### Classes")
            lines.append("")
            for class_name, class_node in classes:
                lines.append(f"#### `{class_name}`")
                class_doc = _doc_first_line(class_node)
                if class_doc:
                    lines.append(f"- {class_doc}")
                fields = _class_fields(class_node)
                if fields:
                    lines.append("- Fields:")
                    for field in fields:
                        lines.append(f"  - `{field}`")
                method_nodes = _public_class_methods(class_node)
                if method_nodes:
                    lines.append("- Methods:")
                    for method_node in method_nodes:
                        lines.append(f"  - `{_format_signature(method_node)}`")
                        method_doc = _doc_first_line(method_node)
                        if method_doc:
                            lines.append(f"    - {method_doc}")
                lines.append("")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    docs_dir = ROOT / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "cli.md").write_text(_render_cli_markdown(), encoding="utf-8")
    (docs_dir / "api.md").write_text(_render_api_markdown(), encoding="utf-8")
    print(f"Updated {docs_dir / 'cli.md'}")
    print(f"Updated {docs_dir / 'api.md'}")


if __name__ == "__main__":
    main()
