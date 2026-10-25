"""Capital-market flow: limit prices, margin financing, northbound flows.

These are the 'who is moving the stock' layer — mostly Tushare because the free
Eastmoney push2 endpoints are unreliable from many networks.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import tushare
from ..envelope import Result
from ..errors import bad_arg, no_data, unsupported
from ..registry import tool
from ..util import clamp, now_iso, num, resolve_symbol


def _fmt_date(raw) -> str:
    text = str(raw)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and text.isdigit() else text


def _norm_date(value: str | None, *, name: str, default: str) -> str:
    from ..util import date_only
    if not value:
        return default
    try:
        return date_only(value).replace("-", "")
    except ValueError as exc:
        raise bad_arg(f"{name} 日期格式不对: {value!r}", hint="用 YYYY-MM-DD 或 YYYYMMDD") from exc


@tool(
    name="market.limit",
    summary="涨跌停价(按交易日,可用于判断封板/跌停)",
    covers=["A 股个股某交易日的涨停价/跌停价"],
    not_covers=["涨停原因/连板梯队", "历史涨跌停统计(可循环查询)"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "股票代码", "example": "600519"},
        "date": {"type": "string", "format": "date", "description": "交易日 YYYY-MM-DD,默认最近交易日(取不到则报 NO_DATA)"},
    },
    returns=[{"field": "up_limit / down_limit", "desc": "涨停价/跌停价(元)"},
             {"field": "prev_close", "desc": "前收盘价(用于核对涨跌幅)"}],
    source="tushare_stk_limit", grade="L1", cache_ttl_s=3600, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"code": "600519", "date": "2026-10-23"},
               "cmd": "ty call market.limit --json '{\"code\":\"600519\",\"date\":\"2026-10-23\"}'"}],
    latency_hint_ms=400,
)
def market_limit(args: dict) -> Result:
    sym = resolve_symbol(args["code"])
    if args.get("date"):
        trade_date = _norm_date(args["date"], name="date", default="")
        rows = tushare.call("stk_limit", {"ts_code": sym.ts_code, "trade_date": trade_date},
                            "ts_code,trade_date,up_limit,down_limit")
        if not rows:
            raise no_data(f"{sym.ts_code} 在 {_fmt_date(trade_date)} 没有涨跌停数据",
                          hint="非交易日或数据未更新;换最近交易日。")
        row = rows[0]
        return Result(
            data={"code": sym.ts_code, "trade_date": _fmt_date(row.get("trade_date")),
                  "up_limit": num(row.get("up_limit")), "down_limit": num(row.get("down_limit"))},
            rows=1, source="tushare_stk_limit", grade="L1",
            source_url="https://api.tushare.pro", as_of=_fmt_date(row.get("trade_date")), units={"price": "CNY"},
        )
    # walk back up to 10 days to find the latest available trading day
    for offset in range(0, 11):
        candidate = (date.today() - timedelta(days=offset)).strftime("%Y%m%d")
        rows = tushare.call("stk_limit", {"ts_code": sym.ts_code, "trade_date": candidate},
                            "ts_code,trade_date,up_limit,down_limit")
        if rows:
            row = rows[0]
            return Result(
                data={"code": sym.ts_code, "trade_date": _fmt_date(row.get("trade_date")),
                      "up_limit": num(row.get("up_limit")), "down_limit": num(row.get("down_limit"))},
                rows=1, source="tushare_stk_limit", grade="L1",
                source_url="https://api.tushare.pro", as_of=_fmt_date(row.get("trade_date")), units={"price": "CNY"},
                warnings=[{"type": "auto_date", "message": f"未指定 date,取最近可用交易日 {candidate}"}],
            )
    raise no_data(f"{sym.ts_code} 近 10 天没有涨跌停数据", hint="指定 date,或确认是否停牌。")


@tool(
    name="market.margin",
    summary="融资融券明细(融资余额/买入/偿还/融券余量),日频",
    covers=["沪深两融标的的日频两融数据"],
    not_covers=["北交所两融", "转融通", "券商层面数据"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "股票代码", "example": "600519"},
        "start_date": {"type": "string", "format": "date", "description": "默认 end-30 天"},
        "end_date": {"type": "string", "format": "date", "description": "默认今天"},
        "limit": {"type": "integer", "min": 1, "max": 120, "description": "返回最近 N 条,默认 20"},
    },
    returns=[
        {"field": "series[].rzye_yi", "desc": "融资余额(亿元)"},
        {"field": "series[].rzmre_yi", "desc": "融资买入额(亿元)"},
        {"field": "series[].rzche_yi", "desc": "融资偿还额(亿元)"},
        {"field": "series[].rqyl", "desc": "融券余量(股)"},
    ],
    source="tushare_margin_detail", grade="L1", cache_ttl_s=1800, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"code": "600519", "limit": 5},
               "cmd": "ty call market.margin --json '{\"code\":\"600519\",\"limit\":5}'"}],
    latency_hint_ms=500,
)
def market_margin(args: dict) -> Result:
    sym = resolve_symbol(args["code"])
    end = _norm_date(args.get("end_date"), name="end_date", default=date.today().strftime("%Y%m%d"))
    start = _norm_date(args.get("start_date"), name="start_date",
                       default=(date.fromisoformat(_fmt_date(end)) - timedelta(days=30)).strftime("%Y%m%d"))
    if start > end:
        raise bad_arg("start_date 不能晚于 end_date")
    limit = clamp(int(args.get("limit") or 20), 1, 120)
    rows = tushare.call("margin_detail",
                        {"ts_code": sym.ts_code, "start_date": start, "end_date": end}, "")
    if not rows:
        raise no_data(f"{sym.ts_code} 在区间内没有两融数据",
                      hint="该股可能不是两融标的,或区间无数据。")
    rows.sort(key=lambda r: str(r.get("trade_date") or ""), reverse=True)
    series = []
    for row in rows[:limit]:
        series.append({
            "trade_date": _fmt_date(row.get("trade_date")),
            "rzye_yi": num(row["rzye"]) / 1e8 if num(row.get("rzye")) is not None else None,
            "rzmre_yi": num(row["rzmre"]) / 1e8 if num(row.get("rzmre")) is not None else None,
            "rzche_yi": num(row["rzche"]) / 1e8 if num(row.get("rzche")) is not None else None,
            "rqyl": num(row.get("rqyl")),
            "rqye_yi": num(row["rqye"]) / 1e8 if num(row.get("rqye")) is not None else None,
            "raw": dict(row),
        })
    return Result(
        data={"code": sym.ts_code, "series": series},
        rows=len(series), source="tushare_margin_detail", grade="L1",
        source_url="https://api.tushare.pro",
        as_of=series[0]["trade_date"],
        units={"amount": "亿元(已从元换算)", "vol": "股"},
    )


@tool(
    name="market.northbound",
    summary="沪深港通源数据:北向按披露时期区分净买入与成交额,南向原值口径未核验",
    covers=["北向历史净买入与后续成交额(分别返回)", "沪股通/深股通分项", "南向原始字段"],
    not_covers=["个股北向持股", "分时北向", "停止披露后的北向净买入", "南向净流入计算"],
    args_schema={
        "start_date": {"type": "string", "format": "date", "description": "默认 end-30 天"},
        "end_date": {"type": "string", "format": "date", "description": "默认今天"},
        "limit": {"type": "integer", "min": 1, "max": 120, "description": "默认 20"},
        "metric": {"type": "string", "enum": ["available", "net_buy", "turnover"],
                   "description": "默认 available:按各日期可用口径返回;指定指标时不自动替换成其他指标"},
    },
    returns=[
        {"field": "series[].north_net_buy / hgt_net_buy / sgt_net_buy", "desc": "披露调整前北向净买入(百万元人民币);其他时期为空"},
        {"field": "series[].north_turnover / hgt_turnover / sgt_turnover", "desc": "披露调整后北向成交额(百万元人民币);其他时期为空"},
        {"field": "series[].raw", "desc": "上游字段原值;南向字段的指标、单位和币种未核验"},
    ],
    source="tushare_moneyflow_hsgt", grade="L1", cache_ttl_s=1800, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE", "UNSUPPORTED"],
    examples=[{"args": {"limit": 5}, "cmd": "ty call market.northbound --json '{\"limit\":5}'"}],
    latency_hint_ms=400,
)
def market_northbound(args: dict) -> Result:
    end = _norm_date(args.get("end_date"), name="end_date", default=date.today().strftime("%Y%m%d"))
    start = _norm_date(args.get("start_date"), name="start_date",
                       default=(date.fromisoformat(_fmt_date(end)) - timedelta(days=30)).strftime("%Y%m%d"))
    if start > end:
        raise bad_arg("start_date 不能晚于 end_date")
    # HKEX CT/110/24: historical buy/sell dissemination changes from this date.
    # https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2024/CT11024E.pdf
    change_date = "20240819"
    metric = args.get("metric") or "available"
    if metric == "net_buy" and end >= change_date:
        raise unsupported("该区间包含停止披露北向净买入的时期",
                          hint="缩小到披露调整前区间,或使用 metric=available 查看可用指标;成交额不能替代净买入")
    if metric == "turnover" and start < change_date:
        raise unsupported("该来源在此区间的旧字段不提供北向成交额",
                          hint="缩小到披露调整后区间,或使用 metric=available;历史净买入不能替代成交额")
    limit = clamp(int(args.get("limit") or 20), 1, 120)
    rows = tushare.call("moneyflow_hsgt", {"start_date": start, "end_date": end}, "")
    if not rows:
        raise no_data("区间内没有沪深港通资金流数据", hint="换区间;2024 年后北向实时数据披露口径有变。")
    rows.sort(key=lambda r: str(r.get("trade_date") or ""), reverse=True)
    series = []
    for row in rows[:limit]:
        day = str(row.get("trade_date") or "")
        try:
            if len(day) != 8 or not day.isdigit():
                raise ValueError("missing trading date")
            date.fromisoformat(_fmt_date(day))
        except ValueError as exc:
            from ..errors import schema_changed
            raise schema_changed("沪深港通记录缺少有效交易日,无法确定适用口径") from exc
        current = day >= change_date
        item = {"trade_date": _fmt_date(day), "raw": dict(row)}
        for source_field, prefix in (("north_money", "north"), ("hgt", "hgt"), ("sgt", "sgt")):
            item[f"{prefix}_net_buy"] = None if current else num(row.get(source_field))
            item[f"{prefix}_turnover"] = num(row.get(source_field)) if current else None
        series.append(item)
    return Result(
        data={"series": series, "metric": metric,
              "query_window": [_fmt_date(start), _fmt_date(end)],
              "disclosure_change": {"effective_date": _fmt_date(change_date),
                                    "before": "北向净买入", "from": "北向成交额",
                                    "source_url": "https://www.hkex.com.hk/News/Market-Communications/2024/2404122news?sc_lang=en"}},
        rows=len(series), source="tushare_moneyflow_hsgt", grade="L1",
        source_url="https://api.tushare.pro",
        as_of=series[0]["trade_date"],
        units={**{f"{p}_{m}": "百万元人民币" for p in ("north", "hgt", "sgt")
                  for m in ("net_buy", "turnover")}, "raw.south_money": None,
               "raw.ggt_ss": None, "raw.ggt_sz": None},
        warnings=[{"type": "source_semantics", "message": "不同日期可用指标不同;空值表示该指标不可得或源值缺失,不得把成交额与净买入拼成同一序列"},
                  {"type": "unverified_semantics", "fields": ["raw.south_money", "raw.ggt_ss", "raw.ggt_sz"],
                   "message": "南向字段按源原样保留,其指标、单位和币种未核验;不能据此计算日净流入"}],
    )


@tool(
    name="market.limit_list",
    summary="某交易日涨停/跌停/炸板名单与家数(市场情绪)",
    covers=["全市场涨停名单、跌停名单、炸板名单", "连板统计 up_stat / limit_times"],
    not_covers=["涨停原因题材归因", "分时封板时间"],
    args_schema={
        "date": {"type": "string", "format": "date", "description": "交易日 YYYY-MM-DD,默认最近交易日"},
        "kind": {"type": "string", "enum": ["all", "up", "down", "broken"],
                 "description": "默认 all;up=涨停,d=跌停,broken=炸板"},
        "limit": {"type": "integer", "min": 1, "max": 500, "description": "每类返回条数,默认 50"},
    },
    returns=[
        {"field": "counts", "desc": "涨停/跌停/炸板家数"},
        {"field": "up[] / down[] / broken[]", "desc": "名单:代码/名称/收盘/涨跌幅/连板统计"},
    ],
    source="tushare_limit_list_d", grade="L1", cache_ttl_s=1800, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {}, "cmd": "ty call market.limit_list --json '{}'"}],
    latency_hint_ms=500,
)
def market_limit_list(args: dict) -> Result:
    limit = clamp(int(args.get("limit") or 50), 1, 500)
    kind = args.get("kind") or "all"
    if args.get("date"):
        trade_date = _norm_date(args["date"], name="date", default="")
    else:
        trade_date = ""
        for offset in range(0, 11):
            candidate = (date.today() - timedelta(days=offset)).strftime("%Y%m%d")
            probe = tushare.call("limit_list_d", {"trade_date": candidate, "limit_type": "U"},
                                 "ts_code")
            if probe:
                trade_date = candidate
                break
        if not trade_date:
            raise no_data("近 10 天没有涨跌停数据", hint="指定 date。")

    type_map = {"up": "U", "down": "D", "broken": "Z"}
    wanted = list(type_map.values()) if kind == "all" else [type_map[kind]]
    buckets: dict[str, list[dict]] = {"up": [], "down": [], "broken": []}
    counts: dict[str, int] = {"up": 0, "down": 0, "broken": 0}
    label = {"U": "up", "D": "down", "Z": "broken"}
    for limit_type in wanted:
        rows = tushare.call("limit_list_d",
                            {"trade_date": trade_date, "limit_type": limit_type},
                            "trade_date,ts_code,name,close,pct_chg,amount,limit_amount,"
                            "float_mv,total_mv,turnover_ratio,fd_amount,first_time,last_time,"
                            "open_times,up_stat,limit_times,limit")
        # counts must be the FULL day's totals; `limit` only caps the returned list.
        counts[label[limit_type]] = len(rows)
        for row in rows[:limit]:
            buckets[label[limit_type]].append({
                "ts_code": row.get("ts_code"),
                "name": row.get("name"),
                "close": num(row.get("close")),
                "pct_chg": num(row.get("pct_chg")),
                "amount_yuan": num(row.get("amount")),
                "up_stat": row.get("up_stat"),
                "limit_times": num(row.get("limit_times")),
                "first_time": row.get("first_time"),
                "open_times": num(row.get("open_times")),
            })
    if not any(buckets.values()):
        raise no_data(f"{_fmt_date(trade_date)} 没有涨跌停数据",
                      hint="非交易日或数据未更新;换交易日。")
    return Result(
        data={"trade_date": _fmt_date(trade_date), "counts": counts,
              "up": buckets["up"], "down": buckets["down"], "broken": buckets["broken"]},
        rows=sum(counts.values()), source="tushare_limit_list_d", grade="L1",
        source_url="https://api.tushare.pro", as_of=_fmt_date(trade_date),
        units={"amount": "元"},
        warnings=[{"type": "capped", "message": f"名单每类最多返回 {limit} 条;counts 是当日全量家数"}],
    )
