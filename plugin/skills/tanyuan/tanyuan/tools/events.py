"""Event-driven fundamentals: earnings pre-announcements / flash reports, and money flow.

These are the pieces a research workflow needs before the news cycle: 业绩预告 and
业绩快报 arrive via filings with an `ann_date` (PIT-safe), while money flow explains
who moved the stock. Tushare only: free sources don't expose these cleanly.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import tushare
from ..envelope import Result
from ..errors import TanyuanError, bad_arg, no_data
from ..registry import tool
from ..util import clamp, now_iso, num, resolve_symbol


def _fmt_date(raw) -> str:
    text = str(raw)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and text.isdigit() else text


def _norm_date(value: str | None, *, name: str) -> str | None:
    if not value:
        return None
    from ..util import date_only
    try:
        return date_only(value).replace("-", "")
    except ValueError as exc:
        raise bad_arg(f"{name} 日期格式不对: {value!r}", hint="用 YYYY-MM-DD 或 YYYYMMDD") from exc


@tool(
    name="fundamental.earnings",
    summary="业绩预告/业绩快报(透传公告日与报告期,不保证历史版本可得性)",
    covers=["A 股业绩预告(forecast):预计净利润区间、同比区间、原因", "业绩快报(express):已披露的快报数据"],
    not_covers=["正式财报(用 fundamental.financials)", "港股/美股", "研报盈利预测(用 fundamental.reports)"],
    args_schema={
        "code": {"type": "string", "description": "股票代码;与 ann_date 至少给一个"},
        "ann_date": {"type": "string", "description": "公告日 YYYY-MM-DD:取该日全市场预告"},
        "start_date": {"type": "string", "format": "date", "description": "单票模式:起始日,默认 end-365 天"},
        "end_date": {"type": "string", "format": "date", "description": "单票模式:结束日,默认今天"},
        "kind": {"type": "string", "enum": ["forecast", "express", "both"], "description": "默认 forecast"},
        "limit": {"type": "integer", "min": 1, "max": 500, "description": "默认 50"},
    },
    returns=[
        {"field": "events[].kind", "desc": "forecast 或 express;provenance.units 按此值选择适用单位"},
        {"field": "events[].ann_date", "desc": "上游公告日;不代表当前返回版本在该日已经可见"},
        {"field": "events[].end_date", "desc": "报告期"},
        {"field": "events[].type", "desc": "预告类型:预增/预减/扭亏/首亏…"},
        {"field": "events[].net_profit_min_wan / net_profit_max_wan", "desc": "forecast 预计净利润区间(万元)"},
        {"field": "events[].revenue / n_income", "desc": "express 营业收入/净利润原值(元)"},
        {"field": "events[].yoy_net_profit", "desc": "express 去年同期修正后净利润(元),不是同比增长率"},
        {"field": "events[].yoy_sales", "desc": "express 营业收入同比增长率(%);上游未提供时为空"},
        {"field": "events[].p_change_min / max", "desc": "同比变动区间(%)"},
        {"field": "events[].summary / change_reason", "desc": "摘要与原因"},
    ],
    source="tushare_earnings", grade="L1", cache_ttl_s=3600, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"code": "600519", "kind": "forecast"},
               "cmd": "ty call fundamental.earnings --json '{\"code\":\"600519\"}'"},
              {"args": {"ann_date": "2026-10-23", "limit": 30},
               "cmd": "ty call fundamental.earnings --json '{\"ann_date\":\"2026-10-23\"}'"}],
    latency_hint_ms=400,
)
def fundamental_earnings(args: dict) -> Result:
    kind = args.get("kind") or "forecast"
    limit = clamp(int(args.get("limit") or 50), 1, 500)
    sym = None
    if args.get("code"):
        sym = resolve_symbol(args["code"])
    elif not args.get("ann_date"):
        raise bad_arg("至少给 code 或 ann_date",
                      hint="Tushare 要求二者之一;全市场按公告日用 ann_date。")
    ann_date = _norm_date(args.get("ann_date"), name="ann_date")

    results: list[dict] = []
    warnings: list[dict] = []
    failures: list[tuple[str, TanyuanError]] = []
    apis = {"forecast": ["forecast"], "express": ["express"], "both": ["forecast", "express"]}[kind]
    if sym and not ann_date:
        end = _norm_date(args.get("end_date") or date.today().isoformat(), name="end_date")
        start = _norm_date(args.get("start_date") or
                           (date.fromisoformat(_fmt_date(end)) - timedelta(days=365)).isoformat(), name="start_date")
        if start > end:
            raise bad_arg("start_date 不能晚于 end_date")
        base = {"ts_code": sym.ts_code, "start_date": start, "end_date": end}
    elif ann_date:
        base = {"ann_date": ann_date}
        if sym:
            base["ts_code"] = sym.ts_code
    else:
        base = {"ts_code": sym.ts_code}

    for api in apis:
        try:
            rows = tushare.call(api, dict(base), "")
        except TanyuanError as exc:
            failures.append((api, exc))
            warnings.append({"type": "source_error", "api": api, "error": exc.to_dict()})
            continue
        for row in rows:
            item = {
                "kind": api,
                "ts_code": row.get("ts_code"),
                "ann_date": _fmt_date(row.get("ann_date")),
                "first_ann_date": _fmt_date(row.get("first_ann_date")) if row.get("first_ann_date") else None,
                "end_date": _fmt_date(row.get("end_date")),
                "type": row.get("type"),
                "p_change_min": num(row.get("p_change_min")),
                "p_change_max": num(row.get("p_change_max")),
                "net_profit_min_wan": num(row.get("net_profit_min")),
                "net_profit_max_wan": num(row.get("net_profit_max")),
                "last_parent_net_wan": num(row.get("last_parent_net")),
                "summary": row.get("summary"),
                "change_reason": row.get("change_reason"),
                "update_flag": row.get("update_flag"),
            }
            if api == "express":
                item.update({
                    "revenue": num(row.get("revenue")),
                    "n_income": num(row.get("n_income")),
                    "yoy_sales": num(row.get("yoy_sales")),
                    "yoy_net_profit": num(row.get("yoy_net_profit")),
                })
            results.append(item)
    if not results:
        if failures:
            raise failures[0][1]
        raise no_data(
            f"{sym.ts_code if sym else '全市场'} 在给定窗口内没有业绩预告/快报"
            + (f"(ann_date={_fmt_date(ann_date)})" if ann_date else ""),
            hint="业绩预告是事件性披露,多数股票多数时间没有;换年度或换 kind=both。",
        )
    results.sort(key=lambda r: (r.get("ann_date") or ""), reverse=True)
    return Result(
        data={"kind": kind, "code": sym.ts_code if sym else None,
              "ann_date": _fmt_date(ann_date) if ann_date else None,
              "events": results[:limit], "total": len(results),
              "truncated": len(results) > limit, "complete": not failures,
              "failed_sources": [api for api, _ in failures]},
        rows=len(results[:limit]), source="tushare_earnings", grade="L1",
        source_url="https://api.tushare.pro",
        units={"forecast": {"net_profit_min_wan": "万元", "net_profit_max_wan": "万元",
                            "last_parent_net_wan": "万元", "p_change_min": "%", "p_change_max": "%"},
               "express": {"revenue": "元", "n_income": "元", "yoy_net_profit": "元", "yoy_sales": "%"}},
        warnings=warnings + [{"type": "historical_versions_unavailable",
                              "message": "公告日和报告期由上游提供;未保存修订历史,不能据此保证严格 PIT 回放"}],
        next=[{"tool": "filing.announcements", "why": "回一手公告 PDF 核验"},
              {"tool": "fundamental.financials", "why": "等正式财报出来后核对"}],
    )


@tool(
    name="market.flow",
    summary="个股资金流向(小/中/大/超大单买卖与净流入,日频)",
    covers=["A 股个股日频资金流(按单笔金额分档)", "净流入 net_mf_amount"],
    not_covers=["板块资金流", "北向资金", "逐笔成交"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "股票代码", "example": "600519"},
        "start_date": {"type": "string", "format": "date", "description": "默认 end-60 天"},
        "end_date": {"type": "string", "format": "date", "description": "默认今天"},
        "limit": {"type": "integer", "min": 1, "max": 120, "description": "默认 20"},
    },
    returns=[
        {"field": "series[].net_mf_amount_wan", "desc": "净流入(万元)"},
        {"field": "series[].buy_elg_amount_wan / sell_elg_amount_wan", "desc": "超大单买/卖(万元)"},
        {"field": "series[].buy_lg_amount_wan / sell_lg_amount_wan", "desc": "大单买/卖(万元)"},
    ],
    source="tushare_moneyflow", grade="L1", cache_ttl_s=600, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"code": "600519", "limit": 5},
               "cmd": "ty call market.flow --json '{\"code\":\"600519\",\"limit\":5}'"}],
    latency_hint_ms=400,
)
def market_flow(args: dict) -> Result:
    sym = resolve_symbol(args["code"])
    end = _norm_date(args.get("end_date") or date.today().isoformat(), name="end_date")
    start = _norm_date(args.get("start_date") or
                       (date.fromisoformat(_fmt_date(end)) - timedelta(days=60)).isoformat(), name="start_date")
    if start > end:
        raise bad_arg("start_date 不能晚于 end_date")
    limit = clamp(int(args.get("limit") or 20), 1, 120)
    rows = tushare.call("moneyflow",
                        {"ts_code": sym.ts_code, "start_date": start, "end_date": end},
                        "")
    if not rows:
        raise no_data(f"{sym.ts_code} 在区间内没有资金流数据", hint="确认代码/日期。")
    rows.sort(key=lambda r: str(r.get("trade_date") or ""), reverse=True)
    series = []
    for row in rows[:limit]:
        series.append({
            "trade_date": _fmt_date(row.get("trade_date")),
            "net_mf_amount_wan": num(row.get("net_mf_amount")),
            "net_mf_vol_hand": num(row.get("net_mf_vol")),
            "buy_sm_amount_wan": num(row.get("buy_sm_amount")),
            "sell_sm_amount_wan": num(row.get("sell_sm_amount")),
            "buy_md_amount_wan": num(row.get("buy_md_amount")),
            "sell_md_amount_wan": num(row.get("sell_md_amount")),
            "buy_lg_amount_wan": num(row.get("buy_lg_amount")),
            "sell_lg_amount_wan": num(row.get("sell_lg_amount")),
            "buy_elg_amount_wan": num(row.get("buy_elg_amount")),
            "sell_elg_amount_wan": num(row.get("sell_elg_amount")),
        })
    return Result(
        data={"code": sym.ts_code, "series": series},
        rows=len(series), source="tushare_moneyflow", grade="L1",
        source_url="https://api.tushare.pro", as_of=series[0]["trade_date"],
        units={"amount": "万元", "vol": "手"},
    )
