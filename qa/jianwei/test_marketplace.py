"""Offline contract tests for deterministic Jianwei Marketplace catalogs."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qa"))

import policy_check  # noqa: E402

_builder_files = [
    path for path in policy_check.RUNNER_CAPABILITIES["marketplace-contract"]["files"]
    if path.startswith("scripts/") and Path(path).suffix == ".py"
]
if len(_builder_files) != 1:
    raise RuntimeError("marketplace-contract must declare exactly one builder")
_builder_path = ROOT / _builder_files[0]
_builder_spec = importlib.util.spec_from_file_location("marketplace_builder_under_test", _builder_path)
if _builder_spec is None or _builder_spec.loader is None:
    raise RuntimeError("cannot load the registered Marketplace builder")
marketplace = importlib.util.module_from_spec(_builder_spec)
_builder_spec.loader.exec_module(marketplace)


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgSign=false",
         "-c", "tag.gpgSign=false", *args], check=True, capture_output=True,
        text=True, shell=False, timeout=15,
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
    subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgSign=false",
         "commit", "-q", "-m", message],
        check=True, shell=False, timeout=15, env=env,
    )
    return run_git(repo, "rev-parse", "HEAD")


def write_plugin_manifest(repo: Path, version: str) -> None:
    plugin = repo / "plugin"
    files = {
        path.relative_to(plugin).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in plugin.rglob("*")
        if path.is_file() and not path.is_symlink() and path.name != "manifest.json"
    }
    write_json(plugin / "manifest.json", {
        "name": "jianwei", "version": version, "files": files,
        "components": {}, "entry_adaptation": "fixture",
    })


def package(version: str, *, name: str = "jianwei") -> dict[str, object]:
    return {
        "name": name,
        "version": version,
        "description": "见微 fixture",
        "author": {"name": "Lanbasara"},
        "homepage": marketplace.HOMEPAGE_URL,
        "repository": {"type": "git", "url": "git+" + marketplace.REPOSITORY_URL},
        "engines": {"dsh": marketplace.DSH_ENGINE},
        "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
    }


def history_entry(
        version: str, commit_id: str, timestamp: int, *, plugin_layout: bool = False,
) -> dict[str, object]:
    source: dict[str, object] = {
        "type": "git", "url": marketplace.REPOSITORY_URL, "commit": commit_id,
    }
    if plugin_layout:
        source["path"] = "plugin"
    return {
        "version": version,
        "publishTimestamp": timestamp,
        "engines": {"dsh": marketplace.DSH_ENGINE},
        "source": source,
    }


def previous_index(old: dict[str, object]) -> dict[str, object]:
    return {
        "format": 1,
        "title": {"en": "Jianwei", "zh": "见微 Jianwei"},
        "description": {"en": "fixture", "zh": "fixture"},
        "plugins": [{
            "name": "jianwei",
            "title": {"en": "Jianwei", "zh": "见微 Jianwei"},
            "description": "old fixture",
            "author": "Lanbasara",
            "homepage": marketplace.HOMEPAGE_URL,
            "repository": {"url": marketplace.HOMEPAGE_URL},
            "metadata": {"keywords": ["old-fixture"]},
            "dist-tags": {"latest": "0.1.1"},
            "versions": [old],
        }],
    }


class MarketplaceContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="jianwei-marketplace-test-")
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / "release"
        self.repo.mkdir()
        run_git(self.repo, "init", "-q")
        run_git(self.repo, "config", "user.name", "Jianwei Test")
        run_git(self.repo, "config", "user.email", "fixture@example.invalid")

        # Commit a real legacy root-layout release before adding plugin/ 0.2.0.
        write_json(self.repo / "package.json", package("0.1.1"))
        run_git(self.repo, "add", "package.json")
        self.old_commit = commit(self.repo, "legacy root fixture", "2025-01-02T03:04:05+00:00")
        self.old_timestamp = int(run_git(self.repo, "show", "-s", "--format=%ct", self.old_commit))

        plugin_package = package("0.2.0")
        write_json(self.repo / "plugin" / "package.json", plugin_package)
        write_json(self.repo / "plugin" / "plugin.json", {
            "name": "jianwei", "version": "0.2.0",
        })
        skill = self.repo / "plugin" / "skills" / "fixture" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("# Fixture skill\n", encoding="utf-8")
        write_plugin_manifest(self.repo, "0.2.0")
        run_git(self.repo, "add", "plugin")
        self.commit = commit(self.repo, "plugin fixture", "2026-01-02T03:04:05+00:00")
        self.timestamp = int(run_git(self.repo, "show", "-s", "--format=%ct", self.commit))
        run_git(self.repo, "-c", "tag.gpgSign=false", "tag", "v0.2.0")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, ref: str = "v0.2.0", previous: Path | None = None) -> Path:
        out = self.base / ("market-" + str(len(list(self.base.glob("market-*")))))
        return marketplace.build(out, ref, self.repo, previous)

    def commit_plugin_package(self, value: dict[str, object], message: str) -> str:
        write_json(self.repo / "plugin/package.json", value)
        write_plugin_manifest(self.repo, str(value["version"]))
        run_git(self.repo, "add", "plugin/package.json", "plugin/manifest.json")
        run_git(self.repo, "commit", "-q", "-m", message)
        return run_git(self.repo, "rev-parse", "HEAD")

    def old_entry(self) -> dict[str, object]:
        return history_entry("0.1.1", self.old_commit, self.old_timestamp)

    def test_catalogs_pin_plugin_subdirectory_and_readme_contract(self) -> None:
        out = self.build()
        claude = json.loads((out / ".claude-plugin/marketplace.json").read_text())
        codex = json.loads((out / ".agents/plugins/marketplace.json").read_text())
        dsh = json.loads((out / "dsh/index.json").read_text())
        claude_source = claude["plugins"][0]["source"]
        codex_source = codex["plugins"][0]["source"]
        self.assertEqual(claude_source, {
            "source": "git-subdir", "url": marketplace.REPOSITORY_URL,
            "path": "plugin", "sha": self.commit, "ref": "v0.2.0",
        })
        self.assertEqual(codex_source, {**claude_source, "path": "./plugin"})
        latest = dsh["plugins"][0]["versions"][0]
        self.assertEqual(latest["version"], "0.2.0")
        self.assertEqual(latest["source"], {
            "type": "git", "url": marketplace.REPOSITORY_URL,
            "commit": self.commit, "path": "plugin",
        })
        readme = (out / "README.md").read_text(encoding="utf-8")
        self.assertIn("/blob/v0.2.0/plugin/INSTALL.md", readme)
        self.assertIn(
            "'git+https://github.com/Lanbasara/jianwei.git#v0.2.0&path:/plugin'",
            readme,
        )
        self.assertIn("根目录仅保留 v0.1.1 兼容布局", readme)
        self.assertNotEqual(latest["version"], "0.1.1")

    def test_sha_ref_has_sha_without_symbolic_ref(self) -> None:
        out = self.build(self.commit)
        for relative in (
            ".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json",
        ):
            source = json.loads((out / relative).read_text())["plugins"][0]["source"]
            self.assertEqual(source["sha"], self.commit)
            self.assertNotIn("ref", source)
        self.assertIn("#" + self.commit + "&path:/plugin", (out / "README.md").read_text())

    def test_previous_history_object_is_preserved_and_new_version_is_first(self) -> None:
        prior = previous_index(self.old_entry())
        path = self.base / "previous.json"
        write_json(path, prior)
        old_object = copy.deepcopy(prior["plugins"][0]["versions"][0])
        out = self.build(previous=path)
        plugin = json.loads((out / "dsh/index.json").read_text())["plugins"][0]
        self.assertEqual(plugin["dist-tags"], {"latest": "0.2.0"})
        self.assertEqual([item["version"] for item in plugin["versions"]], ["0.2.0", "0.1.1"])
        self.assertEqual(plugin["versions"][1], old_object)

    def test_existing_output_and_symbolic_output_are_rejected(self) -> None:
        existing = self.base / "existing"
        existing.mkdir()
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(existing, self.commit, self.repo)
        target = self.base / "target"
        target.mkdir()
        linked = self.base / "linked"
        linked.symlink_to(target, target_is_directory=True)
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(linked, self.commit, self.repo)
        real_parent = self.base / "real-parent"
        real_parent.mkdir()
        linked_parent = self.base / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(linked_parent / "candidate", self.commit, self.repo)

    def test_write_failure_removes_output_and_staging_directory(self) -> None:
        out = self.base / "transaction"
        original = marketplace._write_json
        calls = 0

        def fail_second(path: Path, value: dict[str, object]) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            original(path, value)

        with patch.object(marketplace, "_write_json", side_effect=fail_second):
            with self.assertRaises(OSError):
                marketplace.build(out, self.commit, self.repo)
        self.assertFalse(out.exists())
        self.assertEqual(list(self.base.glob(".transaction.tmp-*")), [])

    def test_publish_does_not_overwrite_target_that_appears_during_staging(self) -> None:
        out = self.base / "raced-output"
        atomic_publish = marketplace._atomic_publish_no_replace

        def populate(stage: Path) -> None:
            (stage / "complete.txt").write_text("complete\n", encoding="utf-8")

        def race(stage: Path, target: Path) -> None:
            out.mkdir()
            (out / "owner.txt").write_text("other writer\n", encoding="utf-8")
            atomic_publish(stage, target)

        with patch.object(marketplace, "_atomic_publish_no_replace", side_effect=race):
            with self.assertRaises(FileExistsError):
                marketplace._transactional_output(out, populate)
        self.assertEqual((out / "owner.txt").read_text(encoding="utf-8"), "other writer\n")
        self.assertFalse((out / "complete.txt").exists())
        self.assertEqual(list(self.base.glob(".raced-output.tmp-*")), [])

    def test_atomic_publish_is_last_and_stage_has_final_mode(self) -> None:
        out = self.base / "atomic-output"
        atomic_publish = marketplace._atomic_publish_no_replace
        original_chmod = Path.chmod

        def reject_post_publish_chmod(path: Path, mode: int) -> None:
            if path == out:
                raise AssertionError("output must not be mutated after atomic publish")
            original_chmod(path, mode)

        def publish(stage: Path, target: Path) -> None:
            self.assertEqual(stage.stat().st_mode & 0o777, 0o755)
            atomic_publish(stage, target)

        with patch.object(Path, "chmod", new=reject_post_publish_chmod):
            with patch.object(marketplace, "_atomic_publish_no_replace", side_effect=publish):
                marketplace._transactional_output(
                    out,
                    lambda stage: (stage / "complete.txt").write_text(
                        "complete\n", encoding="utf-8",
                    ),
                )
        self.assertEqual((out / "complete.txt").read_text(encoding="utf-8"), "complete\n")
        self.assertEqual(out.stat().st_mode & 0o777, 0o755)

    def test_bad_ref_and_tag_version_mismatch_are_rejected(self) -> None:
        for ref in (
            "HEAD", "v0.2", "v01.2.0", "V0.2.0", "A" * 40,
            "../v0.2.0", "v" + "1" * 100 + ".0.0",
        ):
            with self.subTest(ref=ref), self.assertRaises(marketplace.MarketplaceError):
                marketplace.build(self.base / ("bad-" + str(abs(hash(ref)))), ref, self.repo)
        run_git(self.repo, "-c", "tag.gpgSign=false", "tag", "v0.2.1")
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "wrong-tag", "v0.2.1", self.repo)

    def test_tampered_skill_without_manifest_update_is_rejected(self) -> None:
        (self.repo / "plugin/skills/fixture/SKILL.md").write_text(
            "# Tampered fixture\n", encoding="utf-8",
        )
        run_git(self.repo, "add", "plugin/skills/fixture/SKILL.md")
        run_git(self.repo, "commit", "-q", "-m", "tampered skill")
        bad_commit = run_git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "tampered-skill", bad_commit, self.repo)

    def test_manifest_missing_and_extra_hashes_are_rejected(self) -> None:
        manifest_path = self.repo / "plugin/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"].pop("skills/fixture/SKILL.md")
        write_json(manifest_path, manifest)
        run_git(self.repo, "add", "plugin/manifest.json")
        run_git(self.repo, "commit", "-q", "-m", "missing hash")
        missing_commit = run_git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "missing-hash", missing_commit, self.repo)

        write_plugin_manifest(self.repo, "0.2.0")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["not-in-tree.txt"] = "0" * 64
        write_json(manifest_path, manifest)
        run_git(self.repo, "add", "plugin/manifest.json")
        run_git(self.repo, "commit", "-q", "-m", "extra hash")
        extra_commit = run_git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "extra-hash", extra_commit, self.repo)

    def test_plugin_symlink_and_gitlink_entries_are_rejected(self) -> None:
        linked = self.repo / "plugin/linked-package.json"
        linked.symlink_to("package.json")
        run_git(self.repo, "add", "plugin/linked-package.json")
        run_git(self.repo, "commit", "-q", "-m", "plugin symlink")
        symlink_commit = run_git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "symlink-tree", symlink_commit, self.repo)

        run_git(self.repo, "rm", "-q", "plugin/linked-package.json")
        run_git(
            self.repo, "update-index", "--add", "--cacheinfo",
            f"160000,{self.old_commit},plugin/vendor",
        )
        run_git(self.repo, "commit", "-q", "-m", "plugin gitlink")
        gitlink_commit = run_git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "gitlink-tree", gitlink_commit, self.repo)

    def test_release_identity_and_dsh_engine_are_strict(self) -> None:
        bad_author = package("0.2.0")
        bad_author["author"] = {"name": "Someone Else"}
        author_commit = self.commit_plugin_package(bad_author, "bad author")
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "bad-author", author_commit, self.repo)

        bad_engine = package("0.2.0")
        bad_engine["engines"] = {"dsh": ">=0.2.0"}
        engine_commit = self.commit_plugin_package(bad_engine, "bad engine")
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "bad-engine", engine_commit, self.repo)

    def test_missing_plugin_metadata_never_falls_back_to_root(self) -> None:
        run_git(self.repo, "rm", "-q", "plugin/package.json", "plugin/manifest.json")
        run_git(self.repo, "commit", "-q", "-m", "root only")
        root_only = run_git(self.repo, "rev-parse", "HEAD")
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "root-only", root_only, self.repo)

    def test_malformed_history_identity_path_duplicate_and_order_are_rejected(self) -> None:
        cases: dict[str, object] = {}
        wrong_name = previous_index(self.old_entry())
        wrong_name["plugins"][0]["name"] = "other"
        cases["name"] = wrong_name
        wrong_repo = previous_index(self.old_entry())
        wrong_repo["plugins"][0]["versions"][0]["source"]["url"] = "https://example.invalid/repo"
        cases["repo"] = wrong_repo
        wrong_path = previous_index(self.old_entry())
        wrong_path["plugins"][0]["versions"][0]["source"]["path"] = "../plugin"
        cases["path"] = wrong_path
        duplicate = previous_index(self.old_entry())
        duplicate["plugins"][0]["versions"].append(copy.deepcopy(self.old_entry()))
        cases["duplicate"] = duplicate
        bad_title = previous_index(self.old_entry())
        bad_title["title"] = {"en": "Jianwei", "fr": "unexpected"}
        cases["title"] = bad_title
        bad_metadata = previous_index(self.old_entry())
        bad_metadata["plugins"][0]["metadata"]["unexpected"] = True
        cases["metadata"] = bad_metadata
        missing_commit = previous_index(history_entry("0.1.1", "f" * 40, self.old_timestamp))
        cases["missing-commit"] = missing_commit
        bad_timestamp_entry = self.old_entry()
        bad_timestamp_entry["publishTimestamp"] = self.old_timestamp + 1
        cases["timestamp"] = previous_index(bad_timestamp_entry)
        hidden_higher = previous_index(self.old_entry())
        hidden_higher["plugins"][0]["versions"] = [
            history_entry("0.3.0", "e" * 40, self.timestamp, plugin_layout=True),
            self.old_entry(),
        ]
        cases["hidden-higher"] = hidden_higher
        root_new = previous_index(history_entry("0.2.0", self.old_commit, self.old_timestamp))
        root_new["plugins"][0]["dist-tags"]["latest"] = "0.2.0"
        cases["new-root-layout"] = root_new
        wrong_order = previous_index(self.old_entry())
        wrong_order["plugins"][0]["versions"] = [
            history_entry("0.1.0", self.old_commit, self.old_timestamp),
            self.old_entry(),
        ]
        cases["order"] = wrong_order
        conflict = previous_index(history_entry(
            "0.2.0", self.commit, self.timestamp, plugin_layout=True,
        ))
        conflict["plugins"][0]["dist-tags"]["latest"] = "0.2.0"
        cases["same-version"] = conflict
        for label, value in cases.items():
            with self.subTest(label=label):
                path = self.base / f"previous-{label}.json"
                write_json(path, value)
                with self.assertRaises(marketplace.MarketplaceError):
                    marketplace.build(self.base / f"out-{label}", self.commit, self.repo, path)

    def test_new_version_must_exceed_verified_previous_latest(self) -> None:
        newer_commit = self.commit_plugin_package(package("0.3.0"), "newer plugin")
        newer_timestamp = int(run_git(
            self.repo, "show", "-s", "--format=%ct", newer_commit,
        ))
        newer = previous_index(history_entry(
            "0.3.0", newer_commit, newer_timestamp, plugin_layout=True,
        ))
        newer["plugins"][0]["dist-tags"]["latest"] = "0.3.0"
        path = self.base / "previous-newer.json"
        write_json(path, newer)
        with self.assertRaises(marketplace.MarketplaceError):
            marketplace.build(self.base / "not-greater", self.commit, self.repo, path)


if __name__ == "__main__":
    unittest.main()
