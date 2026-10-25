"""Exercise the delivered installation, not just source imports. Entirely offline."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


builder = module("tanyuan_builder", ROOT / "scripts/build_tanyuan.py")
installer = module("tanyuan_installer", ROOT / "tanyuan/install.py")


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tanyuan portable ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.bundle = self.root / "bundle"
        builder.build(self.bundle)
        self.workspace = self.root / "chosen workspace"
        self.workspace.mkdir()
        self.prefix = self.workspace / ".tanyuan/plugin"
        self.bin = self.workspace / ".tanyuan/bin"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("TANYUAN_", "PYTHON", "TUSHARE_", "TAVILY_", "DEEPSEEK_"))}
        self.env.update(PYTHONDONTWRITEBYTECODE="1", TANYUAN_CACHE=str(self.root / "cache"))

    def run_cli(self, command, cwd, expected=0):
        try:
            proc = subprocess.run(command, cwd=cwd, env=self.env, text=True,
                                  capture_output=True, timeout=30, check=True)
        except subprocess.CalledProcessError as exc:
            proc = exc
        self.assertEqual(proc.returncode, expected, proc.stderr)
        return proc.stdout

    def install(self):
        self.run_cli([sys.executable, str(self.bundle / "install.py"), "--host", "claude", "codex", "dsh",
                      "--workspace", str(self.workspace), "--python", sys.executable], cwd=self.root)

    def test_installed_commands_resources_and_outputs_in_unrelated_workspaces(self):
        self.install()
        self.install()  # same install is idempotent, all links keep their target
        shutil.rmtree(self.bundle)  # install is not a link to the distribution or checkout
        installed = self.prefix / "skills/tanyuan"
        for host_dir in installer.HOST_DIRS.values():
            entry = self.workspace / host_dir / "tanyuan"
            self.assertEqual(entry.resolve(), installed)
            self.assertEqual((entry / "SKILL.md").read_bytes(), (installed / "SKILL.md").read_bytes())
            self.run_cli([str(entry / "bin/ty"), "--version"], self.root)
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.prefix.rglob("*") if p.is_file()}
        for name in ("client A", "另一份工作区"):
            workspace = self.root / name
            workspace.mkdir()
            result = self.run_cli([str(self.bin / "ty"), "tools"], workspace)
            self.assertTrue(json.loads(result)["ok"])
            (workspace / "report.json").write_text(result, encoding="utf-8")
            desc = self.run_cli([str(installed / "bin/ty"), "describe", "fetch.url"], workspace)
            self.assertTrue(json.loads(desc)["ok"])
            status = self.run_cli([str(installed / "bin/ty"), "garden", "status"], workspace)
            self.assertTrue(json.loads(status)["ok"])
            error = self.run_cli([str(self.bin / "ty"), "call", "not.a.tool"], workspace, expected=8)
            self.assertEqual(json.loads(error)["error"]["code"], "UNSUPPORTED")
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.prefix.rglob("*") if p.is_file()}
        self.assertEqual(before, after, "using the product must not write reports/cache/bytecode into it")

    def test_build_allowlist_excludes_secrets_tests_reports_and_unlisted_files(self):
        source = self.root / "source"
        shutil.copytree(ROOT / "tanyuan", source)
        for name in (".env", "evals/golden.jsonl", "tests/fixture.py", "reports/score.json",
                     "references/answers.md", "tanyuan/unlisted.py"):
            p = source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("UNIQUE_PRIVATE_SENTINEL", encoding="utf-8")
        out = self.root / "clean"
        builder.build(out, source)
        for p in out.rglob("*"):
            if p.is_file():
                self.assertNotIn(b"UNIQUE_PRIVATE_SENTINEL", p.read_bytes())
        inventory = installer.inventory(out)
        self.assertEqual(set(inventory["files"]), {
            p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.name != "manifest.json"
        })

    def test_builder_refuses_runtime_dependency_on_evaluation(self):
        source = self.root / "source"
        shutil.copytree(ROOT / "tanyuan", source)
        p = source / "tanyuan/config.py"
        p.write_text(p.read_text() + "\nimport evals\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            builder.build(self.root / "bad", source)
        self.assertFalse((self.root / "bad").exists())

    def test_build_refuses_existing_output_and_symlinked_resource(self):
        with self.assertRaises(ValueError):
            builder.build(self.bundle)
        source = self.root / "source"
        shutil.copytree(ROOT / "tanyuan", source)
        resource = source / "references/evidence-rules.md"
        resource.unlink()
        resource.symlink_to(ROOT / "qa/tanyuan/evals/golden.jsonl")
        with self.assertRaises(ValueError):
            builder.build(self.root / "bad", source)

    def test_installer_refuses_corruption_before_writing(self):
        (self.bundle / "skills/tanyuan/SKILL.md").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            installer.install(self.bundle, self.workspace, ["codex"], sys.executable)
        self.assertFalse(self.prefix.exists())

    def test_installer_refuses_path_traversal_and_symlink_members(self):
        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["../outside"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            installer.inventory(self.bundle)
        del manifest["files"]["../outside"]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        skill = self.bundle / "skills/tanyuan/SKILL.md"
        external = self.root / "external"
        shutil.copyfile(skill, external)
        skill.unlink()
        skill.symlink_to(external)
        with self.assertRaises(ValueError):
            installer.inventory(self.bundle)

    def test_installer_preserves_conflicting_registration(self):
        conflict = self.workspace / ".claude/skills/tanyuan"
        conflict.mkdir(parents=True)
        (conflict / "keep.txt").write_text("user data", encoding="utf-8")
        with self.assertRaises(ValueError):
            installer.install(self.bundle, self.workspace, ["claude"], sys.executable)
        self.assertEqual((conflict / "keep.txt").read_text(), "user data")
        self.assertFalse(self.prefix.exists())

    def test_installer_preserves_existing_command(self):
        self.bin.mkdir(parents=True)
        (self.bin / "ty").write_text("user command", encoding="utf-8")
        with self.assertRaises(ValueError):
            installer.install(self.bundle, self.workspace, ["codex"], sys.executable)
        self.assertEqual((self.bin / "ty").read_text(), "user command")
        self.assertFalse(self.prefix.exists())

    def test_dependency_setup_failure_does_not_register_and_can_be_retried(self):
        with patch.object(installer, 'run', side_effect=subprocess.CalledProcessError(1, ['dependency-setup'])):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(self.bundle, self.workspace, ['codex'], None)
        self.assertFalse((self.bin / 'ty').is_symlink())
        self.assertFalse((self.workspace / '.agents/skills/tanyuan').is_symlink())
        self.install()
        self.assertTrue((self.bin / 'ty').is_symlink())

    def test_workspace_required_and_no_global_default(self):
        self.run_cli([sys.executable, str(self.bundle / "install.py"),
                      "--host", "codex"], self.root, expected=2)
        self.assertFalse(self.prefix.exists())
        with patch.object(installer.Path, "home", side_effect=AssertionError("global home accessed")):
            installer.install(self.bundle, self.workspace, ["codex"], sys.executable)
        self.assertTrue((self.workspace / ".agents/skills/tanyuan").is_symlink())

    def test_workspace_symlink_cannot_redirect_installation(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.workspace / ".tanyuan").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install(self.bundle, self.workspace, ["codex"], sys.executable)
        self.assertEqual(list(outside.iterdir()), [])

    def test_configuration_precedence_without_global_or_parent_fallback(self):
        from tanyuan import config
        (self.workspace / ".env").write_text("TY_TEST_A=workspace\nTY_TEST_C=workspace\n", encoding="utf-8")
        explicit = self.root / "explicit.env"
        explicit.write_text("TY_TEST_A=explicit\nTY_TEST_B=explicit\n", encoding="utf-8")
        with patch.dict(os.environ, {"TANYUAN_ENV_FILE": str(explicit), "TY_TEST_A": "process"}, clear=True), \
                patch.object(config, "workspace_root", return_value=self.workspace), \
                patch.object(config, "_DOTENV_LOADED", False), \
                patch.object(config, "_DOTENV_VALUES", {}):
            self.assertEqual(config.secret("TY_TEST_A"), "process")
            self.assertEqual(config.secret("TY_TEST_B"), "explicit")
            self.assertIsNone(config.secret("TY_TEST_C"))
            self.assertNotIn("TY_TEST_B", os.environ)
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(config, "workspace_root", return_value=self.workspace), \
                patch.object(config, "_DOTENV_LOADED", False), \
                patch.object(config, "_DOTENV_VALUES", {}):
            self.assertEqual(config.secret("TY_TEST_C"), "workspace")
            self.assertNotIn("TY_TEST_C", os.environ)

    def test_installed_configuration_follows_selected_workspace(self):
        self.install()
        (self.workspace / ".env").write_text("TY_WORKSPACE_TEST=selected\n", encoding="utf-8")
        other = self.root / "other workspace"
        other.mkdir()
        (other / ".env").write_text("TY_WORKSPACE_TEST=wrong\nTY_OTHER=wrong\n", encoding="utf-8")
        global_config = self.root / "fake home/.config/tanyuan"
        global_config.mkdir(parents=True)
        (global_config / ".env").write_text("TY_GLOBAL=wrong\n", encoding="utf-8")
        self.env["HOME"] = str(self.root / "fake home")
        self.env.pop("TANYUAN_CACHE", None)
        code = "import sys,json; sys.path.insert(0,sys.argv[1]); from tanyuan import config; print(json.dumps([str(config.workspace_root()),str(config.CACHE_DIR),config.secret('TY_WORKSPACE_TEST'),config.secret('TY_OTHER'),config.secret('TY_GLOBAL')]))"
        result = json.loads(self.run_cli([sys.executable, "-c", code,
                            str(self.prefix / "skills/tanyuan")], other))
        self.assertEqual(result, [str(self.workspace), str(self.workspace / ".tanyuan/cache"), "selected", None, None])
        (self.workspace / ".env").unlink()
        result = json.loads(self.run_cli([sys.executable, "-c", code,
                            str(self.prefix / "skills/tanyuan")], other))
        self.assertEqual(result[2:], [None, None, None])

    def test_existing_environment_files_are_preserved(self):
        for name in (".env", ".env.example"):
            (self.workspace / name).write_text("USER_FILE", encoding="utf-8")
        self.install()
        for name in (".env", ".env.example"):
            self.assertEqual((self.workspace / name).read_text(), "USER_FILE")

    def test_bad_interpreter_is_explicit_failure(self):
        self.env["TANYUAN_PYTHON"] = "/does/not/exist/python"
        out = self.run_cli([sys.executable, str(self.bundle / "skills/tanyuan/bin/ty"), "tools"],
                           self.root, expected=1)
        self.assertEqual(out, "")

    def test_interpreter_path_with_parent_segments_does_not_reexec_forever(self):
        executable = Path(sys.executable)
        self.env["TANYUAN_PYTHON"] = str(executable.parent / ".." / executable.parent.name / executable.name)
        out = self.run_cli([sys.executable, str(self.bundle / "skills/tanyuan/bin/ty"), "--version"], self.root)
        self.assertEqual(json.loads(out)["version"], installer.inventory(self.bundle)["version"])


if __name__ == "__main__":
    unittest.main()
