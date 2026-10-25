"""Fund identity and bounded time-series orchestration, separate from CLI tools.

The product reads only runtime sources. Reference mappings are evidence, not
number-prefix guesses. Each partial fetch and publication revision remains visible.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import config, fund_sources as free, tushare
from .errors import Code, TanyuanError, bad_arg, no_data, not_found, schema_changed

TS_URL = "https://api.tushare.pro"
BASIC_FIELDS = ("ts_code,name,management,custodian,fund_type,found_date,due_date,list_date,"
                "issue_date,delist_date,issue_amount,m_fee,c_fee,duration_year,p_value,"
                "min_amount,exp_return,benchmark,status,invest_type,type,trustee,purc_startdate,"
                "redm_startdate,market")
NAV_FIELDS = "ts_code,ann_date,nav_date,unit_nav,accum_nav,accum_div,net_asset,total_netasset,adj_nav"


def bounded_string(value, name, maximum=160):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise bad_arg(f"{name} 必须是 1–{maximum} 字符的非空文本")
    return value.strip()


def day(value, name="date"):
    value = bounded_string(value, name, 10)
    if not re.fullmatch(r"\d{8}|\d{4}-\d{2}-\d{2}", value):
        raise bad_arg(f"{name} 须为 YYYY-MM-DD 或 YYYYMMDD")
    try:
        digits = value.replace("-", "")
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
    except ValueError:
        raise bad_arg(f"{name} 不是有效日期") from None


def source_day(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return day(str(value))
    except TanyuanError:
        raise schema_changed("基金来源日期格式异常") from None


def date_range(args, default_days=365):
    end = day(args.get("end_date", config.now_iso()[:10]), "end_date")
    start = day(args.get("start_date", (date.fromisoformat(end) - timedelta(days=default_days)).isoformat()), "start_date")
    if start > end:
        raise bad_arg("start_date 不能晚于 end_date")
    if (date.fromisoformat(end) - date.fromisoformat(start)).days > 366 * 20:
        raise bad_arg("单次基金时间范围最多 20 年；请分段查询")
    return start, end


def trace(source, url, fn):
    observed = config.now_iso()
    try:
        data = fn()
        empty = data is None or data == [] or data == {} or (
            isinstance(data, dict) and "rows" in data and not data["rows"])
        partial = isinstance(data, dict) and (data.get("incomplete_windows") or data.get("pagination_complete") is False)
        return {"source": source, "source_url": url, "observed_at": observed,
                "status": "partial" if partial else ("empty" if empty else "ok"), "data": data}
    except TanyuanError as err:
        return {"source": source, "source_url": url, "observed_at": observed, "status": "error",
                "error": {"code": err.code.value, "message": f"{source} 请求失败",
                          "retryable": bool(err.retryable), "hint": err.hint}}


def metadata(part):
    return {k: v for k, v in part.items() if k != "data"}


def fail_if_empty(parts, message):
    """Never turn all-source errors into a successful empty dataset."""
    if any(p.get("data") and p["status"] not in ("empty", "error") for p in parts):
        return
    errors = [p for p in parts if p["status"] == "error"]
    if errors and len(errors) == len(parts):
        err = errors[0]["error"]
        raise TanyuanError(Code(err["code"]), message, details={"sources": [metadata(p) for p in parts]})
    raise TanyuanError(Code.NO_DATA, message, details={"sources": [metadata(p) for p in parts]})


def ts_rows(api, params, fields=""):
    rows = tushare.call(api, params, fields)
    if not isinstance(rows, list) or len(rows) > 50_000 or any(not isinstance(r, dict) for r in rows):
        raise schema_changed("Tushare 基金结果不是有界记录列表")
    requested = params.get("ts_code")
    for row in rows:
        if requested and row.get("ts_code") != requested:
            raise schema_changed(f"Tushare {api} 返回标的与请求不一致")
    return rows


def _identity(row, provider):
    code = row.get("ts_code") if provider == "tushare" else row.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}(?:\.(?:OF|SH|SZ))?", code):
        raise schema_changed("基金身份代码格式异常")
    name = free.text(row.get("name"), 300)
    # Name text is evidence for labels only; it is not evidence for a legal A/C relationship.
    share = re.search(r"(?:[-（(\s]|[\u4e00-\u9fff])([A-IKR])(?:[-）)\s]|$)", name or "")
    currencies = [label for label in ("人民币", "美元", "港币", "欧元") if label in (name or "")]
    return {"code": code[:6], "ts_code": code if provider == "tushare" else None,
            "name": name, "fund_type": row.get("fund_type"),
            "market": row.get("market") if provider == "tushare" else None,
            "listed": code.endswith((".SH", ".SZ")) if provider == "tushare" else None,
            "share_class": share.group(1) if share else None,
            "currency": currencies[0] if len(currencies) == 1 else None,
            "label_basis": "explicit_name_text_only; absence means unknown",
            "related_share_classes": [], "share_relationship_status": "not_verified",
            "provider_codes": {provider: code}, "identity_source": provider,
            "source_url": TS_URL if provider == "tushare" else free.DIRECTORY}


def resolve(query, limit=10):
    query = bounded_string(query, "query")
    code_match = re.fullmatch(r"(\d{6})(?:\.(OF|SH|SZ))?", query.upper())
    if not isinstance(limit, int) or not 1 <= limit <= 50:
        raise bad_arg("limit 须为 1–50")
    parts, candidates = [], []
    # Names require a directory. A numeric code can be checked directly, cheaply.
    if not code_match:
        p = trace("eastmoney_directory", free.DIRECTORY, free.directory)
        parts.append(p)
        rows = p.get("data", [])
        exact = [r for r in rows if r.get("name") == query]
        matches = exact or [r for r in rows if query.casefold() in str(r.get("name", "")).casefold()]
        if p["status"] == "error":
            matches = []
            for market in ("E", "O"):
                fallback = trace("tushare_fund_basic", TS_URL,
                                 lambda m=market: ts_rows("fund_basic", {"market": m}, BASIC_FIELDS))
                parts.append(fallback)
                matches.extend(r for r in fallback.get("data", []) if query.casefold() in str(r.get("name", "")).casefold())
            exact = [r for r in matches if r.get("name") == query]
            matches = exact or matches
            candidates = [_identity(r, "tushare") for r in matches]
        else:
            candidates = [_identity(r, "eastmoney") for r in matches]
        if len(candidates) == 1 and not candidates[0]["ts_code"]:
            code_match = re.fullmatch(r"(\d{6})(?:\.(OF|SH|SZ))?", candidates[0]["code"])
    if code_match:
        digits, suffix = code_match.groups()
        ts_candidates = []
        suffixes = [suffix] if suffix else ["OF", "SH", "SZ"]
        for ending in suffixes:
            ts = f"{digits}.{ending}"
            p = trace("tushare_fund_basic", TS_URL,
                      lambda t=ts, e=ending: ts_rows("fund_basic", {"ts_code": t, "market": "O" if e == "OF" else "E"}, BASIC_FIELDS))
            parts.append(p)
            ts_candidates.extend(_identity(r, "tushare") for r in p.get("data", []))
            if p["status"] == "error" and p["error"]["code"] in ("AUTH_FAILED", "QUOTA_EXCEEDED", "RATE_LIMITED"):
                break
        if ts_candidates:
            # Match all known suffixes for bare digits. Never prefer one silently.
            candidates = ts_candidates
        elif not candidates and not suffix:
            p = trace("eastmoney_directory", free.DIRECTORY, free.directory)
            parts.append(p)
            candidates = [_identity(r, "eastmoney") for r in p.get("data", []) if r.get("code") == digits]
        elif suffix:
            candidates = []
    # Deduplicate exact identities, but different market suffixes remain candidates.
    candidates = list({r["ts_code"] or r["code"]: r for r in candidates}.values())
    if not candidates:
        if all(p["status"] == "error" for p in parts):
            fail_if_empty(parts, "基金身份来源均不可用")
        raise not_found("未在已查询来源中确认该基金身份；代码后缀不会自动替换")
    total = len(candidates)
    return {"query": query, "resolved": candidates[0] if total == 1 else None,
            "candidates": candidates[:limit], "candidate_count": total, "truncated": total > limit,
            "sources": [metadata(p) for p in parts],
            "resolution_scope": "queried source records; unavailable sources may contain additional matches"}


def require_identity(code):
    data = resolve(code)
    if data["resolved"] is None:
        raise TanyuanError(Code.AMBIGUOUS, "基金名称或代码有多个候选，须先消歧", candidates=data["candidates"])
    return data["resolved"], data["sources"]


def windows(start, end):
    current, last = date.fromisoformat(start), date.fromisoformat(end)
    while current <= last:
        stop = min(last, current + timedelta(days=365))
        yield current.isoformat(), stop.isoformat()
        current = stop + timedelta(days=1)


def ts_series(api, ts_code, start, end, fields="", date_field="trade_date", max_requests=64):
    """Year windows plus bounded pagination; exact duplicates removed, revisions retained."""
    rows, requests, incomplete, seen, count = [], [], [], set(), 0
    for left, right in windows(start, end):
        offset = 0
        while True:
            if count >= max_requests:
                incomplete.append({"start_date": left, "end_date": end, "reason": "request_budget"})
                break
            params = {"ts_code": ts_code, "start_date": left.replace("-", ""),
                      "end_date": right.replace("-", ""), "limit": 1000, "offset": offset}
            part = trace(f"tushare_{api}", TS_URL, lambda p=params: ts_rows(api, p, fields))
            count += 1
            requests.append({**metadata(part), "params": params})
            if part["status"] == "error":
                incomplete.append({"start_date": left, "end_date": right, "reason": part["error"]["code"]})
                if part["error"]["code"] in ("AUTH_FAILED", "QUOTA_EXCEEDED", "RATE_LIMITED"):
                    incomplete[-1]["end_date"] = end
                    return {"rows": rows, "requests": requests, "incomplete_windows": incomplete}
                break
            page = part["data"]
            added = 0
            for row in page:
                record = dict(row)
                business = source_day(row.get(date_field))
                if business is None or not left <= business <= right:
                    raise schema_changed(f"Tushare {api} 日期不在请求窗口内")
                record[date_field] = business
                if "ann_date" in record:
                    record["ann_date"] = source_day(record["ann_date"])
                key = json.dumps(record, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key)
                    rows.append(record)
                    added += 1
            if len(page) < 1000:
                break
            if not added or offset >= 3000:
                incomplete.append({"start_date": left, "end_date": right, "reason": "pagination_repeated_or_limit"})
                break
            offset += 1000
        if count >= max_requests:
            if right < end:
                next_day = (date.fromisoformat(right) + timedelta(days=1)).isoformat()
                incomplete.append({"start_date": next_day, "end_date": end, "reason": "request_budget"})
            break
    return {"rows": rows, "requests": requests, "incomplete_windows": incomplete}


def free_nav(code, start, end, max_pages=64, fund_type=None):
    rows, parts, seen, incomplete = [], [], set(), []
    first_total = None
    for page in range(1, max_pages + 1):
        p = trace("eastmoney_lsjz", free.API + "/lsjz",
                  lambda n=page: free.nav_page(code, start, end, n, 20, fund_type=fund_type))
        parts.append({**metadata(p), "page": page, "page_size": 20,
                      "params": {"fundCode": code, "startDate": start, "endDate": end}})
        if p["status"] == "error":
            incomplete.append({"page": page, "reason": p["error"]["code"]})
            break
        data, total = p["data"]
        if first_total is None:
            first_total = total
        elif total != first_total:
            incomplete.append({"page": page, "reason": "source_total_changed_during_pagination"})
        added = 0
        for row in data:
            business = source_day(row["nav_date"])
            if not start <= business <= end:
                raise schema_changed("免费净值日期不在请求范围内")
            key = json.dumps(row, sort_keys=True)
            if key not in seen:
                seen.add(key)
                rows.append(row)
                added += 1
        if page * 20 >= total:
            if len(rows) < total:
                incomplete.append({"page": page, "reason": "unique_rows_below_source_total"})
            break
        if not added or page == max_pages:
            incomplete.append({"page": page, "reason": "pagination_repeated_or_limit"})
            break
    return {"rows": rows, "requests": parts, "incomplete_windows": incomplete}


def nav(identity, start, end, provider="auto"):
    if provider not in ("auto", "tushare", "eastmoney"):
        raise bad_arg("source 须为 auto/tushare/eastmoney")
    attempts = []
    selected = None
    identity_kind = free.fund_series_kind(identity.get("fund_type"))
    income_data = None
    if provider != "eastmoney" and identity.get("ts_code"):
        p = trace("tushare_fund_nav", TS_URL,
                  lambda: ts_series("fund_nav", identity["ts_code"], start, end, NAV_FIELDS, "nav_date"))
        attempts.append(p)
        if p.get("data", {}).get("rows"):
            selected = p["data"]
            for row in selected["rows"]:
                for field in ("unit_nav", "accum_nav", "adj_nav", "accum_div", "net_asset", "total_netasset"):
                    row[field] = free.number(row.get(field))
                row["source"] = "tushare_fund_nav"
                row["series_kind"] = "money_market_nav_reference" if identity_kind == "money_market" else ("nav" if identity_kind == "nav" else "nav_reference_unknown_fund_type")
    if provider != "tushare" and (selected is None or selected["incomplete_windows"] or identity_kind == "money_market"):
        p = trace("eastmoney_lsjz", free.API + "/lsjz", lambda: free_nav(identity["code"], start, end, fund_type=identity.get("fund_type")))
        attempts.append(p)
        if any(r.get("series_kind") in ("money_market_yield", "income_unknown_unit") for r in p.get("data", {}).get("rows", [])):
            income_data = p["data"]
        if p.get("data", {}).get("rows") and (selected is None or not p["data"]["incomplete_windows"]):
            if identity_kind != "money_market" or selected is None:
                selected = p["data"]
    if selected is None:
        details = []
        for p in attempts:
            if p.get("data"):
                details.extend(p["data"].get("requests", []))
            else:
                details.append(metadata(p))
        errors = [p for p in details if p.get("status") == "error"]
        code = Code(errors[0]["error"]["code"]) if errors and len(errors) == len(details) else Code.NO_DATA
        raise TanyuanError(code, "本次来源及区间没有可用基金净值", details={"sources": details})
    rows = sorted(selected["rows"], key=lambda r: (r["nav_date"], r.get("ann_date") or ""))
    counts = {}
    for row in rows:
        counts[row["nav_date"]] = counts.get(row["nav_date"], 0) + 1
    all_requests = []
    for p in attempts:
        all_requests.extend(p.get("data", {}).get("requests", [metadata(p)]))
    kinds = sorted({r.get("series_kind", "unknown") for r in rows})
    unit_eligible = identity_kind != "money_market" and bool(kinds) and all(
        kind in ("nav", "nav_reference_unknown_fund_type") for kind in kinds)
    adj_eligible = identity_kind == "nav" and kinds == ["nav"]
    return {"identity": identity, "series": rows, "series_kinds": kinds,
            "income_series": income_data["rows"] if income_data else [],
            "income_coverage": {"incomplete_windows": income_data["incomplete_windows"],
                                "rows": len(income_data["rows"]), "calendar_completeness": "not_verified"} if income_data else None,
            "unit_nav_calculation_eligible": unit_eligible,
            "unit_nav_calculation_reason": None if unit_eligible else "货币/收益型或字段语义不明，不能按单位净值变化分析",
            "adj_nav_calculation_eligible": adj_eligible,
            "return_calculation_eligible": adj_eligible,
            "eligibility_basis": {"return_calculation_eligible": "alias of adj_nav_calculation_eligible; still requires complete positive input values",
                                  "unit_nav_calculation_eligible": "explicit NAV semantics permit NAV change only; not distribution-adjusted investment return",
                                  "adj_nav_calculation_eligible": "confirmed non-money NAV fund type; provider adjusted NAV methodology remains unverified"},
            "return_calculation_reason": None if adj_eligible else
                "货币/收益型或未确认类型不能按NAV变化推算真实回报；需收益分配、结转、再投资规则及完整收益日历",
            "coverage": {"requested_start": start, "requested_end": end,
                         "actual_start": rows[0]["nav_date"], "actual_end": rows[-1]["nav_date"],
                         "unique_dates": len(counts), "rows": len(rows),
                         "revision_dates": [d for d, n in counts.items() if n > 1],
                         "incomplete_windows": selected["incomplete_windows"],
                         "calendar_completeness": "not_verified", "point_in_time": False,
                         "missing_fields": {f: sum(r.get(f) is None for r in rows) for f in
                                            ("unit_nav", "accum_nav", "adj_nav", "net_asset", "total_netasset")}},
            "sources": all_requests, "selected_source": rows[0]["source"],
            "units": {"nav": "currency per share; currency from identity, unknown if absent",
                      "income_per_10000_shares": "元/万份（来源明确每万份收益，适用日期见nav_date）",
                      "seven_day_annualized_yield_pct": "% annualized quotation, NOT realized period return",
                      "net_asset": "元 (share class)", "total_netasset": "元 (fund total)"},
            "limitations": ["不把累计净值涨幅当作分红再投资收益", "修订按公告日期保留；不是严格历史时点回测数据",
                            "货币基金每万份收益和7日年化是独立类型，不能当单位/累计净值；常数NAV不代表零回报",
                            "规模字段通常是稀疏报告期数据，不等于每日规模"]}
