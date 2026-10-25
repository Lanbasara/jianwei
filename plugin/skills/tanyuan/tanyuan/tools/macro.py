"""Macro data: policy rates and official statistics.

Sources ranked by authority: PBoC/NBS/ChinaBond (L1 official) > Eastmoney mirror (L1) >
Tushare macro endpoints (L1, permission-gated). Every response names the exact source
because macro series get revised — the vintage matters.
"""
from __future__ import annotations

from datetime import datetime

from .. import http, tushare  # noqa: F401
from ..envelope import Result
from ..errors import bad_arg, no_data, schema_changed
from ..registry import tool
from ..util import clean_text, now_iso, num

DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"

# Tushare macro APIs -> how to read them
# NOTE: Tushare's macro endpoints reject an empty `fields` string with 50101, so every
# entry carries an explicit field list (cn_pmi was the one that broke in practice).
_TUSHARE_MACRO = {
    "cpi": {"api": "cn_cpi", "value": "nt_yoy", "period": "month", "unit": "同比 %",
            "fields": "month,nt_val,nt_yoy"},
    "ppi": {"api": "cn_ppi", "value": "ppi_yoy", "period": "month", "unit": "同比 %",
            "fields": "month,ppi_yoy,ppi_mp_yoy"},
    "pmi": {"api": "cn_pmi", "value": "pmi010000", "period": "month", "unit": "指数(50=荣枯线)",
            "fields": "month,pmi010000,pmi020100,pmi030000"},
    "gdp": {"api": "cn_gdp", "value": "gdp_yoy", "period": "quarter", "unit": "同比 %",
            "fields": "quarter,gdp,gdp_yoy"},
    "m2": {"api": "cn_m", "value": "m2_yoy", "period": "month", "unit": "同比 %",
           "fields": "month,m2,m2_yoy,m1_yoy"},
    "shibor": {"api": "shibor", "value": "1m", "period": "date", "unit": "%",
               "fields": "date,on,1w,1m,3m"},
}


@tool(
    name="macro.lpr",
    summary="贷款市场报价利率 LPR 全历史(1 年期 / 5 年期)",
    covers=["2013-10 至今 LPR 逐次报价", "1Y 与 5Y 两个品种"],
    not_covers=["贷款基准利率(旧机制,已并入但字段为空的行被剔除)", "MLF/OMO 操作利率"],
    args_schema={
        "limit": {"type": "integer", "min": 1, "max": 500, "description": "返回最近 N 次,默认 24"},
    },
    returns=[{"field": "items[].date / lpr_1y / lpr_5y", "desc": "报价日与两档利率(%)"}],
    source="eastmoney_datacenter", grade="L1", cache_ttl_s=86400, cost="free",
    errors=["NO_DATA", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED"],
    examples=[{"args": {"limit": 6}, "cmd": "ty call macro.lpr --json '{\"limit\":6}'"}],
    latency_hint_ms=700,
)
def macro_lpr(args: dict) -> Result:
    limit = int(args.get("limit") or 24)
    body = http.get_json(
        DATACENTER,
        params={
            "reportName": "RPTA_WEB_RATE",
            "columns": "TRADE_DATE,LPR1Y,LPR5Y",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "pageSize": str(limit),
            "pageNumber": "1",
            "source": "WEB",
            "client": "WEB",
        },
        headers={"Referer": "https://data.eastmoney.com/"},
        timeout=(8, 25),
    )
    if not isinstance(body, dict) or not body.get("success", True):
        raise schema_changed("东财 LPR 报表返回异常", evidence=str(body)[:300])
    records = ((body.get("result") or {}).get("data")) or []
    items = []
    for rec in records:
        lpr1y = num(rec.get("LPR1Y"))
        if lpr1y is None:
            continue
        items.append({
            "date": str(rec.get("TRADE_DATE") or "")[:10],
            "lpr_1y": lpr1y,
            "lpr_5y": num(rec.get("LPR5Y")),
        })
    if not items:
        raise no_data("东财 LPR 报表里没有可用数据")
    items.sort(key=lambda r: r["date"], reverse=True)
    return Result(
        data={"items": items},
        rows=len(items), source="eastmoney_datacenter", grade="L1",
        source_url=DATACENTER, as_of=items[0]["date"],
        units={"rate": "%"},
        warnings=[{"type": "mirror_source",
                   "message": "东财镜像自央行公告;用于正式引用前可回 PBoC 官网核对"}],
    )


@tool(
    name="macro.indicator",
    summary="中国宏观指标(CPI/PPI/PMI/GDP/M2/ShiBor),经 Tushare",
    covers=["月度 CPI/PPI/PMI/M2", "季度 GDP", "Shibor 期限利率"],
    not_covers=["美国/欧洲宏观(用 search.web 或 FRED)", "社融(需单独权限)"],
    args_schema={
        "name": {"type": "string", "enum": list(_TUSHARE_MACRO), "required": True,
                 "description": "指标名"},
        "limit": {"type": "integer", "min": 1, "max": 120, "description": "最近 N 期,默认 12"},
        "start": {"type": "string", "description": "起始期 YYYYMM 或 YYYY-MM-DD"},
        "end": {"type": "string", "description": "结束期"},
    },
    returns=[{"field": "items[]", "desc": "每期一行,含 period 与数值(字段名见 units)"}],
    source="tushare", grade="L1", cache_ttl_s=43200, cost="free",
    errors=["BAD_ARGUMENT", "AUTH_FAILED", "NO_DATA", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"name": "cpi", "limit": 6},
               "cmd": "ty call macro.indicator --json '{\"name\":\"cpi\",\"limit\":6}'"}],
    latency_hint_ms=900,
)
def macro_indicator(args: dict) -> Result:
    name = args["name"]
    spec = _TUSHARE_MACRO[name]
    limit = int(args.get("limit") or 12)
    params: dict = {}
    if args.get("start"):
        params["start_m"] = args["start"].replace("-", "")
    if args.get("end"):
        params["end_m"] = args["end"].replace("-", "")
    rows = tushare.call(spec["api"], params, spec.get("fields", ""))
    if not rows:
        raise no_data(f"Tushare {spec['api']} 返回 0 行",
                      hint="可能该指标无近期数据,或参数区间为空。")
    period_field = spec["period"]
    if period_field == "date":
        rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
    else:
        rows.sort(key=lambda r: str(r.get(period_field) or ""), reverse=True)
    rows = rows[:limit]
    items = []
    for row in rows:
        items.append({
            "period": row.get(period_field) or row.get("month") or row.get("date"),
            "value": num(row.get(spec["value"])),
            "raw": {k: v for k, v in row.items()
                    if k in (period_field, spec["value"], "nt_yoy", "pi_yoy")},
        })
    return Result(
        data={"indicator": name, "items": items},
        rows=len(items), source="tushare", grade="L1",
        source_url="https://api.tushare.pro", as_of=None,
        units={"value": spec["unit"]},
        warnings=[{"type": "revisions_possible",
                   "message": "宏观数据会修订;需要 PIT 回放时请记录抓取时间,并优先用官方 vintage 库"}],
    )


# ── ChinaBond yield curve (official central depository; Tushare yc_cb is not licensed) ──

CHINABOND_URL = "https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery"
_CB_CURVES = {"all": "ycqx", "treasury": "hzsylqx", "bank_aaa": "syyhsylqx", "mtn_aaa": "zdqpjsylqx"}
_CB_HEADER = ["曲线名称", "日期", "3月", "6月", "1年", "3年", "5年", "7年", "10年", "30年"]
_CB_TENORS = ["3m", "6m", "1y", "3y", "5y", "7y", "10y", "30y"]
_CB_NAMES = {"treasury": "中债国债收益率曲线", "bank_aaa": "中债商业银行普通债收益率曲线(AAA)",
             "mtn_aaa": "中债中短期票据收益率曲线(AAA)"}
_CB_FIRST = {"treasury": "2006-03-01", "mtn_aaa": "2006-12-25", "bank_aaa": "2009-12-24"}


@tool(
    name="macro.yield_curve",
    summary="中债收益率曲线(国债/商业银行 AAA/中短期票据 AAA),3 月至 30 年共 8 档",
    covers=["中债官方收益率曲线,日频", "国债收益率(含 10Y,宏观利率锚)"],
    not_covers=["信用利差计算", "美国国债(用 search.web 或 FRED)", "回购定盘利率"],
    args_schema={
        "curve": {"type": "string", "enum": ["treasury", "bank_aaa", "mtn_aaa"],
                  "description": "默认 treasury(国债)"},
        "start_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD;默认 end-30 天"},
        "end_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD;默认今天"},
    },
    returns=[
        {"field": "series[].date", "desc": "日期"},
        {"field": "series[].10y", "desc": "10 年期收益率(%)"},
        {"field": "series[].3m/6m/1y/3y/5y/7y/30y", "desc": "其余期限收益率(%)"},
    ],
    source="chinabond", grade="L1", cache_ttl_s=21600, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED"],
    examples=[{"args": {"curve": "treasury"},
               "cmd": "ty call macro.yield_curve --json '{\"curve\":\"treasury\"}'"}],
    latency_hint_ms=1500,
)
def macro_yield_curve(args: dict) -> Result:
    import re as _re
    from datetime import date as _date, datetime as _dt, timedelta as _td
    from ..errors import schema_changed

    curve = args.get("curve") or "treasury"
    end_dt = _dt.strptime(args.get("end_date") or _date.today().isoformat(), "%Y-%m-%d").date()
    start_dt = _dt.strptime(args.get("start_date") or (end_dt - _td(days=30)).isoformat(),
                            "%Y-%m-%d").date()
    begin = _dt.strptime(_CB_FIRST[curve], "%Y-%m-%d").date()
    if start_dt > end_dt:
        raise bad_arg("start_date 不能晚于 end_date")
    start_dt = max(start_dt, begin)
    if end_dt < begin:
        raise no_data(f"中债 {curve} 曲线从 {begin} 起才有数据")

    rows: list[dict] = []
    cursor = start_dt
    while cursor <= end_dt:
        stop = min(cursor + _td(days=358), end_dt)
        text = http.get_text(
            CHINABOND_URL,
            params={"startDate": cursor.isoformat(), "endDate": stop.isoformat(),
                    "gjqx": 0, "qxId": _CB_CURVES[curve], "locale": "cn_ZH"},
            headers={"Referer": "https://yield.chinabond.com.cn/"},
            timeout=(10, 75),
        )
        text = _re.sub(r"<!--.*?-->", "", text, flags=_re.S)
        tables = text.split("<table")
        table_rows = _re.findall(r"<tr[^>]*>(.*?)</tr>", tables[-1], _re.S) if len(tables) > 2 else []
        cells = [[_re.sub(r"<[^>]+>|\s+", "", c)
                  for c in _re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, _re.S)] for r in table_rows]
        if not cells or cells[0] != _CB_HEADER:
            raise schema_changed("中债收益率页面表头改变,不能按原列序解析",
                                 evidence=str(cells[:1])[:300])
        for rec in cells[1:]:
            if len(rec) != len(_CB_HEADER):
                raise schema_changed("中债收益率行列数不对", evidence=str(rec)[:200])
            day = rec[1]
            if not (cursor.isoformat() <= day <= stop.isoformat()):
                raise schema_changed("中债返回了请求区间外的日期", evidence=day)
            row = {"date": day, "curve_name": rec[0]}
            for tenor, value in zip(_CB_TENORS, rec[2:]):
                row[tenor] = num(value)
            rows.append(row)
        cursor = stop + _td(days=1)
    if not rows:
        raise no_data(f"{start_dt}~{end_dt} 没有中债收益率数据(区间内无交易日)",
                      hint="换交易日区间;中债按银行间交易日发布。")
    rows.sort(key=lambda r: r["date"])
    return Result(
        data={"curve": curve, "curve_name": _CB_NAMES[curve], "series": rows},
        rows=len(rows), source="chinabond", grade="L1",
        source_url=CHINABOND_URL, as_of=rows[-1]["date"],
        units={"yield": "%"},
        warnings=[{"type": "official_source",
                   "message": "中央结算公司官方曲线;银行间市场口径,与交易所国债收益率略有差异"}],
    )


# ── global rates (S-level official sources; see research/global-stock-data-audit.md) ──

_TREASURY_CSV = ("https://home.treasury.gov/resource-center/data-chart-center/"
                 "interest-rates/daily-treasury-rates.csv/{year}/all"
                 "?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
                 "&page&_format=csv")
_NYFED = "https://markets.newyorkfed.org/api/rates/{kind}/{rate}/last/{n}.json"
_NYFED_KINDS = {"sofr": "secured", "effr": "unsecured", "obfr": "unsecured",
                "tgcr": "secured", "bgcr": "secured"}
_BIS_POLICY = "https://stats.bis.org/api/v1/data/WS_CBPOL/{areas}/all"


def _treasury_yield_curve(limit: int) -> list[dict]:
    import csv
    import io
    from datetime import datetime as _dt
    year = _dt.now().year
    text = http.get_text(_TREASURY_CSV.format(year=year), timeout=(10, 60),
                         headers={"Accept": "text/csv"})
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        # early January: current year CSV may be empty, fall back to previous year
        text = http.get_text(_TREASURY_CSV.format(year=year - 1), timeout=(10, 60))
        rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise no_data("美国财政部收益率曲线 CSV 为空", hint="可能在年初数据未发布,稍后重试。")
    mapping = {"1 Mo": "1m", "2 Mo": "2m", "3 Mo": "3m", "4 Mo": "4m", "6 Mo": "6m",
               "1 Yr": "1y", "2 Yr": "2y", "3 Yr": "3y", "5 Yr": "5y", "7 Yr": "7y",
               "10 Yr": "10y", "20 Yr": "20y", "30 Yr": "30y"}
    items = []
    import datetime as _datetime
    for row in sorted(rows, key=lambda r: r.get("Date") or "", reverse=True)[:limit]:
        raw_date = row.get("Date") or ""
        try:                                   # Treasury CSV dates are US-style M/D/YYYY
            iso_date = _datetime.datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            iso_date = raw_date
        items.append({"date": iso_date,
                      **{short: num(row.get(full)) for full, short in mapping.items()}})
    return items


def _nyfed_rates(rate: str, limit: int) -> list[dict]:
    kind = _NYFED_KINDS.get(rate)
    if not kind:
        raise bad_arg(f"rate 只能是 {sorted(_NYFED_KINDS)}")
    body = http.get_json(_NYFED.format(kind=kind, rate=rate, n=limit), timeout=(8, 30))
    rows = (body or {}).get("refRates") or []
    if not rows:
        raise no_data(f"纽约联储 {rate.upper()} 没有数据", hint="SOFR 2018-04-02 起;换 rate。")
    return [{"date": r.get("effectiveDate"), "rate_pct": num(r.get("percentRate")),
             "volume_bn": num(r.get("volumeInBillions")),
             "target_from": num(r.get("targetRateFrom")), "target_to": num(r.get("targetRateTo"))}
            for r in sorted(rows, key=lambda r: r.get("effectiveDate") or "")]


def _bis_policy_rates(areas: list[str] | None, limit: int) -> list[dict]:
    import csv
    import io
    key = "D." + "+".join(a.upper() for a in (areas or []))
    text = http.get_text(_BIS_POLICY.format(areas=key),
                         params={"format": "csv", "lastNObservations": str(max(1, limit) + 10)},
                         timeout=(10, 60))
    if not text or "OBS_VALUE" not in text:
        raise schema_changed("BIS 返回不是预期的 CSV", evidence=text[:200])
    rows = list(csv.DictReader(io.StringIO(text)))
    by_area: dict[str, list[dict]] = {}
    for row in rows:
        rate = num(row.get("OBS_VALUE"))
        if rate is None:
            continue
        title = (row.get("TITLE") or "").split(" - ")
        by_area.setdefault(row.get("REF_AREA") or "??", []).append({
            "area": row.get("REF_AREA"),
            "name": title[1].strip() if len(title) > 2 else row.get("REF_AREA"),
            "date": row.get("TIME_PERIOD"),
            "rate_pct": rate,
        })
    items = []
    for area in sorted(by_area):
        items.extend(sorted(by_area[area], key=lambda r: r["date"] or "")[-limit:])
    if not items:
        raise no_data("BIS 政策利率没有解析出数据")
    return items


@tool(
    name="macro.global_rates",
    summary="全球利率:美债收益率曲线 / 纽约联储 SOFR·EFFR / BIS 央行政策利率(S 级官方源)",
    covers=["美国国债收益率曲线 1M–30Y(日频)", "SOFR/EFFR/OBFR/TGCR/BGCR(日频)",
            "BIS 全球 38+ 经济体央行政策利率"],
    not_covers=["实时汇率", "各国央行会议日历", "中国利率(用 macro.lpr / macro.yield_curve)"],
    args_schema={
        "kind": {"type": "string", "enum": ["yield_curve", "sofr", "policy_rates"],
                 "required": True, "description": "yield_curve / sofr / policy_rates"},
        "rate": {"type": "string", "enum": ["sofr", "effr", "obfr", "tgcr", "bgcr"],
                 "description": "kind=sofr 时用,默认 sofr"},
        "areas": {"type": "array[string]", "description": "kind=policy_rates 时的经济体代码,如 ['US','JP','XM']"},
        "limit": {"type": "integer", "min": 1, "max": 120, "description": "默认 20"},
    },
    returns=[
        {"field": "items[].date", "desc": "观测日期(务必带上,官方频率不同)"},
        {"field": "items[].10y/2y/…", "desc": "kind=yield_curve:各期限收益率(%)"},
        {"field": "items[].rate_pct", "desc": "kind=sofr/policy_rates:利率(%)"},
    ],
    source="us_treasury|nyfed|bis", grade="L1", cache_ttl_s=21600, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED"],
    examples=[{"args": {"kind": "yield_curve", "limit": 5},
               "cmd": "ty call macro.global_rates --json '{\"kind\":\"yield_curve\",\"limit\":5}'"},
              {"args": {"kind": "policy_rates", "areas": ["US", "JP", "XM"], "limit": 1},
               "cmd": "ty call macro.global_rates --json '{\"kind\":\"policy_rates\",\"areas\":[\"US\",\"JP\",\"XM\"]}'"}],
    latency_hint_ms=1800,
)
def macro_global_rates(args: dict) -> Result:
    kind = args.get("kind")
    limit = int(args.get("limit") or 20)
    if kind == "yield_curve":
        items = _treasury_yield_curve(limit)
        return Result(
            data={"kind": kind, "items": items}, rows=len(items),
            source="us_treasury", grade="L1",
            source_url=_TREASURY_CSV.format(year=datetime.now().year),
            as_of=max(i["date"] for i in items), units={"yield": "%"},
            warnings=[{"type": "citation", "message": "美国财政部公开数据(17 U.S.C. §105 不受版权保护);注明来源"},
                      {"type": "frequency", "message": "日频,下一工作日更新;10Y-2Y 利差需自行计算"}],
        )
    if kind == "sofr":
        rate = (args.get("rate") or "sofr").lower()
        items = _nyfed_rates(rate, limit)
        return Result(
            data={"kind": kind, "rate": rate, "items": items}, rows=len(items),
            source="nyfed", grade="L1",
            source_url=_NYFED.format(kind=_NYFED_KINDS[rate], rate=rate, n=limit),
            as_of=max(i["date"] for i in items), units={"rate": "%", "volume": "十亿美元"},
            warnings=[{"type": "citation",
                       "message": "对外展示请附纽约联储声明:数据受 newyorkfed.org 使用条款约束"}],
        )
    if kind == "policy_rates":
        areas = args.get("areas")
        items = _bis_policy_rates(areas, limit)
        return Result(
            data={"kind": kind, "items": items}, rows=len(items),
            source="bis", grade="L1", source_url=_BIS_POLICY.format(areas="D.*"),
            as_of=None, units={"rate": "%"},
            warnings=[{"type": "citation", "message": "BIS 数据转载须注明来源:BIS statistics"},
                      {"type": "lag", "message": "各国更新有滞后(几天到几周);用 date 判断,不要当今日值"}],
        )
    raise bad_arg("kind 只能是 yield_curve / sofr / policy_rates")
