"""Prepare a branch release locally; Actions saves artifacts before the atomic push.

uv owns version edits and locking. Git owns release identity. Retry lookup happens
before version calculation, so later stable tags cannot change an earlier release.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def git(*args: str) -> str:
    return run("git", *args)


def build(source: str) -> None:
    """Build once from the bumped version, without stale distributions."""
    shutil.rmtree("dist", ignore_errors=True)
    subprocess.run(
        ["uvx", "--from", "poethepoet==0.48.0", "poe", "build"],
        check=True,
        env={
            **os.environ,
            "SOURCE_DATE_EPOCH": git("show", "-s", "--format=%ct", source),
        },
    )


def existing_release(branch: str, source: str) -> tuple[str, str] | None:
    """Find a tagged release made for this exact source and branch."""
    marker = f"Release-Source: {source}"
    for sha in git(
        "log", "--tags", "--format=%H", "--fixed-strings", f"--grep={marker}"
    ).splitlines():
        message = git("show", "-s", "--format=%B", sha).splitlines()
        if marker not in message or f"Release-Branch: {branch}" not in message:
            continue
        version = tomllib.loads(git("show", f"{sha}:pyproject.toml"))["project"][
            "version"
        ]
        tag = f"v{version}"
        changed = set(
            git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines()
        )
        if (
            git("show", "-s", "--format=%P", sha) != source
            or not changed <= {"pyproject.toml", "uv.lock"}
            or tag not in git("tag", "--points-at", sha).splitlines()
        ):
            raise RuntimeError(
                f"Invalid release identity for {tag}; inspect it manually."
            )
        return tag, sha
    return None


def bump_args(branch: str, version: str, tags: set[str]) -> list[str]:
    """Select uv's bump flags for the repository's stable/development policy."""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?(?:\.dev\d+)?", version):
        raise ValueError(f"Unsupported release version: {version}")
    base = re.split(r"(?:a|b|rc|\.dev)", version)[0]
    if branch == "main":
        return ["--bump", "stable" if version != base else "patch"]
    if branch == "dev":
        if ".dev" in version and f"v{base}" not in tags:
            return ["--bump", "dev"]
        return ["--bump", "patch", "--bump", "dev"]
    raise ValueError(f"Unsupported release branch: {branch}")


def prepare(branch: str, source: str) -> dict[str, str]:
    """Validate, bump, build, and tag without changing any remote refs."""
    if branch not in {"dev", "main"}:
        raise ValueError(f"Unsupported release branch: {branch}")
    if not re.fullmatch(r"[0-9a-f]{40}", source) or git("rev-parse", "HEAD") != source:
        raise ValueError(
            "Check out the full triggering source SHA before preparing a release."
        )
    if git("status", "--porcelain"):
        raise RuntimeError("Release preparation requires a clean working tree.")
    git("fetch", "--tags", "origin", f"{branch}:refs/remotes/origin/{branch}")
    existing = existing_release(branch, source)
    if existing:
        tag, sha = existing
        return {"tag": tag, "release_sha": sha, "reused": "true"}
    if git("rev-parse", f"refs/remotes/origin/{branch}") != source:
        raise RuntimeError(
            f"Branch {branch} advanced; release its latest push instead."
        )

    version = run("uv", "version", "--short")
    tags = set(git("tag", "--list").splitlines())
    run("uv", "version", *bump_args(branch, version, tags), "--no-sync")
    tag = f"v{run('uv', 'version', '--short')}"
    if tag in tags:
        raise RuntimeError(
            f"Tag {tag} already belongs to another source; refusing to overwrite it."
        )
    build(source)
    if not list(Path("dist").glob("*.whl")) or not list(Path("dist").glob("*.tar.gz")):
        raise RuntimeError("Build must produce both a wheel and a source distribution.")
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("add", "pyproject.toml", "uv.lock")
    git(
        "commit",
        "-m",
        f"chore(release): {tag}",
        "-m",
        f"Release-Source: {source}\nRelease-Branch: {branch}",
    )
    sha = git("rev-parse", "HEAD")
    git("tag", "-a", tag, "-m", tag)
    return {"tag": tag, "release_sha": sha, "reused": "false"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", choices=["dev", "main"], required=True)
    parser.add_argument("--source", required=True, help="Full triggering commit SHA")
    args = parser.parse_args()
    outputs = prepare(args.branch, args.source)
    content = "".join(f"{key}={value}\n" for key, value in outputs.items())
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(content)
    print(content, end="")


if __name__ == "__main__":
    main()
