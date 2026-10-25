"""External synthetic contract/semantic tests; no experiments, answers or credentials imported."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tanyuan"))
from tanyuan import fund_data as data, fund_sources as free, fund_metrics as metrics, registry
from tanyuan.tools import fund
from tanyuan.errors import TanyuanError, Code, auth_failed, unavailable


IDENTITY = {"code": "123456", "ts_code": "123456.SH", "name": "合成ETF", "listed": True,
            "currency": None, "market": "E", "fund_type": "股票型"}


def nav_row(day, unit, adj=None, ann="2026-01-06"):
    return {"nav_date": day, "unit_nav": unit, "adj_nav": unit if adj is None else adj,
            "accum_nav": unit, "ann_date": ann, "source": "synthetic"}


class FundSources(unittest.TestCase):
    def test_directory_static_json_and_bom(self):
        with patch.object(free, "get", return_value='\ufeffvar r = [["123456","HC","合成基金A","混合型"]];'):
            self.assertEqual(free.directory()[0]["name"], "合成基金A")

    def test_directory_rejects_executable_payload(self):
        with patch.object(free, "get", return_value='var r = malicious();'):
            with self.assertRaises(TanyuanError) as cm:
                free.directory()
        self.assertEqual(cm.exception.code, Code.SOURCE_SCHEMA_CHANGED)

    def test_archive_rejects_javascript(self):
        with self.assertRaises(TanyuanError):
            free.archive_html('var apidata = { content: function(){return "x"}() };')

    def test_tables_ignore_scripts_and_missing_endtags(self):
        p = free.parse_html('<table><tr><th>键<td>值<th>另一键<td>另值</tr></table><script>BAD</script>')
        self.assertEqual(p.tables[0]["rows"], [["键", "值", "另一键", "另值"]])

    def test_money_yields_not_mislabelled_nav_zero_negative_retained(self):
        obj = {"ErrCode": 0, "PageSize": 20, "PageIndex": 1, "TotalCount": 2,
               "Data": {"SYType": "每万份收益", "FundType": "005", "LSJZList": [
                   {"FSRQ": "2026-01-01", "DWJZ": "0", "LJJZ": "1.2", "NAVTYPE": "1"},
                   {"FSRQ": "2026-01-02", "DWJZ": "-0.1", "LJJZ": "-0.2", "NAVTYPE": "1"}]}}
        with patch.object(free, "get", return_value=json.dumps(obj)):
            rows, _ = free.nav_page("123456", "2026-01-01", "2026-01-02", fund_type="货币型")
        self.assertEqual([r["income_per_10000_shares"] for r in rows], [0, -.1])
        self.assertEqual(rows[1]["seven_day_annualized_yield_pct"], -.2)
        self.assertTrue(all(r["unit_nav"] is None and r["accum_nav"] is None for r in rows))
        self.assertEqual(rows[0]["raw_values"]["DWJZ"], "0")

    def test_unknown_series_type_retains_raw_without_guessing_nav(self):
        obj = {"ErrCode": 0, "PageSize": 20, "PageIndex": 1, "TotalCount": 1,
               "Data": {"SYType": None, "LSJZList": [{"FSRQ": "2026-01-01", "DWJZ": "1.2", "LJJZ": "2"}]}}
        with patch.object(free, "get", return_value=json.dumps(obj)):
            rows, _ = free.nav_page("123456", "2026-01-01", "2026-01-02")
        self.assertEqual(rows[0]["series_kind"], "unknown")
        self.assertIsNone(rows[0]["unit_nav"])
        with patch.object(free, "get", return_value=json.dumps(obj)):
            rows, _ = free.nav_page("123456", "2026-01-01", "2026-01-02", fund_type="债券型")
        self.assertEqual(rows[0]["unit_nav"], 1.2)
        self.assertEqual(rows[0]["series_kind"], "nav")

    def test_confirmed_commodity_is_regular_nav_type(self):
        self.assertEqual(free.fund_series_kind("商品型"), "nav")
        self.assertEqual(free.fund_series_kind("未知型"), "unknown")

    def test_whitespace_header_keeps_correct_denominator(self):
        body = '<h4>截止至：2026-06-30</h4><table><tr><th>股票代码</th><th>占净值 比例</th><th>占股票资产比例</th></tr><tr><td>X</td><td>7.25%</td><td>9.50%</td></tr></table>'
        with patch.object(free, "get", return_value='var apidata={content:' + json.dumps(body) + '};'):
            row = free.holdings("123456", "2026-06-30", "stocks")[0]["rows"][0]
        self.assertEqual(row["nav_weight_pct"], 7.25)
        self.assertIn("占净值 比例", row["raw_cells"])

    def test_holding_columns_by_header_and_zero_preserved(self):
        html = ('<h4>截止至：2026-06-30</h4><table><tr><th>股票名称</th><th>占净值比例</th>'
                '<th>股票代码</th><th>持股数（万股）</th><th>持仓市值（万元人民币）</th></tr>'
                '<tr><td>示例</td><td>0.00%</td><td>00123</td><td>2,000</td><td>--</td></tr></table>')
        with patch.object(free, "get", return_value='var apidata={content:' + json.dumps(html) + '};'):
            group = free.holdings("123456", "2026-06-30", "stocks")[0]
        self.assertEqual(group["rows"][0]["symbol"], "00123")
        self.assertEqual(group["rows"][0]["nav_weight_pct"], 0)
        self.assertEqual(group["rows"][0]["shares_10k"], 2000)
        self.assertIsNone(group["rows"][0]["market_value_10k"])
        self.assertFalse(group["complete_portfolio"])

    def test_holdings_missing_header_fails_not_empty(self):
        with patch.object(free, "get", return_value='var apidata={content:"<table><tr><th>changed</th></tr></table>"};'):
            with self.assertRaises(TanyuanError):
                free.holdings("123456", "2026-06-30", "bonds")

    def test_bond_subset_never_full_portfolio(self):
        body = '<h4>截止至：2026-06-30</h4><table><tr><th>债券代码</th><th>债券名称</th><th>占净值比例</th><th>持仓市值（万元）</th></tr><tr><td>ABC</td><td>债券</td><td>2%</td><td>12</td></tr></table>'
        with patch.object(free, "get", return_value='var apidata={content:' + json.dumps(body) + '};'):
            r = free.holdings("123456", "2026-06-30", "bonds")[0]
        self.assertEqual(r["scope"], "mixed_disclosed_subset")
        self.assertFalse(r["complete_portfolio"])

    def test_holdings_explicit_small_units_converted_unknown_not_guessed(self):
        for header, expected in (("持仓市值（元）", 2), ("持仓市值（万元）", 20000), ("持仓市值", None)):
            body = f'<h4>截止至：2026-06-30</h4><table><tr><th>股票代码</th><th>持股数（股）</th><th>{header}</th></tr><tr><td>X</td><td>10000</td><td>20000</td></tr></table>'
            with patch.object(free, "get", return_value='var apidata={content:' + json.dumps(body) + '};'):
                r = free.holdings("123456", "2026-06-30", "stocks")[0]["rows"][0]
            self.assertEqual(r["shares_10k"], 1)
            self.assertEqual(r["market_value_10k"], expected)

    def test_asset_allocation_not_force_sum_100(self):
        body = '<table><tr><th>报告期</th><th>股票占净比</th><th>债券占净比</th><th>现金占净比</th><th>净资产（亿元）</th></tr><tr><td>2026-06-30</td><td>80%</td><td>30%</td><td>0%</td><td>1.2</td></tr></table>'
        with patch.object(free, "get", return_value=body):
            r = free.allocation("123456", "2026-06-30")
        self.assertEqual(r["rows"][0]["bond_nav_pct"], 30)
        self.assertEqual(r["rows"][0]["cash_nav_pct"], 0)

    def test_nav_empty_distinct_from_schema_change(self):
        with patch.object(free, "get", return_value=json.dumps({"ErrCode": 0, "Data": {"LSJZList": []}, "TotalCount": 0, "PageSize": 20, "PageIndex": 1})):
            self.assertEqual(free.nav_page("123456", "2026-01-01", "2026-01-02"), ([], 0))
        with patch.object(free, "get", return_value=json.dumps({"ErrCode": 0, "Data": [], "TotalCount": 0, "PageSize": 20, "PageIndex": 1})):
            with self.assertRaises(TanyuanError):
                free.nav_page("123456", "2026-01-01", "2026-01-02")

    def test_disclosure_pagination_and_unverified_pdf(self):
        obj = {"ErrCode": 0, "TotalCount": 3, "PageSize": 1, "PageIndex": 1, "Data": [{"FUNDCODE": "123456", "TITLE": "报告更正",
                "ID": "AN202601010000000001", "PUBLISHDATE": "2026-01-01T00:00:00", "NEWCATEGORY": "3"}]}
        with patch.object(free, "get", return_value=json.dumps(obj)):
            r = free.announcements("123456", "report", 1, 1)
        self.assertEqual(r["next_page"], 2)
        self.assertEqual(r["items"][0]["published_date"], "2026-01-01")
        self.assertEqual(r["items"][0]["pdf_link_status"], "pattern_derived_unverified")
        self.assertIsNone(r["items"][0]["supersedes"])

    def test_disclosure_wrong_identity_rejected(self):
        obj = {"ErrCode": 0, "TotalCount": 1, "Data": [{"FUNDCODE": "654321"}]}
        with patch.object(free, "get", return_value=json.dumps(obj)):
            with self.assertRaises(TanyuanError):
                free.announcements("123456", "all", 1, 20)

    def test_clamped_nav_page_size_rejected(self):
        obj = {"ErrCode": 0, "Data": {"LSJZList": []}, "TotalCount": 100, "PageSize": 10, "PageIndex": 1}
        with patch.object(free, "get", return_value=json.dumps(obj)):
            with self.assertRaises(TanyuanError):
                free.nav_page("123456", "2026-01-01", "2026-01-02", size=20)

    def test_clamped_disclosure_page_rejected(self):
        obj = {"ErrCode": 0, "Data": [], "TotalCount": 0, "PageSize": 20, "PageIndex": 1}
        with patch.object(free, "get", return_value=json.dumps(obj)):
            with self.assertRaises(TanyuanError):
                free.announcements("123456", "report", 1, 100)


class FundIdentity(unittest.TestCase):
    def test_bare_digits_check_all_markets(self):
        def call(api, params, fields=""):
            return [{"ts_code": params["ts_code"], "name": "同号基金", "market": params["market"]}] if params["ts_code"].endswith((".OF", ".SZ")) else []
        with patch.object(data.tushare, "call", side_effect=call):
            r = data.resolve("123456", 1)
        self.assertIsNone(r["resolved"])
        self.assertEqual(r["candidate_count"], 2)
        self.assertTrue(r["truncated"])

    def test_wrong_suffix_never_changed(self):
        with patch.object(data.tushare, "call", return_value=[]):
            with self.assertRaises(TanyuanError) as cm:
                data.resolve("123456.OF")
        self.assertEqual(cm.exception.code, Code.NOT_FOUND)

    def test_name_many_candidates_never_first_from_limit(self):
        rows = [{"code": "123456", "name": "合成A"}, {"code": "123457", "name": "合成C"}]
        with patch.object(free, "directory", return_value=rows):
            r = data.resolve("合成", 1)
        self.assertIsNone(r["resolved"])
        self.assertEqual(r["candidate_count"], 2)

    def test_free_identity_when_permission_missing_no_suffix_guess(self):
        with patch.object(data.tushare, "call", side_effect=auth_failed("denied")), patch.object(free, "directory", return_value=[{"code": "123456", "name": "合成A"}]):
            r = data.resolve("123456")
        self.assertIsNone(r["resolved"]["ts_code"])
        self.assertEqual(r["resolved"]["share_class"], "A")
        self.assertIsNone(r["resolved"]["currency"])
        self.assertEqual(r["resolved"]["related_share_classes"], [])

    def test_oversize_input_stops_before_network(self):
        with patch.object(data.tushare, "call") as p:
            with self.assertRaises(TanyuanError):
                data.resolve("x" * 161)
        p.assert_not_called()

    def test_wrong_source_code_rejected(self):
        with patch.object(data.tushare, "call", return_value=[{"ts_code": "654321.OF"}]):
            with self.assertRaises(TanyuanError):
                data.ts_rows("fund_nav", {"ts_code": "123456.OF"})


class FundTimeSeries(unittest.TestCase):
    def test_windows_contiguous_no_overlap(self):
        w = list(data.windows("2024-01-01", "2026-01-01"))
        self.assertEqual(w[0], ("2024-01-01", "2024-12-31"))
        self.assertEqual(w[1][0], "2025-01-01")

    def test_revision_preserved_exact_duplicate_removed(self):
        rows = [{"ts_code": "123456.OF", "nav_date": "20260102", "ann_date": "20260103", "unit_nav": 1},
                {"ts_code": "123456.OF", "nav_date": "20260102", "ann_date": "20260104", "unit_nav": 2}]
        with patch.object(data.tushare, "call", return_value=rows + [rows[0]]):
            r = data.ts_series("fund_nav", "123456.OF", "2026-01-01", "2026-01-05", date_field="nav_date")
        self.assertEqual(len(r["rows"]), 2)
        self.assertEqual(r["rows"][1]["ann_date"], "2026-01-04")

    def test_wrong_window_rejected(self):
        with patch.object(data.tushare, "call", return_value=[{"ts_code": "123456.OF", "nav_date": "20200101"}]):
            with self.assertRaises(TanyuanError):
                data.ts_series("fund_nav", "123456.OF", "2026-01-01", "2026-01-05", date_field="nav_date")

    def test_authorization_failure_stops_retries(self):
        with patch.object(data.tushare, "call", side_effect=auth_failed("denied")) as p:
            r = data.ts_series("fund_nav", "123456.OF", "2020-01-01", "2026-01-05", date_field="nav_date")
        self.assertEqual(p.call_count, 1)
        self.assertEqual(r["incomplete_windows"][0]["reason"], "AUTH_FAILED")

    def test_nav_fallback_no_invented_adjusted_values(self):
        with patch.object(data.tushare, "call", side_effect=auth_failed("denied")), patch.object(free, "nav_page", return_value=([{"nav_date": "2026-01-02", "unit_nav": 1.2, "accum_nav": 2.4, "adj_nav": None, "ann_date": None, "source": "eastmoney_lsjz"}], 1)):
            r = data.nav(IDENTITY, "2026-01-01", "2026-01-05")
        self.assertEqual(r["selected_source"], "eastmoney_lsjz")
        self.assertIsNone(r["series"][0]["adj_nav"])
        self.assertEqual(r["sources"][0]["status"], "error")

    def test_all_sources_failed_preserves_error(self):
        with patch.object(data.tushare, "call", side_effect=auth_failed("denied")), patch.object(free, "nav_page", side_effect=unavailable("down")):
            with self.assertRaises(TanyuanError) as cm:
                data.nav(IDENTITY, "2026-01-01", "2026-01-05")
        self.assertEqual(cm.exception.code, Code.AUTH_FAILED)

    def test_date_range_validation(self):
        for args in ({"start_date": "2026-02-30"}, {"start_date": "2026-01-02", "end_date": "2026-01-01"}, {"start_date": "1990-01-01", "end_date": "2026-01-01"}):
            with self.assertRaises(TanyuanError):
                data.date_range(args)

    def test_short_page_budget_preserves_remaining_years(self):
        with patch.object(data.tushare, "call", return_value=[]):
            r = data.ts_series("fund_nav", "123456.OF", "2024-01-01", "2026-01-01", date_field="nav_date", max_requests=1)
        self.assertEqual(r["incomplete_windows"], [{"start_date": "2025-01-01", "end_date": "2026-01-01", "reason": "request_budget"}])

    def test_free_nav_total_mismatch_marked_partial(self):
        with patch.object(free, "nav_page", return_value=([nav_row("2026-01-01", 1)], 19)):
            r = data.free_nav("123456", "2026-01-01", "2026-01-02")
        self.assertEqual(r["incomplete_windows"][0]["reason"], "unique_rows_below_source_total")

    def test_free_nav_repeated_page_stops_and_marks_partial(self):
        with patch.object(free, "nav_page", return_value=([nav_row("2026-01-01", 1)], 100)) as p:
            r = data.free_nav("123456", "2026-01-01", "2026-01-02")
        self.assertEqual(p.call_count, 2)
        self.assertTrue(r["incomplete_windows"])

    def test_money_auto_fetches_yield_even_with_constant_ts_nav(self):
        money = {**IDENTITY, "fund_type": "货币型"}
        ts_rows = [{"ts_code": IDENTITY["ts_code"], "nav_date": "20260101", "unit_nav": 1, "adj_nav": 1}]
        yield_row = {"nav_date": "2026-01-01", "unit_nav": None, "accum_nav": None, "adj_nav": None,
                     "series_kind": "money_market_yield", "income_per_10000_shares": -.1, "source": "eastmoney_lsjz"}
        with patch.object(data.tushare, "call", return_value=ts_rows), patch.object(free, "nav_page", return_value=([yield_row], 1)) as source:
            r = data.nav(money, "2026-01-01", "2026-01-02")
        source.assert_called_once()
        self.assertEqual(r["series"][0]["unit_nav"], 1)
        self.assertEqual(r["series"][0]["series_kind"], "money_market_nav_reference")
        self.assertEqual(r["income_series"][0]["income_per_10000_shares"], -.1)
        self.assertFalse(r["return_calculation_eligible"])

    def test_explicit_free_nav_semantics_unknown_identity_allows_only_nav_change(self):
        obj = {"ErrCode": 0, "PageSize": 20, "PageIndex": 1, "TotalCount": 2,
               "Data": {"SYType": "单位净值", "LSJZList": [
                   {"FSRQ": "2026-01-01", "DWJZ": "1.0", "LJJZ": "1.0"},
                   {"FSRQ": "2026-01-02", "DWJZ": "1.1", "LJJZ": "1.1"}]}}
        with patch.object(free, "get", return_value=json.dumps(obj)):
            r = data.nav({**IDENTITY, "fund_type": None}, "2026-01-01", "2026-01-02", "eastmoney")
        self.assertTrue(r["unit_nav_calculation_eligible"])
        self.assertFalse(r["adj_nav_calculation_eligible"])
        self.assertFalse(r["return_calculation_eligible"])
        with patch.object(data, "require_identity", return_value=({**IDENTITY, "fund_type": None}, [])), patch.object(data, "nav", return_value=r):
            out = fund.fund_metrics({"code": "123456", "series": "unit_nav"}).data
        self.assertEqual(out["metrics"]["statistics"]["status"], "ok")
        self.assertAlmostEqual(out["metrics"]["statistics"]["period_return"], .1)
        self.assertEqual(out["metrics"]["basis"], "unit_nav_change_excludes_distribution_adjustments")

    def test_unknown_identity_and_unknown_fields_not_eligible(self):
        obj = {"ErrCode": 0, "PageSize": 20, "PageIndex": 1, "TotalCount": 1,
               "Data": {"SYType": None, "LSJZList": [{"FSRQ": "2026-01-01", "DWJZ": "1", "LJJZ": "1"}]}}
        with patch.object(free, "get", return_value=json.dumps(obj)):
            r = data.nav({**IDENTITY, "fund_type": None}, "2026-01-01", "2026-01-02", "eastmoney")
        self.assertFalse(r["unit_nav_calculation_eligible"])
        self.assertFalse(r["adj_nav_calculation_eligible"])


class FundCalculations(unittest.TestCase):
    def test_drawdown_and_period_return_known_path(self):
        rows = [nav_row("2026-01-01", 100), nav_row("2026-01-02", 120), nav_row("2026-01-03", 90), nav_row("2026-01-04", 108)]
        r = metrics.calculate(rows)["statistics"]
        self.assertAlmostEqual(r["period_return"], .08)
        self.assertAlmostEqual(r["max_drawdown"], -.25)
        self.assertEqual(r["drawdown_peak_date"], "2026-01-02")
        self.assertEqual(r["drawdown_trough_date"], "2026-01-03")

    def test_missing_adjusted_no_silent_downgrade(self):
        rows = [nav_row("2026-01-01", 1), {**nav_row("2026-01-02", 2), "adj_nav": None}]
        self.assertEqual(metrics.calculate(rows)["statistics"]["status"], "unavailable")

    def test_same_ann_conflict_rejects_metric(self):
        rows = [nav_row("2026-01-01", 1), nav_row("2026-01-01", 2), nav_row("2026-01-02", 3)]
        r = metrics.calculate(rows)
        self.assertEqual(r["selection"]["conflicting_dates"], ["2026-01-01"])
        self.assertEqual(r["statistics"]["status"], "unavailable")

    def test_newer_revision_selected_explicitly(self):
        rows = [nav_row("2026-01-01", 1, ann="2026-01-02"), nav_row("2026-01-01", 2, ann="2026-01-03"), nav_row("2026-01-02", 4)]
        r = metrics.calculate(rows)
        self.assertAlmostEqual(r["statistics"]["period_return"], 1)
        self.assertEqual(r["selection"]["revision_selection"][0]["chosen_ann_date"], "2026-01-03")

    def test_accumulated_nav_not_allowed_for_total_return(self):
        with self.assertRaises(TanyuanError):
            metrics.calculate([], "accum_nav")

    def test_price_benchmark_not_mixed_with_adjusted_nav(self):
        r = metrics.benchmark_compare([], [], "adj_nav", "price", True)
        self.assertEqual(r["status"], "unavailable")

    def test_tracking_zero_for_identical_series(self):
        points = [{"date": f"2026-01-0{i+1}", "value": v} for i, v in enumerate((10, 11, 9))]
        rows = [{"trade_date": p["date"], "close": p["value"]} for p in points]
        r = metrics.benchmark_compare(points, rows, "adj_nav", "total_return", True)
        self.assertEqual(r["tracking_error_estimate"], 0)
        self.assertEqual(r["basis_verification"], "caller_declaration_not_independently_verified")

    def test_extreme_finite_values_never_emit_infinity(self):
        rows = [nav_row("2026-01-01", 1e-300), nav_row("2026-01-02", 1e300)]
        r = metrics.calculate(rows)
        self.assertEqual(r["statistics"]["status"], "unavailable")
        json.dumps(r, allow_nan=False)

    def test_annualization_overflow_local_missing(self):
        r = metrics.statistics_for([{"date": "2026-01-01", "value": 1}, {"date": "2026-01-02", "value": 1e100}])
        self.assertEqual(r["status"], "ok")
        self.assertIsNone(r["annualized_return"])
        json.dumps(r, allow_nan=False)


class FundTools(unittest.TestCase):
    def test_all_seven_tools_discoverable_and_validated(self):
        names = ["resolve", "profile", "nav", "holdings", "disclosures", "etf", "metrics"]
        for name in names:
            spec = registry.REGISTRY["fund." + name]
            self.assertTrue(spec.returns)
            for example in spec.examples:
                registry.validate_args(spec, example["args"])

    def test_holdings_separates_announcements(self):
        rows = [{"ts_code": IDENTITY["ts_code"], "end_date": "20260630", "ann_date": day, "symbol": "XX", "mkv": 1, "stk_mkv_ratio": 12} for day in ("20260720", "20260830")]
        with patch.object(data, "require_identity", return_value=(IDENTITY, [])), patch.object(data.tushare, "call", return_value=rows), patch.object(free, "holdings", return_value=[]):
            r = fund.fund_holdings({"code": "123456.SH", "period": "2026-06-30", "assets": ["stocks"]}).data
        self.assertEqual(len(r["groups"]), 2)
        self.assertEqual([g["ann_date"] for g in r["groups"]], ["2026-07-20", "2026-08-30"])
        self.assertIn("NOT fund NAV", r["groups"][0]["units"]["stock_value_weight_pct"])

    def test_holdings_all_errors_not_success_empty(self):
        with patch.object(data, "require_identity", return_value=(IDENTITY, [])), patch.object(data.tushare, "call", side_effect=auth_failed("no")), patch.object(free, "holdings", side_effect=unavailable("down")):
            with self.assertRaises(TanyuanError):
                fund.fund_holdings({"code": "123456.SH", "period": "2026-06-30", "assets": ["stocks"]})

    def test_etf_never_forward_fills_nav(self):
        def series(api, ts, start, end, *args):
            rows = [{"trade_date": "2026-01-02", "close": 1.1}] if api == "fund_daily" else ([{"trade_date": "2026-01-02", "fd_share": 0}] if api == "fund_share" else [])
            return {"rows": rows, "requests": [], "incomplete_windows": []}
        nav = {"series": [nav_row("2026-01-01", 1)], "sources": []}
        with patch.object(data, "ts_series", side_effect=series), patch.object(data, "ts_rows", return_value=[{"ts_code": IDENTITY["ts_code"]}]), patch.object(data, "nav", return_value=nav):
            r = fund.etf_data(IDENTITY, "2026-01-01", "2026-01-02")
        self.assertTrue(all(x["closing_premium_ratio"] is None for x in r["derived"]))

    def test_etf_zero_shares_real_zero_estimate(self):
        def series(api, *args):
            rows = [{"trade_date": "2026-01-02", "close": 1.1}] if api == "fund_daily" else ([{"trade_date": "2026-01-02", "fd_share": 0}] if api == "fund_share" else [])
            return {"rows": rows, "requests": [], "incomplete_windows": []}
        with patch.object(data, "ts_series", side_effect=series), patch.object(data, "ts_rows", return_value=[{"ts_code": IDENTITY["ts_code"]}]), patch.object(data, "nav", return_value={"series": [nav_row("2026-01-02", 1)]}):
            r = fund.etf_data(IDENTITY, "2026-01-01", "2026-01-02")
        self.assertEqual(r["derived"][0]["estimated_net_assets_yuan"], 0)
        self.assertAlmostEqual(r["derived"][0]["closing_premium_ratio"], .1)

    def test_etf_all_failures_preserve_auth_code(self):
        from tanyuan.tools import market
        with patch.object(data, "ts_rows", side_effect=auth_failed("no")), patch.object(data, "ts_series", side_effect=auth_failed("no")), patch.object(data, "nav", side_effect=auth_failed("no")), patch.object(market, "market_kline", side_effect=unavailable("down")):
            with self.assertRaises(TanyuanError) as cm:
                fund.etf_data(IDENTITY, "2026-01-01", "2026-01-02")
        self.assertEqual(cm.exception.code, Code.AUTH_FAILED)

    def test_etf_overflow_is_null_with_reason(self):
        def series(api, *args):
            rows = [{"trade_date": "2026-01-02", "close": 1e308}] if api == "fund_daily" else ([{"trade_date": "2026-01-02", "fd_share": 1e308}] if api == "fund_share" else [])
            return {"rows": rows, "requests": [], "incomplete_windows": []}
        with patch.object(data, "ts_series", side_effect=series), patch.object(data, "ts_rows", return_value=[{"ts_code": IDENTITY["ts_code"]}]), patch.object(data, "nav", return_value={"series": [nav_row("2026-01-02", 1e-300)]}):
            r = fund.etf_data(IDENTITY, "2026-01-01", "2026-01-02")
        self.assertIsNone(r["derived"][0]["closing_premium_ratio"])
        self.assertEqual(r["derived"][0]["unavailable_metrics"]["closing_premium_ratio"], "arithmetic_out_of_finite_range")
        json.dumps(r, allow_nan=False)

    def test_disclosure_local_filters_keep_unfiltered_pagination(self):
        response = {"items": [{"title": "年度报告", "published_date": "2026-01-01"}, {"title": "经理变更", "published_date": "2026-02-01"}],
                    "has_more": True, "next_page": 2, "total_count": 100, "source_url": "https://example.org"}
        with patch.object(data, "require_identity", return_value=(IDENTITY, [])), patch.object(free, "announcements", return_value=response):
            r = fund.fund_disclosures({"code": "123456", "keyword": "经理", "start_date": "2026-01-15"}).data
        self.assertEqual(len(r["items"]), 1)
        self.assertEqual(r["next_page"], 2)
        self.assertEqual(r["total_count"], 100)
        self.assertFalse(r["filter"]["search_complete"])

    def test_money_metrics_neither_series_nor_benchmark_produces_false_return(self):
        payload = {"series": [nav_row("2026-01-01", 1), nav_row("2026-01-02", 1)],
                   "coverage": {"incomplete_windows": []}, "sources": [], "return_calculation_eligible": False,
                   "return_calculation_reason": "货币基金需收益结转规则", "income_series": [{"income_per_10000_shares": 0}],
                   "units": {"income_per_10000_shares": "元/万份", "seven_day_annualized_yield_pct": "%"}}
        for series in ("adj_nav", "unit_nav"):
            with patch.object(data, "require_identity", return_value=({**IDENTITY, "fund_type": "货币型"}, [])), patch.object(data, "nav", return_value=payload), patch.object(data, "ts_series", return_value={"rows": [], "requests": [], "incomplete_windows": []}):
                r = fund.fund_metrics({"code": "123456", "series": series, "benchmark_code": "EXAMPLE.CSI", "benchmark_basis": "total_return", "same_currency_confirmed": True}).data
            self.assertEqual(r["metrics"]["statistics"]["status"], "unavailable")
            self.assertNotIn("period_return", r["metrics"]["statistics"])
            self.assertEqual(r["comparison"]["status"], "unavailable")
            self.assertEqual(r["income_series"][0]["income_per_10000_shares"], 0)
            self.assertEqual(r["units"]["income_per_10000_shares"], "元/万份")
            self.assertEqual(r["units"]["seven_day_annualized_yield_pct"], "%")

    def test_money_etf_reference_nav_not_used_for_price_comparison(self):
        def series(api, *args):
            rows = [{"trade_date": "2026-01-02", "close": 100}] if api == "fund_daily" else ([{"trade_date": "2026-01-02", "fd_share": 1}] if api == "fund_share" else [])
            return {"rows": rows, "requests": [], "incomplete_windows": []}
        with patch.object(data, "ts_series", side_effect=series), patch.object(data, "ts_rows", return_value=[{"ts_code": IDENTITY["ts_code"]}]), patch.object(data, "nav", return_value={"series": [nav_row("2026-01-02", 1)]}):
            r = fund.etf_data({**IDENTITY, "fund_type": "货币型"}, "2026-01-01", "2026-01-02")
        self.assertIsNone(r["derived"][0]["closing_premium_ratio"])
        self.assertIsNone(r["derived"][0]["estimated_net_assets_yuan"])


if __name__ == "__main__":
    unittest.main()
