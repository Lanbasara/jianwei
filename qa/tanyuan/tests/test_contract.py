"""Contract tests — the parts that must never regress.

Offline by default. Set TANYUAN_LIVE=1 to also hit upstreams.
Run: .venv/bin/python -m unittest discover -s qa/tanyuan/tests -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str((Path(__file__).resolve().parents[3] / "tanyuan")))

from tanyuan import cache, registry  # noqa: E402
from tanyuan.errors import Code, TanyuanError, bad_arg  # noqa: E402
from tanyuan.util import resolve_symbol  # noqa: E402


def live_checks_enabled(value: str | None) -> bool:
    """Only the exact documented opt-in enables network tests."""
    return value == "1"


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


@unittest.skipUnless(live_checks_enabled(os.environ.get("TANYUAN_LIVE")),
                     "set TANYUAN_LIVE=1 for live checks")
class TestLive(unittest.TestCase):
    """Smoke the real upstreams. Not part of CI-by-default (network + rate limits)."""

    @classmethod
    def setUpClass(cls):
        registry.load_tools()

    def _call(self, tool, args):
        envelope, code = registry.dispatch(tool, args, use_cache=False)
        return envelope, code

    def test_quote(self):
        env, code = self._call("market.quote", {"codes": ["600519"]})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertGreater(env["data"]["quotes"]["600519"]["price"], 0)
        self.assertEqual(env["provenance"]["source_grade"], "L1")

    def test_kline(self):
        env, code = self._call("market.kline", {"code": "600519", "count": 5})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertEqual(len(env["data"]["bars"]), 5)

    def test_announcements(self):
        env, code = self._call("filing.announcements", {"code": "600519", "page_size": 3})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertEqual(env["provenance"]["source_grade"], "L0")
        for item in env["data"]["announcements"]:
            self.assertTrue(item["pdf_url"].startswith("https://static.cninfo.com.cn/"))

    def test_industry(self):
        env, code = self._call("industry.ranking", {"top_n": 3, "level": "l1"})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertEqual(len(env["data"]["top"]), 3)

    def test_lpr(self):
        env, code = self._call("macro.lpr", {"limit": 3})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertIsInstance(env["data"]["items"][0]["lpr_1y"], float)

    def test_financials_returns_raw_values(self):
        env, code = self._call("fundamental.financials", {"code": "600519", "periods": 1})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:300])
        period = env["data"]["periods"][0]
        # data layer must NOT translate: raw string + honest ratio naming
        self.assertIsInstance(period["items"]["营业总收入"], str)
        self.assertNotIn("amount_unit", period)
        self.assertIn("yoy_ratio", period)
        self.assertNotIn("yoy_pct", period)

    def test_daily_series_and_adjust(self):
        env, code = self._call("market.daily",
                               {"code": "600519", "start_date": "2026-10-20", "adjust": "qfq"})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertTrue(env["data"]["bars"])
        self.assertEqual(env["provenance"]["units"]["amount"], "千元")

    def test_daily_market_mode(self):
        env, code = self._call("market.daily", {"date": "2026-10-23", "limit": 5})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertGreaterEqual(env["data"]["universe_size"], 5000)
        self.assertEqual(len(env["data"]["bars"]), 5)

    def test_valuation_history(self):
        env, code = self._call("fundamental.valuation_history",
                               {"code": "600519", "start_date": "2026-10-20"})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertGreater(env["data"]["series"][0]["pe_ttm"], 0)

    def test_earnings_by_ann_date(self):
        env, code = self._call("fundamental.earnings", {"ann_date": "2026-10-23", "limit": 3})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertTrue(env["data"]["events"][0]["ann_date"])

    def test_flow(self):
        env, code = self._call("market.flow", {"code": "600519", "limit": 2})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertIsNotNone(env["data"]["series"][0]["net_mf_amount_wan"])

    def test_yield_curve(self):
        env, code = self._call("macro.yield_curve", {"curve": "treasury", "start_date": "2026-10-01"})
        if not env["ok"]:
            # ChinaBond is an official but slow/flaky endpoint (audit: 3.6s–32.9s tail).
            # Transient unavailability is acceptable; wrong data is not.
            self.assertIn(env["error"]["code"], ("SOURCE_UNAVAILABLE", "RATE_LIMITED"),
                          json.dumps(env, ensure_ascii=False)[:400])
            self.skipTest(f"中债暂时不可用: {env['error']['message']}")
        self.assertGreater(env["data"]["series"][0]["10y"], 0)

    def test_limit_list(self):
        env, code = self._call("market.limit_list", {"limit": 3})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertIn("counts", env["data"])

    def test_ambiguous_code_is_error(self):
        env, code = self._call("market.quote", {"codes": ["000001"]})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], "AMBIGUOUS")
        names = {c["name"] for c in env["error"]["candidates"]}
        self.assertEqual(names, {"上证指数", "平安银行"})

    def test_kline_non_trading_day_does_not_silently_fallback(self):
        env, code = self._call("market.kline",
                               {"code": "600519", "start": "2026-10-24", "end": "2026-10-24"})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], "NO_DATA")
        self.assertIn("周末", env["error"]["hint"])

    def test_foreign_symbol_is_unsupported(self):
        env, code = self._call("market.quote", {"codes": ["00700"]})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], "UNSUPPORTED")
        self.assertEqual(code, 8)

    def test_delisted_requires_flag(self):
        env, code = self._call("entity.resolve", {"query": "920680"})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], "NOT_FOUND")
        self.assertIn("include_delisted", env["error"]["hint"])
        env2, _ = self._call("entity.resolve", {"query": "920680", "include_delisted": True})
        self.assertTrue(env2["ok"], json.dumps(env2, ensure_ascii=False)[:300])

    def test_search_noise_detected_as_no_data(self):
        env, code = self._call("search.web",
                               {"query": "zxqjwvbnrrtyuiopgfdsa 1234567890 qpzm", "count": 5})
        self.assertFalse(env["ok"], json.dumps(env, ensure_ascii=False)[:300])
        self.assertEqual(env["error"]["code"], "NO_DATA")

    def test_fetch_pdf_returns_full_blob_and_pages(self):
        url = "https://static.cninfo.com.cn/finalpage/2026-08-15/1225475868.PDF"
        env, code = self._call("fetch.url", {"url": url})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:300])
        data = env["data"]
        self.assertGreater(data["total_chars"], 100_000)   # 110-page report
        self.assertTrue(data["truncated"])
        self.assertEqual(data["returned_chars"], 12000)
        self.assertEqual(data["next_offset"], 12000)
        artifact = env["artifacts"][0]
        from pathlib import Path
        self.assertTrue(Path(artifact["path"]).is_file())
        self.assertEqual(len(Path(artifact["path"]).read_text(encoding="utf-8")),
                         data["total_chars"])
        # page to the tail using the stored blob (no upstream call)
        env2, _ = self._call("fetch.url", {"url": url, "offset": data["total_chars"] - 500})
        self.assertTrue(env2["ok"])
        self.assertFalse(env2["data"]["truncated"])
        self.assertEqual(env2["data"]["returned_chars"], 500)
        self.assertEqual(env2["provenance"]["source"], "blob")

    def test_fetch_pdf_via_auto_router(self):
        env, code = self._call("fetch.url", {
            "url": "https://static.cninfo.com.cn/finalpage/2026-08-15/1225475868.PDF",
            "max_chars": 600,
        })
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:300])
        # first fetch goes to tavily_extract (PDF); later calls may be served from the blob
        self.assertIn(env["provenance"]["source"], ("tavily_extract", "blob"))

    def test_global_quote_hk_us(self):
        env, code = self._call("market.quote_global", {"codes": ["00700", "AAPL"]})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        hk = env["data"]["quotes"]["00700"]
        self.assertEqual(hk["currency"], "HKD")
        us = env["data"]["quotes"]["AAPL"]
        self.assertEqual(us["currency"], "USD")
        self.assertGreater(hk["price"], 0)

    def test_global_kline_hk_and_us(self):
        for code in ("00700", "AAPL"):
            env, _ = self._call("market.kline_global", {"code": code, "count": 3})
            self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
            self.assertEqual(len(env["data"]["bars"]), 3)

    def test_a_share_code_rejected_by_global_quote(self):
        env, code = self._call("market.quote_global", {"codes": ["600519"]})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], "UNSUPPORTED")
        self.assertIn("market.quote", env["error"]["message"])

    def test_sec_filings(self):
        env, code = self._call("filing.sec", {"ticker": "AAPL", "forms": ["10-K"], "limit": 2})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertEqual(env["provenance"]["source_grade"], "L0")
        self.assertTrue(env["data"]["filings"][0]["primary_doc_url"].startswith("https://www.sec.gov/"))

    def test_global_rates(self):
        env, code = self._call("macro.global_rates", {"kind": "yield_curve", "limit": 2})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertGreater(env["data"]["items"][0]["10y"], 0)
        env2, _ = self._call("macro.global_rates", {"kind": "sofr", "limit": 2})
        self.assertTrue(env2["ok"], json.dumps(env2, ensure_ascii=False)[:400])

    def test_sec_daily_index(self):
        env, code = self._call("filing.sec_index", {"date": "2026-10-23", "forms": ["8-K"], "limit": 2})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertGreater(env["data"]["total_filings"], 1000)
        self.assertEqual(env["data"]["filings"][0]["form"], "8-K")
        self.assertTrue(env["data"]["filings"][0]["url"].startswith("https://www.sec.gov/Archives/"))

    def test_policy_news_source_set(self):
        env, code = self._call("news.feed",
                               {"source_set": "policy-news", "sector": "cn_policy", "since": "720h"})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertIn("coverage", env["data"])
        self.assertGreaterEqual(env["data"]["coverage"]["ok_sources"], 1)

    def test_news_feed(self):
        env, code = self._call("news.feed", {"sectors": ["ai"], "since": "240h", "limit": 3})
        self.assertTrue(env["ok"], json.dumps(env, ensure_ascii=False)[:400])
        self.assertLessEqual(len(env["data"]["items"]), 3)
        self.assertGreaterEqual(env["data"]["coverage"]["ok_sources"], 1)

    def test_unsupported_bse_kline(self):
        env, code = self._call("market.kline", {"code": "920021"})
        self.assertFalse(env["ok"])
        self.assertEqual(env["error"]["code"], "UNSUPPORTED")
        self.assertTrue(env["error"].get("alternatives"))


if __name__ == "__main__":
    unittest.main()
