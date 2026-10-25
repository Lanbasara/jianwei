"""Industry / sector ranking.

Eastmoney's push2 board API is TCP-blocked from many networks (verified 2026-10-24:
connection established then reset, all hosts). Tushare's 申万行业日行情 (`sw_daily`) is
the more reliable L1 substitute and is also a cleaner concept: an actual index with
close/pct_change/PE/PB rather than a scraped board list.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import cache, tushare
from ..envelope import Result
from ..errors import bad_arg, no_data
from ..registry import tool
from ..util import now_cn, now_iso, num

_LATEST_KEY = "latest_trade_date"
_LATEST_TTL = 3600


_SW_KEY = "sw_classify_v1"
_SW_TTL = 7 * 24 * 3600


def _sw_levels() -> dict[str, str]:
    """index_code -> L1/L2/L3 map (Tushare index_classify, cached 7 days)."""
    cached, _ = cache.get(_SW_KEY)
    if cached and cached.get("data", {}).get("map"):
        return cached["data"]["map"]
    mapping: dict[str, str] = {}
    for level in ("L1", "L2", "L3"):
        try:
            rows = tushare.call("index_classify",
                                {"src": "SW2021", "level": level},
                                "index_code,industry_name,level")
        except Exception:  # noqa: BLE001 - degrade to advisory filtering
            continue
        for row in rows:
            code = str(row.get("index_code") or "")
            if code:
                mapping[code] = str(row.get("level") or level)
    cache.put(_SW_KEY, {"ok": True, "data": {"map": mapping}}, _SW_TTL)
    return mapping


def latest_trade_date() -> str:
    cached, _ = cache.get(_LATEST_KEY)
    if cached and cached.get("data", {}).get("date"):
        return cached["data"]["date"]
    today = now_cn().date()
    start = today - timedelta(days=20)
    rows = tushare.call(
        "trade_cal",
        {"exchange": "SSE", "start_date": start.strftime("%Y%m%d"),
         "end_date": today.strftime("%Y%m%d")},
        "cal_date,is_open",
    )
    open_days = [str(r["cal_date"]) for r in rows if str(r.get("is_open")) == "1"]
    if not open_days:
        raise no_data("交易日历里找不到近期开市日", hint="检查 Tushare 权限/网络。")
    latest = max(open_days)
    formatted = f"{latest[:4]}-{latest[4:6]}-{latest[6:8]}"
    cache.put(_LATEST_KEY, {"ok": True, "data": {"date": formatted}}, _LATEST_TTL)
    return formatted


@tool(
    name="industry.ranking",
    summary="申万行业/风格指数当日涨跌排名(收盘、涨跌幅、成交额、PE/PB)",
    covers=["申万一级/二级/三级行业指数及风格指数", "指定交易日或最近交易日"],
    not_covers=["东财概念板块(口径不同)", "个股所属板块(用 entity.resolve 的 industry 字段)",
                "盘中实时(sw_daily 为日频收盘数据)"],
    args_schema={
        "date": {"type": "string", "format": "date", "description": "YYYYMMDD 或 YYYY-MM-DD;默认最近交易日"},
        "top_n": {"type": "integer", "min": 1, "max": 100, "description": "默认 10"},
        "level": {"type": "string", "enum": ["all", "l1", "l2", "l3"],
                  "description": "申万层级:l1=31 个一级行业(默认),l3=细分行业,all=全部"},
    },
    returns=[
        {"field": "top[].total_mv / bottom[].total_mv", "desc": "总市值原值(万元)", "unit": "万元"},
        {"field": "top[].name / pct_change", "desc": "板块名与涨跌幅(%)"},
        {"field": "top[].close / amount_wan", "desc": "指数点位与成交额(万元)"},
        {"field": "top[].pe / pb", "desc": "板块估值"},
        {"field": "bottom[]", "desc": "跌幅最大的板块(最差在前;total<=top_n 时为空)"},
    ],
    source="tushare_sw_daily", grade="L1", cache_ttl_s=300, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"top_n": 5},
               "cmd": "ty call industry.ranking --json '{\"top_n\":5}'"}],
    latency_hint_ms=900,
)
def industry_ranking(args: dict) -> Result:
    from ..util import date_only
    top_n = int(args.get("top_n") or 10)
    if args.get("date"):
        raw = date_only(args["date"]).replace("-", "")
        trade_date = raw
    else:
        trade_date = latest_trade_date().replace("-", "")
    rows = tushare.call(
        "sw_daily",
        {"trade_date": trade_date},
        "ts_code,name,close,pct_change,vol,amount,pe,pb,total_mv",
    )
    if not rows:
        raise no_data(
            f"{trade_date} 没有申万行业行情(非交易日或数据未更新)",
            hint="换一个交易日;或先确认当日是否开市。",
        )
    level = args.get("level") or "l1"
    levels = _sw_levels()
    if level != "all" and levels:
        wanted = level.upper()
        rows = [r for r in rows if levels.get(str(r.get("ts_code")), "").upper() == wanted]
        if not rows:
            raise no_data(f"按 {level} 过滤后没有申万行业数据", hint="换 level=all 或检查 date")
    for row in rows:
        row["level"] = levels.get(str(row.get("ts_code")), None)
        row["pct_change"] = num(row.get("pct_change"))
    rows.sort(key=lambda r: (r.get("pct_change") is None, -(r.get("pct_change") or 0)))
    items = [{
        "ts_code": r.get("ts_code"),
        "name": r.get("name"),
        "level": r.get("level"),
        "close": num(r.get("close")),
        "pct_change": r.get("pct_change"),
        "amount_wan": num(r.get("amount")),
        "pe": num(r.get("pe")),
        "pb": num(r.get("pb")),
        "total_mv": num(r.get("total_mv")),
    } for r in rows]
    return Result(
        data={"trade_date": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
              "universe": "申万行业/风格指数",
              "top": items[:top_n],
              "bottom": list(reversed(items[-top_n:])) if len(items) > top_n else [],
              "total": len(items)},
        rows=len(items), source="tushare_sw_daily", grade="L1",
        source_url="https://api.tushare.pro",
        as_of=f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
        units={"pct_change": "%", "amount_wan": "万元", "total_mv": "万元"},
        next=[{"tool": "fundamental.reports", "why": "看该行业的卖方观点"}],
    )
