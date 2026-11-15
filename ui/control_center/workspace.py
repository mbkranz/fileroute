"""Thin adapters for the pinned Fileroute CLI and Python API."""

from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import subprocess
import sys
from collections import Counter
from contextlib import closing
from difflib import unified_diff
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from typer._completion_shared import get_completion_script, install

from fileroute.auth import profiles
from fileroute.auth.describe import AuthDescribeArgs, describe_auth
from fileroute.commands.config import ACTIVE_DESCRIPTOR, set_active_descriptor
from fileroute.commands.descriptor import _add_resource_to_descriptor
from fileroute.commands.toolkit import coerce_field_value
from fileroute.descriptor import (
    _pointer_json_path,
    find,
    load,
    local_path,
    resolve,
    resolve_selection,
    save,
    select,
    walk,
)
from fileroute.diagram import build_graph, load_graph, render_mermaid, render_svg
from fileroute.diagram_reports import render_html, render_markdown
from fileroute.discovery import _prepare_discovery, discover_resources
from fileroute.migration import migrate
from fileroute.models import Catalog, Location, Resource
from fileroute.resolution import parse_location
from fileroute.transfer import plan_pull, plan_push, pull, push

from .contracts import AUTHORED_MODELS


def authored(model):
    return model.model_dump(mode="json", by_alias=True, exclude_unset=True)


def catalog_relationships(doc, entries):
    """Give Fileroute's graph edges the catalog selectors used by the inspector."""
    by_selector = {row["selector"]: row for row in entries}
    keys = {"$": "catalog:root"}
    for row in entries[1:]:
        keys[row["selector"]] = f"{keys[row['parent']]}/{row['kind']}:{row['name']}"
    rows = {key: by_selector[selector] for selector, key in keys.items()}
    locations = {
        row["selector"]: {
            field: [
                loc["selector"]
                for loc in row["locations"]
                if loc["pointer"].startswith(f"{row['pointer']}/{field}/")
            ]
            for field in ("sources", "targets")
        }
        for row in entries
    }
    counts = Counter()
    relationships = []
    for edge in build_graph(doc).edges:
        if edge.kind == "contains":
            source = rows[edge.source]["selector"]
            target = rows[edge.target]["selector"]
        elif edge.kind == "source":
            target = rows[edge.target]["selector"]
            source = locations[target]["sources"][counts["source", target]]
            counts["source", target] += 1
        else:
            source = rows[edge.source]["selector"]
            owner = rows[edge.inherited_from]["selector"] if edge.inherited_from else source
            target = locations[owner]["targets"][counts["target", source]]
            counts["target", source] += 1
        relationships.append(
            {"from": source, "to": target, "kind": edge.kind, "inherited": bool(edge.inherited_from)}
        )
    return relationships


class Workspace:
    def __init__(self, root: Path, descriptor: str | None = None):
        self.root = root.resolve()
        self.default_descriptor = descriptor
        self.previews: dict[str, tuple[dict, dict]] = {}
        self.discoveries: dict[str, tuple[Path, str | None, Catalog]] = {}
        self.operations = {
            "catalog.open": self.catalog,
            "list": self.list_catalog,
            "activate": self.activate,
            "add": self.add,
            "update": self.update,
            "resolve": self.resolve,
            "discovery.preview": self.discovery_preview,
            "discovery.run": self.discovery_run,
            "discovery.save": self.discovery_save,
            "transfer.preview": self.transfer_preview,
            "transfer.run": self.transfer_run,
            "diagram": self.diagram,
            "clone.descriptor": self.clone,
            "migrate": self.migrate,
            "auth.describe": self.auth_describe,
            "auth.profile.list": self.profile_list,
            "auth.profile.show": self.profile_show,
            "auth.profile.save": self.profile_save,
            "auth.profile.remove": self.profile_remove,
            "auth.check": self.auth_check,
            "cli.help": self.help,
            "cli.completion.show": self.completion,
            "cli.completion.install": self.completion_install,
        }

    def path(self, value: str | Path) -> Path:
        return local_path(str(value), self.root, reject_symlinks=True)

    def descriptor(self, value: str | Path | None = None) -> Path:
        if value is not None:
            return self.path(value)
        if self.default_descriptor:
            return self.path(self.default_descriptor)
        # The server starts in the fixed project directory; match the CLI's selection.
        from fileroute.commands.toolkit import prepare_descriptor_path

        return self.path(prepare_descriptor_path(require_exists=False))

    def label(self, value: Path | str | None) -> str:
        if value is None:
            return ""
        path = Path(value)
        return (
            str(path.relative_to(self.root))
            if path.is_relative_to(self.root)
            else str(path)
        )

    def scoped_command(self, command, path, selector=None, profile=None):
        args = ["fileroute", command, self.label(path)]
        if selector:
            args += ["--select", selector]
        if profile:
            args += ["--profile", profile]
        return args

    def revision(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def require_revision(self, path: Path, revision: str | None):
        if not revision or self.revision(path) != revision:
            raise ValueError(
                "Review a fresh preview before writing. The descriptor may have changed."
            )

    def invoke(self, operation: str, args: dict) -> dict:
        if operation.startswith("auth.login."):
            return self.login(operation.rsplit(".", 1)[1], args)
        return self.operations[operation](**args)

    def auth_describe(self, *, config_path=None, **arguments):
        cli = ["fileroute", "auth", "describe"]
        for key, value in arguments.items():
            if key == "provider":
                cli.insert(3, str(value))
            elif key != "url":
                cli.extend(("--select" if key == "selector" else "--" + key, str(value)))
        if arguments.get("operation"):
            path = self.descriptor(arguments.get("descriptor"))
            if not arguments.get("descriptor"):
                cli.extend(("--descriptor", self.label(path)))
            arguments["descriptor"] = path
        result = describe_auth(AuthDescribeArgs(**arguments), config_path=config_path)
        for route in result.routes:
            if route.origin_descriptor:
                route.origin_descriptor = Path(self.label(route.origin_descriptor))
        if arguments.get("url") and result.routes:
            cli.extend(("--url", result.routes[0].url))
        return {"kind": "auth-description", "title": "Authentication guide",
                "command": shlex.join(cli), "workingRoot": str(self.root),
                **result.model_dump(mode="json")}

    def catalog(self, descriptor=None):
        path = self.descriptor(descriptor)
        doc = load(path, resolve_references=True)
        rows = list(walk(doc, include_self=True))
        by_pointer = {row.json_pointer: row for row in rows}
        revisions = {
            origin: self.revision(origin)
            for origin in {row.origin_descriptor for row in rows}
        }
        authored_files = {}
        entries = []
        for row in rows:
            selected = select(doc, row.json_path)
            entity = selected.as_dict()
            entity["originDescriptor"] = self.label(row.origin_descriptor)
            entity["referenceChain"] = [self.label(p) for p in row.reference_chain]
            entity["selector"] = row.json_path
            entity["pointer"] = row.json_pointer
            entity["originPointer"] = row.origin_pointer
            entity["parent"] = _pointer_json_path(row.json_pointer.rsplit("/", 2)[0])
            entity["linked"] = len(row.reference_chain) > 1 and not row.origin_pointer
            entity["revision"] = revisions[row.origin_descriptor]
            if entity["linked"]:
                parent = by_pointer[row.json_pointer.rsplit("/", 2)[0]]
                suffix = row.json_pointer.removeprefix(parent.json_pointer)
                pointer = parent.origin_pointer + suffix
                if parent.origin_descriptor not in authored_files:
                    authored_files[parent.origin_descriptor] = load(parent.origin_descriptor)
                entity["link"] = {
                    "originDescriptor": self.label(parent.origin_descriptor),
                    "originSelector": _pointer_json_path(pointer),
                    "originPointer": pointer,
                    "revision": revisions[parent.origin_descriptor],
                    "entity": authored(find(authored_files[parent.origin_descriptor], pointer)),
                }
            ancestor = row.json_pointer
            declaring = None
            while True:
                candidate = by_pointer[ancestor]
                if getattr(candidate.model, "targets", None) is not None:
                    declaring = candidate
                    break
                if not ancestor:
                    break
                ancestor = ancestor.rsplit("/", 2)[0]
            entity["targetOrigin"] = (
                {
                    "descriptor": self.label(declaring.origin_descriptor),
                    "selector": declaring.json_path,
                }
                if declaring
                else None
            )
            content = entity["entity"]
            # Child records have independent origins and are represented by their own rows.
            if entity["kind"] == "catalog":
                entity["entity"] = {
                    k: v
                    for k, v in content.items()
                    if k not in {"catalogs", "resources"}
                }
            entity["locations"] = []
            for field, locations in (
                ("sources", getattr(row.model, "sources", [])),
                ("targets", getattr(row.model, "targets", None) or []),
            ):
                for index, location in enumerate(locations):
                    pointer = f"{row.json_pointer}/{field}/{index}"
                    origin = select(doc, pointer)
                    record = origin.as_dict()
                    record.update(
                        {
                            "originDescriptor": self.label(row.origin_descriptor),
                            "referenceChain": [
                                self.label(p) for p in row.reference_chain
                            ],
                            "selector": _pointer_json_path(pointer),
                            "pointer": pointer,
                            "originPointer": origin.origin_pointer,
                            "parent": row.json_path,
                            "revision": entity["revision"],
                            "kind": "location",
                            "name": f"{field}[{index}]",
                            "artifactPath": location.path,
                        }
                    )
                    record["provider"] = self.location_provider(location)
                    entity["locations"].append(record)
            entries.append(entity)
        active = self.path(ACTIVE_DESCRIPTOR)
        saved = self.label(active.read_text().strip()) if active.is_file() else None
        return {
            "descriptor": self.label(path),
            "activeDescriptor": saved,
            "entries": entries,
            "relationships": catalog_relationships(doc, entries),
        }

    @staticmethod
    def location_provider(location: Location):
        try:
            parsed = parse_location(location.model_copy(deep=True), required=False)
            return str(parsed.service_type) if parsed.service_type else None
        except ValueError:
            return None

    def list_catalog(self, descriptor=None, selector=None, kind="all", format="text"):
        catalog = self.catalog(descriptor)
        rows = catalog["entries"]
        all_rows = [item for row in rows for item in (row, *row["locations"])]
        if selector and selector != "$":
            path = self.descriptor(descriptor)
            scope = select(load(path, resolve_references=True), selector)
            pointer = scope.entry.json_pointer
            if isinstance(scope.model, Location):
                suffix = scope.origin_pointer.removeprefix(scope.entry.origin_pointer)
                pointer += suffix
            all_rows = [
                row
                for row in all_rows
                if row["pointer"] == pointer or row["pointer"].startswith(pointer + "/")
            ]
        if kind != "all":
            all_rows = [row for row in all_rows if row["kind"] == kind]
        content = "\n".join(
            f"{row['selector']}\t{row['kind']}\t{row['artifactPath'] or ''}"
            for row in all_rows
        )
        if format == "json":
            content = json.dumps(all_rows, indent=2, ensure_ascii=False)
        command = self.scoped_command("list", catalog["descriptor"], selector)
        if kind != "all":
            command += ["--kind", kind]
        if format != "text":
            command += ["--format", format]
        return {
            "kind": "export",
            "title": "Catalog inspection",
            "format": format,
            "content": content,
            "count": len(all_rows),
            "command": shlex.join(command),
            "workingRoot": str(self.root),
        }

    def activate(self, descriptor):
        path = self.descriptor(descriptor)
        load(path)
        self.path(ACTIVE_DESCRIPTOR)
        set_active_descriptor(path)
        return {"title": "Active descriptor saved", "descriptor": self.label(path)}

    def add(
        self,
        name,
        kind,
        entry,
        descriptor=None,
        parent=None,
        extensions=None,
        dry_run=True,
    ):
        path = self.descriptor(descriptor)
        physical_selector = parent
        if parent and path.is_file():
            scope = select(load(path, resolve_references=True), parent)
            path = scope.entry.origin_descriptor
            physical_selector = _pointer_json_path(scope.origin_pointer)
        data = {**authored(entry), **(extensions or {})}
        model = AUTHORED_MODELS[kind].model_validate(data)
        if kind == "link":
            from fileroute.models import validate_name

            name = name.strip()
            validate_name(name)
            document = load(path) if path.is_file() else Catalog()
            node = find(document, physical_selector) if physical_selector else document
            if not isinstance(node, Catalog):
                raise ValueError("Select a catalog as the parent.")
            if any(
                key.casefold() == name.casefold()
                for key in (*node.catalogs, *node.resources)
            ):
                raise ValueError(f"An entry named {name} already exists.")
            child = local_path(model.descriptor, path.parent)
            linked = load(child, resolve_references=True)
            if path in {
                row.origin_descriptor for row in walk(linked, include_self=True)
            }:
                raise ValueError("This catalog link would create a reference cycle.")
            node.catalogs[name] = model
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                save(document, path)
        else:
            with TemporaryDirectory() as tmp:
                destination = path
                if dry_run:
                    destination = Path(tmp) / path.name
                    if path.is_file():
                        shutil.copyfile(path, destination)
                _add_resource_to_descriptor(
                    destination,
                    name=name,
                    create_if_missing=True,
                    catalog=kind == "catalog",
                    parent=physical_selector,
                    **authored(model),
                )
        return {
            "kind": "diff",
            "title": "Entry preview" if dry_run else "Entry added",
            "origin": self.label(path),
            "before": {},
            "after": {name: authored(model)},
            "revision": self.revision(path) if path.is_file() else None,
        }

    def update(
        self,
        kind,
        fields,
        descriptor=None,
        selector=None,
        extensions=None,
        unset=None,
        dry_run=True,
        revision=None,
    ):
        path = self.descriptor(descriptor)
        expanded = load(path, resolve_references=True)
        selected = select(expanded, selector or "$")
        origin = selected.entry.origin_descriptor
        # A CatalogLink is authored in its parent file, even when that link expands.
        pointer = selected.origin_pointer
        if kind == "link":
            parent = selected.entry.json_pointer.rsplit("/", 2)[0]
            context = select(expanded, _pointer_json_path(parent))
            origin = context.entry.origin_descriptor
            pointer = context.origin_pointer + selected.entry.json_pointer.removeprefix(
                parent
            )
        origin = self.path(origin)
        original = origin.read_bytes()
        reviewed_revision = hashlib.sha256(original).hexdigest()
        if not dry_run:
            self.require_revision(origin, revision)
        source = load(origin)
        subject = find(source, pointer or "$")
        model = AUTHORED_MODELS[kind]
        if not isinstance(subject, model):
            raise ValueError("Choose the authored type of the selected entry.")
        before = authored(subject)
        changes = {**authored(fields), **(extensions or {})}
        removed = set(unset or ())
        if changes.keys() & removed:
            raise ValueError("A field can be updated or omitted in the same edit.")
        proposed = model.model_validate(
            {key: value for key, value in (before | changes).items() if key not in removed}
        )
        after = authored(proposed)
        patch = ""
        if before != after:
            if kind == "link" and before.get("descriptor") != after["descriptor"]:
                linked = load(local_path(after["descriptor"], origin.parent), resolve_references=True)
                if any(row.origin_descriptor == origin for row in walk(linked, include_self=True)):
                    raise ValueError("This catalog link would create a reference cycle.")
            if not pointer:
                source = proposed
            else:
                document = authored(source)
                tokens = [
                    token.replace("~1", "/").replace("~0", "~")
                    for token in pointer.lstrip("/").split("/")
                ]
                node = document
                for token in tokens[:-1]:
                    node = node[int(token)] if isinstance(node, list) else node[token]
                key = int(tokens[-1]) if isinstance(node, list) else tokens[-1]
                node[key] = after
                source = Catalog.model_validate(document)
            with TemporaryDirectory() as temp:
                candidate = Path(temp) / origin.name
                candidate.write_bytes(original)
                save(source, candidate)
                proposed_bytes = candidate.read_bytes()
            label = self.label(origin)
            patch = "".join(
                unified_diff(
                    original.decode().splitlines(keepends=True),
                    proposed_bytes.decode().splitlines(keepends=True),
                    fromfile=f"a/{label}",
                    tofile=f"b/{label}",
                )
            )
            if not dry_run:
                self.require_revision(origin, reviewed_revision)
                save(source, origin)
        command = None
        # The CLI's field parser coerces scalar strings. Offer a command only
        # when it preserves the exact value, and scope it to the physical file.
        if patch and not removed and changes.keys() <= {"title", "description", "path"}:
            fields_cli = [(key, json.dumps(val) if not isinstance(val, str) else val)
                          for key, val in changes.items()]
            if all(coerce_field_value(val) == changes[key] for key, val in fields_cli):
                command = shlex.join([
                    "fileroute", "update", "--descriptor", self.label(origin),
                    "--select", _pointer_json_path(pointer), "--dry-run",
                    *(f"--{key}={val}" for key, val in fields_cli),
                ])
        return {
            "kind": "diff",
            "title": "Edit preview" if dry_run else "Descriptor updated",
            "origin": self.label(origin),
            "before": before,
            "after": after,
            "revision": reviewed_revision if dry_run else self.revision(origin),
            "originSelector": _pointer_json_path(pointer),
            "patch": patch,
            "command": command,
            "workingRoot": str(self.root),
        }

    def resolve(
        self,
        descriptor=None,
        selector=None,
        online=False,
        profile=None,
        write=False,
        revision=None,
    ):
        if profile and not online:
            raise ValueError("Default profiles apply to online resolution.")
        path = self.descriptor(descriptor)
        if selector:
            previous = select(load(path, resolve_references=True), selector)
            origin = previous.entry.origin_descriptor
            if write:
                self.require_revision(origin, revision)
            changed = resolve_selection(
                path, selector, online=online, profile=profile, write=write
            )
            before, after = authored(previous.model), authored(changed.model)
        else:
            origin = path
            document = load(path)
            if write:
                self.require_revision(origin, revision)
            changed = resolve(document, online=online, profile=profile)
            before, after = authored(document), authored(changed)
            if write:
                save(changed, origin)
        command = self.scoped_command("resolve", path, selector, profile)
        if online:
            command.append("--online")
        if write:
            command.append("--write")
        return {
            "kind": "diff",
            "title": "Resolution saved" if write else "Resolution preview",
            "origin": self.label(origin),
            "before": before,
            "after": after,
            "revision": self.revision(origin),
            "verification": "Online metadata check" if online else "Offline inference",
            "command": shlex.join(command),
            "workingRoot": str(self.root),
        }

    def discovery_preview(self, descriptor=None, selector=None, profile=None):
        path = self.descriptor(descriptor)
        sources = _prepare_discovery(path, self.root, selector)
        rows = [
            {
                "root": row.location.path,
                "template": row.location.resource_path_template,
                "local": self.label(row.local),
                "provider": row.service,
                "profile": row.location.auth_profile or profile or "Provider default",
            }
            for row in sources
        ]
        command = self.scoped_command("discover", path, selector, profile)
        return {
            "kind": "discovery", "title": "Discovery scope", "rows": rows,
            "command": shlex.join([*command, "--dry-run"]),
            "workingRoot": str(self.root),
        }

    def discovery_run(
        self, descriptor=None, selector=None, profile=None, format="text", output=None
    ):
        path = self.descriptor(descriptor)
        if output:
            _prepare_discovery(path, self.root, selector, output=self.path(output))
        result = discover_resources(
            path, root=self.root, selector=selector, profile=profile
        )
        result_id = uuid4().hex
        self.discoveries[result_id] = (path, selector, result)
        rows = [
            {
                "local": row.model.path,
                "captures": row.model.path_properties,
                "remote": row.model.sources[0].path,
            }
            for row in walk(result)
            if isinstance(row.model, Resource)
        ]
        response = {
            "kind": "matches",
            "title": "Discovery matches",
            "rows": rows,
            "result_id": result_id,
            "suggestedOutput": str(output) if output else None,
        }
        if format == "json":
            response["content"] = json.dumps(authored(result), indent=2)
        return response

    def discovery_save(self, result_id, output):
        if result_id not in self.discoveries:
            raise ValueError("Run discovery and review its results first.")
        descriptor, selector, result = self.discoveries[result_id]
        target = self.path(output)
        _prepare_discovery(descriptor, self.root, selector, output=target)
        for row in walk(result):
            if isinstance(row.model, Resource):
                local = self.path(row.model.path)
                if target.is_relative_to(local) or local.is_relative_to(target):
                    raise ValueError("Discovery output overlaps a pull destination.")
        save(result, target)
        return {"title": "Discovered catalog saved", "descriptor": self.label(target)}

    def _transfer(self, direction, descriptor=None, selector=None, profile=None):
        path = self.descriptor(descriptor)
        selector = None if selector == "$" else selector
        entries = {"pull": plan_pull, "push": plan_push}[direction](
            path, root=self.root, selector=selector
        )
        rows = []
        for entry in entries:
            existing = entry.local.stat() if entry.local.exists() else None
            rows.append(
                {
                    "from": entry.remote
                    if direction == "pull"
                    else self.label(entry.local),
                    "to": self.label(entry.local)
                    if direction == "pull"
                    else entry.destination,
                    "provider": entry.service_type,
                    "profile": getattr(entry.location, "auth_profile", None)
                    or profile
                    or "Provider default",
                    "localState": [existing.st_mtime_ns, existing.st_size]
                    if existing
                    else None,
                    "locator": authored(entry.location) if entry.location else None,
                    "directory": getattr(entry, "directory", False),
                }
            )
        command = self.scoped_command(direction, path, selector, profile)
        return entries, {
            "kind": "transfer",
            "title": "Download preview" if direction == "pull" else "Publish preview",
            "rows": rows,
            "command": shlex.join([*command, "--dry-run"]),
            "workingRoot": str(self.root),
            "verification": "Remote state and permissions unverified",
        }

    def transfer_preview(self, **arguments):
        arguments["descriptor"] = self.descriptor(arguments.get("descriptor"))
        _, payload = self._transfer(**arguments)
        key = uuid4().hex
        self.previews[key] = (arguments, payload)
        return {**payload, "preview_id": key}

    def transfer_run(self, preview_id):
        if preview_id not in self.previews:
            raise ValueError(
                "Create and review a transfer preview in this session first."
            )
        arguments, reviewed = self.previews[preview_id]
        entries, current = self._transfer(**arguments)
        if current != reviewed:
            raise ValueError(
                "The transfer plan or local files changed. Review a fresh preview before running."
            )
        action = {"pull": pull, "push": push}[arguments["direction"]]
        action(entries, profile=arguments.get("profile"))
        return {
            "kind": "transfer-result",
            "title": "Transfer completed",
            "rows": current["rows"],
        }

    def diagram(self, format, descriptor=None, detail="summary", output=None):
        path = self.descriptor(descriptor)
        graph = load_graph(path)
        renderer, suffix = {
            "svg": (render_svg, ".svg"),
            "html": (render_html, ".html"),
            "markdown": (render_markdown, ".md"),
            "mermaid": (render_mermaid, ".mmd"),
        }[format]
        with TemporaryDirectory() as temp:
            target = (
                self.path(output)
                if output
                else Path(temp) / f"fileroute-diagram{suffix}"
            )
            if output:
                generated = (
                    [target, target.with_suffix(".svg")]
                    if format == "markdown"
                    else [target]
                )
                for candidate in generated:
                    self.path(candidate)
                origins = {
                    row.origin_descriptor
                    for row in walk(
                        load(path, resolve_references=True), include_self=True
                    )
                }
                if any(candidate in origins for candidate in generated):
                    raise ValueError(
                        "Choose a diagram output distinct from the source descriptors."
                    )
            result = (
                renderer(graph, target, detail=detail)
                if format in {"html", "markdown"}
                else renderer(graph, target)
            )
            companion = result.with_suffix(".svg")
            return {
                "kind": "diagram",
                "title": "Descriptor diagram",
                "format": format,
                "content": result.read_text(),
                "filename": result.name,
                "companion": companion.read_text() if format == "markdown" else None,
                "nodes": len(graph.nodes),
                "edges": len(graph.edges),
            }

    def offline_cli(self, command):
        result = subprocess.run(
            [sys.executable, "-m", "fileroute.cli", *command],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if result.returncode:
            raise ValueError((result.stderr or result.stdout).strip())
        return result.stdout

    def clone(self, target_path, descriptor=None, dry_run=True, force=False):
        source = self.descriptor(descriptor)
        target = self.path(target_path)
        command = ["clone", "descriptor", str(target), "--descriptor", str(source)]
        if dry_run:
            command.append("--dry-run")
        if force:
            command.append("--force")
        output = self.offline_cli(command)
        return {
            "title": "Clone preview" if dry_run else "Descriptor cloned",
            "content": output,
            "origin": self.label(source),
        }

    def migrate(self, descriptor, output, dry_run=True):
        rows = migrate(self.path(descriptor), self.path(output), dry_run=dry_run)
        for row in rows:
            row["source"], row["output"] = (
                self.label(row["source"]),
                self.label(row["output"]),
            )
        return {
            "kind": "migration",
            "title": "Migration preview" if dry_run else "Migration completed",
            "rows": rows,
        }

    def profile_file(self, config):
        return self.path(config) if config else None

    def profile_list(self, config=None):
        configured = profiles.load_profiles(
            config_path=self.profile_file(config)
        ).profiles
        return {
            "kind": "profiles",
            "title": "Configured profiles",
            "rows": [
                {"name": name, "provider": profile.provider, "mode": profile.mode}
                for name, profile in sorted(configured.items())
            ],
        }

    def profile_show(self, name, config=None):
        profile = profiles.get_profile(name, config_path=self.profile_file(config))
        return {
            "title": f"Profile {name}",
            "settings": profile.model_dump(mode="json", exclude_none=True),
        }

    def profile_save(self, name, settings, config=None, replace=False):
        overrides = {
            field: (self.root / value.expanduser()).absolute()
            for field in ("credentials_file", "oauth_client_secrets")
            if (value := getattr(settings, field)) is not None
        }
        receipt = profiles.save_profile(
            name,
            settings.model_copy(update=overrides),
            config_path=self.profile_file(config),
            replace=replace,
        )
        return {"title": "Profile saved", "receipt": receipt.model_dump(mode="json")}

    def profile_remove(self, name, config=None):
        receipt = profiles.remove_profile(name, config_path=self.profile_file(config))
        return {
            "title": "Profile and Fileroute token cache removed",
            "receipt": receipt.model_dump(mode="json"),
        }

    def auth_check(self, profile, url=None, config=None):
        config_path = self.profile_file(config)
        if url is None:
            receipt = profiles.authenticate_profile(profile, config_path=config_path)
            return {
                "title": "Credentials checked",
                "receipt": receipt.model_dump(mode="json"),
            }
        selected = profiles.get_profile(profile, config_path=config_path)
        location = parse_location(Location(path=url), required=True)
        if location.service_type != selected.provider:
            raise ValueError("This URL's provider does not match the profile.")
        with closing(
            profiles.build_profile_client(
                profile, selected.provider, config_path=config_path
            )
        ) as client:
            client.resolve_location(location)
        return {"title": "Read access checked", "profile": profile, "url": url}

    def login(self, provider, args):
        command = ["auth", "login", provider]
        for key, value in args.items():
            if key == "method" or value is False:
                continue
            if key in {"config", "env_file", "oauth_token_path"}:
                value = self.path(value)
            for item in value if isinstance(value, list) else [value]:
                command.append(f"--{key.replace('_', '-')}={item}")
        # The CLI owns OAuth and its environment for this single operation.
        result = subprocess.run(
            [sys.executable, "-m", "fileroute.cli", *command],
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode:
            raise ValueError(
                "Sign-in did not complete. Review credentials and retry from the Fileroute CLI."
            )
        return {"title": "Provider sign-in completed", "provider": provider}

    def help(self, command=None):
        parts = command.split() if command else []
        if any(part.startswith("-") for part in parts):
            raise ValueError("Enter a registered command path such as auth login s3.")
        content = self.offline_cli([*parts, "--help"])
        return {
            "kind": "export",
            "title": "CLI help",
            "content": content,
            "format": "text",
        }

    def completion(self, shell):
        return {
            "kind": "export",
            "title": f"{shell} completion script",
            "format": "text",
            "content": get_completion_script(
                prog_name="fileroute", complete_var="_FILEROUTE_COMPLETE", shell=shell
            ),
        }

    def completion_install(self, shell):
        _, installed = install(
            shell=shell, prog_name="fileroute", complete_var="_FILEROUTE_COMPLETE"
        )
        return {"title": "Completion installed", "path": str(installed)}
