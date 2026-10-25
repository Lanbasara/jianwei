"""Standalone deterministic checks for local evidence and live-test opt-in."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from evidence import EvidencePathError, evidence_root, golden_path  # noqa: E402
from test_contract import live_checks_enabled  # noqa: E402


class EvidenceSecurityTests(unittest.TestCase):
    def test_root_requires_explicit_absolute_existing_directory(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(EvidencePathError):
            evidence_root()
        with patch.dict(os.environ, {"JIANWEI_QA_HOME": "relative/golden"}, clear=True), \
                self.assertRaises(EvidencePathError):
            evidence_root()
        with patch.dict(os.environ, {"JIANWEI_QA_HOME": "/does-not-exist"}, clear=True), \
                self.assertRaises(EvidencePathError):
            evidence_root()

    def test_paths_cannot_escape_root_or_follow_an_escaping_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root = base / "golden"
            root.mkdir()
            raw = root / "raw"
            raw.mkdir()
            item = raw / "item.json"
            item.write_text("{}", encoding="utf-8")
            self.assertEqual(golden_path(root, "artifacts/golden/raw/item.json"), item.resolve())
            with self.assertRaises(EvidencePathError):
                golden_path(root, "artifacts/golden/../../outside.json")
            (root / "escape").symlink_to(base)
            with self.assertRaises(EvidencePathError):
                golden_path(root, "artifacts/golden/escape/outside.json")

    def test_live_checks_require_exact_one(self):
        for value in (None, "", "0", "false", "true", "yes", "01"):
            with self.subTest(value=value):
                self.assertFalse(live_checks_enabled(value))
        self.assertTrue(live_checks_enabled("1"))


if __name__ == "__main__":
    unittest.main()
