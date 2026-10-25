"""Offline contract tests for deterministic Jianwei release artifacts."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import build_jianwei_marketplace as marketplace  # noqa: E402
import build_jianwei_release as release  # noqa: E402


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgSign=false",
         "-c", "tag.gpgSign=false", *args],
        check=True, capture_output=True, text=True, shell=False, timeout=15,
        env=env,
    ).stdout.strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def commit(repo: Path, message: str, timestamp: str) -> str:
    env = {
        "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
        "GIT_AUTHOR_DATE": timestamp,
        "GIT_COMMITTER_DATE": timestamp,
    }
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, env=env)
    return git(repo, "rev-parse", "HEAD")


def package(version: str) -> dict[str, object]:
    return {
        "name": "jianwei", "version": version, "description": "fixture",
        "author": {"name": "Lanbasara"},
        "homepage": marketplace.HOMEPAGE_URL,
        "repository": {"type": "git", "url": "git+" + marketplace.REPOSITORY_URL},
        "engines": {"dsh": marketplace.DSH_ENGINE},
        "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
    }


def manifest(plugin: Path, version: str) -> None:
    files = {
        path.relative_to(plugin).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in plugin.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != "manifest.json"
    }
    write_json(plugin / "manifest.json", {
        "name": "jianwei", "version": version, "files": files,
        "components": {}, "entry_adaptation": "fixture",
    })


class ReleaseContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="jianwei-release-test-")
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "release"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Release Test")
        git(self.repo, "config", "user.email", "release@example.invalid")
        write_json(self.repo / "package.json", package("0.1.1"))
        self.old_commit = commit(self.repo, "legacy", "2025-01-02T03:04:05+00:00")
        self.old_timestamp = int(git(self.repo, "show", "-s", "--format=%ct", self.old_commit))

        plugin = self.repo / "plugin"
        write_json(plugin / "package.json", package("0.2.0"))
        write_json(plugin / "plugin.json", {"name": "jianwei", "version": "0.2.0"})
        skill = plugin / "skills/research/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# committed fixture\n", encoding="utf-8")
        skill.chmod(0o755)
        manifest(plugin, "0.2.0")
        self.commit = commit(self.repo, "plugin", "2026-01-02T03:04:05+00:00")
        git(self.repo, "tag", "v0.2.0")
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.commit)

        old = {
            "version": "0.1.1", "publishTimestamp": self.old_timestamp,
            "engines": {"dsh": marketplace.DSH_ENGINE},
            "source": {"type": "git", "url": marketplace.REPOSITORY_URL,
                       "commit": self.old_commit},
        }
        index = {
            "format": 1,
            "title": {"en": "Jianwei", "zh": "见微 Jianwei"},
            "description": {"en": "fixture", "zh": "fixture"},
            "plugins": [{
                "name": "jianwei", "title": {"en": "Jianwei", "zh": "见微 Jianwei"},
                "description": "fixture", "author": "Lanbasara",
                "homepage": marketplace.HOMEPAGE_URL,
                "repository": {"url": marketplace.HOMEPAGE_URL},
                "metadata": {"keywords": ["fixture"]},
                "dist-tags": {"latest": "0.1.1"}, "versions": [old],
            }],
        }
        self.market = self.base / "marketplace"
        self.market.mkdir()
        git(self.market, "init", "-q")
        git(self.market, "config", "user.name", "Market Test")
        git(self.market, "config", "user.email", "market@example.invalid")
        git(self.market, "remote", "add", "origin", release.MARKETPLACE_REPOSITORY_URL)
        write_json(self.market / "dsh/index.json", index)
        self.market_commit = commit(self.market, "index", "2026-01-01T00:00:00+00:00")
        git(self.market, "update-ref", "refs/remotes/origin/main", self.market_commit)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, name: str, ref: str | None = None, **kwargs: object) -> Path:
        return release.build(
            self.base / name, ref or self.commit, self.repo, self.market, **kwargs,
        )

    def test_same_committed_inputs_are_byte_for_byte_reproducible(self) -> None:
        first = self.build("first")
        (self.repo / "plugin/skills/research/SKILL.md").write_text("dirty\n", encoding="utf-8")
        (self.repo / "jianwei").mkdir()
        (self.repo / "jianwei/UNCOMMITTED.md").write_text("dirty source\n", encoding="utf-8")
        (self.repo / ".env").write_text("OPENAI_API_KEY=UNTRACKED_SECRET\n", encoding="utf-8")
        write_json(self.market / "dsh/index.json", {"dirty": True})
        second = self.build("second")
        for name in (*release.OUTPUT_FILES, "SHA256SUMS"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)
        self.assertNotIn(b"UNTRACKED_SECRET", (second / "jianwei-plugin.zip").read_bytes())

    def test_product_archive_is_exact_committed_plugin_tree_with_modes(self) -> None:
        out = self.build("archive")
        committed = marketplace._plugin_tree(self.repo, self.commit)
        with zipfile.ZipFile(out / "jianwei-plugin.zip") as archive:
            self.assertEqual(archive.namelist(), ["jianwei/" + name for name in sorted(committed)])
            self.assertTrue(all(not name.startswith("/") and ".." not in Path(name).parts
                                and not name.endswith("/") for name in archive.namelist()))
            for name, raw in committed.items():
                info = archive.getinfo("jianwei/" + name)
                self.assertEqual(archive.read(info), raw)
                expected = 0o755 if name == "skills/research/SKILL.md" else 0o644
                self.assertEqual((info.external_attr >> 16) & 0o777, expected)

    def test_provenance_pins_committed_marketplace_head_and_digests(self) -> None:
        out = self.build("provenance")
        data = json.loads((out / "release.json").read_text(encoding="utf-8"))
        self.assertFalse(data["official"])
        self.assertEqual(data["commit"], self.commit)
        self.assertEqual(data["marketplace"]["baseCommit"], self.market_commit)
        committed_index = git(self.market, "show", self.market_commit + ":dsh/index.json") + "\n"
        self.assertEqual(
            data["marketplace"]["baseIndexSha256"],
            hashlib.sha256(committed_index.encode()).hexdigest(),
        )
        for name in release.OUTPUT_FILES:
            self.assertIn(hashlib.sha256((out / name).read_bytes()).hexdigest(),
                          (out / "SHA256SUMS").read_text(encoding="ascii"))

    def test_official_tag_requires_main_ancestry_and_expected_commit(self) -> None:
        with self.assertRaises(release.ReleaseError):
            self.build("missing-ancestor", "v0.2.0")
        out = self.build(
            "official", "v0.2.0", expected_commit=self.commit,
            required_ancestor="origin/main",
        )
        self.assertTrue(json.loads((out / "release.json").read_text())["official"])
        with self.assertRaises(release.ReleaseError):
            self.build(
                "wrong-expected", "v0.2.0", expected_commit="f" * 40,
                required_ancestor="origin/main",
            )
        git(self.repo, "update-ref", "refs/remotes/origin/main", self.old_commit)
        with self.assertRaises(release.ReleaseError):
            self.build(
                "not-main", "v0.2.0", expected_commit=self.commit,
                required_ancestor="origin/main",
            )

    def test_dispatch_context_uses_sha_and_never_becomes_official(self) -> None:
        with patch.dict(os.environ, {
            "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_SHA": self.commit,
            "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.2.0",
        }, clear=True):
            ref, expected = release._github_context()
        self.assertEqual((ref, expected), (self.commit, self.commit))
        out = release.build(
            self.base / "dispatch", ref, self.repo, self.market,
            expected_commit=expected, required_ancestor="origin/main",
        )
        self.assertFalse(json.loads((out / "release.json").read_text())["official"])

    def test_tag_context_and_bad_context_are_strict(self) -> None:
        with patch.dict(os.environ, {
            "GITHUB_EVENT_NAME": "push", "GITHUB_SHA": self.commit,
            "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v0.2.0",
        }, clear=True):
            self.assertEqual(release._github_context(), ("v0.2.0", self.commit))
        for values in (
            {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_SHA": self.commit},
            {"GITHUB_EVENT_NAME": "push", "GITHUB_SHA": self.commit,
             "GITHUB_REF_TYPE": "branch", "GITHUB_REF_NAME": "main"},
        ):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(release.ReleaseError):
                    release._github_context()

    def test_marketplace_origin_main_and_committed_index_are_strict(self) -> None:
        git(self.market, "remote", "set-url", "origin",
            release.MARKETPLACE_REPOSITORY_URL.removesuffix(".git"))
        self.build("origin-without-dot-git")
        git(self.market, "remote", "set-url", "origin", "https://example.invalid/market.git")
        with self.assertRaises(release.ReleaseError):
            self.build("bad-origin")
        git(self.market, "remote", "set-url", "origin", release.MARKETPLACE_REPOSITORY_URL)
        (self.market / "UNPUBLISHED").write_text("not origin/main\n", encoding="utf-8")
        commit(self.market, "unpublished market work", "2026-01-02T00:00:00+00:00")
        with self.assertRaises(release.ReleaseError):
            self.build("bad-main")

    def test_bad_committed_marketplace_index_is_rejected(self) -> None:
        (self.market / "dsh/index.json").write_text("{not-json\n", encoding="utf-8")
        bad_commit = commit(self.market, "bad committed index", "2026-01-03T00:00:00+00:00")
        git(self.market, "update-ref", "refs/remotes/origin/main", bad_commit)
        with self.assertRaises(marketplace.MarketplaceError):
            self.build("bad-index")

    def test_existing_and_symlink_output_are_rejected(self) -> None:
        existing = self.base / "existing"
        existing.mkdir()
        with self.assertRaises(marketplace.MarketplaceError):
            release.build(existing, self.commit, self.repo, self.market)
        target = self.base / "target"
        target.mkdir()
        linked = self.base / "linked"
        linked.symlink_to(target, target_is_directory=True)
        with self.assertRaises(marketplace.MarketplaceError):
            release.build(linked, self.commit, self.repo, self.market)

    def test_zip_timestamp_rejects_years_after_2107(self) -> None:
        too_late = int(dt.datetime(2108, 1, 1, tzinfo=dt.timezone.utc).timestamp())
        with self.assertRaises(release.ReleaseError):
            release._zip_timestamp(too_late)


if __name__ == "__main__":
    unittest.main()
