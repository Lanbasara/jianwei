"""Deterministic offline contract tests."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str((Path(__file__).resolve().parents[3] / "tanyuan")))

from tanyuan import cache, registry  # noqa: E402
from tanyuan.errors import Code, TanyuanError, bad_arg  # noqa: E402
from tanyuan.util import resolve_symbol  # noqa: E402
class TestSymbolResolution(unittest.TestCase):
    def test_prefixes(self):
        self.assertEqual(resolve_symbol("600519").prefixed, "sh600519")
        with self.assertRaises(TanyuanError):
            resolve_symbol("000001")
        self.assertEqual(resolve_symbol("sh000001").prefixed, "sh000001")
        self.assertEqual(resolve_symbol("sh000001").kind, "index")
        self.assertEqual(resolve_symbol("000300").prefixed, "sh000300")
        self.assertEqual(resolve_symbol("399006").prefixed, "sz399006")
        self.assertEqual(resolve_symbol("920021").prefixed, "bj920021")
        self.assertEqual(resolve_symbol("510300").prefixed, "sh510300")

    def test_suffix_and_joinquant_spellings(self):
        self.assertEqual(resolve_symbol("600519.SH").prefixed, "sh600519")
        self.assertEqual(resolve_symbol("000001.SZ").prefixed, "sz000001")
        self.assertEqual(resolve_symbol("600519.XSHG").prefixed, "sh600519")
        self.assertEqual(resolve_symbol("SH600519").prefixed, "sh600519")

    def test_nonsense_raises(self):
        for bad in ["", "60051", "6005190", "foo600519bar", "SH000001.SZ", "贵州茅台"]:
            with self.assertRaises(ValueError):
                resolve_symbol(bad)


class TestErrors(unittest.TestCase):
    def test_exit_codes(self):
        self.assertEqual(bad_arg("x").exit_code, 2)
        self.assertEqual(TanyuanError(Code.NO_DATA, "x").exit_code, 3)
        self.assertEqual(TanyuanError(Code.SOURCE_UNAVAILABLE, "x").exit_code, 4)
        self.assertEqual(TanyuanError(Code.RATE_LIMITED, "x").exit_code, 6)
        self.assertEqual(TanyuanError(Code.UNSUPPORTED, "x").exit_code, 8)

    def test_retryable_default(self):
        self.assertFalse(bad_arg("x").retryable)
        self.assertTrue(TanyuanError(Code.SOURCE_UNAVAILABLE, "x").retryable)
        self.assertTrue(TanyuanError(Code.RATE_LIMITED, "x").retryable)
        self.assertFalse(TanyuanError(Code.NO_DATA, "x").retryable)

    def test_to_dict_shape(self):
        err = bad_arg("参数错", hint="改成 600519")
        payload = err.to_dict()
        self.assertEqual(payload["code"], "BAD_ARGUMENT")
        self.assertEqual(payload["hint"], "改成 600519")
        self.assertIn("retryable", payload)


class TestDispatcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_tools()

    def test_unknown_tool_is_unsupported_with_suggestions(self):
        envelope, code = registry.dispatch("market.quotez", {})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "UNSUPPORTED")
        self.assertEqual(code, 8)
        self.assertTrue(envelope["error"].get("alternatives"))

    def test_bad_argument_via_dispatcher(self):
        envelope, code = registry.dispatch("market.quote", {"nope": 1})
        self.assertEqual(envelope["error"]["code"], "BAD_ARGUMENT")
        self.assertEqual(code, 2)
        self.assertIn("hint", envelope["error"])

    def test_missing_required(self):
        envelope, code = registry.dispatch("market.kline", {})
        self.assertEqual(envelope["error"]["code"], "BAD_ARGUMENT")
        self.assertIn("code", envelope["error"]["message"])

    def test_plain_text_preserves_lines(self):
        from tanyuan.util import to_plain_text
        raw = "<p>第一段</p><p>第二段<br>尾行</p>"
        text = to_plain_text(raw)
        self.assertIn("\n", text)
        self.assertGreaterEqual(text.count("\n"), 2)

    def test_clip_text_metadata(self):
        from tanyuan.util import clip_text
        text = "x" * 1000
        clipped, meta = clip_text(text, 100)
        self.assertEqual(len(clipped), 100)
        self.assertEqual(meta["total_chars"], 1000)
        self.assertEqual(meta["next_offset"], 100)
        full, meta2 = clip_text(text, 2000)
        self.assertIsNone(meta2)
        self.assertEqual(len(full), 1000)

    def test_blob_roundtrip(self):
        from tanyuan import blobs
        text = "行1\n行2\n" * 1000
        rec = blobs.store(text, url="https://test.local/blob", kind="test")
        self.assertTrue(rec["persisted"])
        self.assertEqual(rec["chars"], len(text))
        slice_, total = blobs.read(rec["sha256"], 10, 20)
        self.assertEqual(total, len(text))
        self.assertEqual(slice_, text[10:30])
        self.assertEqual(blobs.latest_for_url("https://test.local/blob")["sha256"], rec["sha256"])

    def test_cli_arg_keeps_strings(self):
        from tanyuan.cli import _parse_value
        self.assertEqual(_parse_value("600519"), "600519")     # not int
        self.assertEqual(_parse_value("600519.5"), "600519.5")  # not float
        self.assertEqual(_parse_value("8"), "8")
        self.assertEqual(_parse_value("true"), True)
        self.assertEqual(_parse_value('["600519"]'), ["600519"])
        self.assertEqual(_parse_value('{"a":1}'), {"a": 1})

    def test_coercion_by_schema(self):
        from tanyuan.registry import _coerce
        self.assertEqual(_coerce("count", "8", "integer"), 8)
        self.assertEqual(_coerce("codes", '["600519"]', "array[string]"), ["600519"])
        self.assertIs(_coerce("flag", "true", "boolean"), True)
        with self.assertRaises(Exception):
            _coerce("count", "abc", "integer")

    def test_search_query_length_guard(self):
        long_query = "长" * 129
        envelope, code = registry.dispatch("search.web",
                                          {"query": long_query, "provider": "deepseek"})
        self.assertEqual(envelope["error"]["code"], "BAD_ARGUMENT")
        self.assertIn("128", envelope["error"]["message"])

    def test_enum_validation(self):
        envelope, code = registry.dispatch("market.kline",
                                           {"code": "600519", "period": "hour"})
        self.assertEqual(envelope["error"]["code"], "BAD_ARGUMENT")
        self.assertIn("period", envelope["error"]["message"])

    def test_tool_catalog_is_complete(self):
        self.assertGreaterEqual(len(registry.REGISTRY), 29)
        for name, spec in registry.REGISTRY.items():
            self.assertTrue(spec.summary, name)
            self.assertIn(spec.grade, {"L0", "L1", "L2", "L3"}, name)
            self.assertTrue(spec.args_schema or name == "market.ping", name)


class TestCache(unittest.TestCase):
    def test_key_is_order_insensitive(self):
        self.assertEqual(cache.make_key("t", {"a": 1, "b": 2}),
                         cache.make_key("t", {"b": 2, "a": 1}))

    def test_roundtrip_and_expiry(self):
        key = cache.make_key("test.tool", {"x": __name__})
        cache.put(key, {"ok": True, "data": {"v": 1}}, ttl_s=60)
        got, meta = cache.get(key)
        self.assertEqual(got["data"]["v"], 1)
        self.assertTrue(meta["hit"])
        key2 = cache.make_key("test.tool", {"x": __name__ + "-expired"})
        cache.put(key2, {"ok": True}, ttl_s=-1)
        self.assertEqual(cache.get(key2), (None, None))



if __name__ == "__main__":
    unittest.main()
