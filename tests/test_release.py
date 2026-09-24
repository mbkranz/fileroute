"""Exercise release policy with real Git/uv and a mocked distribution build."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "release", Path(__file__).resolve().parents[1] / "scripts" / "release.py"
)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.previous = Path.cwd()
        self.addCleanup(os.chdir, self.previous)
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(self.remote)], check=True)
        work = self.root / "work"
        work.mkdir()
        os.chdir(work)
        release.git("init", "-q", "-b", "dev")
        release.git("config", "user.name", "Release test")
        release.git("config", "user.email", "release@example.invalid")
        release.git("remote", "add", "origin", str(self.remote))
        Path("pyproject.toml").write_text(
            '[project]\nname = "release-fixture"\nversion = "1.0.0"\n'
            'requires-python = ">=3.11"\n'
        )
        Path(".gitignore").write_text("dist/\n.venv/\n")
        release.run("uv", "lock", "--offline")
        release.git("add", ".")
        release.git("commit", "-qm", "Initial source")
        release.git("push", "-q", "origin", "HEAD:dev")
        self.source = release.git("rev-parse", "HEAD")
        self.build = self.enterContext(
            patch.object(release, "build", side_effect=self.fake_build)
        )

    @staticmethod
    def fake_build(source: str) -> None:
        Path("dist").mkdir(exist_ok=True)
        Path("dist/fixture.whl").write_bytes(b"wheel")
        Path("dist/fixture.tar.gz").write_bytes(b"sdist")

    def push(self, result: dict[str, str], branch: str = "dev") -> None:
        release.git(
            "push",
            "--atomic",
            "origin",
            f"{result['release_sha']}:refs/heads/{branch}",
            f"refs/tags/{result['tag']}",
        )

    def change(self, branch: str = "dev") -> str:
        release.git("commit", "--allow-empty", "-qm", "Next change")
        release.git("push", "-q", "origin", f"HEAD:{branch}")
        return release.git("rev-parse", "HEAD")

    def test_lifecycle_and_retry_after_stable_promotion(self) -> None:
        first = release.prepare("dev", self.source)
        self.assertEqual(first["tag"], "v1.0.1.dev1")
        self.push(first)
        second = release.prepare("dev", self.change())
        self.assertEqual(second["tag"], "v1.0.1.dev2")
        self.push(second)
        release.git("push", "-q", "origin", "HEAD:main")
        stable = release.prepare("main", second["release_sha"])
        self.assertEqual(stable["tag"], "v1.0.1")
        self.push(stable, "main")
        direct = release.prepare("main", self.change("main"))
        self.assertEqual(direct["tag"], "v1.0.2")
        self.push(direct, "main")

        # Original dev source is retried after the stable tag and newer pushes exist.
        release.git("checkout", "--detach", self.source)
        self.build.reset_mock()
        retry = release.prepare("dev", self.source)
        self.assertEqual(retry, {**first, "reused": "true"})
        self.build.assert_not_called()

        release.git("checkout", "--detach", second["release_sha"])
        next_dev = release.prepare("dev", self.change())
        self.assertEqual(next_dev["tag"], "v1.0.2.dev1")
        self.assertIn("1.0.2.dev1", Path("uv.lock").read_text())

    def test_branch_advance_refuses_to_release_stale_source(self) -> None:
        self.change()
        release.git("checkout", "--detach", self.source)
        with self.assertRaisesRegex(RuntimeError, "advanced"):
            release.prepare("dev", self.source)
        self.build.assert_not_called()

    def test_collision_does_not_overwrite_tag(self) -> None:
        release.git("tag", "v1.0.1.dev1")
        release.git("push", "-q", "origin", "v1.0.1.dev1")
        with self.assertRaisesRegex(RuntimeError, "another source"):
            release.prepare("dev", self.source)
        self.assertEqual(release.git("rev-parse", "v1.0.1.dev1"), self.source)
        self.build.assert_not_called()

    def test_failed_build_creates_no_release_commit_or_tag(self) -> None:
        self.build.side_effect = RuntimeError("Build failed")
        with self.assertRaisesRegex(RuntimeError, "Build failed"):
            release.prepare("dev", self.source)
        self.assertEqual(release.git("rev-parse", "HEAD"), self.source)
        self.assertEqual(release.git("tag", "--list"), "")

    def test_atomic_push_rejection_leaves_both_remote_refs_unchanged(self) -> None:
        result = release.prepare("dev", self.source)
        hook = self.remote / "hooks" / "update"
        hook.write_text('#!/bin/sh\ncase "$1" in refs/tags/*) exit 1;; esac\n')
        hook.chmod(0o755)
        with self.assertRaises(subprocess.CalledProcessError):
            self.push(result)
        self.assertEqual(
            release.git("ls-remote", "origin", "refs/heads/dev").split()[0], self.source
        )
        self.assertEqual(release.git("ls-remote", "origin", "refs/tags/*"), "")

    def test_push_race_does_not_publish_tag(self) -> None:
        result = release.prepare("dev", self.source)
        other = self.root / "other"
        subprocess.run(
            ["git", "clone", "-q", "-b", "dev", str(self.remote), str(other)],
            check=True,
        )
        for args in [
            ["config", "user.name", "Concurrent author"],
            ["config", "user.email", "author@example.invalid"],
            ["commit", "--allow-empty", "-qm", "Concurrent push"],
            ["push", "-q", "origin", "dev"],
        ]:
            subprocess.run(["git", "-C", str(other), *args], check=True)
        with self.assertRaises(subprocess.CalledProcessError):
            self.push(result)
        self.assertEqual(release.git("ls-remote", "origin", "refs/tags/*"), "")

    def test_invalid_source_branch_and_dirty_checkout_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            release.prepare("feature", self.source)
        with self.assertRaises(ValueError):
            release.prepare("dev", "HEAD")
        Path("untracked.txt").write_text("Uncommitted work")
        with self.assertRaisesRegex(RuntimeError, "clean working tree"):
            release.prepare("dev", self.source)

    def test_invalid_release_identity_is_not_reused(self) -> None:
        Path("extra.txt").write_text("Not a version-only change")
        release.git("add", ".")
        release.git(
            "commit",
            "-qm",
            "Forged release",
            "-m",
            f"Release-Source: {self.source}\nRelease-Branch: dev",
        )
        release.git("tag", "v1.0.0")
        release.git("push", "-q", "origin", "HEAD:dev", "v1.0.0")
        release.git("checkout", "--detach", self.source)
        with self.assertRaisesRegex(RuntimeError, "Invalid release identity"):
            release.prepare("dev", self.source)


if __name__ == "__main__":
    unittest.main()
