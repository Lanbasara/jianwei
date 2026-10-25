"""Market data tools: realtime quotes and K-lines (Tencent).

Why Tencent first: no key, no IP ban policy for normal rates, sub-200ms, and the
response embeds PE/PB/market-cap so one call answers most "how is X doing" questions.
Known traps handled here: BSE legacy codes returning frozen quotes, 688/689 volume
being in shares while others are in lots, 000001 ambiguity.
"""
from __future__ import annotations

from .. import http
from ..envelope import Result
from ..errors import bad_arg, no_data, not_found, schema_changed, unavailable
from ..registry import tool
from ..util import (
    clamp, clean_text, date_context_hint,
    latest_weekday, looks_like_foreign_symbol, now_cn, now_iso, num, resolve_symbol,
)

QUOTE_URL = "https://qt.gtimg.cn/q={}"
KLINE_HOSTS = [
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com/ifzqgtimg",
    "https://ifzq.gtimg.cn",
]
_QUOTE_FIELDS = {
    "name": 1, "price": 3, "prev_close": 4, "open": 5, "volume": 6,
    "change_amt": 31, "change_pct": 32, "high": 33, "low": 34,
    "amount_wan": 37, "turnover_pct": 38, "pe_ttm": 39, "amplitude_pct": 43,
    "float_mcap_yi": 44, "mcap_yi": 45, "pb": 46, "limit_up": 47,
    "limit_down": 48, "vol_ratio": 49,
}
# Field 52 has no verified static-PE meaning; do not expose it as static PE.
_TIME_INDEX = 30


def fetch_quotes(codes: list[str]) -> dict:
    """Internal: batch realtime quotes. Returns (quotes_by_input, warnings, missing)."""
    symbols = []
    for raw in codes:
        try:
            sym = resolve_symbol(raw)
        except ValueError as exc:
            if looks_like_foreign_symbol(raw):
                from ..errors import unsupported
                raise unsupported(
                    f"{raw} 看起来是港股/美股代号,本工具只覆盖 A 股",
                    hint="港股/美股不在能力范围;如需此数据请扩展数据源,不要用 A 股代码近似。",
                )
            raise bad_arg(str(exc), hint="示例: 600519 / sh600519 / 600519.SH")
        symbols.append((raw, sym))
    if len(symbols) > 60:
        raise bad_arg("一次最多查 60 个代码", hint="分批调用,或先用 entity.resolve 筛选。")

    url = QUOTE_URL.format(",".join(sym.prefixed for _, sym in symbols))
    text = http.get_text(url, headers={"Referer": "https://gu.qq.com/"}, encoding="gbk")
    if not text or "=" not in text:
        raise schema_changed("腾讯行情返回为空或格式异常", evidence=text[:200])

    by_key: dict[str, dict] = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        raw_vals = line.split('"')[1].split("~")
        if len(raw_vals) < 53:
            continue
        quote = {}
        for name, idx in _QUOTE_FIELDS.items():
            if name == "name":
                quote[name] = clean_text(raw_vals[idx], 60)
            else:
                quote[name] = num(raw_vals[idx])
        parsed: dict = {}
        for k, v in quote.items():
            parsed[k] = v
        if len(raw_vals) > _TIME_INDEX and raw_vals[_TIME_INDEX]:
            parsed["quote_time"] = _parse_quote_time(raw_vals[_TIME_INDEX])
        # Sentinels the upstream uses that must not leak as real numbers:
        #   pe/pb == 0  -> not applicable (indices, loss-makers, newly listed)
        #   limit_up/down < 0 or non-stock -> no price limit
        for field in ("pe_ttm", "pb"):
            if parsed.get(field) == 0:
                parsed[field] = None
        if parsed.get("limit_up") is not None and parsed["limit_up"] < 0:
            parsed["limit_up"] = None
        if parsed.get("limit_down") is not None and parsed["limit_down"] < 0:
            parsed["limit_down"] = None
        price = parsed.get("price") or 0
        prev = parsed.get("prev_close") or 0
        amount = parsed.get("amount_wan") or 0
        parsed["is_stale"] = bool(amount == 0 and price and price == prev)
        if parsed["is_stale"]:
            parsed["stale_reason"] = (
                "成交量为 0(停牌/未开盘/废码),报价不是当日真实成交"
            )
        by_key[key] = parsed

    out: dict[str, dict] = {}
    warnings: list = []
    missing: list[dict] = []
    for raw, sym in symbols:
        if raw in out:
            continue
        quote = by_key.get(sym.prefixed)
        if quote is None:
            missing.append({"code": raw, "reason": "上游未返回该代码的行情"})
            continue
        item = dict(quote)
        item["code"] = sym.prefixed
        item["kind"] = sym.kind
        if sym.kind != "stock":
            item["limit_up"] = None
            item["limit_down"] = None
        if sym.digits.startswith(("688", "689")):
            item["volume_unit"] = "share"
        else:
            item["volume_unit"] = "hand"
        out[raw] = item
    return {"quotes": out, "missing": missing, "warnings": warnings}


def _parse_quote_time(raw: str) -> str | None:
    text = str(raw).strip()
    if len(text) == 14 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}T{text[8:10]}:{text[10:12]}:{text[12:14]}+08:00"
    return None


@tool(
    name="market.quote",
    summary="A 股个股/指数/ETF 实时行情(价格、涨跌幅、PE/PB、市值、换手、涨跌停价)",
    covers=["沪深京 A 股个股", "沪深指数(用 sh000001 等显式前缀)", "沪深 ETF(510300 等)"],
    not_covers=["港股/美股", "盘后逐笔成交", "历史序列(用 market.kline)",
                "静态市盈率(上游字段语义存疑,已不返回;估值请用 pe_ttm)"],
    args_schema={
        "codes": {"type": "array[string]", "min_items": 1, "max_items": 60, "description": "代码列表,1–60 个",
                  "example": '["600519","300750"]'},
        "code": {"type": "string", "min_length": 1, "description": "单个代码(与 codes 等价,二选一)",
                 "example": "600519"},
    },
    arg_groups=[{"keys": ["code", "codes"], "min": 1, "max": 1}],
    returns=[
        {"field": "quotes.<code>.price", "desc": "最新价(元)"},
        {"field": "quotes.<code>.change_pct", "desc": "涨跌幅(%)"},
        {"field": "quotes.<code>.pe_ttm", "desc": "滚动市盈率(倍),归母口径;null=不适用(亏损/指数/ETF)"},
        {"field": "quotes.<code>.pb", "desc": "市净率(倍);null=不适用"},
        {"field": "quotes.<code>.mcap_yi", "desc": "总市值(亿元);float_mcap_yi=流通市值(全流通时两者相等)"},
        {"field": "quotes.<code>.volume", "desc": "成交量;单位看 volume_unit(科创板=股,其余=手)"},
        {"field": "quotes.<code>.is_stale", "desc": "true=僵尸报价,不可当真实成交"},
        {"field": "quotes.<code>.quote_time", "desc": "上游报价时间(北京时间)"},
        {"field": "missing", "desc": "未取到的代码及原因"},
    ],
    source="tencent_qt", grade="L1", cache_ttl_s=15, cost="free",
    errors=["BAD_ARGUMENT", "NOT_FOUND", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED"],
    examples=[{"args": {"codes": ["600519"]},
               "cmd": "ty call market.quote --json '{\"codes\":[\"600519\"]}'"}],
    latency_hint_ms=200,
)
def market_quote(args: dict) -> Result:
    codes = args.get("codes") or ([args["code"]] if args.get("code") else None)
    if not codes:
        raise bad_arg("需要 codes(数组)或 code(单值)",
                      hint='例: {"codes":["600519"]} 或 {"code":"600519"}')
    payload = fetch_quotes(list(codes))
    if not payload["quotes"]:
        detail = "; ".join(f"{m['code']}:{m['reason']}" for m in payload["missing"][:5])
        raise not_found(
            f"所有代码都没有行情: {detail}",
            hint="确认代码是否存在、是否已退市;指数请用 sh000001 这类显式前缀。",
        )
    warnings = list(payload["warnings"])
    equal_cap = [code for code, quote in payload["quotes"].items()
                 if quote.get("mcap_yi") and quote.get("mcap_yi") == quote.get("float_mcap_yi")]
    if equal_cap and len(equal_cap) == len(payload["quotes"]):
        warnings.append({
            "type": "total_equals_float_cap",
            "message": "总市值与流通市值相等:通常表示全流通,也可能是上游未区分;需要精确股本请用 market.daily_basic 类数据",
        })
    stale = [code for code, quote in payload["quotes"].items() if quote.get("is_stale")]
    if stale:
        # Stale quotes are HTTP 200 with frozen prices (suspended/delisted/BSE-legacy codes).
        # Surface them at envelope level too: interface experiments showed per-field flags
        # inside `data` get ignored, while top-level warnings are at least visible.
        warnings.append({
            "type": "stale_quote",
            "message": f"{len(stale)} 个代码是僵尸/停牌报价(成交量 0),不是当日真实成交,不可用于估值",
            "codes": stale[:10],
        })
    if payload["missing"]:
        warnings.append({
            "type": "partial_result",
            "message": f"{len(payload['missing'])} 个代码没有取到",
            "detail": payload["missing"][:10],
        })
    # as_of must be the *data* time, not the fetch time: agents asking "今日/当日" get
    # confused when as_of says Saturday but the quote is Friday's close.
    quote_times = [q["quote_time"] for q in payload["quotes"].values() if q.get("quote_time")]
    trading_dates = sorted({t[:10] for t in quote_times})
    if len(trading_dates) > 1:
        warnings.append({"type": "mixed_trading_dates",
                         "message": f"这批代码的报价来自不同交易日: {trading_dates}"})
    return Result(
        data={"quotes": payload["quotes"], "missing": payload["missing"], "stale": stale,
              "trading_date": trading_dates[-1] if trading_dates else None,
              "quote_time": max(quote_times) if quote_times else None},
        rows=len(payload["quotes"]),
        source="tencent_qt",
        grade="L1",
        source_url=QUOTE_URL.format(",".join(payload["quotes"])),
        as_of=max(quote_times) if quote_times else None,
        warnings=warnings,
        units={"price": "CNY", "amount": "万元", "market_cap": "亿元",
               "pct": "%", "pe": "倍",
               "volume": "手(1 手 = 100 股);科创板 688/689 单位为股"},
        next=[{"tool": "market.kline", "why": "需要历史序列/复权价时"}],
    )


# ── K-line ───────────────────────────────────────────────────────────────────

_SPAN_DAYS = {"day": 700, "week": 3650, "month": 18250}
_MINUTE_PERIODS = ("m1", "m5", "m15", "m30", "m60")


def _kline_fetch(path: str, param: str) -> tuple[dict, str]:
    errors: list[str] = []
    for host in KLINE_HOSTS:
        try:
            body = http.get_json(
                host + path,
                params={"param": param},
                headers={"Referer": "https://gu.qq.com/"},
                timeout=(8, 25),
            )
        except Exception as exc:  # noqa: BLE001 - try next mirror
            errors.append(f"{host}: {type(exc).__name__}")
            continue
        if not isinstance(body, dict):
            errors.append(f"{host}: 顶层不是对象")
            continue
        if body.get("msg") == "param error":
            raise bad_arg(f"腾讯 K 线参数错误: {param}",
                          hint="区间过长/代码不存在/复权参数不支持都会报这个。")
        data = body.get("data")
        if isinstance(data, dict) and data:
            return data, host
        errors.append(f"{host}: 空响应")
    raise unavailable("腾讯 K 线所有入口均不可用(可能被限流)", evidence="; ".join(errors))


@tool(
    name="market.kline",
    summary="A 股日/周/月 K 线(前复权/后复权/不复权)与 1–60 分钟线",
    covers=["沪深 A 股个股与指数(日/周/月)", "1/5/15/30/60 分钟线(仅最近 ≤320 根,不复权)"],
    not_covers=["北交所", "成交额(本接口不返回,用 market.daily_all 或 Tushare daily 补)",
                "分钟线的复权数据"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "单只代码", "example": "600519"},
        "period": {"type": "string", "enum": ["day", "week", "month", "m1", "m5", "m15", "m30", "m60"],
                   "description": "周期,默认 day"},
        "adjust": {"type": "string", "enum": ["qfq", "hfq", "none"],
                   "description": "复权方式,默认 qfq;分钟线只能 none"},
        "count": {"type": "integer", "min": 1, "max": 640,
                  "description": "不传 start 时取最近 N 根,默认 120"},
        "start": {"type": "string", "format": "date", "description": "YYYY-MM-DD,仅日周月"},
        "end": {"type": "string", "format": "date", "description": "YYYY-MM-DD,仅日周月"},
    },
    returns=[
        {"field": "bars[].date", "desc": "交易日期 YYYY-MM-DD(分钟线为 datetime)"},
        {"field": "bars[].open/high/low/close", "desc": "价格(元),已按 adjust 处理"},
        {"field": "bars[].volume", "desc": "成交量;单位见 provenance.units.volume"},
        {"field": "adjust", "desc": "实际复权口径"},
    ],
    source="tencent_kline", grade="L1", cache_ttl_s=60, cost="free",
    errors=["BAD_ARGUMENT", "UNSUPPORTED", "NO_DATA", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED"],
    examples=[{"args": {"code": "600519", "period": "day", "count": 30},
               "cmd": "ty call market.kline --json '{\"code\":\"600519\",\"count\":30}'"}],
    latency_hint_ms=250,
)
def market_kline(args: dict) -> Result:
    sym = resolve_symbol(args["code"])
    if sym.market == "bj":
        from ..errors import unsupported
        raise unsupported(
            "腾讯 K 线不支持北交所(只返回最新 1 根/区间为空)",
            hint="北交所日线改用 Tushare daily,或按交易日取全市场日线。",
            alternatives=[{"tool": "market.quote", "why": "至少能取到实时快照"},
                          {"tool": "entity.resolve", "why": "确认真实现行代码(含北交所新旧号段)"}],
        )
    period = args.get("period", "day")
    adjust = args.get("adjust", "qfq")
    if period in _MINUTE_PERIODS:
        if adjust not in (None, "none"):
            raise bad_arg("分钟线只有不复权数据,adjust 只能是 none")
        if args.get("start") or args.get("end"):
            raise bad_arg("分钟线只能取最近 count 根,不支持 start/end")
        count = clamp(int(args.get("count") or 96), 1, 320)
        data, host = _kline_fetch("/appstock/app/kline/mkline", f"{sym.prefixed},{period},,{count}")
        node = data.get(sym.prefixed)
        if not isinstance(node, dict) or not isinstance(node.get(period), list):
            raise schema_changed(
                f"腾讯分钟线返回里没有 {period} 列表",
                evidence=str(data)[:300],
            )
        bars = []
        for item in node[period]:
            bars.append({
                "date": _fmt_minute(item[0]),
                "open": num(item[1]), "high": num(item[3]), "low": num(item[4]),
                "close": num(item[2]), "volume": num(item[5]),
            })
        if not bars:
            raise no_data(f"{sym.digits} {period} 返回 0 根", hint="非交易时段/停牌/代码无分钟数据。")
        return Result(
            data={"code": sym.prefixed, "period": period, "adjust": "none", "bars": bars},
            rows=len(bars), source="tencent_kline", grade="L1",
            source_url=f"{host}/appstock/app/kline/mkline?param={sym.prefixed},{period},,{count}",
            as_of=f"{bars[-1]['date']}+08:00",
            warnings=[{"type": "minute_only_recent", "message": f"分钟线只保留最近 {count} 根且不复权"}],
            units={"price": "CNY", "volume": "科创板为股,其余为手"},
        )

    if period not in _SPAN_DAYS:
        raise bad_arg(f"period 只能是 day/week/month 或 {'/'.join(_MINUTE_PERIODS)}")
    if adjust not in ("qfq", "hfq", "none"):
        raise bad_arg("adjust 只能是 qfq/hfq/none")
    if args.get("end") and not args.get("start"):
        raise bad_arg("只给 end 时需要同时给 start")

    count = clamp(int(args.get("count") or 120), 1, 640)
    if args.get("start"):
        start = args["start"]
        end = args.get("end") or now_cn().date().isoformat()
        param = f"{sym.prefixed},{period},{start},{end},640,{'' if adjust == 'none' else adjust}"
        windows = [(start, end)]
    else:
        param = f"{sym.prefixed},{period},,,{count},{'' if adjust == 'none' else adjust}"
        windows = []

    data, host = _kline_fetch("/appstock/app/fqkline/get", param)
    node = data.get(sym.prefixed)
    key = "" if adjust == "none" else adjust
    series_key = f"{key}{period}"
    if not isinstance(node, dict):
        raise schema_changed(f"腾讯 K 线返回里没有 {sym.prefixed}", evidence=str(data)[:300])
    items = node.get(series_key)
    if items is None:
        items = node.get(period)
    if not isinstance(items, list):
        raise schema_changed(
            f"腾讯 K 线返回里没有 {series_key}/{period} 列表",
            evidence=f"keys={list(node)[:10]}",
        )
    bars = []
    for item in items:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            raise schema_changed("腾讯 K 线行格式改变", evidence=str(item)[:200])
        bars.append({
            "date": _fmt_day(item[0]),
            "open": num(item[1]), "close": num(item[2]),
            "high": num(item[3]), "low": num(item[4]),
            "volume": num(item[5]),
        })
    bars.sort(key=lambda b: b["date"])
    warnings: list = []
    if args.get("start"):
        raw_start, raw_end = args["start"], args.get("end") or now_cn().date().isoformat()
        inside = [b for b in bars if raw_start <= b["date"] <= raw_end]
        outside = [b for b in bars if not (raw_start <= b["date"] <= raw_end)]
        if outside and not inside:
            # Tencent silently returns the previous trading day's bar for a non-trading
            # requested date. Returning that as-is is silent wrong data (interface exp P0-1).
            raise no_data(
                f"{sym.digits} 在 {raw_start}~{raw_end} 没有 K 线(请求区间无交易日)",
                hint=date_context_hint(raw_start)
                or f"该区间没有交易日;最近可用交易日约为 {latest_weekday(raw_start)}。",
            )
        if outside:
            warnings.append({"type": "out_of_range_dropped",
                             "message": f"丢弃 {len(outside)} 根落在请求区间外的 K 线",
                             "dates": [b["date"] for b in outside[:5]]})
        bars = inside
    if not bars:
        raise no_data(
            f"{sym.digits} {period} 在区间内 0 根",
            hint="未上市/停牌区间/代码有误;或该股是北交所(不支持)。",
        )
    if any(min(b["open"] or 0, b["close"] or 0, b["high"] or 0, b["low"] or 0) <= 0 for b in bars):
        raise schema_changed(
            "复权价出现 ≤0(等差复权副作用),结果不可用",
            evidence=f"code={sym.digits} adjust={adjust}",
        )
    if sym.digits.startswith(("688", "689")):
        volume_unit = "share"
    else:
        volume_unit = "hand"
    return Result(
        data={"code": sym.prefixed, "period": period, "adjust": adjust, "bars": bars},
        rows=len(bars), source="tencent_kline", grade="L1",
        source_url=f"{host}/appstock/app/fqkline/get?param={param}",
        as_of=bars[-1]["date"], warnings=warnings,
        units={"price": "CNY", "volume": volume_unit},
    )


def _fmt_day(raw) -> str:
    text = str(raw)
    if len(text) >= 10:
        return text[:10]
    return text


def _fmt_minute(raw) -> str:
    text = str(raw)
    if len(text) == 12 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}"
    return text
