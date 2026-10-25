"""Fundamentals: sell-side research reports, financial statements, valuation history.

The reports tool returns metadata + PDF links from Eastmoney's report library (free,
covers domestic brokers). The financials tool uses Sina's statement API; Tushare is used
when the token has permission. Numbers are returned verbatim (strings preserved where
precision matters) and units are declared in provenance.
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import config, http, tushare
from ..envelope import Result
from ..errors import TanyuanError, bad_arg, no_data, schema_changed
from ..registry import tool
from ..util import clean_text, num, resolve_symbol

REPORT_API = "https://reportapi.eastmoney.com/report/list"
PDF_TPL = "https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
SINA_FIN = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"


# ── research reports ────────────────────────────────────────────────────────

@tool(
    name="fundamental.reports",
    summary="个股/行业卖方研报列表(标题、机构、评级、EPS 预测、PDF 直链)",
    covers=["A 股个股研报(境内卖方)", "按日期区间/页码翻取", "评级与一致预期 EPS/PE 字段"],
    not_covers=["研报正文内容(只有 PDF 链接)", "外资券商英文研报", "港股/美股研报"],
    args_schema={
        "code": {"type": "string", "description": "6 位代码;查行业研报时省略", "example": "600519"},
        "industry_code": {"type": "string", "description": "东财行业码;或与 code 二选一"},
        "kind": {"type": "string", "enum": ["stock", "industry"], "description": "默认 stock"},
        "start_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD,默认近 180 天"},
        "end_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD,默认今天"},
        "page_size": {"type": "integer", "min": 1, "max": 100, "description": "默认 20"},
        "page": {"type": "integer", "min": 1, "max": 50, "description": "页码,默认 1"},
    },
    returns=[
        {"field": "reports[].title / org / author", "desc": "标题、机构、研究员"},
        {"field": "reports[].published_date", "desc": "发布日期"},
        {"field": "reports[].rating", "desc": "评级(买入/增持…)"},
        {"field": "reports[].eps_forecast", "desc": "未来三年 EPS 预测(元/股)"},
        {"field": "reports[].pdf_url", "desc": "研报 PDF 直链(版权受限,仅内部研究引用)"},
    ],
    source="eastmoney_reportapi", grade="L2", cache_ttl_s=3600, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "SOURCE_UNAVAILABLE", "RATE_LIMITED"],
    examples=[{"args": {"code": "600519", "page_size": 5},
               "cmd": "ty call fundamental.reports --json '{\"code\":\"600519\",\"page_size\":5}'"}],
    latency_hint_ms=800,
)
def fundamental_reports(args: dict) -> Result:
    kind = args.get("kind") or "stock"
    end = args.get("end_date") or date.today().isoformat()
    start = args.get("start_date") or (date.fromisoformat(end) - timedelta(days=180)).isoformat()
    page = int(args.get("page") or 1)
    page_size = int(args.get("page_size") or 20)
    params = {
        "industryCode": "*",
        "pageSize": str(page_size),
        "industry": "*",
        "rating": "*",
        "ratingChange": "*",
        "beginTime": start,
        "endTime": end,
        "pageNo": str(page),
        "fields": "",
        "qType": "0" if kind == "stock" else "1",
        "orgCode": "",
        "code": "",
        "rcode": "",
        "p": str(page),
        "pageNum": str(page),
        "pageNumber": str(page),
    }
    if kind == "stock":
        if not args.get("code"):
            raise bad_arg("kind=stock 时必须给 code", hint="查行业研报请用 kind=industry + industry_code")
        sym = resolve_symbol(args["code"])
        params["code"] = sym.digits
    else:
        params["industryCode"] = args.get("industry_code") or "*"

    body = http.get_json(REPORT_API, params=params,
                         headers={"Referer": "https://data.eastmoney.com/"}, timeout=(8, 30))
    if not isinstance(body, dict):
        raise schema_changed("东财研报接口返回不是对象", evidence=str(body)[:300])
    records = body.get("data") or []
    if not records:
        raise no_data(
            f"没有命中研报(区间 {start}~{end})",
            hint="放宽日期区间;该股可能没有卖方覆盖;行业研报请传 kind=industry。",
        )
    reports = []
    for rec in records:
        info_code = rec.get("infoCode") or ""
        reports.append({
            "title": clean_text(rec.get("title"), 200),
            "org": rec.get("orgSName") or rec.get("orgName"),
            "author": rec.get("researcher"),
            "published_date": (rec.get("publishDate") or "")[:10],
            "rating": rec.get("emRatingName"),
            "rating_change": rec.get("ratingChange"),
            "industry": rec.get("indvInduName") or rec.get("industryName"),
            "eps_forecast": {
                "this_year": num(rec.get("predictThisYearEps")),
                "next_year": num(rec.get("predictNextYearEps")),
                "next_two_year": num(rec.get("predictNextTwoYearEps")),
            },
            "pe_forecast": {
                "this_year": num(rec.get("predictThisYearPe")),
                "next_year": num(rec.get("predictNextYearPe")),
            },
            "target_price": num(rec.get("indvAimPriceT")),
            "info_code": info_code,
            "pdf_url": PDF_TPL.format(info_code=info_code) if info_code else None,
        })
    report_dates = [r["published_date"] for r in reports if r.get("published_date")]
    return Result(
        data={"kind": kind, "query_window": [start, end], "page": page,
              "data_span": ({"latest": max(report_dates), "earliest": min(report_dates)}
                            if report_dates else None),
              "total": body.get("hits"), "reports": reports},
        rows=len(reports), source="eastmoney_reportapi", grade="L2",
        source_url=REPORT_API, as_of=None,
        warnings=[{"type": "copyright", "message": "研报 PDF 仅限内部研究引用,不得再分发"}],
        units={"price": "CNY", "eps": "元/股"},
        next=[{"tool": "entity.resolve", "why": "确认公司行业归属"}],
    )


# ── financial statements ────────────────────────────────────────────────────

_SINA_TYPES = {"income": "lrb", "balance": "fzb", "cashflow": "llb"}


@tool(
    name="fundamental.financials",
    summary="A 股财报三表(利润表/资产负债表/现金流量表),按报告期倒序",
    covers=["A 股(沪深)财报三表最近 N 期", "报告期口径(period=报告期末,非披露日)"],
    not_covers=["北交所部分标的", "港股/美股", "公告披露日(见 filing.announcements)"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "6 位代码", "example": "600519"},
        "statement": {"type": "string", "enum": list(_SINA_TYPES),
                      "description": "income/balance/cashflow,默认 income"},
        "periods": {"type": "integer", "min": 1, "max": 12,
                    "description": "最近几期,默认 4"},
        "provider": {"type": "string", "enum": ["sina", "tushare"], "description": "默认 sina"},
    },
    returns=[
        {"field": "periods[].report_period", "desc": "报告期末 YYYY-MM-DD(不是披露日)"},
        {"field": "periods[].items", "desc": "科目字典:源原始值(字符串),未做单位换算"},
        {"field": "periods[].yoy_ratio", "desc": "同比比例(0.013 = 1.3%);×100 得百分比"},
    ],
    source="sina_finance", grade="L1", cache_ttl_s=86400, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED", "AUTH_FAILED"],
    examples=[{"args": {"code": "600519", "statement": "income", "periods": 2},
               "cmd": "ty call fundamental.financials --json '{\"code\":\"600519\",\"statement\":\"income\"}'"}],
    latency_hint_ms=900,
)
def fundamental_financials(args: dict) -> Result:
    sym = resolve_symbol(args["code"])
    statement = args.get("statement") or "income"
    periods = int(args.get("periods") or 4)
    provider = args.get("provider") or "sina"

    if provider == "tushare":
        return _financials_tushare(sym, statement, periods)
    return _financials_sina(sym, statement, periods)


def _financials_sina(sym, statement: str, periods: int) -> Result:
    params = {
        "paperCode": sym.prefixed,
        "source": _SINA_TYPES[statement],
        "type": "0",
        "page": "1",
        "num": str(periods),
    }
    body = http.get_json(SINA_FIN, params=params, timeout=(8, 30))
    try:
        report_list = body["result"]["data"]["report_list"]
    except (KeyError, TypeError) as exc:
        raise schema_changed("新浪财报返回结构改变", evidence=str(body)[:300]) from exc
    if not report_list:
        raise no_data(
            f"{sym.digits} 没有新浪财报数据",
            hint="确认代码是否沪深股票;北交所部分标的无此接口数据,可用 provider=tushare。",
        )
    rows = []
    for period_key in sorted(report_list, reverse=True)[:periods]:
        obj = report_list[period_key] or {}
        items: dict = {}
        tongbi: dict = {}
        for item in obj.get("data") or []:
            title = item.get("item_title")
            value = item.get("item_value")
            if not title or value is None:
                continue
            # 原样返回源值(字符串),不做单位换算、不做轮转 —— 数据层只报"值是什么",
            # 不替读者决定"该怎么写"。写法见 references/number-style.md。
            items[title] = value
            ratio = item.get("item_tongbi")
            if ratio not in (None, ""):
                tongbi[title] = ratio          # 源语义是"比例"(0.013 = 1.3%),字段名如实写 ratio
        rows.append({
            "report_period": f"{period_key[:4]}-{period_key[4:6]}-{period_key[6:8]}",
            "items": items,
            "yoy_ratio": tongbi,
        })
    return Result(
        data={"code": sym.prefixed, "statement": statement, "periods": rows},
        rows=len(rows), source="sina_finance", grade="L1",
        source_url=SINA_FIN,
        as_of=None,
        warnings=[{"type": "report_period_not_filing_date",
                   "message": "报告期为期末日期;此源未提供披露日和历史版本,不能保证严格 PIT。公告时间可另查 filing.announcements"}],
        units={"currency": "CNY",
               "note": "原始值未做单位换算;单位与呈现规范见 references/number-style.md"},
    )


def _financials_tushare(sym, statement: str, periods: int) -> Result:
    api = {"income": "income", "balance": "balancesheet", "cashflow": "cashflow"}[statement]
    rows = tushare.call(api, {"ts_code": sym.ts_code}, "")
    if not rows:
        raise no_data(f"Tushare {api} 没有 {sym.ts_code} 的数据")
    rows = sorted(rows, key=lambda r: r.get("end_date") or "", reverse=True)
    seen: set = set()
    deduped = []
    for row in rows:
        key = row.get("end_date")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    rows = deduped[:periods]
    return Result(
        data={"code": sym.prefixed, "statement": statement, "periods": rows},
        rows=len(rows), source="tushare", grade="L1",
        source_url="https://api.tushare.pro", as_of=None,
        units={"currency": "CNY"},
    )


# ── dividends ────────────────────────────────────────────────────────────────

@tool(
    name="fundamental.dividend",
    summary="分红送转历史(每股派息/送股/转增,含预案→实施进度与除权除息日)",
    covers=["A 股分红送转全历史与进度", "每股派息(税前)、送股/转增比例、除权除息日"],
    not_covers=["股息率(用 fundamental.valuation_history 的 dv_ttm)", "红利税后金额", "港股/美股分红"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "股票代码", "example": "600519"},
        "start_date": {"type": "string", "format": "date", "description": "按公告日过滤起始,可选"},
        "end_date": {"type": "string", "format": "date", "description": "按公告日过滤结束,可选"},
        "limit": {"type": "integer", "min": 1, "max": 100, "description": "默认 20"},
    },
    returns=[
        {"field": "dividends[].end_date", "desc": "分红对应报告期"},
        {"field": "dividends[].ann_date", "desc": "公告日(PIT 用)"},
        {"field": "dividends[].div_proc", "desc": "进度:预案/股东大会通过/实施"},
        {"field": "dividends[].cash_div_tax", "desc": "每股派息(元/股,税前)"},
        {"field": "dividends[].stk_div", "desc": "每股送转(股/股)"},
        {"field": "dividends[].ex_date", "desc": "除权除息日(复权计算的关键日期)"},
    ],
    source="tushare_dividend", grade="L1", cache_ttl_s=86400, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"code": "600519", "limit": 5},
               "cmd": "ty call fundamental.dividend --json '{\"code\":\"600519\",\"limit\":5}'"}],
    latency_hint_ms=400,
)
def fundamental_dividend(args: dict) -> Result:
    sym = resolve_symbol(args["code"])
    limit = int(args.get("limit") or 20)
    params: dict = {"ts_code": sym.ts_code}
    if args.get("start_date"):
        params["start_date"] = args["start_date"].replace("-", "")
    if args.get("end_date"):
        params["end_date"] = args["end_date"].replace("-", "")
    rows = tushare.call(
        "dividend", params,
        "ts_code,end_date,ann_date,div_proc,stk_div,cash_div_tax,record_date,ex_date,pay_date,base_date,base_share",
    )
    if not rows:
        raise no_data(f"{sym.ts_code} 没有分红送转记录",
                      hint="该公司可能从未分红;确认代码。")
    rows.sort(key=lambda r: (str(r.get("ann_date") or ""), str(r.get("end_date") or "")),
              reverse=True)
    items = []
    for row in rows[:limit]:
        items.append({
            "end_date": _fmt8(row.get("end_date")),
            "ann_date": _fmt8(row.get("ann_date")),
            "div_proc": row.get("div_proc"),
            "cash_div_tax_yuan_per_share": num(row.get("cash_div_tax")),
            "stk_div_share_per_share": num(row.get("stk_div")),
            "record_date": _fmt8(row.get("record_date")),
            "ex_date": _fmt8(row.get("ex_date")),
            "pay_date": _fmt8(row.get("pay_date")),
        })
    return Result(
        data={"code": sym.ts_code, "dividends": items},
        rows=len(items), source="tushare_dividend", grade="L1",
        source_url="https://api.tushare.pro", as_of=None,
        units={"cash_div": "元/股(税前)", "stk_div": "股/股"},
        warnings=[{"type": "progress_matters",
                   "message": "同一报告期会有多行不同 div_proc(预案/通过/实施),引用时指明进度"}],
    )


def _fmt8(raw) -> str | None:
    text = str(raw) if raw else ""
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text[:10] if text else None
