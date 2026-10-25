"""Offline release and runtime verification, deliberately outside the product."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from build_jianwei import build, relative_file, target_path


class Distribution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="jianwei release ")
        cls.base = Path(cls.temp.name).resolve()
        cls.bundle = cls.base / "installed plugin"
        build(cls.bundle)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.base, prefix="task "))
        self.home = self.work / "user data"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("TANYUAN_", "JIANWEI_"))}
        self.env.update(JIANWEI_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")

    def run_cli(self, args, *, cwd=None, env=None):
        return subprocess.run([sys.executable, str(self.bundle / "skills/tanyuan/bin/ty"), *args],
                              env=env or self.env, cwd=cwd or self.work, capture_output=True, text=True)

    def setup_runtime(self, *args):
        return subprocess.run([sys.executable, str(self.bundle / "scripts/setup.py"),
                               "--python", sys.executable, *args], cwd=self.work,
                              env=self.env, capture_output=True, text=True)

    def test_component_and_research_are_unchanged(self):
        manifest = json.loads((self.bundle / "manifest.json").read_text())
        for relative, digest in manifest["files"].items():
            self.assertEqual(hashlib.sha256((self.bundle / relative).read_bytes()).hexdigest(), digest)
        for relative, digest in manifest["components"]["tanyuan"]["source_files"].items():
            path = self.bundle / relative
            if relative == "skills/tanyuan/bin/ty":
                path = path.with_name("ty-upstream")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertEqual((self.bundle / "skills/research/SKILL.md").read_bytes(),
                         (ROOT / "jianwei/skills/research/SKILL.md").read_bytes())

    def test_source_layers_and_adapter_inventory_are_explicit(self):
        source = ROOT / "jianwei"
        distribution = json.loads((source / "distribution.json").read_text(encoding="utf-8"))
        self.assertEqual(set(distribution["adapters"]), {"claude", "codex", "dsh"})
        declared = set()
        for adapter in distribution["adapters"].values():
            declared.update(adapter["templates"])
            declared.update(adapter["files"])
        actual = {path.relative_to(source).as_posix()
                  for path in (source / "adapters").rglob("*") if path.is_file()}
        self.assertEqual(actual, declared)
        self.assertFalse(any(path.name == "SKILL.md" for path in (source / "adapters").rglob("*")))
        self.assertEqual(set(distribution["runtime_files"]), {
            "runtime/runtime.py", "runtime/setup.py", "runtime/ty-launcher"
        })

    def test_distribution_paths_reject_escape_and_links(self):
        with self.assertRaises(ValueError):
            target_path(self.bundle, "../outside")
        with self.assertRaises(ValueError):
            target_path(self.bundle, "/absolute")
        source = self.work / "declared source"
        source.mkdir()
        outside = self.work / "outside"
        outside.write_text("fixture", encoding="utf-8")
        (source / "linked").symlink_to(outside)
        with self.assertRaises(ValueError):
            relative_file(source, "linked")

    def test_host_manifests_share_identity_and_skill_catalog(self):
        distribution = json.loads((ROOT / "jianwei/distribution.json").read_text(encoding="utf-8"))
        portable = json.loads((self.bundle / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((self.bundle / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
        codex = json.loads((self.bundle / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        dsh = json.loads((self.bundle / "package.json").read_text(encoding="utf-8"))
        identity_fields = ("name", "version", "description", "author")
        for manifest in (claude, codex, dsh):
            self.assertEqual({key: manifest[key] for key in identity_fields},
                             {key: portable[key] for key in identity_fields})
        catalog = json.loads((self.bundle / "host/catalog.json").read_text(encoding="utf-8"))
        self.assertEqual([entry["name"] for entry in catalog], distribution["skills"])
        self.assertEqual(sorted(path.name for path in (self.bundle / "skills").iterdir()),
                         sorted(distribution["skills"]))
        for source_only in ("AGENTS.md", "ARCHITECTURE.md", "distribution.json", "adapters", "runtime"):
            self.assertFalse((self.bundle / source_only).exists(), source_only)

    def test_payload_has_no_development_material_or_local_paths(self):
        forbidden = {"qa", "tests", "evals", "experiments", "reports", "__pycache__", ".venv", ".env"}
        for path in self.bundle.rglob("*"):
            self.assertFalse(forbidden.intersection(path.relative_to(self.bundle).parts), path)
            if path.is_file():
                self.assertNotIn(str(ROOT).encode(), path.read_bytes(), path)
        self.assertFalse((self.bundle / "skills/report").exists())

    def test_unconfigured_launcher_gives_exact_setup_path_without_writing(self):
        result = self.run_cli(["--version"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.bundle / "scripts/setup.py"), result.stderr)
        self.assertFalse(self.home.exists())

    def test_runtime_reuse_across_directories_and_config_preservation(self):
        result = self.setup_runtime()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.home / "config.env"
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        config.write_text("TUSHARE_TOKEN=fixture-not-a-real-secret\n", encoding="utf-8")
        before = config.read_bytes()
        self.assertEqual(self.setup_runtime().returncode, 0)
        self.assertEqual(config.read_bytes(), before)
        other = self.work / "another directory"
        other.mkdir()
        for cwd in [self.work, other]:
            result = self.run_cli(["--version"], cwd=cwd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("0.5.0", result.stdout)
        result = self.run_cli(["tools"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list(self.bundle.rglob("__pycache__")))
        self.assertFalse(list(self.bundle.rglob(".venv")))

    def test_existing_config_selection_stores_only_path(self):
        config = self.work / "existing.env"
        config.write_text("TUSHARE_TOKEN=fixture-not-a-real-secret\n", encoding="utf-8")
        result = self.setup_runtime("--env-file", str(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("fixture-not-a-real-secret", result.stdout + result.stderr)
        self.assertEqual((self.home / "env-file").read_text().strip(), str(config))
        self.assertFalse((self.home / "config.env").exists())

    def test_explicit_env_and_cwd_reach_unmodified_config_module(self):
        self.assertEqual(self.setup_runtime().returncode, 0)
        probe = self.work / "probe bundle"
        shutil.copytree(self.bundle, probe)
        # This test-only entry inspects the real upstream config module.
        (probe / "skills/tanyuan/bin/ty-upstream").write_text(
            "import json,sys,os\nfrom pathlib import Path\n"
            "sys.path.insert(0,str(Path(__file__).resolve().parents[1]))\n"
            "from tanyuan.config import secret,CACHE_DIR\n"
            "print(json.dumps({'selected':secret('TUSHARE_TOKEN')=='override-fixture',"
            "'cache':str(CACHE_DIR),'cwd':os.getcwd()}))\n", encoding="utf-8")
        config = self.work / "override.env"
        config.write_text("TUSHARE_TOKEN=override-fixture\n", encoding="utf-8")
        cache = self.work / "selected cache"
        env = dict(self.env, TANYUAN_ENV_FILE=str(config), TANYUAN_CACHE=str(cache))
        result = subprocess.run([sys.executable, str(probe / "skills/tanyuan/bin/ty")],
                                cwd=self.work, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), {"selected": True, "cache": str(cache), "cwd": str(self.work)})

    def test_linked_home_is_rejected(self):
        target = self.work / "unrelated"
        target.mkdir()
        self.home.symlink_to(target, target_is_directory=True)
        result = self.setup_runtime()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(target.iterdir()), [])

    def test_missing_config_does_not_fall_back_to_task_credentials(self):
        self.assertEqual(self.setup_runtime().returncode, 0)
        (self.home / "config.env").unlink()
        (self.work / ".env").write_text("TUSHARE_TOKEN=wrong-context\n", encoding="utf-8")
        result = self.run_cli(["--version"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("凭据文件不存在", result.stderr)


if __name__ == "__main__":
    unittest.main()
