"""Fund research tools. Business semantics stay here; common infrastructure is reused."""
from __future__ import annotations

import json
import math
import re

from .. import fund_data as data, fund_sources as free, fund_metrics as metrics
from ..envelope import Result
from ..errors import Code, TanyuanError, bad_arg, schema_changed, unsupported
from ..registry import tool

CODE = {"type": "string", "required": True, "min_length": 1, "max_length": 160,
        "description": "基金名称/六位代码/经确认的 .OF/.SH/.SZ 代码；有歧义先 fund.resolve"}
DATES = {"start_date": {"type": "string", "format": "date", "description": "区间起日，默认 end_date 前365天；单次最多20年"},
         "end_date": {"type": "string", "format": "date", "description": "区间止日，默认上海时区今天"}}
SOURCE = {"type": "string", "enum": ["auto", "tushare", "eastmoney"],
          "description": "auto 优先 Tushare，失败/空结果时免费补取；来源切换显式披露"}
REFRESH = {"type": "boolean", "description": "默认false；true绕过并更新本工具响应缓存，重试部分缺口"}
COMMON = {"source": "fund_runtime_sources", "grade": "L1", "cache_ttl_s": 900,
          "cost": "existing_subscription_and_free_sources", "errors": ["BAD_ARGUMENT", "AMBIGUOUS", "NOT_FOUND", "NO_DATA", "AUTH_FAILED", "RATE_LIMITED", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED"]}


def result(payload, rows=None, warnings=None):
    sources = payload.get("sources", [])
    issues = [s for s in sources if s.get("status") in ("error", "partial")]
    return Result(data=payload, rows=rows, source="fund_runtime_sources", grade="L1",
                  warnings=(warnings or []) + ([{"type": "partial_sources", "message": "部分来源失败，见 data.sources；成功不代表完整覆盖", "sources": issues}] if issues else []),
                  units=payload.get("units"), next=[{"tool": "fund.disclosures", "why": "回查报告与公告原文"}])


def _component(source, url, fn):
    return data.trace(source, url, fn)


def _part_rows(part):
    value = part.get("data")
    if isinstance(value, dict):
        return value.get("rows", [])
    return value if isinstance(value, list) else []


def _sources(parts):
    out = []
    for p in parts:
        meta = data.metadata(p)
        value = p.get("data")
        if isinstance(value, dict) and "incomplete_windows" in value:
            meta["status"] = "partial" if value["incomplete_windows"] else ("ok" if value["rows"] else "empty")
            meta["incomplete_windows"] = value["incomplete_windows"]
            meta["requests"] = value["requests"]
        out.append(meta)
    return out


def _paged_records(api, params, fields="", max_pages=5):
    rows, sources, seen, complete = [], [], set(), True
    for page in range(max_pages):
        p = _component("tushare_" + api, data.TS_URL,
                       lambda n=page: data.ts_rows(api, {**params, "limit": 1000, "offset": n * 1000}, fields))
        sources.append(data.metadata(p))
        if p["status"] == "error":
            if not rows:
                raise TanyuanError(Code(p["error"]["code"]), f"{api} 来源不可用")
            complete = False
            break
        new = 0
        for row in p["data"]:
            key = json.dumps(row, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                rows.append(row)
                new += 1
        if len(p["data"]) < 1000:
            break
        if not new or page == max_pages - 1:
            complete = False
            break
    return {"rows": rows, "pagination_complete": complete, "sources": sources}


@tool(name="fund.resolve", summary="确认基金身份、份额标签和来源代码；歧义返回候选，不猜代码后缀",
      covers=["公开目录及 Tushare 确认的公募基金/ETF/LOF", "名称歧义与同数字多市场代码"],
      not_covers=["未经核实的 A/C 同产品关系", "仅凭六位数字推测市场、币种或份额"],
      args_schema={"refresh": REFRESH, "query": {**CODE, "description": "基金名称或代码"},
                   "limit": {"type": "integer", "min": 1, "max": 50, "description": "候选显示上限，默认10；歧义按完整匹配数判断"}},
      returns=[{"field": "resolved/candidates", "desc": "唯一身份或候选；未知币种/份额为 null"},
               {"field": "candidate_count/truncated/sources", "desc": "总匹配数、截断和各源状态"}],
      examples=[{"args": {"query": "沪深300ETF"}}], **COMMON)
def fund_resolve(args):
    value = data.resolve(args["query"], args.get("limit", 10))
    return result(value, len(value["candidates"]), [{"type": "identity_ambiguity", "message": "请按候选消歧；显示上限不会自动选中首项"}] if value["resolved"] is None else [])


@tool(name="fund.profile", summary="基金档案、经理任职、策略、费用和基准，保留不同来源原文口径",
      covers=["基金基本资料、经理历史、公开策略及费率"],
      not_covers=["全部销售渠道实际费用", "实时经理交易或未经证实的份额关联"],
      args_schema={"refresh": REFRESH, "code": CODE},
      returns=[{"field": "identity/basic/managers/free_profile/free_managers", "desc": "分源资料与任职记录，不合并冲突"},
               {"field": "sources/missing", "desc": "取得状态与缺项；资料有效日期可能未知"}],
      examples=[{"args": {"code": "000001"}}], **COMMON)
def fund_profile(args):
    identity, identity_sources = data.require_identity(args["code"])
    parts = {}
    if identity.get("ts_code"):
        ts = identity["ts_code"]
        parts["basic"] = _component("tushare_fund_basic", data.TS_URL,
                                    lambda: data.ts_rows("fund_basic", {"ts_code": ts,
                                        "market": "E" if identity["listed"] else "O"}, data.BASIC_FIELDS))
        parts["managers"] = _component("tushare_fund_manager", data.TS_URL,
                                       lambda: _paged_records("fund_manager", {"ts_code": ts}, "ts_code,ann_date,name,begin_date,end_date"))
        for row in _part_rows(parts["managers"]):
            for field in ("ann_date", "begin_date", "end_date"):
                row[field] = data.source_day(row.get(field))
    parts["free_profile"] = _component("eastmoney_profile", free.F10 + f"/jbgk_{identity['code']}.html", lambda: free.profile(identity["code"]))
    parts["free_managers"] = _component("eastmoney_managers", free.F10 + f"/jjjl_{identity['code']}.html", lambda: free.managers(identity["code"]))
    data.fail_if_empty(list(parts.values()), "本次未取得基金档案")
    payload = {"identity": identity, **{k: p.get("data") for k, p in parts.items()},
               "sources": identity_sources + _sources(list(parts.values())),
               "missing": [k for k, p in parts.items() if p["status"] in ("empty", "error")],
               "limitations": ["不同来源任职日期可能分别指连续管理历史和合同起点；不静默合并",
                               "档案为当前来源快照，历史生效日期需回查披露", "未知份额关联和币种不从默认市场推断"],
               "units": {"m_fee": "% per year", "c_fee": "% per year", "fees": "source labels retained; not all-in investor cost"}}
    return result(payload)


@tool(name="fund.nav", summary="分段取得基金净值、修订及分红资料；区分单位/累计/复权净值和披露日期",
      covers=["单次最多20年净值", "免费源回退、修订记录、稀疏规模字段", "Tushare 分红记录"],
      not_covers=["严格历史时点可得性", "货币基金万份收益再投资复算", "累计净值涨幅冒充总收益"],
      args_schema={"refresh": REFRESH, "code": CODE, **DATES, "source": SOURCE,
                   "include_dividends": {"type": "boolean", "description": "附 Tushare 分红事件，默认true；事件不直接用于推断投资者现金流"}},
      returns=[{"field": "series/series_kinds", "desc": "按业务日/公告日排列；货币收益不映射NAV，TS货币NAV仅参考"},
               {"field": "income_series/income_coverage", "desc": "每万份收益及7日年化独立字段、原字段、来源类型和覆盖；未复算总收益"},
               {"field": "coverage/sources/dividends", "desc": "覆盖、各窗口来源状态及分红事件"}],
      examples=[{"args": {"code": "000001", "start_date": "2025-01-01", "end_date": "2025-12-31"}}], **COMMON)
def fund_nav(args):
    identity, sources = data.require_identity(args["code"])
    start, end = data.date_range(args)
    payload = data.nav(identity, start, end, args.get("source", "auto"))
    payload["sources"] = sources + payload["sources"]
    if args.get("include_dividends", True) and identity.get("ts_code"):
        p = _component("tushare_fund_div", data.TS_URL,
                       lambda: _paged_records("fund_div", {"ts_code": identity["ts_code"]}))
        if p.get("data"):
            retained = []
            for row in p["data"]["rows"]:
                for key in ("ann_date", "imp_anndate", "base_date", "div_proc", "record_date", "ex_date", "pay_date"):
                    if key in row and key != "div_proc":
                        row[key] = data.source_day(row[key])
                relevant = row.get("ex_date") or row.get("ann_date")
                if relevant is None or start <= relevant <= end:
                    retained.append(row)
            payload["dividends"] = {**p["data"], "rows": retained,
                                    "filter_basis": "ex_date, fallback ann_date; unknown dates retained",
                                    "units": {"div_cash": "元/份", "base_unit": "万份", "ear_distr": "元", "ear_amount": "元"}}
        else:
            payload["dividends"] = None
        payload["sources"].append(data.metadata(p))
    return result(payload, len(payload["series"]))


@tool(name="fund.holdings", summary="基金报告期持仓及资产配置；按披露日期分组，标明股票/债券子集与比例分母",
      covers=["Tushare 股票持仓", "免费披露的境内外股票、债券子集", "报告期资产配置"],
      not_covers=["实时完整组合", "未披露资产穿透", "把不同公告日表格直接相加"],
      args_schema={"refresh": REFRESH, "code": CODE, "period": {"type": "string", "format": "date", "required": True, "description": "报告期末，必须明确提供，例如2026-06-30"},
                   "assets": {"type": "array[string]", "min_items": 1, "max_items": 3,
                              "description": "stocks/bonds/allocation，默认三类；不得重复"}},
      returns=[{"field": "groups", "desc": "按来源、资产类型、报告期、公告日分组的披露子集"},
               {"field": "allocation/coverage/sources", "desc": "配置、覆盖边界和来源状态"}],
      examples=[{"args": {"code": "000001", "period": "2026-06-30"}}], **COMMON)
def fund_holdings(args):
    period = data.day(args["period"], "period")
    assets = args.get("assets", ["stocks", "bonds", "allocation"])
    if not isinstance(assets, list) or not assets or len(assets) > 3 or any(a not in ("stocks", "bonds", "allocation") for a in assets) or len(set(assets)) != len(assets):
        raise bad_arg("assets 仅可包含不重复的 stocks/bonds/allocation")
    identity, sources = data.require_identity(args["code"])
    parts, groups, allocation = [], [], None
    if "stocks" in assets and identity.get("ts_code"):
        p = _component("tushare_fund_portfolio", data.TS_URL,
                       lambda: _paged_records("fund_portfolio", {"ts_code": identity["ts_code"], "period": period.replace("-", "")}))
        parts.append(p)
        grouped = {}
        for row in _part_rows(p):
            if data.source_day(row.get("end_date")) != period:
                raise schema_changed("Tushare 持仓报告期与请求不一致")
            ann = data.source_day(row.get("ann_date"))
            grouped.setdefault(ann, []).append({"symbol": row.get("symbol"), "market_value": free.number(row.get("mkv")),
                                                "shares": free.number(row.get("amount")),
                                                "stock_value_weight_pct": free.number(row.get("stk_mkv_ratio")),
                                                "stock_float_weight_pct": free.number(row.get("stk_float_ratio"))})
        for ann, rows in sorted(grouped.items(), key=lambda item: item[0] or ""):
            groups.append({"asset_class": "stocks", "report_date": period, "ann_date": ann,
                           "source": "tushare_fund_portfolio", "source_url": data.TS_URL,
                           "rows": rows, "scope": "source_disclosed_stock_subset", "complete_portfolio": False,
                           "pagination_complete": p["data"]["pagination_complete"],
                           "units": {"market_value": "元", "shares": "股", "stock_value_weight_pct": "% of stock market value, NOT fund NAV",
                                     "stock_float_weight_pct": "% of security float"},
                           "limitations": ["不同公告日表保留为独立快照；不得相加或仅按证券代码跨快照去重"]})
    for asset in ("stocks", "bonds"):
        if asset in assets:
            p = _component("eastmoney_" + asset, free.ARCHIVES,
                           lambda a=asset: free.holdings(identity["code"], period, a))
            parts.append(p)
            groups.extend(p.get("data", []))
    if "allocation" in assets:
        p = _component("eastmoney_allocation", free.F10 + f"/zcpz_{identity['code']}.html",
                       lambda: free.allocation(identity["code"], period))
        parts.append(p)
        allocation = p.get("data")
    if not groups and not (allocation and allocation.get("rows")):
        errors = [p for p in parts if p["status"] == "error"]
        code = Code(errors[0]["error"]["code"]) if len(errors) == len(parts) else Code.NO_DATA
        raise TanyuanError(code, "本次报告期未取得持仓或配置数据；不代表基金没有资产", details={"sources": _sources(parts)})
    covered = {g["asset_class"] for g in groups}
    if allocation and allocation.get("rows"):
        covered.add("allocation")
    return result({"identity": identity, "period": period, "groups": groups, "allocation": allocation,
                   "coverage": {"requested_assets": assets, "available_assets": sorted(covered),
                                "missing_assets": [a for a in assets if a not in covered], "complete_portfolio": False},
                   "sources": sources + _sources(parts)}, sum(len(g["rows"]) for g in groups))


@tool(name="fund.disclosures", summary="分页查基金报告/合同/发行运作/经理变更公告与原文入口",
      covers=["公开基金披露目录、HTML原文入口与待核验PDF入口", "公告日期和更正标题标记"],
      not_covers=["仅凭标题推断精确报告期或更正关系", "所有历史公告完整率保证", "正文下载（用 fetch.url）"],
      args_schema={"refresh": REFRESH, "code": CODE,
                   "category": {"type": "string", "enum": ["all", "report", "issuance", "dividend", "personnel", "sales", "other"], "description": "默认all；issuance含发行运作及合同材料；report定期报告"},
                   "start_date": {"type": "string", "format": "date", "description": "可选披露日期起点，仅过滤本页；源总数未过滤"},
                   "end_date": {"type": "string", "format": "date", "description": "可选披露日期终点，仅过滤本页；仍需按next_page继续"},
                   "keyword": {"type": "string", "min_length": 1, "max_length": 100, "description": "可选标题包含文字，仅过滤本页，不代表全历史检索完成"},
                   "page": {"type": "integer", "min": 1, "max": 1000, "description": "页码，默认1"},
                   "page_size": {"type": "integer", "min": 1, "max": 100, "description": "每页条数，默认20"}},
      returns=[{"field": "items", "desc": "公告日期/标题/类别/原文入口；PDF链接为模式生成，未经下载验证"},
               {"field": "has_more/next_page/total_count", "desc": "来源分页状态，不推断全网完整率"}],
      examples=[{"args": {"code": "000001", "category": "report", "page_size": 5}}], **COMMON)
def fund_disclosures(args):
    start = data.day(args["start_date"], "start_date") if "start_date" in args else None
    end = data.day(args["end_date"], "end_date") if "end_date" in args else None
    if start and end and start > end:
        raise bad_arg("start_date 不能晚于 end_date")
    keyword = data.bounded_string(args["keyword"], "keyword", 100) if "keyword" in args else None
    identity, sources = data.require_identity(args["code"])
    page, size = args.get("page", 1), args.get("page_size", 20)
    if not isinstance(page, int) or not 1 <= page <= 1000 or not isinstance(size, int) or not 1 <= size <= 100:
        raise bad_arg("page/page_size 超出范围")
    value = free.announcements(identity["code"], args.get("category", "all"), page, size)
    original_count = len(value["items"])
    if start or end or keyword:
        value["items"] = [r for r in value["items"] if (not start or r["published_date"] >= start)
                          and (not end or r["published_date"] <= end)
                          and (not keyword or keyword.casefold() in (r["title"] or "").casefold())]
    value["filter"] = {"scope": "current_source_page_only", "start_date": start, "end_date": end,
                       "keyword": keyword, "source_page_items": original_count,
                       "matched_page_items": len(value["items"]), "total_count_is_unfiltered": True,
                       "search_complete": not value["has_more"] and page == 1}
    value["identity"] = identity
    value["sources"] = sources + [{"source": "eastmoney_JJGG", "source_url": value["source_url"],
                                   "observed_at": data.config.now_iso(),
                                   "status": "ok" if value["items"] else "empty"}]
    response = result(value, len(value["items"]))
    response.next = [{"tool": "fetch.url", "why": "对选定 pdf_url 用 provider=local 下载并核验正文；链接可能不存在，保留原文入口回查"}]
    response.grade = "L3"
    return response


def etf_data(identity, start, end):
    ts = identity.get("ts_code")
    if not ts or not identity["listed"]:
        raise unsupported("fund.etf 需要经确认的交易所上市基金代码；不猜 .SH/.SZ")
    parts = {}
    parts["metadata"] = _component("tushare_etf_basic", data.TS_URL,
                                   lambda: data.ts_rows("etf_basic", {"ts_code": ts}))
    for label, api in (("daily", "fund_daily"), ("shares", "fund_share"), ("adjustments", "fund_adj")):
        parts[label] = _component("tushare_" + api, data.TS_URL,
                                 lambda name=api: data.ts_series(name, ts, start, end))
    if not _part_rows(parts["daily"]):
        # Existing unadjusted Tencent implementation is reused, not rewritten.
        from .market import market_kline
        def fallback():
            r = market_kline({"code": ts, "period": "day", "adjust": "none", "start": start,
                              "end": end, "count": 640})
            return {"rows": [{"trade_date": b["date"], **{k: v for k, v in b.items() if k != "date"}} for b in r.data["bars"]],
                    "source": r.source, "source_url": r.source_url, "warnings": r.warnings,
                    "units": r.units, "coverage": "at most 640 bars; not verified full requested range"}
        parts["daily_fallback"] = _component("tencent_kline", "https://web.ifzq.gtimg.cn", fallback)
    parts["nav"] = _component("fund_nav", data.TS_URL, lambda: data.nav(identity, start, end))
    basic = parts["metadata"].get("data", [])
    indices = sorted({r.get("index_code") for r in basic if r.get("index_code")})
    benchmark = None
    if len(indices) == 1 and re.fullmatch(r"[A-Za-z0-9]{1,20}\.[A-Z]{2,8}", indices[0]):
        benchmark = _component("tushare_index_daily", data.TS_URL,
                               lambda: data.ts_series("index_daily", indices[0], start, end))
        parts["benchmark"] = benchmark
    nav_payload = parts["nav"].get("data", {})
    monetary_or_income = free.fund_series_kind(identity.get("fund_type")) == "money_market" or any(
        r.get("series_kind") in ("money_market_yield", "money_market_nav_reference", "income_unknown_unit", "unknown", "nav_reference_unknown_fund_type")
        for r in nav_payload.get("series", []))
    nav_points, nav_selection = metrics.select_series(nav_payload.get("series", []), "unit_nav")
    nav_map = {p["date"]: p["value"] for p in nav_points}
    def unique_map(rows, key):
        out, conflict = {}, set()
        for r in rows:
            d, value = r["trade_date"], free.number(r.get(key))
            if d in out and out[d] != value:
                conflict.add(d)
            out[d] = value
        return {d: v for d, v in out.items() if d not in conflict}, sorted(conflict)
    daily = _part_rows(parts["daily"]) or _part_rows(parts.get("daily_fallback", {}))
    closes, price_conflicts = unique_map(daily, "close")
    shares, share_conflicts = unique_map(_part_rows(parts["shares"]), "fd_share")
    derived = []
    for d in sorted(set(closes) | set(shares) | set(nav_map)):
        close, nav_value, share = closes.get(d), nav_map.get(d), shares.get(d)
        compatible_currency = identity.get("currency") not in ("美元", "港币", "欧元") and not monetary_or_income
        premium = close / nav_value - 1 if close is not None and close > 0 and nav_value and compatible_currency else None
        estimate = share * 10_000 * nav_value if share is not None and share >= 0 and nav_value and compatible_currency else None
        missing = {}
        for name, value in (("closing_premium_ratio", premium), ("estimated_net_assets_yuan", estimate)):
            if value is None:
                missing[name] = "money_or_income_series_not_verified_NAV_basis" if monetary_or_income else "missing_same_date_input_or_currency_incompatible"
            elif not math.isfinite(value):
                missing[name] = "arithmetic_out_of_finite_range"
        if "closing_premium_ratio" in missing:
            premium = None
        if "estimated_net_assets_yuan" in missing:
            estimate = None
        derived.append({"date": d, "close": close, "unit_nav": nav_value, "shares_10k": share,
                        "closing_premium_ratio": premium, "estimated_net_assets_yuan": estimate,
                        "unavailable_metrics": missing})
    if not daily and not nav_points and not _part_rows(parts["shares"]) and not basic:
        outcomes = []
        for p in parts.values():
            value = p.get("data")
            if isinstance(value, dict) and "requests" in value:
                outcomes.extend(value["requests"])
            else:
                outcomes.append(data.metadata(p))
        errors = [p for p in outcomes if p["status"] == "error"]
        code = Code(errors[0]["error"]["code"]) if errors and len(errors) == len(outcomes) else Code.NO_DATA
        raise TanyuanError(code, "本次未取得上市基金数据", details={"sources": _sources(list(parts.values()))})
    return {"identity": identity, "etf_status": "confirmed_by_etf_basic" if basic else "listed_fund_only_etf_not_confirmed",
            **{key: p.get("data") for key, p in parts.items()},
            "derived": derived, "derived_basis": {"closing_premium_ratio": "same-date close / unit_nav - 1; not IOPV",
                "estimated_net_assets_yuan": "same-date fd_share (万份) * 10000 * unit_nav; estimate not official AUM",
                "currency_assumption": "listed CNY price and yuan NAV field contract; foreign currency-labelled shares excluded",
                "missing_date_policy": "no forward-fill; unequal dates produce null", "nav_selection": nav_selection,
                "conflicting_price_dates": price_conflicts, "conflicting_share_dates": share_conflicts},
            "benchmark_code": indices[0] if len(indices) == 1 else None,
            "benchmark_basis": "unknown; index_daily close does not prove price vs total-return methodology",
            "sources": _sources(list(parts.values())),
            "units": {"fund_daily.price": "CNY", "fund_daily.vol": "手", "fund_daily.amount": "千元",
                      "fund_share.fd_share": "万份", "closing_premium_ratio": "ratio", "estimated_net_assets_yuan": "CNY estimate"},
            "limitations": ["份额变化不是现金净流入", "日终折溢价不是盘中IOPV", "基准口径未知，不自动计算跟踪误差"]}


@tool(name="fund.etf", summary="ETF/上市基金日线、份额、复权因子、基准及同日折溢价/规模估算",
      covers=["已确认上市基金行情、NAV和份额", "ETF档案与基准", "同日对齐推导值"],
      not_covers=["实时IOPV", "份额变动等于资金净流入", "价格指数与总收益指数混算"],
      args_schema={"refresh": REFRESH, "code": CODE, **DATES},
      returns=[{"field": "metadata/daily/shares/adjustments/nav/benchmark", "desc": "分源数据和窗口覆盖"},
               {"field": "derived/derived_basis", "desc": "同日输入和推导公式；缺失/冲突不填充"}],
      examples=[{"args": {"code": "510300.SH", "start_date": "2026-10-01", "end_date": "2026-10-23"}}], **COMMON)
def fund_etf(args):
    identity, sources = data.require_identity(args["code"])
    start, end = data.date_range(args)
    payload = etf_data(identity, start, end)
    payload["sources"] = sources + payload["sources"]
    return result(payload, len(payload["derived"]))


@tool(name="fund.metrics", summary="可复核净值收益、回撤、波动和明确口径的基准比较；保留计算输入",
      covers=["复权净值或单位净值变化", "最大回撤、收益、波动估算", "口径及币种已确认的基准比较"],
      not_covers=["累计净值简单涨幅作为总收益", "个人实际到手收益", "未确认口径的排名/跟踪误差"],
      args_schema={"refresh": REFRESH, "code": CODE, **DATES, "source": SOURCE,
                   "series": {"type": "string", "enum": ["adj_nav", "unit_nav"], "description": "默认adj_nav；不可用时不偷偷降为单位净值"},
                   "periods_per_year": {"type": "integer", "min": 1, "max": 366, "description": "波动率年化观测数假设，默认252；不是已核验日历"},
                   "benchmark_code": {"type": "string", "max_length": 30, "description": "可选明确指数代码，不从基金名称猜测"},
                   "benchmark_basis": {"type": "string", "enum": ["unknown", "price", "total_return"], "description": "调用者据来源确认的指数口径，默认unknown；price/unknown不与复权净值混算"},
                   "same_currency_confirmed": {"type": "boolean", "description": "调用者是否已核实基金与基准同币种，默认false；保留该声明并非工具独立认证"}},
      returns=[{"field": "metrics.statistics/input_points/selection", "desc": "指标、公式、实际输入、修订/冲突处理"},
               {"field": "benchmark/comparison", "desc": "指数数据及可比性；不满足条件时仅此组不可计算"}],
      examples=[{"args": {"code": "000001", "start_date": "2025-01-01", "end_date": "2025-12-31"}}], **COMMON)
def fund_metrics(args):
    benchmark_code = args.get("benchmark_code")
    if benchmark_code is not None:
        benchmark_code = data.bounded_string(benchmark_code, "benchmark_code", 30)
        if not re.fullmatch(r"[A-Za-z0-9]{1,20}\.[A-Z]{2,8}", benchmark_code):
            raise bad_arg("benchmark_code 须为明确的指数代码，如 000300.SH")
    elif "benchmark_basis" in args or "same_currency_confirmed" in args:
        raise bad_arg("基准口径和币种确认需同时提供 benchmark_code")
    identity, sources = data.require_identity(args["code"])
    start, end = data.date_range(args)
    nav_payload = data.nav(identity, start, end, args.get("source", "auto"))
    field, periods = args.get("series", "adj_nav"), args.get("periods_per_year", 252)
    calculated = metrics.calculate(nav_payload["series"], field, periods)
    eligible = nav_payload.get("unit_nav_calculation_eligible", False) if field == "unit_nav" else nav_payload.get("adj_nav_calculation_eligible", nav_payload.get("return_calculation_eligible", False))
    if not eligible:
        reason = nav_payload.get("unit_nav_calculation_reason") if field == "unit_nav" else nav_payload.get("return_calculation_reason")
        calculated["statistics"] = {"status": "unavailable", "reason": reason or "基金收益类型未确认"}
        calculated["basis"] = "not_a_verified_return_index_for_this_fund_type"
    if nav_payload["coverage"]["incomplete_windows"]:
        calculated["statistics"] = {"status": "unavailable", "reason": "净值窗口未取完整，不用局部区间冒充请求区间"}
    payload = {"identity": identity, "metrics": calculated, "coverage": nav_payload["coverage"],
               "income_series": nav_payload.get("income_series", []), "income_coverage": nav_payload.get("income_coverage"),
               "sources": sources + nav_payload["sources"], "units": {
                   **nav_payload.get("units", {}), "returns_and_drawdown": "ratios; multiply by100 for%"}}
    if benchmark_code:
        p = _component("tushare_index_daily", data.TS_URL,
                       lambda: data.ts_series("index_daily", benchmark_code, start, end))
        payload["benchmark"] = {"code": benchmark_code, **(p.get("data") or {})}
        payload["sources"].extend(_sources([p]))
        if calculated["statistics"]["status"] != "ok" or p["status"] == "error" or p.get("data", {}).get("incomplete_windows"):
            payload["comparison"] = {"status": "unavailable", "reason": "基金或基准输入不足/不完整"}
        else:
            payload["comparison"] = metrics.benchmark_compare(calculated["input_points"], _part_rows(p), field,
                args.get("benchmark_basis", "unknown"), args.get("same_currency_confirmed", False), periods)
    return result(payload, len(calculated["input_points"]))
