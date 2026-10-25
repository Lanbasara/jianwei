"""Filings & announcements — CNINFO (巨潮) is the primary L0 source for A-shares.

It needs an `orgId` per stock (not derivable from the code), so we cache the official
mapping table and fall back to a rule. Announcement times arrive as UTC epoch ms.
"""
from __future__ import annotations

import json
import time
from datetime import datetime

from .. import config, http
from ..envelope import Result
from ..errors import bad_arg, no_data, schema_changed, unavailable
from ..registry import tool
from ..util import CN_TZ, clamp, clean_text, resolve_symbol

QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
ORG_MAP_URL = "https://www.cninfo.com.cn/new/data/szse_stock.json"
DETAIL_URL = (
    "https://www.cninfo.com.cn/new/disclosure/detail"
    "?stockCode={digits}&announcementId={anno_id}&orgId={org}&announcementTime={day}"
)
PDF_URL = "https://static.cninfo.com.cn/{adjunct}"

CATEGORIES = {
    "annual": "category_ndbg_szsh",
    "half": "category_bndbg_szsh",
    "q1": "category_yjdbg_szsh",
    "q3": "category_sjdbg_szsh",
    "forecast": "category_yjygjxz_szsh",
    "express": "category_yjkb_szsh",
}

_ORG_CACHE_NAME = "cninfo_orgmap.json"
_ORG_TTL = 7 * 24 * 3600


def _org_map() -> dict[str, str]:
    path = config.cache_dir() / _ORG_CACHE_NAME
    if path.is_file():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - blob.get("fetched_at", 0) < _ORG_TTL:
                return blob.get("map", {})
        except (OSError, ValueError):
            pass
    try:
        body = http.get_json(ORG_MAP_URL, headers={"Referer": "https://www.cninfo.com.cn/"})
        mapping = {s["code"]: s["orgId"] for s in body.get("stockList", []) if s.get("code")}
    except Exception:  # noqa: BLE001 - fall back to rule below
        return {}
    try:
        path.write_text(json.dumps({"fetched_at": time.time(), "map": mapping}, ensure_ascii=False),
                        encoding="utf-8")
    except OSError:
        pass
    return mapping


def _org_id(digits: str, market: str) -> str:
    mapping = _org_map()
    if digits in mapping:
        return mapping[digits]
    return f"gss{market}0{digits}" if False else f"gs{market}0{digits}"


def _fmt_ts(value) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        stamp = datetime.fromtimestamp(value / 1000, CN_TZ)
        # CNINFO often only knows the date. Emitting "00:00" implies a precision we don't
        # have, and agents start reasoning about intra-day ordering (observed 2026-10-24).
        if (stamp.hour, stamp.minute) == (0, 0):
            return stamp.strftime("%Y-%m-%d")
        return stamp.strftime("%Y-%m-%d %H:%M")
    text = str(value or "").strip()
    return text[:16] if text else None


@tool(
    name="filing.announcements",
    summary="A 股公告检索(巨潮资讯,含年报/季报/业绩预告/临时公告,附 PDF 直链)",
    covers=["沪深北 A 股全部公告", "按股票、日期区间、关键词、公告类别过滤"],
    not_covers=["港股公告(HKEXnews)", "美股公告(SEC EDGAR)", "公告正文内容解析(只给标题与链接)"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "6 位代码", "example": "600519"},
        "start_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD,默认近 90 天"},
        "end_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD,默认今天"},
        "keyword": {"type": "string", "description": "标题关键词,如 回购、减持"},
        "category": {"type": "string", "enum": list(CATEGORIES) + ["all"],
                     "description": "公告类别,默认 all"},
        "page_size": {"type": "integer", "min": 1, "max": 30,
                      "description": "每页条数(巨潮硬上限 30),默认 20"},
    },
    returns=[
        {"field": "announcements[].title", "desc": "公告标题"},
        {"field": "announcements[].published_at", "desc": "公告时间(北京时间)"},
        {"field": "announcements[].pdf_url", "desc": "PDF 直链(L0 证据,可直接引用)"},
        {"field": "announcements[].detail_url", "desc": "巨潮详情页"},
        {"field": "total", "desc": "命中总数(可能大于返回条数)"},
    ],
    source="cninfo", grade="L0", cache_ttl_s=600, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED"],
    examples=[{"args": {"code": "600519", "keyword": "分红", "page_size": 5},
               "cmd": "ty call filing.announcements --json '{\"code\":\"600519\",\"keyword\":\"分红\"}'"}],
    latency_hint_ms=600,
)
def filing_announcements(args: dict) -> Result:
    sym = resolve_symbol(args["code"])
    if sym.market == "bj":
        pass  # CNINFO covers BSE too
    page_size = clamp(int(args.get("page_size") or 20), 1, 30)
    end_date = args.get("end_date") or config.now_iso()[:10]
    if args.get("start_date"):
        start_date = args["start_date"]
    else:
        from datetime import date, timedelta
        start_date = (date.fromisoformat(end_date) - timedelta(days=90)).isoformat()
    category = args.get("category") or "all"

    form = {
        "stock": f"{sym.digits},{_org_id(sym.digits, sym.market)}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": CATEGORIES.get(category, ""),
        "plate": "",
        "seDate": f"{start_date}~{end_date}",
        "searchkey": args.get("keyword") or "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    body = http.post_json(QUERY_URL, data=form, headers=headers, timeout=(8, 25))
    if not isinstance(body, dict):
        raise schema_changed("巨潮返回不是对象", evidence=str(body)[:300])
    if body.get("totalAnnouncement") is None and "announcements" not in body:
        raise unavailable("巨潮返回缺字段(totalAnnouncement/announcements)",
                          evidence=str(body)[:300])

    org = _org_id(sym.digits, sym.market)
    rows = []
    for item in body.get("announcements") or []:
        adjunct = item.get("adjunctUrl") or ""
        published = _fmt_ts(item.get("announcementTime"))
        rows.append({
            "title": clean_text(item.get("announcementTitle"), 300),
            "announcement_id": item.get("announcementId"),
            "published_at": published,
            "type": item.get("announcementTypeName"),
            "pdf_url": PDF_URL.format(adjunct=adjunct) if adjunct else None,
            "detail_url": DETAIL_URL.format(
                digits=sym.digits, anno_id=item.get("announcementId"), org=org,
                day=(published or "")[:10],
            ),
            "adjunct_size_kb": item.get("adjunctSize"),
        })
    if not rows:
        raise no_data(
            f"{sym.digits} 在 {start_date}~{end_date} 没有命中公告"
            + (f"(关键词={args.get('keyword')})" if args.get("keyword") else ""),
            hint="放宽日期区间、去掉关键词或换类别再试;这不是源故障。",
        )
    published = [r["published_at"] or "" for r in rows if r.get("published_at")]
    latest = max(published) if published else None
    return Result(
        data={"code": sym.prefixed, "total": body.get("totalAnnouncement"),
              "returned": len(rows), "sort": "published_at desc, announcement_id desc",
              # 区分「我查的窗口」和「实际拿到的数据跨度」——合成一个字段会让读者误判。
              "query_window": [start_date, end_date],
              "data_span": ({"latest": max(published), "earliest": min(published)}
                            if published else None),
              "announcements": rows},
        rows=len(rows), source="cninfo", grade="L0",
        source_url=QUERY_URL,
        as_of=latest,
        units={"time": "Asia/Shanghai"},
        next=[{"tool": "search.web", "why": "需要公告的背景/解读时(仅作线索)"}],
    )
