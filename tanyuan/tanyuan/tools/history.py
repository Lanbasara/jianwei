"""Daily-series tools backed by Tushare (the paid token earns its keep here).

Why Tushare for these: it returns adjusted-price inputs (`adj_factor`), PIT fields
(`ann_date`), and full-market snapshots that free sources either lack or make you page
through a scraped API for. Units differ from Tencent — declared in `units` every time.

Trap learned from the audit: Tushare rejects bare 6-digit codes. Always pass ts_code
(600519.SH), otherwise you silently get an empty result.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import tushare
from ..envelope import Result
from ..errors import bad_arg, no_data
from ..registry import tool
from ..util import clamp, now_iso, num, resolve_symbol

_FIELDS_DAILY = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
_FIELDS_BASIC = ("ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,"
                 "pe,pe_ttm,pb,ps_ttm,dv_ttm,total_share,float_share,total_mv,circ_mv")


def _r2(value):
    return None if value is None else round(value, 2)


def _fmt_date(raw) -> str:
    text = str(raw)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and text.isdigit() else text


def _parse_date_arg(value: str, *, name: str) -> str:
    from ..util import date_only
    try:
        return date_only(value).replace("-", "")
    except ValueError as exc:
        raise bad_arg(f"{name} 日期格式不对: {value!r}", hint="用 YYYY-MM-DD 或 YYYYMMDD") from exc


@tool(
    name="market.daily",
    summary="日线行情:单票区间(可选前/后复权)或某交易日全市场(按成交额降序)",
    covers=["沪深京 A 股日线(开高低收/成交量/成交额/涨跌幅)", "单票长区间", "某交易日全市场排序"],
    not_covers=["分钟线(用 market.kline)", "指数日线(用 market.quote + Tushare index_daily 未封装)",
                "B 股/港股/美股"],
    args_schema={
        "code": {"type": "string", "description": "单票模式:代码。与 date 二选一", "example": "600519"},
        "date": {"type": "string", "format": "date", "description": "全市场模式:交易日 YYYY-MM-DD"},
        "start_date": {"type": "string", "format": "date", "description": "单票模式起始日,默认 end-180 天"},
        "end_date": {"type": "string", "format": "date", "description": "单票模式结束日,默认今天"},
        "adjust": {"type": "string", "enum": ["none", "qfq", "hfq"], "description": "默认 none"},
        "limit": {"type": "integer", "min": 1, "max": 6000,
                  "description": "全市场模式返回前 N 条(按成交额降序),默认 100"},
    },
    returns=[
        {"field": "bars[].date/open/high/low/close", "desc": "价格(元);复权时已按 adjust 处理"},
        {"field": "bars[].volume", "desc": "成交量(手)"},
        {"field": "bars[].amount", "desc": "成交额(千元)"},
        {"field": "bars[].pct_chg", "desc": "涨跌幅(%)"},
    ],
    source="tushare_daily", grade="L1", cache_ttl_s=300, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"code": "600519", "start_date": "2026-09-01", "adjust": "qfq"},
               "cmd": "ty call market.daily --json '{\"code\":\"600519\",\"start_date\":\"2026-09-01\",\"adjust\":\"qfq\"}'"},
              {"args": {"date": "2026-10-23", "limit": 20},
               "cmd": "ty call market.daily --json '{\"date\":\"2026-10-23\",\"limit\":20}'"}],
    latency_hint_ms=400,
)
def market_daily(args: dict) -> Result:
    if args.get("date"):
        return _market_daily_all(args)
    if not args.get("code"):
        raise bad_arg("需要 code(单票模式)或 date(全市场模式)",
                      hint='单票: {"code":"600519","start_date":"2026-09-01"}；全市场: {"date":"2026-10-23"}')
    return _market_daily_one(args)


def _market_daily_all(args: dict) -> Result:
    trade_date = _parse_date_arg(args["date"], name="date")
    limit = clamp(int(args.get("limit") or 100), 1, 6000)
    rows = tushare.call("daily", {"trade_date": trade_date}, _FIELDS_DAILY)
    if not rows:
        raise no_data(f"{trade_date} 没有日线数据(非交易日或数据未更新)",
                      hint="换一个交易日;Tushare 日线在收盘后更新。")
    bars = []
    for row in rows:
        bars.append({
            "code": row.get("ts_code"),
            "date": _fmt_date(row.get("trade_date")),
            "open": num(row.get("open")), "high": num(row.get("high")),
            "low": num(row.get("low")), "close": num(row.get("close")),
            "pct_chg": num(row.get("pct_chg")),
            "volume": num(row.get("vol")), "amount": num(row.get("amount")),
        })
    bars.sort(key=lambda b: b["amount"] or 0, reverse=True)
    return Result(
        data={"mode": "market", "trade_date": _fmt_date(trade_date),
              "universe_size": len(bars), "bars": bars[:limit]},
        rows=min(limit, len(bars)), source="tushare_daily", grade="L1",
        source_url="https://api.tushare.pro",
        as_of=bars[0]["date"] if bars else None,
        units={"price": "CNY", "volume": "手", "amount": "千元"},
        warnings=[{"type": "sorted_by_amount",
                   "message": f"已按成交额降序取前 {limit} 条;全市场共 {len(bars)} 只"}],
    )


def _market_daily_one(args: dict) -> Result:
    sym = resolve_symbol(args["code"])
    end = _parse_date_arg(args.get("end_date") or date.today().isoformat(), name="end_date")
    start = _parse_date_arg(
        args.get("start_date") or (date.fromisoformat(_fmt_date(end)) - timedelta(days=180)).isoformat(),
        name="start_date",
    )
    if start > end:
        raise bad_arg("start_date 不能晚于 end_date")
    adjust = args.get("adjust") or "none"
    rows = tushare.call("daily", {"ts_code": sym.ts_code, "start_date": start, "end_date": end},
                        _FIELDS_DAILY)
    if not rows:
        raise no_data(
            f"{sym.ts_code} 在 {_fmt_date(start)}~{_fmt_date(end)} 没有日线",
            hint="确认代码是否正确/是否已上市/区间内是否有交易日;退市股可用 Tushare 历史数据。",
        )
    rows.sort(key=lambda r: str(r.get("trade_date") or ""))
    factor_by_date: dict[str, float] = {}
    if adjust != "none":
        factors = tushare.call("adj_factor",
                               {"ts_code": sym.ts_code, "start_date": start, "end_date": end},
                               "ts_code,trade_date,adj_factor")
        factor_by_date = {str(f.get("trade_date")): num(f.get("adj_factor"), 1.0) or 1.0
                          for f in factors}
        if not factor_by_date:
            raise no_data(f"{sym.ts_code} 没有复权因子,无法按 {adjust} 复权",
                          hint="改用 adjust=none,或换 market.kline(腾讯自带复权)。")
        base = factor_by_date[max(factor_by_date)]     # 以区间末因子为基准
    bars = []
    warnings = []
    for row in rows:
        raw_date = str(row.get("trade_date") or "")
        bar = {
            "date": _fmt_date(raw_date),
            "open": num(row.get("open")), "high": num(row.get("high")),
            "low": num(row.get("low")), "close": num(row.get("close")),
            "pct_chg": num(row.get("pct_chg")),
            "volume": num(row.get("vol")), "amount": num(row.get("amount")),
        }
        if adjust != "none":
            factor = factor_by_date.get(raw_date)
            if factor is None:
                warnings.append({"type": "missing_adj_factor", "date": bar["date"]})
                bar["adjusted"] = False
            else:
                ratio = factor / base if adjust == "qfq" else factor
                for key in ("open", "high", "low", "close"):
                    if bar[key] is not None:
                        bar[key] = round(bar[key] * ratio, 4)
                bar["adjusted"] = True
        bars.append(bar)
    return Result(
        data={"mode": "series", "code": sym.ts_code, "adjust": adjust,
              "query_window": [_fmt_date(start), _fmt_date(end)],
              "data_span": ({"latest": bars[-1]["date"], "earliest": bars[0]["date"]}
                            if bars else None),
              "bars": bars},
        rows=len(bars), source="tushare_daily", grade="L1",
        source_url="https://api.tushare.pro",
        as_of=bars[-1]["date"],
        units={"price": "CNY", "volume": "手", "amount": "千元"},
        warnings=([{"type": "adjust_base", "message": "qfq 以区间末因子为基准;跨区间比较请统一基准"}]
                  if adjust != "none" else []) + warnings[:5],
    )


@tool(
    name="fundamental.valuation_history",
    summary="个股估值时间序列:PE(TTM)/PE/PB/PS/股息率/换手/总市值/流通市值",
    covers=["A 股个股日频估值序列", "任意日期区间(默认近 1 年)"],
    not_covers=["指数估值(用 index_dailybasic)", "港股/美股", "行业估值"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "股票代码", "example": "600519"},
        "start_date": {"type": "string", "format": "date", "description": "默认 end-365 天"},
        "end_date": {"type": "string", "format": "date", "description": "默认今天"},
    },
    returns=[
        {"field": "series[].trade_date", "desc": "交易日"},
        {"field": "series[].pe_ttm / pb / ps_ttm / dv_ttm", "desc": "估值倍数与股息率(%)"},
        {"field": "series[].total_mv / circ_mv", "desc": "总市值/流通市值(万元)"},
        {"field": "series[].turnover_rate", "desc": "换手率(%)"},
    ],
    source="tushare_daily_basic", grade="L1", cache_ttl_s=3600, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"code": "600519", "start_date": "2026-01-01"},
               "cmd": "ty call fundamental.valuation_history --json '{\"code\":\"600519\",\"start_date\":\"2026-01-01\"}'"}],
    latency_hint_ms=400,
)
def fundamental_valuation_history(args: dict) -> Result:
    sym = resolve_symbol(args["code"])
    end = _parse_date_arg(args.get("end_date") or date.today().isoformat(), name="end_date")
    start = _parse_date_arg(
        args.get("start_date") or (date.fromisoformat(_fmt_date(end)) - timedelta(days=365)).isoformat(),
        name="start_date",
    )
    if start > end:
        raise bad_arg("start_date 不能晚于 end_date")
    rows = tushare.call("daily_basic",
                        {"ts_code": sym.ts_code, "start_date": start, "end_date": end},
                        _FIELDS_BASIC)
    if not rows:
        raise no_data(f"{sym.ts_code} 在区间内没有估值数据",
                      hint="确认代码/日期;停牌期可能无数据。")
    rows.sort(key=lambda r: str(r.get("trade_date") or ""))
    series = []
    for row in rows:
        series.append({
            "trade_date": _fmt_date(row.get("trade_date")),
            "close": num(row.get("close")),
            "pe": num(row.get("pe")),
            "pe_ttm": num(row.get("pe_ttm")),
            "pb": num(row.get("pb")),
            "ps_ttm": num(row.get("ps_ttm")),
            "dv_ttm": num(row.get("dv_ttm")),
            "turnover_rate": num(row.get("turnover_rate")),
            "volume_ratio": num(row.get("volume_ratio")),
            "total_mv_wan": num(row.get("total_mv")),
            "circ_mv_wan": num(row.get("circ_mv")),
        })
    return Result(
        data={"code": sym.ts_code, "query_window": [_fmt_date(start), _fmt_date(end)],
              "data_span": ({"latest": series[-1]["trade_date"], "earliest": series[0]["trade_date"]}
                            if series else None),
              "series": series},
        rows=len(series), source="tushare_daily_basic", grade="L1",
        source_url="https://api.tushare.pro",
        as_of=series[-1]["trade_date"],
        units={"pe": "倍", "dv": "%", "market_cap": "万元"},
        warnings=[{"type": "description_only",
                   "message": "只给序列,不下判断;分位数/估值高低由上层分析"}],
    )
