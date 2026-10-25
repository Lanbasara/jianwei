"""Garden tests — offline. They exercise the approval gate and the registry lifecycle
without touching the real source sets or the network."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str((Path(__file__).resolve().parents[3] / "tanyuan")))

from tanyuan import garden  # noqa: E402


def _item(hours_ago: float) -> dict:
    ts = time.time() - hours_ago * 3600
    return {"title": "t", "url": "https://x/1", "published_ts": ts, "published": "2026-10-24T00:00+08:00"}


class TestGarden(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        sets = self.tmp / "source-sets"
        sets.mkdir()
        registry = {
            "version": 1,
            "source_set": "test-set",
            "sectors": {"cn_policy": "中国政策"},
            "sources": [
                {"name": "good", "sector": "cn_policy", "url": "https://example.com/good",
                 "grade": "L2"},
                {"name": "dead", "sector": "cn_policy", "url": "https://example.com/dead",
                 "grade": "L2"},
                {"name": "old", "sector": "cn_policy", "url": "https://example.com/old",
                 "grade": "L2"},
            ],
        }
        (sets / "test-set.json").write_text(json.dumps(registry), encoding="utf-8")
        self.sets = sets
        self.reg_path = sets / "test-set.json"
        self._orig = garden.SOURCE_SETS
        garden.SOURCE_SETS = sets
        self.addCleanup(self._restore)

    def _restore(self):
        garden.SOURCE_SETS = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_fetch(self, source, timeout, attempts):
        if "dead" in source["url"]:
            return source, [], "HTTP 500"
        if "old" in source["url"]:
            return source, [_item(24 * 60)], None       # 60 days old
        return source, [_item(1), _item(2), _item(3)], None

    def test_check_is_read_only(self):
        before = self.reg_path.read_text(encoding="utf-8")
        with patch("tanyuan.tools.newsfeed._fetch_one", side_effect=self._fake_fetch):
            result = garden.check("test-set")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["counts"], {"ok": 2, "failed": 1})
        self.assertEqual(self.reg_path.read_text(encoding="utf-8"), before, "check must not mutate")

    def test_plan_proposes_and_requires_approval(self):
        with patch("tanyuan.tools.newsfeed._fetch_one", side_effect=self._fake_fetch):
            proposal = garden.plan("test-set")
        self.assertTrue(proposal["requires_human_approval"])
        actions = {a["name"]: a["action"] for a in proposal["actions"]}
        self.assertEqual(actions["old"], "disable")
        self.assertEqual(actions["dead"], "watch")   # first failure → watch, not disable
        self.assertNotIn("good", actions)
        self.assertEqual(proposal["registry_sha256"],
                         garden.load_registry("test-set")[2])

    def test_apply_refuses_without_human_approval(self):
        with patch("tanyuan.tools.newsfeed._fetch_one", side_effect=self._fake_fetch):
            proposal = garden.plan("test-set")
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(proposal), encoding="utf-8")
        with self.assertRaises(Exception) as ctx:
            garden.apply_plan(str(plan_path))
        self.assertIn("需要人类批准", str(ctx.exception))
        self.assertNotIn("status", json.loads(self.reg_path.read_text(encoding="utf-8"))["sources"][0])

    def test_apply_with_yes_writes_backup_and_status(self):
        with patch("tanyuan.tools.newsfeed._fetch_one", side_effect=self._fake_fetch):
            proposal = garden.plan("test-set")
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(proposal), encoding="utf-8")
        result = garden.apply_plan(str(plan_path), yes=True)
        self.assertTrue(Path(result["backup"]).is_file(), "backup must exist before write")
        registry = json.loads(self.reg_path.read_text(encoding="utf-8"))
        by_name = {s["name"]: s for s in registry["sources"]}
        self.assertEqual(by_name["old"]["status"], "disabled")
        self.assertEqual(by_name["dead"]["status"], "watch")
        self.assertEqual(by_name["good"].get("status", "active"), "active")
        self.assertTrue(by_name["good"].get("last_checked"))
        self.assertGreaterEqual(result["stamped"], 3)
        # disabled sources stay in the file (never deleted)
        self.assertIn("old", by_name)
        # restore returns to the pre-apply bytes
        garden.restore(result["backup"], source_set="test-set", yes=True)
        restored = {s["name"]: s for s in
                    json.loads(self.reg_path.read_text(encoding="utf-8"))["sources"]}
        self.assertIsNone(restored["old"].get("status"))

    def test_apply_refuses_stale_plan(self):
        with patch("tanyuan.tools.newsfeed._fetch_one", side_effect=self._fake_fetch):
            proposal = garden.plan("test-set")
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(proposal), encoding="utf-8")
        registry = json.loads(self.reg_path.read_text(encoding="utf-8"))
        registry["note"] = "edited after the plan was generated"
        self.reg_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaises(Exception) as ctx:
            garden.apply_plan(str(plan_path), yes=True)
        self.assertIn("plan 已过期", str(ctx.exception))

    def test_revive_proposal_for_healthy_disabled_source(self):
        registry = json.loads(self.reg_path.read_text(encoding="utf-8"))
        registry["sources"].append({"name": "back", "sector": "cn_policy",
                                    "url": "https://example.com/back", "grade": "L2",
                                    "status": "disabled"})
        self.reg_path.write_text(json.dumps(registry), encoding="utf-8")

        def fake(source, timeout, attempts):
            return source, [_item(2), _item(3), _item(4)], None

        with patch("tanyuan.tools.newsfeed._fetch_one", side_effect=fake):
            proposal = garden.plan("test-set")
        revived = [a for a in proposal["actions"] if a["action"] == "revive"]
        self.assertEqual([a["name"] for a in revived], ["back"])


if __name__ == "__main__":
    unittest.main()
