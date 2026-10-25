#!/usr/bin/env python
"""build_golden.py — 由 truth_bundle.json + 题目设计 生成 golden.jsonl。

为什么不用手写 golden.jsonl:
  期望值都从 fetch_truth.py 刚采到的真值里取, 避免「手抄数字抄错」;
  题目设计(题面/判分器/取证)在下面 QUESTION_SPECS 里显式可读, 生成物可 diff。

用法: .venv/bin/python qa/tanyuan/evals/build_golden.py
产物: qa/tanyuan/evals/golden.jsonl (一题一行, 57 题)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BUNDLE_PATH = ROOT / "artifacts" / "golden" / "truth_bundle.json"
OUT_PATH = ROOT / "qa" / "tanyuan" / "evals" / "golden.jsonl"

AS_OF = "2026-10-24T11:40:00+08:00"
LAST_TD = "2026-10-23"

CITE_PRIMARY = ["tushare.pro", "gtimg.cn", "qq.com", "eastmoney.com", "sse.com.cn", "szse.cn", "cninfo.com.cn", "sina.com.cn", "sina.cn"]
CITE_TEXT_PRIMARY = ["tushare", "腾讯行情", "腾讯财经", "巨潮", "cninfo", "交易所", "新浪财经"]
CITE_CNINFO = ["cninfo.com.cn", "sse.com.cn", "szse.cn"]
CITE_NEWS_CCTV = ["cctv.com", "央视"]
CITE_CLS = ["cls.cn", "财联社"]
CITE_REPORT = ["eastmoney.com", "东财", "研报", "东方财富"]
CITE_MACRO = ["tushare.pro", "eastmoney.com", "stats.gov.cn", "chinamoney.com.cn", "chinabond.com.cn", "pbc.gov.cn"]


# --------------------------------------------------------------------------- #
# judges 构造器
# --------------------------------------------------------------------------- #
def nj(label, accept, weight=2, tol=None, keywords=None, unit_optional=True):
    """numeric judge。accept: [(value, unit), ...] 任一命中即通过。"""
    return {
        "type": "numeric",
        "label": label,
        "accept": [{"value": v, "unit": u} for v, u in accept],
        "tolerance": tol or {"rel": 0.001},
        "weight": weight,
        "keywords": keywords or [],
        "unit_optional": unit_optional,
    }


def tj(label, options, weight=1):
    return {"type": "text_any", "label": label, "options": options, "weight": weight}


def aj(label, options, weight=1):
    return {"type": "text_all", "label": label, "options": options, "weight": weight}


def sj(label, options, min_hits, weight=2):
    return {"type": "set_match", "label": label, "options": options, "min_hits": min_hits, "weight": weight}


def dj(label, dates, weight=1):
    return {"type": "date_in", "label": label, "dates": dates, "weight": weight}


def cj(label, domains, texts, weight=1):
    return {"type": "cite", "label": label, "domains": domains, "texts": texts, "weight": weight}


def rj(label, markers, weight=3):
    return {"type": "refusal", "label": label, "markers": markers, "weight": weight}


def fj(label, keywords, weight=2, units=None, positive_only=False, window=80):
    return {
        "type": "forbid_numbers",
        "label": label,
        "keywords": keywords,
        "weight": weight,
        "units": units or ["元", "亿元", "万元", "千元", "%", "倍", "点"],
        "positive_only": positive_only,
        "window": window,
    }


def ftj(label, texts, weight=2):
    return {"type": "forbid_text", "label": label, "texts": texts, "weight": weight}


PERIOD_H1 = ["2026年上半年", "2026年半年度", "2026H1", "2026年中报", "半年度报告", "半年报", "中报", "报告期2026-06-30", "2026-06-30", "20260630"]
PERIOD_Q3 = ["2026年前三季度", "2026年三季报", "2026Q3", "2026年第三季度", "三季度报告", "2026-09-30", "20260930", "三季报"]
PERIOD_SEP = ["2026年9月", "2026-09", "202609", "9月"]
DATE_LAST_TD = ["2026-10-23", "2026年10月23日", "10月23日", "20261023"]


# --------------------------------------------------------------------------- #
# 题目设计
# --------------------------------------------------------------------------- #
def build_specs(T: dict, RF: dict) -> list[dict]:
    def ev(*keys):
        return [RF[k] for k in keys if k in RF]

    specs: list[dict] = []

    m = T["m_600519"]
    h1 = T["f_600519_h1"]
    ann_latest = T["f_600519_ann_latest"]
    ann_0815 = T["f_600519_ann_0815"]

    # ======================= 一手披露 filing (7) =======================
    specs.append(
        dict(
            qid="F-01",
            category="filing",
            question="贵州茅台（600519）2026 年半年度报告是哪一天披露的？",
            answerable=True,
            expected_display=f"2026-08-15（巨潮资讯《贵州茅台2026年半年度报告》，Tushare income.ann_date 一致）",
            judges=[
                dj("披露日期", [h1["ann_date"]], weight=3),
                tj("文件类型", ["半年度报告", "半年报", "中报"], weight=1),
                cj("一手来源", CITE_CNINFO, ["巨潮", "cninfo", "上交所", "sse"], weight=1),
                tj("报告期", PERIOD_H1, weight=1),
            ],
            must_cite=True,
            primary_sources=["cninfo.com.cn(巨潮公告)", "tushare income.ann_date"],
            verify_by="cninfo_announcements('600519') 首页可见《贵州茅台2026年半年度报告》date=2026-08-15；Tushare income(period=20260630).ann_date=20260815。双源一致。",
            source_url="https://www.cninfo.com.cn/new/disclosure/stock?stockCode=600519",
            evidence=ev("cninfo_600519", "income_600519_h1"),
            notes="最基础的一手披露时点题；评分同时看日期与「报告期」是否点名。",
            difficulty="easy",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="F-02",
            category="filing",
            question="截至 2026-10-24，贵州茅台（600519）的 2026 年三季报披露了吗？如果没披露，它最近一次定期报告是什么、什么时候发的？",
            answerable=True,
            expected_display=f"未披露。截至 {AS_OF[:10]}，600519 最新公告为 {ann_latest['latest_date']} 的《{ann_latest['latest_title']}》；Tushare 2026Q3 财报为空。",
            judges=[
                rj("正确拒答/否定", ["未披露", "没有披露", "尚未披露", "未发布", "还没", "无三季报", "尚无"], weight=3),
                fj("不得编造三季报数字", ["三季报", "三季度", "前三季度", "Q3"], weight=3, window=30),
                dj("最近报告日期", ["2026-08-15"], weight=1),
                tj("报告期定位", PERIOD_H1, weight=1),
            ],
            must_cite=False,
            primary_sources=["cninfo.com.cn(巨潮公告)", "tushare income(period=20260930)=空"],
            verify_by="cninfo_announcements('600519',30) 最新一条 date=2026-08-15，其后至 as_of 无公告；Tushare income(600519.SH, period=20260930) rows=0。",
            source_url="https://www.cninfo.com.cn/new/disclosure/stock?stockCode=600519",
            evidence=ev("cninfo_600519", "q3_income"),
            notes="as-of 陷阱：用户以为「现在应该有三季报了」。正确行为是明确「未披露」+ 给最近一期，而不是编数字。",
            difficulty="medium",
            trap=True,
            expected_refusal=True,
        )
    )
    q3_zj = T["f_601899_q3"]
    specs.append(
        dict(
            qid="F-03",
            category="filing",
            question="紫金矿业（601899）2026 年三季报的披露日期和年初至报告期末归母净利润分别是多少？",
            answerable=True,
            expected_display=f"{q3_zj['ann_date'][:4]}-{q3_zj['ann_date'][4:6]}-{q3_zj['ann_date'][6:]} 披露；归母净利润 {q3_zj['n_income_attr_p_yi']:.2f} 亿元（{q3_zj['n_income_attr_p']:.2f} 元）。",
            judges=[
                dj("披露日期", [q3_zj["ann_date"]], weight=2),
                nj("归母净利润", [(q3_zj["n_income_attr_p_yi"], "亿元"), (q3_zj["n_income_attr_p"], "元")], weight=3, keywords=["归母", "净利润"]),
                tj("报告期", PERIOD_Q3, weight=1),
                cj("一手来源", CITE_CNINFO, ["巨潮", "cninfo", "上交所", "sse"], weight=1),
            ],
            must_cite=True,
            primary_sources=["cninfo.com.cn(巨潮公告)", "tushare income(period=20260930)"],
            verify_by="Tushare income(601899.SH, period=20260930) → ann_date=20261024, n_income_attr_p=57525672087.0；巨潮 2026-10-24《紫金矿业2026年第三季度报告》。",
            source_url="https://www.cninfo.com.cn/new/disclosure/stock?stockCode=601899",
            evidence=ev("q3_income.601899.SH", "cninfo_601899"),
            notes="2026Q3 报告当日（10/24）披露，数据层需能拿到「当天披露」的一手文件。",
            difficulty="medium",
            trap=False,
        )
    )
    q3_nd = T["f_300750_q3"]
    specs.append(
        dict(
            qid="F-04",
            category="filing",
            question="宁德时代（300750）2026 年三季报何时披露？前三季度营业总收入是多少？",
            answerable=True,
            expected_display=f"{q3_nd['ann_date'][:4]}-{q3_nd['ann_date'][4:6]}-{q3_nd['ann_date'][6:]} 披露；营业总收入 {q3_nd['total_revenue_yi']:.2f} 亿元。",
            judges=[
                dj("披露日期", [q3_nd["ann_date"]], weight=2),
                nj("营业总收入", [(q3_nd["total_revenue_yi"], "亿元"), (q3_nd["total_revenue"], "元")], weight=3, keywords=["营业总收入", "营收", "收入"]),
                tj("报告期", PERIOD_Q3, weight=1),
                cj("一手来源", CITE_CNINFO, ["巨潮", "cninfo", "深交所", "szse"], weight=1),
            ],
            must_cite=True,
            primary_sources=["cninfo.com.cn(巨潮公告)", "tushare income(period=20260930)"],
            verify_by="Tushare income(300750.SZ, period=20260930) → ann_date=20261021, total_revenue=442137424000.0；巨潮 2026-10-21《2026年三季度报告》。",
            source_url="https://www.cninfo.com.cn/new/disclosure/stock?stockCode=300750",
            evidence=ev("q3_income.300750.SZ", "q3_fina.300750.SZ", "cninfo_300750"),
            notes="与 F-03 同型但披露日不同，考「别把披露日都猜成同一天」。",
            difficulty="medium",
            trap=False,
        )
    )
    div = T["f_600519_dividend"]
    specs.append(
        dict(
            qid="F-05",
            category="filing",
            question="贵州茅台 2025 年度利润分配方案中，每股派发现金红利（税前）是多少？除权除息日是哪天？",
            answerable=True,
            expected_display=f"每股派现 {div['cash_div_tax']} 元（税前）；股权登记日 {div['record_date']}，除权除息日 {div['ex_date']}。",
            judges=[
                nj("每股派现", [(div["cash_div_tax"], "元")], weight=3, tol={"abs": 0.001}, keywords=["每股", "派", "现金", "红利"]),
                dj("除权除息日", [div["ex_date"]], weight=2),
                tj("口径", ["税前", "含税", "现金红利", "派现"], weight=1),
                cj("一手来源", CITE_PRIMARY + CITE_CNINFO, CITE_TEXT_PRIMARY + ["巨潮", "cninfo", "上交所"], weight=1),
            ],
            must_cite=True,
            primary_sources=["tushare dividend(600519.SH, div_proc=实施)", "cninfo.com.cn(权益分派实施公告)"],
            verify_by="Tushare dividend(600519.SH) → end_date=20251231, div_proc=实施, cash_div_tax=28.02423, ex_date=20260626。",
            source_url="https://www.cninfo.com.cn/new/disclosure/stock?stockCode=600519",
            evidence=ev("dividend_600519"),
            notes="分红题最容易错在「每 10 股派多少」与「每股」混用：28.02423 元/股 = 280.2423 元/10 股。",
            difficulty="medium",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="F-06",
            category="filing",
            question="贵州茅台在 2026-08-15 这一天一共披露了哪几份公告？请至少列出 3 份的标题。",
            answerable=True,
            expected_display=f"共 {ann_0815['count']} 份：" + "；".join(ann_0815["titles"]),
            judges=[
                sj(
                    "公告标题集合",
                    [
                        "半年度报告",
                        "会计政策变更",
                        "风险评估报告",
                        "董事会",
                        "半年度报告摘要",
                        "第五届董事会",
                    ],
                    min_hits=3,
                    weight=3,
                ),
                dj("公告日期", ["2026-08-15"], weight=1),
                cj("一手来源", CITE_CNINFO, ["巨潮", "cninfo", "上交所"], weight=1),
            ],
            must_cite=True,
            primary_sources=["cninfo.com.cn(巨潮公告列表)"],
            verify_by="cninfo_announcements('600519',30) 过滤 date=2026-08-15 → 5 条：半年度报告/会计政策变更/财务公司风险评估/董事会决议/半年度报告摘要。",
            source_url="https://www.cninfo.com.cn/new/disclosure/stock?stockCode=600519",
            evidence=ev("cninfo_600519"),
            notes="集合匹配题：不要求一字不差，命中 ≥3 个关键标题即通过。",
            difficulty="easy",
            trap=False,
        )
    )
    fc = T["f_301563_forecast"]
    specs.append(
        dict(
            qid="F-07",
            category="filing",
            question="301563（301563.SZ）在 2026-10-23 披露的 2026 年前三季度业绩预告中，预计归母净利润区间是多少？同比增幅区间是多少？",
            answerable=True,
            expected_display=f"归母净利润 {fc['net_profit_min']} 万 ~ {fc['net_profit_max']} 万元；同比 +{fc['p_change_min']}% ~ +{fc['p_change_max']}%；类型「{fc['type']}」。",
            judges=[
                nj("预告下限", [(fc["net_profit_min"], "万元")], weight=2, keywords=["下限", "至", "预计", "净利润"]),
                nj("预告上限", [(fc["net_profit_max"], "万元")], weight=2, keywords=["上限", "至", "预计", "净利润"]),
                nj("同比下限", [(fc["p_change_min"], "%")], weight=1, keywords=["同比", "增长"]),
                nj("同比上限", [(fc["p_change_max"], "%")], weight=1, keywords=["同比", "增长"]),
                tj("预告类型", ["预增"], weight=1),
                cj("一手来源", CITE_PRIMARY + CITE_CNINFO, CITE_TEXT_PRIMARY + ["巨潮", "cninfo"], weight=1),
            ],
            must_cite=True,
            primary_sources=["tushare forecast(301563.SZ, period=20260930)", "cninfo.com.cn(业绩预告)"],
            verify_by="Tushare forecast(301563.SZ, period=20260930) → type=预增, p_change_min=311.65, p_change_max=354.98, net_profit_min=33533.35, net_profit_max=37063.17。",
            source_url="https://www.cninfo.com.cn/new/disclosure/stock?stockCode=301563",
            evidence=ev("forecast_301563"),
            notes="区间题：上下限都要对；单位是「万元」，不是元。",
            difficulty="hard",
            trap=False,
        )
    )

    # ======================= 财务数字 as-of (9) =======================
    specs.append(
        dict(
            qid="U-01",
            category="fundamental",
            question="贵州茅台 2026 年半年度报告的营业总收入是多少（亿元，保留两位小数）？",
            answerable=True,
            expected_display=f"{h1['total_revenue_yi']:.2f} 亿元（{h1['total_revenue']:.2f} 元）",
            judges=[
                nj("营业总收入", [(h1["total_revenue_yi"], "亿元"), (h1["total_revenue"], "元")], weight=3, keywords=["营业总收入", "营收", "收入"]),
                tj("报告期", PERIOD_H1, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare income(600519.SH, period=20260630)"],
            verify_by="Tushare income(600519.SH, period=20260630, report_type=1) → total_revenue=92278072083.21。注意 total_revenue(营业总收入) 与 revenue(营业收入 907.03 亿) 不同口径。",
            source_url="https://api.tushare.pro (api=income, ts_code=600519.SH, period=20260630)",
            evidence=ev("income_600519_h1"),
            notes="as-of 必须点名 2026H1；另含口径坑：营业总收入≠营业收入。",
            difficulty="easy",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="U-02",
            category="fundamental",
            question="贵州茅台 2026 年上半年归属于上市公司股东的净利润是多少亿元？",
            answerable=True,
            expected_display=f"{h1['n_income_attr_p_yi']:.2f} 亿元（{h1['n_income_attr_p']:.2f} 元）",
            judges=[
                nj("归母净利润", [(h1["n_income_attr_p_yi"], "亿元"), (h1["n_income_attr_p"], "元")], weight=3, keywords=["归母", "归属", "净利润"]),
                tj("报告期", PERIOD_H1, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare income(600519.SH, period=20260630)"],
            verify_by="Tushare income(600519.SH, period=20260630) → n_income_attr_p=44516880421.86。",
            source_url="https://api.tushare.pro (api=income, ts_code=600519.SH, period=20260630)",
            evidence=ev("income_600519_h1"),
            notes="与 T-12（含少数股东的净利润）成对，考「归母 vs 合并」口径。",
            difficulty="easy",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="U-03",
            category="fundamental",
            question="贵州茅台 2026 年上半年的销售毛利率是多少（%）？",
            answerable=True,
            expected_display=f"{h1['gross_margin_pct']:.2f}%",
            judges=[
                nj("毛利率", [(h1["gross_margin_pct"], "%")], weight=3, tol={"abs": 0.05}, keywords=["毛利率", "毛利"]),
                tj("报告期", PERIOD_H1, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare fina_indicator(600519.SH, period=20260630)"],
            verify_by="Tushare fina_indicator(600519.SH, period=20260630) → grossprofit_margin=89.5552；（1-oper_cost/revenue 复核：1-9473762565.88/90703260964.48=89.56%）。",
            source_url="https://api.tushare.pro (api=fina_indicator, ts_code=600519.SH, period=20260630)",
            evidence=ev("fina_600519_h1"),
            notes="毛利率有「营业收入 vs 营业总收入」两种算法，差约 0.5pp；容差 0.05pp 可覆盖口径差。",
            difficulty="medium",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="U-04",
            category="fundamental",
            question="贵州茅台 2026 年上半年基本每股收益是多少元？",
            answerable=True,
            expected_display=f"{h1['basic_eps']} 元/股",
            judges=[
                nj("基本EPS", [(h1["basic_eps"], "元")], weight=3, tol={"abs": 0.005}, keywords=["每股收益", "EPS", "基本"]),
                tj("报告期", PERIOD_H1, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare income(600519.SH, period=20260630)"],
            verify_by="Tushare income(600519.SH, period=20260630) → basic_eps=35.57。",
            source_url="https://api.tushare.pro (api=income, ts_code=600519.SH, period=20260630)",
            evidence=ev("income_600519_h1"),
            notes="简单题，作为「精确命中率」基线。",
            difficulty="easy",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="U-05",
            category="fundamental",
            question="宁德时代 2026 年前三季度的营业总收入同比增速是多少（%）？",
            answerable=True,
            expected_display=f"+{q3_nd['or_yoy']:.2f}%",
            judges=[
                nj("营收同比", [(q3_nd["or_yoy"], "%"), (q3_nd.get("total_revenue_yoy_pct"), "%")], weight=3, tol={"abs": 0.1}, keywords=["同比", "增长", "增速"]),
                tj("报告期", PERIOD_Q3, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare fina_indicator(300750.SZ, period=20260930)"],
            verify_by="Tushare fina_indicator(300750.SZ, period=20260930) → or_yoy=56.1926；自算 442137424000/283071987000-1=56.19%。",
            source_url="https://api.tushare.pro (api=fina_indicator, ts_code=300750.SZ, period=20260930)",
            evidence=ev("q3_fina.300750.SZ", "q3_income.300750.SZ", "q3_income_prev.300750.SZ"),
            notes="同比题：既可以用官方 or_yoy，也可以自己拿两期算，两条路都要对得上。",
            difficulty="medium",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="U-06",
            category="fundamental",
            question="紫金矿业 2026 年前三季度归母净利润同比增速是多少（%）？",
            answerable=True,
            expected_display=f"+{q3_zj['netprofit_yoy']:.2f}%（自算 {q3_zj.get('np_attr_yoy_pct', 0):.2f}%）",
            judges=[
                nj("归母净利同比", [(q3_zj["netprofit_yoy"], "%"), (q3_zj.get("np_attr_yoy_pct"), "%")], weight=3, tol={"abs": 0.15}, keywords=["同比", "增长", "增速", "归母"]),
                tj("报告期", PERIOD_Q3, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare fina_indicator(601899.SH, period=20260930)"],
            verify_by="Tushare fina_indicator(601899.SH, period=20260930) → netprofit_yoy=51.9286；自算 57525672087/37863620974-1=51.93%。",
            source_url="https://api.tushare.pro (api=fina_indicator, ts_code=601899.SH, period=20260930)",
            evidence=ev("q3_fina.601899.SH", "q3_income.601899.SH", "q3_income_prev.601899.SH"),
            notes="同上，双路互验。",
            difficulty="medium",
            trap=False,
        )
    )
    q3_hk = T["f_002415_q3"]
    specs.append(
        dict(
            qid="U-07",
            category="fundamental",
            question="海康威视（002415）2026 年前三季度归母净利润是多少亿元？",
            answerable=True,
            expected_display=f"{q3_hk['n_income_attr_p_yi']:.2f} 亿元（披露日 {q3_hk['ann_date']}）",
            judges=[
                nj("归母净利润", [(q3_hk["n_income_attr_p_yi"], "亿元"), (q3_hk["n_income_attr_p"], "元")], weight=3, keywords=["归母", "净利润"]),
                tj("报告期", PERIOD_Q3, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare income(002415.SZ, period=20260930)"],
            verify_by="Tushare income(002415.SZ, period=20260930) → ann_date=20261017, n_income_attr_p=12911386245.87。",
            source_url="https://api.tushare.pro (api=income, ts_code=002415.SZ, period=20260930)",
            evidence=ev("q3_income.002415.SZ", "cninfo_002415"),
            notes="考「行业中独一无二的披露日（10/17）」，避免用别的公司日期套。",
            difficulty="medium",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="U-08",
            category="fundamental",
            question="贵州茅台 2026 年半年报的合同负债是多少亿元？（资产负债表口径）",
            answerable=True,
            expected_display=f"{h1['contract_liab_yi']:.2f} 亿元（{h1['contract_liab']:.2f} 元）",
            judges=[
                nj("合同负债", [(h1["contract_liab_yi"], "亿元"), (h1["contract_liab"], "元")], weight=3, keywords=["合同负债"]),
                tj("报告期", PERIOD_H1, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare balancesheet(600519.SH, period=20260630)"],
            verify_by="Tushare balancesheet(600519.SH, period=20260630) → contract_liab=3177561597.07；注意与 acct_payable=3509015535.31（应付账款）区分。",
            source_url="https://api.tushare.pro (api=balancesheet, ts_code=600519.SH, period=20260630)",
            evidence=ev("balancesheet_600519_h1"),
            notes="必须查资产负债表；贴近的两个数（合同负债 31.78亿 / 应付账款 35.09亿）是干扰项。",
            difficulty="medium",
            trap=False,
        )
    )
    q3_pa = T["f_000001_q3"]
    specs.append(
        dict(
            qid="U-09",
            category="fundamental",
            question="平安银行（000001）2026 年前三季度归属于本行股东的净利润是多少亿元？",
            answerable=True,
            expected_display=f"{q3_pa['n_income_attr_p_yi']:.2f} 亿元（披露日 {q3_pa['ann_date']}）",
            judges=[
                nj("归母净利润", [(q3_pa["n_income_attr_p_yi"], "亿元"), (q3_pa["n_income_attr_p"], "元")], weight=3, keywords=["归母", "股东", "净利润"]),
                tj("报告期", PERIOD_Q3, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare income(000001.SZ, period=20260930)"],
            verify_by="Tushare income(000001.SZ, period=20260930) → ann_date=20261024, n_income_attr_p=39186000000.0。",
            source_url="https://api.tushare.pro (api=income, ts_code=000001.SZ, period=20260930)",
            evidence=ev("q3_income.000001.SZ", "cninfo_000001"),
            notes="银行报表口径（归母=净利润），且 000001 兼具指数歧义（见 T-01）。",
            difficulty="medium",
            trap=False,
        )
    )

    # ======================= 行情 market (8) =======================
    specs.append(
        dict(
            qid="M-01",
            category="market",
            question="贵州茅台（600519）2026-10-23 收盘价是多少元？",
            answerable=True,
            expected_display=f"{m['close']:.2f} 元",
            judges=[
                nj("收盘价", [(m["close"], "元")], weight=3, tol={"abs": 0.01}, keywords=["收盘"]),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare daily(600519.SH, 20261023)", "腾讯 qt.gtimg.cn/q=sh600519"],
            verify_by="Tushare daily(600519.SH, trade_date=20261023) → close=1335.0；腾讯 tencent_quote('600519').price=1335.0。双源一致。",
            source_url="https://qt.gtimg.cn/q=sh600519",
            evidence=ev("daily_20261023", "tencent_quote"),
            notes="基线题；容差 0.01 元。",
            difficulty="easy",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="M-02",
            category="market",
            question="贵州茅台（600519）2026-10-23 的涨跌幅是多少（%）？",
            answerable=True,
            expected_display=f"+{m['pct_chg']:.2f}%",
            judges=[
                nj("涨跌幅", [(m["pct_chg"], "%"), (m["tencent_change_pct"], "%")], weight=3, tol={"abs": 0.02}, keywords=["涨跌", "涨幅", "跌幅"]),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare daily.pct_chg", "腾讯 tencent_quote.change_pct"],
            verify_by="Tushare daily(600519.SH,20261023).pct_chg=1.2522；（1335.0/1318.49-1)*100=1.2522%。腾讯 change_pct=1.25。",
            source_url="https://qt.gtimg.cn/q=sh600519",
            evidence=ev("daily_20261023", "tencent_quote"),
            notes="涨跌幅有「四舍五入 1.25」与「1.2522」两种呈现，容差 0.02pp 都收。",
            difficulty="easy",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="M-03",
            category="market",
            question="贵州茅台（600519）2026-10-23 的成交额是多少亿元？",
            answerable=True,
            expected_display=f"{m['amount_yi']:.2f} 亿元（= {m['amount_qian']:.2f} 千元 = 腾讯 {m['tencent_amount_wan']:.2f} 万元）",
            judges=[
                nj(
                    "成交额",
                    [
                        (round(m["amount_yi"], 2), "亿元"),
                        (m["amount_qian"], "千元"),
                        (m["tencent_amount_wan"], "万元"),
                        (round(m["amount_qian"] * 1000, 2), "元"),
                    ],
                    weight=3,
                    tol={"rel": 0.002},
                    keywords=["成交额", "成交金额"],
                ),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare daily.amount(千元)", "腾讯 tencent_quote.amount_wan"],
            verify_by="Tushare daily(600519.SH,20261023).amount=7677216.283 千元 → 76.77 亿元；腾讯 amount_wan=767722 万元 → 76.77 亿元。两源同值互证。",
            source_url="https://qt.gtimg.cn/q=sh600519",
            evidence=ev("daily_20261023", "tencent_quote"),
            notes="单位陷阱：Tushare amount 单位是千元、腾讯是万元，问的是亿元。",
            difficulty="medium",
            trap=True,
        )
    )
    sh = T["m_sh000001"]
    specs.append(
        dict(
            qid="M-04",
            category="market",
            question="2026-10-23 上证指数的收盘点位和涨跌幅分别是多少？",
            answerable=True,
            expected_display=f"收 {sh['close']:.2f} 点，涨 {sh['pct_chg']:.2f}%",
            judges=[
                nj("收盘点位", [(round(sh["close"], 2), "点"), (sh["close"], "点")], weight=2, tol={"abs": 0.01}, keywords=["收盘", "点位", "上证"]),
                nj("涨跌幅", [(sh["pct_chg"], "%")], weight=2, tol={"abs": 0.01}, keywords=["涨", "跌"]),
                dj("交易日", DATE_LAST_TD, weight=1),
                tj("标的", ["上证指数", "上证综指"], weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare index_daily(000001.SH)", "腾讯 qt.gtimg.cn/q=sh000001"],
            verify_by="Tushare index_daily(000001.SH,20261023) → close=3690.4753, pct_chg=0.8871；腾讯 sh000001 price=3690.48 change_pct=0.89。",
            source_url="https://qt.gtimg.cn/q=sh000001",
            evidence=ev("index_daily_20261023", "tencent_quote"),
            notes="与 T-01 联动：000001 不加前缀会被当成平安银行。",
            difficulty="easy",
            trap=False,
        )
    )
    hs = T["m_hs300"]
    specs.append(
        dict(
            qid="M-05",
            category="market",
            question="沪深300指数 2026-10-23 的收盘点位是多少？",
            answerable=True,
            expected_display=f"{hs['close']:.2f} 点（涨 {hs['pct_chg']:.2f}%）",
            judges=[
                nj("收盘点位", [(round(hs["close"], 2), "点"), (hs["close"], "点")], weight=3, tol={"abs": 0.01}, keywords=["收盘", "点位", "沪深300"]),
                tj("标的", ["沪深300", "沪深 300", "HS300"], weight=1),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare index_daily(000300.SH)"],
            verify_by="Tushare index_daily(000300.SH,20261023) → close=4105.3286, pct_chg=1.7103。",
            source_url="https://api.tushare.pro (api=index_daily, ts_code=000300.SH)",
            evidence=ev("index_daily_20261023"),
            notes="与 T-10 同码不同题：000300 只能是指数。",
            difficulty="easy",
            trap=False,
        )
    )
    cyb = T["m_cyb"]
    specs.append(
        dict(
            qid="M-06",
            category="market",
            question="2026-10-23 创业板指的涨跌幅是多少（%）？",
            answerable=True,
            expected_display=f"+{cyb['pct_chg']:.2f}%（收 {cyb['close']:.2f} 点）",
            judges=[
                nj("涨跌幅", [(cyb["pct_chg"], "%")], weight=3, tol={"abs": 0.02}, keywords=["涨跌", "涨幅", "创业板"]),
                tj("标的", ["创业板指", "创业板指数", "399006"], weight=1),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare index_daily(399006.SZ)"],
            verify_by="Tushare index_daily(399006.SZ,20261023) → close=2684.0971, pct_chg=4.1599。",
            source_url="https://api.tushare.pro (api=index_daily, ts_code=399006.SZ)",
            evidence=ev("index_daily_20261023"),
            notes="当日创业板大涨 4.16%，与银行 -1.3% 形成风格对照（见 I-01）。",
            difficulty="easy",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="M-07",
            category="market",
            question="贵州茅台（600519）2026-10-23 收盘时的市盈率（TTM）和市净率分别是多少？",
            answerable=True,
            expected_display=f"PE(TTM) {m['pe_ttm']:.2f} 倍；PB {m['pb']:.2f} 倍（Tushare daily_basic）",
            judges=[
                nj("PE(TTM)", [(m["pe_ttm"], "倍")], weight=2, tol={"abs": 0.02}, keywords=["市盈率", "PE", "TTM"]),
                nj("PB", [(m["pb"], "倍")], weight=2, tol={"abs": 0.02}, keywords=["市净率", "PB"]),
                dj("估值日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare daily_basic.pe_ttm/pb"],
            verify_by="Tushare daily_basic(600519.SH,20261023) → pe_ttm=20.4934, pb=6.6421；腾讯 tencent_quote → pe_ttm=20.49, pb=6.64。",
            source_url="https://qt.gtimg.cn/q=sh600519",
            evidence=ev("daily_basic_20261023", "tencent_quote"),
            notes="PE 有静态/TTM/动态多口径，题干限定 TTM。",
            difficulty="medium",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="M-08",
            category="market",
            question="恒生指数 2026-10-23 的收盘点位是多少？",
            answerable=True,
            expected_display=f"{T['m_hsi']['close']:.2f} 点（涨 {T['m_hsi']['pct_chg']:.2f}%）",
            judges=[
                nj("收盘点位", [(round(T["m_hsi"]["close"], 2), "点"), (T["m_hsi"]["close"], "点")], weight=3, tol={"abs": 0.05}, keywords=["收盘", "恒生", "点位"]),
                tj("标的", ["恒生指数", "HSI", "恒指"], weight=1),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY + ["hkex.com.hk"], CITE_TEXT_PRIMARY + ["港交所"], weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare index_global(HSI)"],
            verify_by="Tushare index_global(HSI,20261023) → close=23093.37, pct_chg=0.6919。",
            source_url="https://api.tushare.pro (api=index_global, ts_code=HSI)",
            evidence=ev("index_global_20261023"),
            notes="考「A 股数据源之外还要能取港股指数」。",
            difficulty="medium",
            trap=False,
        )
    )

    # ======================= 宏观 macro (6) =======================
    mc = T["c_macro"]
    lpr = mc["lpr_20261020"]
    specs.append(
        dict(
            qid="C-01",
            category="macro",
            question="2026 年 10 月 20 日发布的最新一期 LPR：1 年期和 5 年期以上分别是多少？",
            answerable=True,
            expected_display=f"1Y {lpr['lpr_1y']}%，5Y+ {lpr['lpr_5y']}%（{lpr['date']} 报价）",
            judges=[
                nj("1年期LPR", [(lpr["lpr_1y"], "%")], weight=2, tol={"abs": 0.001}, keywords=["1年", "一年期", "1Y"]),
                nj("5年期LPR", [(lpr["lpr_5y"], "%")], weight=2, tol={"abs": 0.001}, keywords=["5年", "五年期", "5Y"]),
                dj("报价日", [lpr["date"]], weight=1),
                cj("一手来源", CITE_MACRO + ["chinamoney.com.cn"], ["全国银行间同业拆借中心", "LPR", "央行"], weight=1),
            ],
            must_cite=True,
            primary_sources=["全国银行间同业拆借中心(eastmoney RPTA_WEB_RATE 镜像)", "tushare shibor/none"],
            verify_by="lpr_history() 最后一行 date=2026-10-20, lpr_1y=3.0, lpr_5y=3.5（来源 eastmoney RPTA_WEB_RATE，官方发布为 chinamoney.com.cn）。",
            source_url="https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_WEB_RATE",
            evidence=ev("lpr"),
            notes="官方源是 chinamoney.com.cn；取数可用镜像但须标 source。",
            difficulty="medium",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="C-02",
            category="macro",
            question="2026 年 9 月全国居民消费价格指数（CPI）同比涨幅是多少（%）？",
            answerable=True,
            expected_display=f"+{mc['cpi_202609']['nt_yoy']}%（环比 +{mc['cpi_202609']['nt_mom']}%）",
            judges=[
                nj("CPI同比", [(mc["cpi_202609"]["nt_yoy"], "%")], weight=3, tol={"abs": 0.02}, keywords=["同比", "CPI", "居民消费价格"]),
                tj("月份", PERIOD_SEP, weight=1),
                cj("一手来源", CITE_MACRO + ["stats.gov.cn"], ["国家统计局", "统计局", "NBS"], weight=1),
            ],
            must_cite=True,
            primary_sources=["tushare cn_cpi(202609)", "国家统计局"],
            verify_by="Tushare cn_cpi(202609) → nt_yoy=0.9, nt_mom=0.2；官方为国家统计局月度数据。",
            source_url="https://data.stats.gov.cn/easyquery.htm?cn=A01",
            evidence=ev("macro.cn_cpi"),
            notes="月度宏观数据必须点名月份。",
            difficulty="easy",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="C-03",
            category="macro",
            question="2026 年 9 月中国制造业采购经理指数（PMI）是多少？",
            answerable=True,
            expected_display=f"{mc['pmi_202609']['manufacturing_pmi']}",
            judges=[
                nj("制造业PMI", [(mc["pmi_202609"]["manufacturing_pmi"], "")], weight=3, tol={"abs": 0.05}, keywords=["制造业", "PMI"]),
                tj("月份", PERIOD_SEP, weight=1),
                cj("一手来源", CITE_MACRO + ["stats.gov.cn"], ["国家统计局", "统计局", "中国物流与采购联合会"], weight=1),
            ],
            must_cite=True,
            primary_sources=["tushare cn_pmi(202609).pmi010000", "国家统计局"],
            verify_by="Tushare cn_pmi(start_m=202609, fields=month,pmi010000,...) → pmi010000=50.1（制造业 PMI）。",
            source_url="https://data.stats.gov.cn/easyquery.htm?cn=A01",
            evidence=ev("macro.cn_pmi"),
            notes="PMI 是 50 荣枯线语境数据，答 50.1 正确；不要与财新 PMI 混。",
            difficulty="medium",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="C-04",
            category="macro",
            question="2026 年三季度国内生产总值（GDP）同比增长多少（%）？",
            answerable=True,
            expected_display=f"+{mc['gdp_2026q3']['gdp_yoy']}%（前三季度累计 {mc['gdp_2026q3']['gdp_yi']} 亿元）",
            judges=[
                nj("GDP同比", [(mc["gdp_2026q3"]["gdp_yoy"], "%")], weight=3, tol={"abs": 0.02}, keywords=["同比", "增长", "GDP", "国内生产总值"]),
                tj("季度", ["2026年三季度", "2026Q3", "三季度", "前三季度"], weight=1),
                cj("一手来源", CITE_MACRO + ["stats.gov.cn"], ["国家统计局", "统计局"], weight=1),
            ],
            must_cite=True,
            primary_sources=["tushare cn_gdp(2026Q3)", "国家统计局"],
            verify_by="Tushare cn_gdp(start_q=2026Q1,end_q=2026Q3) → 2026Q3 gdp=1071071.0 亿元, gdp_yoy=4.6。",
            source_url="https://data.stats.gov.cn/easyquery.htm?cn=B01",
            evidence=ev("macro.cn_gdp"),
            notes="Q3 GDP 通常 10 月中下旬发布，as_of 时点已可查。",
            difficulty="medium",
            trap=False,
        )
    )
    specs.append(
        dict(
            qid="C-05",
            category="macro",
            question="2026 年 9 月末 M2 余额同比增速是多少（%）？",
            answerable=True,
            expected_display=f"+{mc['m2_202609']['m2_yoy']}%（余额 {mc['m2_202609']['m2_yi']:.2f} 亿元）",
            judges=[
                nj("M2同比", [(mc["m2_202609"]["m2_yoy"], "%")], weight=3, tol={"abs": 0.02}, keywords=["M2", "同比", "广义货币"]),
                tj("月份", PERIOD_SEP, weight=1),
                cj("一手来源", CITE_MACRO + ["pbc.gov.cn"], ["中国人民银行", "央行", "人民银行"], weight=1),
            ],
            must_cite=True,
            primary_sources=["tushare cn_m(202609)", "中国人民银行"],
            verify_by="Tushare cn_m(202609) → m2=3596289.7 亿元, m2_yoy=7.2；官方为人民银行《金融统计数据报告》。",
            source_url="http://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html",
            evidence=ev("macro.cn_m"),
            notes="M1/M2 同比容易与 M2 余额单位（亿元）混淆。",
            difficulty="medium",
            trap=False,
        )
    )
    bond = mc["bond_10y_20261023"]
    specs.append(
        dict(
            qid="C-06",
            category="macro",
            question="2026-10-23 中债 10 年期国债到期收益率是多少（%）？",
            answerable=True,
            expected_display=f"{bond['y10']}%（中债国债收益率曲线）",
            judges=[
                nj("10Y国债收益率", [(bond["y10"], "%")], weight=3, tol={"abs": 0.01}, keywords=["10年", "十年", "国债", "收益率"]),
                dj("日期", ["2026-10-23"], weight=1),
                cj("一手来源", ["chinabond.com.cn", "tushare.pro"], ["中债", "中国债券信息网", "chinabond"], weight=1),
            ],
            must_cite=True,
            primary_sources=["中债登 chinabond 收益率曲线", "tushare shibor(交叉)"],
            verify_by="chinabond_yield_curve('20261023') → curve=中债国债收益率曲线, 10y=1.64。",
            source_url="https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery",
            evidence=ev("chinabond_20261023"),
            notes="10Y 国债是资产定价锚；别和 Shibor/MLF 混。",
            difficulty="medium",
            trap=False,
        )
    )

    # ======================= 行业/资金 industry (5) =======================
    swr = T["i_sw_rank"]
    specs.append(
        dict(
            qid="I-01",
            category="industry",
            question="按申万一级行业分类，2026-10-23 涨幅第一的行业是哪个？涨了多少（%）？",
            answerable=True,
            expected_display=f"{swr['top3'][0]['name']}（{swr['top3'][0]['pct_change']}%）；第2/3名：{swr['top3'][1]['name']} {swr['top3'][1]['pct_change']}%、{swr['top3'][2]['name']} {swr['top3'][2]['pct_change']}%",
            judges=[
                tj("行业名", [swr["top3"][0]["name"]], weight=2),
                nj("涨幅", [(swr["top3"][0]["pct_change"], "%")], weight=2, tol={"abs": 0.02}, keywords=["涨", "%"]),
                dj("交易日", DATE_LAST_TD, weight=1),
                tj("分类", ["申万", "SW"], weight=1),
                cj("一手来源", CITE_PRIMARY, ["tushare", "sw_daily", "申万"], weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare sw_daily(申万一级)"],
            verify_by="Tushare sw_daily(trade_date=20261023) 取 index_classify(level=L1,src=SW2021) 的 31 个行业，按 pct_change 降序：计算机 4.67% > 传媒 3.78% > 电力设备 3.67%。",
            source_url="https://api.tushare.pro (api=sw_daily, trade_date=20261023)",
            evidence=ev("sw_daily_20261023", "index_classify_L1"),
            notes="对比 T-01/M-04：这是「行业」而非「指数/个股」。",
            difficulty="hard",
            trap=False,
        )
    )
    lim = T["i_limit"]
    specs.append(
        dict(
            qid="I-02",
            category="industry",
            question="2026-10-23 沪深京全市场（含北交所）涨停家数和跌停家数分别是多少？",
            answerable=True,
            expected_display=f"涨停 {lim['limit_up']} 家、跌停 {lim['limit_down']} 家（Tushare limit_list_d）",
            judges=[
                nj("涨停家数", [(lim["limit_up"], "家")], weight=2, tol={"abs": 0}, keywords=["涨停"]),
                nj("跌停家数", [(lim["limit_down"], "家")], weight=2, tol={"abs": 0}, keywords=["跌停"]),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare limit_list_d(U/D)"],
            verify_by="Tushare limit_list_d(trade_date=20261023, limit_type=U, limit=1000) → 82 行；limit_type=D → 6 行。注意 limit 参数默认 100 会截断，必须放大。",
            source_url="https://api.tushare.pro (api=limit_list_d, trade_date=20261023)",
            evidence=ev("limit_list_20261023.U", "limit_list_20261023.D"),
            notes="易错点：默认分页上限 100 会截断（本例涨停 82 恰好<100，换天就错）；计数含北交所与 ST。",
            difficulty="hard",
            trap=False,
        )
    )
    specs.append(dict(
        qid="I-03", category="industry",
        question="2026-10-23 北向资金净流入多少亿元？",
        answerable=False, expected_display="隔离中:旧标准答案混淆净买入、成交额与单位",
        judges=[], validation_status="quarantined",
        quarantine_reason="需要独立核验披露制度和指标含义;不参与总分或数值准确率",
        must_cite=True, primary_sources=["HKEX Stock Connect disclosure rules"],
        verify_by="独立核验指标可得性与源语义后重建判据;不能复用工具的换算假设",
        source_url="https://www.hkex.com.hk/News/Market-Communications/2024/2404122news?sc_lang=en",
        evidence=ev("hsgt_20261023"), notes="旧数值期望已移除", difficulty="hard", trap=True,
    ))
    flow = T["i_600519_flow"]
    specs.append(
        dict(
            qid="I-04",
            category="industry",
            question="贵州茅台（600519）2026-10-23 的主力资金（大单+特大单）净流入是多少亿元？",
            answerable=True,
            expected_display=f"+{flow['main_yi']:.2f} 亿元（{flow['main_wan']:.2f} 万元；大单+特大单买卖差）",
            judges=[
                nj("主力净流入", [(flow["main_yi"], "亿元"), (flow["main_wan"], "万元")], weight=3, tol={"abs": 0.03}, keywords=["主力", "净流入", "净买入"]),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare moneyflow(600519.SH)"],
            verify_by="Tushare moneyflow(600519.SH,20261023): main=(buy_lg+buy_elg)-(sell_lg+sell_elg)=(299717.95+140604.86)-(228050.58+189112.45)=23159.78 万元=2.32 亿元。注意 net_mf_amount=59189.01 万是全单口径，不等同主力口径。",
            source_url="https://api.tushare.pro (api=moneyflow, ts_code=600519.SH, trade_date=20261023)",
            evidence=ev("moneyflow_20261023"),
            notes="「主力」有全单/大单+特大单两种口径，题干已限定；答题须说明口径。",
            difficulty="hard",
            trap=False,
        )
    )
    mg = T["i_600519_margin"]
    specs.append(
        dict(
            qid="I-05",
            category="industry",
            question="贵州茅台（600519）2026-10-23 的融资余额是多少亿元？",
            answerable=True,
            expected_display=f"{mg['rzye_yi']:.2f} 亿元（{mg['rzye_yuan']:.2f} 元）",
            judges=[
                nj("融资余额", [(mg["rzye_yi"], "亿元"), (mg["rzye_yuan"], "元")], weight=3, tol={"rel": 0.001}, keywords=["融资余额", "融资"]),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare margin_detail(600519.SH)"],
            verify_by="Tushare margin_detail(trade_date=20261023, ts_code=600519.SH) → rzye=16104615203.0 元=161.05 亿元；rzrqye=163.82 亿（含融券）。",
            source_url="https://api.tushare.pro (api=margin_detail, trade_date=20261023)",
            evidence=ev("margin_20261023"),
            notes="融资余额 ≠ 融资融券余额（后者含融券），差额 2.78 亿。",
            difficulty="medium",
            trap=False,
        )
    )

    # ======================= 新闻事件 news (4) =======================
    quake = T["n_earthquake"][0] if T["n_earthquake"] else {}
    specs.append(
        dict(
            qid="N-01",
            category="news",
            question="2026 年 10 月 22 日西藏那曲市巴青县地震的震级是多少？截至 10 月 23 日 13 时，伤亡情况如何？请给权威来源链接。",
            answerable=True,
            expected_display="7.0 级；截至 2026-10-23 13 时，16 人遇难、76 人受伤（央视新闻 2026-10-23 报道）",
            judges=[
                nj("震级", [(7.0, "级")], weight=2, tol={"abs": 0.01}, keywords=["震级", "级"]),
                nj("遇难人数", [(16, "人")], weight=2, tol={"abs": 0}, keywords=["遇难", "死亡"]),
                nj("受伤人数", [(76, "人")], weight=2, tol={"abs": 0}, keywords=["受伤", "伤"]),
                dj("日期", ["2026-10-22", "10月22日"], weight=1),
                cj("权威来源", CITE_NEWS_CCTV, ["央视", "CCTV", "新华社"], weight=1),
            ],
            must_cite=True,
            primary_sources=["cctv.com(央视新闻 2026-10-23)"],
            verify_by="cctv_news('2026-10-23') → 《西藏那曲市巴青县7.0级地震抢险救援工作抓紧进行》：截至10月23日下午1时，16人遇难、76人受伤。",
            source_url=quake.get("url", "https://tv.cctv.com/2026/10/23/"),
            evidence=ev("cctv_20261023"),
            notes="数字会随救援更新，题干锁定「截至10月23日13时」。新闻题必须给权威链接。",
            difficulty="medium",
            trap=False,
        )
    )
    donate = T["n_601899_donate"][0] if T["n_601899_donate"] else {}
    specs.append(
        dict(
            qid="N-02",
            category="news",
            question="2026-10-24 是哪家上市公司公告要向西藏那曲市巴青县地震灾区捐赠？公告标题是什么？",
            answerable=True,
            expected_display=f"紫金矿业（601899）；《{donate.get('title', '关于向西藏那曲市巴青县地震灾区捐赠的公告')}》（2026-10-24）",
            judges=[
                tj("公司", ["紫金矿业", "601899"], weight=3),
                dj("公告日期", ["2026-10-24"], weight=1),
                tj("事件", ["捐赠", "地震", "巴青"], weight=1),
                cj("一手来源", CITE_CNINFO, ["巨潮", "cninfo", "上交所"], weight=1),
            ],
            must_cite=True,
            primary_sources=["cninfo.com.cn(紫金矿业公告 2026-10-24)"],
            verify_by="cninfo_announcements('601899') → 2026-10-24《紫金矿业集团股份有限公司关于向西藏那曲市巴青县地震灾区捐赠的公告》。",
            source_url=donate.get("url", "https://www.cninfo.com.cn/new/disclosure/stock?stockCode=601899"),
            evidence=ev("cninfo_601899"),
            notes="「新闻线索(地震)→一手公告(捐赠)」两步交叉题。",
            difficulty="medium",
            trap=False,
        )
    )
    pdd = T["n_pdd"][0] if T["n_pdd"] else {}
    specs.append(
        dict(
            qid="N-03",
            category="news",
            question="2026-10-24 财联社报道拼多多为西藏地震捐赠了多少钱？用于什么？",
            answerable=True,
            expected_display="1000 万元现金（通过中华慈善总会，用于采购救灾物资/安置受灾群众/灾后重建）",
            judges=[
                nj("捐赠金额", [(1000, "万元"), (0.1, "亿元")], weight=3, tol={"rel": 0.001}, keywords=["捐赠", "万元", "亿元"]),
                tj("公司", ["拼多多"], weight=1),
                tj("用途/渠道", ["中华慈善总会", "救灾", "灾后重建", "安置"], weight=1),
                cj("来源", CITE_CLS, ["财联社", "cls"], weight=1),
            ],
            must_cite=True,
            primary_sources=["cls.cn(财联社电报 2026-10-24 11:27)"],
            verify_by="cls_telegraph() → 2026-10-24 11:27:02《拼多多捐赠1000万元 驰援西藏那曲抗震救灾》。",
            source_url=pdd.get("url") or "https://www.cls.cn/telegraph",
            evidence=ev("cls_telegraph"),
            notes="新闻题：可答「发现线索」，但金额需引用署名来源（L2）。",
            difficulty="easy",
            trap=False,
        )
    )
    lm = T["n_longmarch"][0] if T["n_longmarch"] else {}
    specs.append(
        dict(
            qid="N-04",
            category="news",
            question="2026-10-22 在北京举行的纪念大会主题是什么？",
            answerable=True,
            expected_display="纪念中国工农红军长征胜利 90 周年大会（2026-10-22 在北京隆重举行，央视新闻联播报道）",
            judges=[
                tj("主题", ["长征胜利90周年", "长征胜利 90 周年", "红军长征胜利九十周年", "长征胜利九十周年"], weight=3),
                tj("地点/形式", ["北京", "纪念大会"], weight=1),
                dj("日期", ["2026-10-22", "10月22日"], weight=1),
                cj("权威来源", CITE_NEWS_CCTV, ["央视", "CCTV", "新华社", "人民日报"], weight=1),
            ],
            must_cite=True,
            primary_sources=["cctv.com(新闻联播 2026-10-22)"],
            verify_by="cctv_news('2026-10-22') → 《纪念中国工农红军长征胜利90周年大会在京隆重举行》。",
            source_url=lm.get("url", "https://tv.cctv.com/2026/10/22/"),
            evidence=ev("cctv_20261022"),
            notes="考「新闻事件是否真的发生」而非观点；来源须可回查。",
            difficulty="easy",
            trap=False,
        )
    )

    # ======================= 研报观点 research (4) =======================
    rr = T["r_reports"]["600519"]
    specs.append(
        dict(
            qid="R-01",
            category="research",
            question="贵州茅台（600519）最近一份券商研报是哪家机构、什么评级、对 2026 年 EPS 的预测是多少？发布日期？",
            answerable=True,
            expected_display=f"{rr['publish_date']} {rr['org']} 评级「{rr['rating']}」，2026E EPS {float(rr['eps_this_year']):.2f} 元",
            judges=[
                tj("机构", [rr["org"]], weight=2),
                tj("评级", [rr["rating"]], weight=1),
                nj("2026E EPS", [(round(float(rr["eps_this_year"]), 2), "元")], weight=3, tol={"abs": 0.01}, keywords=["EPS", "每股收益", "2026"]),
                dj("发布日期", [rr["publish_date"]], weight=1),
                cj("来源", CITE_REPORT, ["研报", "东财", "东方财富", "eastmoney"], weight=1),
            ],
            must_cite=True,
            primary_sources=["eastmoney.com(研报中心)"],
            verify_by="eastmoney_reports('600519',1) 按 publishDate 降序第一条：publishDate=2026-09-21, orgSName=诚通证券, emRatingName=买入, predictThisYearEps=65.14。",
            source_url="https://data.eastmoney.com/report/",
            evidence=ev("em_reports.600519"),
            notes="研报是观点(L2)，不是事实；评分只认「最新一份」+ 明确预测值。",
            difficulty="medium",
            trap=False,
        )
    )
    rr2 = T["r_reports"]["300750"]
    specs.append(
        dict(
            qid="R-02",
            category="research",
            question="宁德时代（300750）2026-10-21 之后最新的一份研报，机构、评级和 2026 年 EPS 预测是多少？",
            answerable=True,
            expected_display=f"{rr2['publish_date']} {rr2['org']} 评级「{rr2['rating']}」，2026E EPS {float(rr2['eps_this_year']):.2f} 元",
            judges=[
                tj("机构", [rr2["org"]], weight=2),
                nj("2026E EPS", [(round(float(rr2["eps_this_year"]), 2), "元")], weight=3, tol={"abs": 0.01}, keywords=["EPS", "每股收益", "2026"]),
                dj("发布日期", [rr2["publish_date"]], weight=1),
                cj("来源", CITE_REPORT, ["研报", "东财", "东方财富"], weight=1),
            ],
            must_cite=True,
            primary_sources=["eastmoney.com(研报中心)"],
            verify_by="eastmoney_reports('300750',1) 降序第一条：2026-10-21 山西证券 买入 predictThisYearEps=18.15。",
            source_url="https://data.eastmoney.com/report/",
            evidence=ev("em_reports.300750"),
            notes="一份研报有多个 EPS 预测年份，必须取 2026E。",
            difficulty="medium",
            trap=False,
        )
    )
    rr3 = T["r_reports"]["601899"]
    specs.append(
        dict(
            qid="R-03",
            category="research",
            question="紫金矿业（601899）最新一份研报的机构、评级和 2026 年 EPS 预测是多少？",
            answerable=True,
            expected_display=f"{rr3['publish_date']} {rr3['org']} 评级「{rr3['rating']}」，2026E EPS {float(rr3['eps_this_year']):.2f} 元",
            judges=[
                tj("机构", [rr3["org"]], weight=2),
                tj("评级", [rr3["rating"]], weight=1),
                nj("2026E EPS", [(round(float(rr3["eps_this_year"]), 2), "元")], weight=3, tol={"abs": 0.01}, keywords=["EPS", "每股收益", "2026"]),
                cj("来源", CITE_REPORT, ["研报", "东财", "东方财富"], weight=1),
            ],
            must_cite=True,
            primary_sources=["eastmoney.com(研报中心)"],
            verify_by="eastmoney_reports('601899',1) 降序第一条：2026-08-28 华龙证券 增持 predictThisYearEps=3.11。",
            source_url="https://data.eastmoney.com/report/",
            evidence=ev("em_reports.601899"),
            notes="与 T-14 的重述题交叉：同一家公司既有公告又有研报，别混源。",
            difficulty="medium",
            trap=False,
        )
    )
    rr4 = T["r_reports"]["002415"]
    specs.append(
        dict(
            qid="R-04",
            category="research",
            question="海康威视（002415）2026 年三季报发布后最新一份点评研报的机构和 2026 年 EPS 预测是多少？",
            answerable=True,
            expected_display=f"{rr4['publish_date']} {rr4['org']} 评级「{rr4['rating']}」，2026E EPS {float(rr4['eps_this_year']):.2f} 元",
            judges=[
                tj("机构", [rr4["org"]], weight=2),
                nj("2026E EPS", [(round(float(rr4["eps_this_year"]), 2), "元")], weight=3, tol={"abs": 0.01}, keywords=["EPS", "每股收益", "2026"]),
                dj("发布日期", [rr4["publish_date"]], weight=1),
                cj("来源", CITE_REPORT, ["研报", "东财", "东方财富"], weight=1),
            ],
            must_cite=True,
            primary_sources=["eastmoney.com(研报中心)"],
            verify_by="eastmoney_reports('002415',1) 降序第一条：2026-10-21 群益证券 增持 predictThisYearEps=2.07。",
            source_url="https://data.eastmoney.com/report/",
            evidence=ev("em_reports.002415"),
            notes="题干约束「三季报发布后」，需要把研报日期与财报日期对齐。",
            difficulty="hard",
            trap=False,
        )
    )

    # ======================= 边界与陷阱 trap (14) =======================
    amb = T["t_ambiguous"]
    specs.append(
        dict(
            qid="T-01",
            category="trap",
            question="000001 现在多少钱？",
            answerable=True,
            expected_display=f"歧义：sh000001=上证指数（{amb['sh000001_index']['price']} 点），sz000001=平安银行（{amb['sz000001_stock']['price']} 元）。正确做法是消歧后分别回答。",
            judges=[
                tj("指数候选", ["上证指数", "上证综指"], weight=1),
                tj("个股候选", ["平安银行"], weight=1),
                nj("指数点位", [(round(amb["sh000001_index"]["price"], 2), "点"), (amb["sh000001_index"]["price"], "点")], weight=1, tol={"abs": 0.05}, keywords=["上证", "指数"]),
                nj("个股价格", [(amb["sz000001_stock"]["price"], "元")], weight=1, tol={"abs": 0.01}, keywords=["平安银行", "个股", "股票"]),
                tj("消歧说明", ["歧义", "消歧", "不明确", "分别", "取决于", "两个", "需明确", "两种"], weight=2),
            ],
            must_cite=False,
            primary_sources=["tencent_quote(sh000001) vs tencent_quote(000001)", "tushare daily(000001.SZ)"],
            verify_by="tencent_quote('sh000001')→上证指数 3690.48 点；tencent_quote('000001')→平安银行 11.2 元；Tushare daily(000001.SZ,20261023).close=11.2。",
            source_url="https://qt.gtimg.cn/q=sh000001,sz000001",
            evidence=ev("tencent_quote", "daily_20261023"),
            notes="最经典的代码歧义题：不能默认当个股，也不能默认当指数；应报歧义 + 两解。",
            difficulty="hard",
            trap=True,
        )
    )
    specs.append(
        dict(
            qid="T-02",
            category="trap",
            question="贵州茅台（600519）2026-10-24 的收盘价是多少元？",
            answerable=True,
            expected_display=f"2026-10-24 是周六、非交易日，无当日收盘价；最近交易日 2026-10-23 收盘 {m['close']:.2f} 元。",
            judges=[
                rj("指出非交易日", ["非交易日", "不是交易日", "周六", "休市", "未开盘", "没有交易"], weight=2),
                nj("回退最近收盘价", [(m["close"], "元")], weight=2, tol={"abs": 0.01}, keywords=["收盘", "10-23", "10月23"]),
                dj("最近交易日", DATE_LAST_TD, weight=1),
            ],
            must_cite=False,
            primary_sources=["szse 交易日历", "tushare daily(20261023)"],
            verify_by="trading_calendar(2026,10) → 2026-10-24 is_open=False；Tushare daily(600519.SH,20261023).close=1335.0；20261024 无行情。",
            source_url="https://www.szse.cn/api/report/exchange/onepersistenthour/monthList?month=2026-10",
            evidence=ev("trading_calendar_2026_10", "daily_20261023"),
            notes="正确行为：说明非交易日 + 回退最近交易日并标注日期；编一个 10-24 价格=幻觉。",
            difficulty="hard",
            trap=True,
            expected_refusal=True,
        )
    )
    sus = T["t_601326"]
    specs.append(
        dict(
            qid="T-03",
            category="trap",
            question="秦港股份（601326）2026-10-23 的收盘价和成交额是多少？",
            answerable=True,
            expected_display=f"601326 在 2026-10-23 停牌（Tushare suspend_d 标记 S，daily 无记录），当日无成交、无有效收盘价；腾讯返回的 {sus['tencent']['price'] if sus['tencent'] else '—'} 元是停牌前报价（is_stale=true），不能当当日成交。",
            judges=[
                rj("指出停牌/无成交", ["停牌", "无成交", "未交易", "暂停交易", "没有交易", "0 成交"], weight=3),
                tj("僵尸报价提示", ["stale", "僵尸", "停牌前", "非当日", "冻结", "不可靠"], weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare suspend_d(20261023)", "腾讯 is_stale"],
            verify_by="Tushare suspend_d(trade_date=20261023) 含 601326.SH type=S；Tushare daily(601326.SH,20261023) rows=0；tencent_quote('601326').is_stale=true, stale_reason='成交量为0(停牌)'。",
            source_url="https://api.tushare.pro (api=suspend_d, trade_date=20261023)",
            evidence=ev("suspend_d_20261023", "t_601326"),
            notes="停牌股是「僵尸报价」高发区：接口 HTTP 200 + 旧价，必须判 stale。",
            difficulty="hard",
            trap=True,
            expected_refusal=True,
        )
    )
    specs.append(
        dict(
            qid="T-04",
            category="trap",
            question="贵州茅台（600519）2026-12-31 的收盘价会是多少？",
            answerable=True,
            expected_display="未来日期（as_of 2026-10-24 时 2026-12-31 尚未发生），不可能有收盘价；正确做法是拒答/说明不可预知（可给历史区间但不得当成答案）。",
            judges=[
                rj("正确拒答", ["未来", "尚未发生", "无法预知", "不可预知", "不可能", "没有数据", "超出", "尚未公布", "无法获取", "不能预测"], weight=4),
                fj("不得编造未来价格", ["2026-12-31", "2026年12月31日", "12月31日"], weight=4),
            ],
            must_cite=False,
            primary_sources=["as_of 定义(interface-spec §3)"],
            verify_by="as_of=2026-10-24；2026-12-31 > as_of，任何行情源都不可能有该日数据。verify: trading_calendar 有该日但行情不存在。",
            source_url="docs/interface-spec.md §3 (as_of)",
            evidence=ev("trading_calendar_2026_10"),
            notes="未来日期陷阱：给任何具体价格都是幻觉；拒答得分。",
            difficulty="hard",
            trap=True,
            expected_refusal=True,
        )
    )
    dl = T["t_delisted"]
    specs.append(
        dict(
            qid="T-05",
            category="trap",
            question="乐视网（300104）现在股价多少？",
            answerable=True,
            expected_display=f"乐视网已退市（乐视退），无实时行情；腾讯返回 {dl['300104']['price'] if dl['300104'] else '—'} 元为退市后的冻结/僵尸报价（is_stale=true），不能当现价。",
            judges=[
                rj("指出已退市/无实时行情", ["退市", "已退市", "无实时", "没有实时", "不在交易", "摘牌", "终止上市"], weight=3),
                tj("僵尸报价提示", ["僵尸", "冻结", "stale", "历史报价", "不可用", "0.18"], weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare stock_basic(list_status=D)", "腾讯 is_stale"],
            verify_by="Tushare stock_basic(list_status='D') 含 300104.SZ 乐视退；tencent_quote('300104').is_stale=true, stale_reason='成交量为0(停牌/未开盘/废码)'。",
            source_url="https://api.tushare.pro (api=stock_basic, list_status=D)",
            evidence=ev("stock_basic_delisted", "t_delisted"),
            notes="退市股陷阱：数据源仍返回 200 + 旧价；必须查上市状态。",
            difficulty="hard",
            trap=True,
            expected_refusal=True,
        )
    )
    specs.append(
        dict(
            qid="T-06",
            category="trap",
            question="贵州茅台（600519）2026-10-23 的成交额换成「亿元」是多少？",
            answerable=True,
            expected_display=f"{m['amount_yi']:.2f} 亿元（Tushare amount={m['amount_qian']} 千元；腾讯 amount_wan={m['tencent_amount_wan']} 万元）",
            judges=[
                nj(
                    "成交额(亿元)",
                    [
                        (round(m["amount_yi"], 2), "亿元"),
                        (m["amount_qian"], "千元"),
                        (m["tencent_amount_wan"], "万元"),
                    ],
                    weight=3,
                    tol={"rel": 0.002},
                    keywords=["成交额", "亿元"],
                ),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare daily.amount(千元)", "腾讯 amount_wan(万元)"],
            verify_by="7677216.283 千元 / 1e5 = 76.77 亿元；767722 万元 / 1e4 = 76.77 亿元。若把 767722 当亿元=错(放大 1万倍)。",
            source_url="https://qt.gtimg.cn/q=sh600519",
            evidence=ev("daily_20261023", "tencent_quote"),
            notes="单位换算陷阱：Tushare 千元、腾讯万元、问题亿元。",
            difficulty="hard",
            trap=True,
        )
    )
    specs.append(
        dict(
            qid="T-07",
            category="trap",
            question="贵州茅台（600519）2026-10-23 的成交量折合多少股？",
            answerable=True,
            expected_display=f"{m['vol_gu']:,} 股（Tushare vol={m['vol_shou']} 手 × 100）",
            judges=[
                nj("成交量(股)", [(m["vol_gu"], "股"), (m["vol_shou"], "手")], weight=3, tol={"rel": 0.0005}, keywords=["成交量", "股"]),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare daily.vol(手)"],
            verify_by="Tushare daily(600519.SH,20261023).vol=57685.93 手 × 100 = 5,768,593 股。",
            source_url="https://api.tushare.pro (api=daily, ts_code=600519.SH)",
            evidence=ev("daily_20261023"),
            notes="1 手=100 股；把 57685.93 直接当股数则错 100 倍。",
            difficulty="hard",
            trap=True,
        )
    )
    hk = T.get("m_hk00700", {})
    specs.append(
        dict(
            qid="T-08",
            category="trap",
            question="腾讯控股（00700.HK）2026-10-23 收盘价是多少？",
            answerable=True,
            expected_display=f"{hk.get('price_hkd')} 港元（港股，港币计价；A 股接口不直接支持港股代码）",
            judges=[
                nj("收盘价(港元)", [(hk.get("price_hkd"), "港元")], weight=3, tol={"abs": 0.01}, keywords=["收盘", "港元", "港币"], unit_optional=False),
                tj("币种", ["港元", "港币", "HKD"], weight=2),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("来源", ["gtimg.cn", "qq.com", "hkex.com.hk"], ["腾讯", "港交所"], weight=1),
            ],
            must_cite=False,
            primary_sources=["腾讯 qt.gtimg.cn/q=hk00700"],
            verify_by="http GET https://qt.gtimg.cn/q=hk00700 → 386.000 港元（2026/10/23 16:08 收盘）；Tushare hk_daily 亦可用(本次限频未取)。",
            source_url="https://qt.gtimg.cn/q=hk00700",
            evidence=ev("tencent_raw_hk_us"),
            notes="货币陷阱：同样「多少钱」, 港股是港币; 报人民币是错的。",
            difficulty="hard",
            trap=True,
        )
    )
    bj = T["t_bj"]
    specs.append(
        dict(
            qid="T-09",
            category="trap",
            question="中科美菱（920992.BJ）2026-10-23 收盘价是多少元？",
            answerable=True,
            expected_display=f"{bj['quote_920992_bj']['price']:.2f} 元（北交所；腾讯需 bj 前缀, 无前缀返回空)）",
            judges=[
                nj("收盘价", [(bj["quote_920992_bj"]["price"], "元")], weight=3, tol={"abs": 0.01}, keywords=["收盘", "收盘价"]),
                tj("北交所标识", ["北交所", "北京证券交易所", "920992", "BJ"], weight=1),
                dj("交易日", DATE_LAST_TD, weight=1),
                cj("来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["腾讯 qt.gtimg.cn/q=bj920992", "tushare daily(920992.BJ)"],
            verify_by="tencent_quote('bj920992') → price=11.79, is_stale=false；tencent_quote('920992') → {}（需前缀）；Tencent K线对北交所直接 ValueError。",
            source_url="https://qt.gtimg.cn/q=bj920992",
            evidence=ev("tencent_quote_bj", "q3_income.920992.BJ"),
            notes="北交所陷阱：不补 bj 前缀取不到；且北交所 K 线能力边界要显式 UNSUPPORTED。",
            difficulty="hard",
            trap=True,
        )
    )
    specs.append(
        dict(
            qid="T-10",
            category="trap",
            question="000300 2026-10-23 收盘多少？",
            answerable=True,
            expected_display=f"000300 是指数代码，对应沪深300指数；2026-10-23 收 {hs['close']:.2f} 点。",
            judges=[
                nj("指数点位", [(round(hs["close"], 2), "点"), (hs["close"], "点")], weight=2, tol={"abs": 0.01}, keywords=["收盘", "点"], unit_optional=False),
                tj("正确识别为指数", ["沪深300", "沪深 300", "指数"], weight=2),
                dj("交易日", DATE_LAST_TD, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare index_daily(000300.SH)"],
            verify_by="Tushare index_daily(000300.SH,20261023) → close=4105.3286。指数只能用 6 位指数代码。",
            source_url="https://api.tushare.pro (api=index_daily, ts_code=000300.SH)",
            evidence=ev("index_daily_20261023"),
            notes="「指数与个股同码/纯数字代码」陷阱：000300 不是股票。",
            difficulty="medium",
            trap=True,
        )
    )
    specs.append(
        dict(
            qid="T-11",
            category="trap",
            question="贵州茅台（600519）在 2026 年 9 月是否发布过公告？如果有，请列出；如果没有，请说明。",
            answerable=True,
            expected_display=f"没有。截至 as_of，600519 最新公告是 {ann_latest['latest_date']} 的《{ann_latest['latest_title']}》，2026-09 无公告。",
            judges=[
                rj("正确判空", ["无公告", "没有公告", "未发布", "空结果", "没有发布", "不存在"], weight=3),
                fj("不得编造 9 月公告", ["2026-09", "9月", "202609"], weight=3),
                dj("最近公告日期", ["2026-08-15"], weight=1),
                cj("来源", CITE_CNINFO, ["巨潮", "cninfo"], weight=1),
            ],
            must_cite=True,
            primary_sources=["cninfo.com.cn(巨潮公告列表)"],
            verify_by="cninfo_announcements('600519',30) 最新 date=2026-08-15，2026-08-16 至 2026-10-24 无任何公告。",
            source_url="https://www.cninfo.com.cn/new/disclosure/stock?stockCode=600519",
            evidence=ev("cninfo_600519"),
            notes="空结果陷阱：合法空结果(NO_DATA)不能当「源挂了」，更不能编公告。",
            difficulty="hard",
            trap=True,
            expected_refusal=True,
        )
    )
    specs.append(
        dict(
            qid="T-12",
            category="trap",
            question="贵州茅台 2026 年半年报的「净利润」（含少数股东损益，合并口径）是多少亿元？",
            answerable=True,
            expected_display=f"{h1['n_income_yi']:.2f} 亿元（归母净利润为 {h1['n_income_attr_p_yi']:.2f} 亿元，两者差 {h1['n_income_yi']-h1['n_income_attr_p_yi']:.2f} 亿）",
            judges=[
                nj("合并净利润", [(h1["n_income_yi"], "亿元"), (h1["n_income"], "元")], weight=3, tol={"rel": 0.002}, keywords=["净利润"]),
                tj("报告期", PERIOD_H1, weight=1),
                tj("口径说明", ["含少数股东", "合并", "少数股东损益"], weight=1),
                cj("一手来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare income.n_income / n_income_attr_p"],
            verify_by="Tushare income(600519.SH,20260630) → n_income=46033330566.78 (含少数股东), n_income_attr_p=44516880421.86 (归母)；差 15.16 亿。",
            source_url="https://api.tushare.pro (api=income, ts_code=600519.SH, period=20260630)",
            evidence=ev("income_600519_h1"),
            notes="口径陷阱：问「净利润」若答成归母 445.17 亿即错；两者差 3.3%。",
            difficulty="hard",
            trap=True,
        )
    )
    pe = T["m_000002_pe"]
    specs.append(
        dict(
            qid="T-13",
            category="trap",
            question="万科A（000002）2026-10-23 的市盈率（TTM）是多少倍？",
            answerable=True,
            expected_display=f"PE(TTM) 不可用（公司亏损，Tushare daily_basic.pe/pe_ttm 为 null）；当日收盘 {pe['close']:.2f} 元、PB←可用。不能给一个正数 PE。",
            judges=[
                rj("指出亏损/PE不可用", ["亏损", "为负", "不可计算", "无法计算", "不适用", "null", "无PE", "没有市盈率", "不披露", "负值"], weight=3),
                tj("价格可给", [f"{pe['close']:.2f}", str(pe["close"])], weight=1),
                cj("来源", CITE_PRIMARY, CITE_TEXT_PRIMARY, weight=1),
            ],
            must_cite=False,
            primary_sources=["tushare daily_basic(000002.SZ)"],
            verify_by="Tushare daily_basic(000002.SZ,20261023) → pe=None, pe_ttm=None, pb=0.3935, close=3.37（亏损股 PE 无意义）。",
            source_url="https://api.tushare.pro (api=daily_basic, trade_date=20261023)",
            evidence=ev("daily_basic_20261023"),
            notes="PE 异常陷阱：亏损股 PE=null/负；给出正数 PE 即幻觉。",
            difficulty="hard",
            trap=True,
            expected_refusal=True,
        )
    )
    rest = T["t_restate"]
    specs.append(
        dict(
            qid="T-14",
            category="trap",
            question="2026-10-20 至 2026-10-24 期间，A 股有哪些公司发布了「前期会计差错更正」公告？请至少举出两家，并给出各自公告日期。",
            answerable=True,
            expected_display="至少两家：新华锦（600735，2026-10-20，前期会计差错更正公告）；药易购（300937，2026-10-23，前期会计差错补充更正公告 + 专项鉴证报告）。",
            judges=[
                tj("案例1-新华锦", ["600735", "新华锦"], weight=2),
                tj("案例2-药易购", ["300937", "药易购"], weight=2),
                dj("公告日期", ["2026-10-20", "2026-10-23"], weight=1),
                cj("来源", CITE_CNINFO, ["巨潮", "cninfo"], weight=1),
            ],
            must_cite=True,
            primary_sources=["cninfo.com.cn(全文检索 会计差错更正, 2026-10-20~24)", "cninfo 公司公告页"],
            verify_by="巨潮全文检索 searchkey=会计差错更正, sdate=2026-10-20, edate=2026-10-24 → 600735 新华锦 2026-10-20《关于前期会计差错更正的公告》; 300937 药易购 2026-10-23《关于前期会计差错补充更正的公告》。可再按公司公告页复核。",
            source_url="http://www.cninfo.com.cn/new/fulltextSearch/full?searchkey=会计差错更正",
            evidence=ev("cninfo_search_restate", "cninfo_300937", "cninfo_600735"),
            notes="财报重述陷阱：财报「更新后」版本与原始版本并存，as_of 之后才更正的不能用旧数当最终值；并且必须从公告全文检索找到公司，而不是瞎猜。",
            difficulty="hard",
            trap=True,
        )
    )
    return specs


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
AS_OF_LABELS = {"报告期", "月份", "季度", "交易日", "估值日", "报价日", "日期", "披露日期", "公告日期", "发布日期", "最近公告日期"}


def _role(judge: dict) -> str:
    if judge["type"] == "numeric":
        return "value"
    if judge["type"] == "date_in" or (judge["type"] == "text_any" and judge["label"] in AS_OF_LABELS):
        return "as_of"
    if judge["type"] == "cite":
        return "source"
    if judge["type"] in ("refusal", "forbid_numbers", "forbid_text"):
        return "safety"
    return "content"


def derive_flat_fields(spec: dict) -> dict:
    numeric = [j for j in spec["judges"] if j["type"] == "numeric"]
    out = dict(spec)
    out["as_of"] = AS_OF
    out["expected_refusal"] = bool(spec.get("expected_refusal", False))
    def _fix_date(j):
        j = dict(j)
        j["role"] = _role(j)
        if j["type"] == "date_in":
            fixed = []
            for d in j["dates"]:
                m = re.match(r"^(\d{4})(\d{2})(\d{2})$", str(d))
                fixed.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else str(d))
            j["dates"] = fixed
        return j

    out["judges"] = [_fix_date(j) for j in spec["judges"]]
    out["expected"] = {
        "display": spec["expected_display"],
        "values": [
            {"name": j["label"], "accept": j["accept"], "tolerance": j["tolerance"]} for j in numeric
        ],
    }
    if len(numeric) == 1:
        out["unit"] = numeric[0]["accept"][0]["unit"]
        out["tolerance"] = numeric[0]["tolerance"]
    elif numeric:
        out["unit"] = "mixed(见 expected.values)"
        out["tolerance"] = "per-value(见 expected.values)"
    else:
        out["unit"] = "n/a"
        out["tolerance"] = "n/a"
    out["match_method"] = sorted({j["type"] for j in spec["judges"]})
    # 去掉内部字段
    out.pop("expected_display", None)
    return out


def main() -> int:
    raise SystemExit("历史题库已冻结，不再原地重建。使用 verify_golden.py 检查现有快照；新题库独立审核。")


def _historical_main() -> int:
    """Historical generation procedure, retained for audit only."""
    bundle = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
    T, RF = bundle["truth"], bundle["raw_files"]
    specs = build_specs(T, RF)
    qids = [s["qid"] for s in specs]
    assert len(qids) == len(set(qids)), "qid 重复"
    records = []
    for spec in specs:
        rec = derive_flat_fields(spec)
        assert rec["evidence"], f"{rec['qid']} 缺 evidence"
        for path in rec["evidence"]:
            assert (ROOT / path).exists(), f"{rec['qid']} evidence 不存在: {path}"
        records.append(rec)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # 覆盖矩阵摘要
    from collections import Counter

    cats = Counter(r["category"] for r in records)
    traps = [r["qid"] for r in records if r["trap"]]
    print(f"[build_golden] wrote {len(records)} questions -> {OUT_PATH.relative_to(ROOT)}")
    print(f"[build_golden] categories: {dict(cats)}")
    print(f"[build_golden] traps({len(traps)}): {traps}")
    unanswerable = [r["qid"] for r in records if not r["answerable"]]
    print(f"[build_golden] unanswerable: {unanswerable}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
