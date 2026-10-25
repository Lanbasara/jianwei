"""Entity resolution: turn "茅台"/"600519"/"SH600519" into one canonical instrument.

This is the cheapest fix for the most expensive class of error — wrong-entity data that
looks plausible. Agents should call this first when a query names a company rather than
a code.
"""
from __future__ import annotations

import json
import time

from .. import cache, config, http, tushare
from ..envelope import Result
from ..errors import TanyuanError, bad_arg, no_data
from ..registry import tool
from ..util import resolve_symbol

TUSHARE_API = "https://api.tushare.pro"
_CACHE_NAME = "stock_basic_v2.json"
_TTL = 24 * 3600
_FIELDS = "ts_code,symbol,name,area,industry,market,list_date,delist_date,exchange,list_status"


def _stock_basic() -> tuple[list[dict], list[dict], str]:
    """(listed, delisted, status). Disk-cached 24h.

    Delisted coverage is a Tushare-only capability (342 names as of 2026-10) and matters
    for survivorship-bias-free research — free sources drop these names entirely.
    """
    path = config.cache_dir() / _CACHE_NAME
    if path.is_file():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - blob.get("fetched_at", 0) < _TTL:
                return blob.get("listed", []), blob.get("delisted", []), "cache"
        except (OSError, ValueError):
            pass
    try:
        listed = tushare.call("stock_basic", {"list_status": "L"}, _FIELDS)
        try:
            delisted = tushare.call("stock_basic", {"list_status": "D"}, _FIELDS)
        except TanyuanError:
            delisted = []
    except TanyuanError as err:
        if path.is_file():
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
                return (blob.get("listed", []), blob.get("delisted", []),
                        f"stale-cache({err.code.value})")
            except (OSError, ValueError):
                pass
        raise
    payload = {"fetched_at": time.time(), "listed": listed, "delisted": delisted}
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return listed, delisted, "live"


def _match(rows: list[dict], query: str) -> list[dict]:
    q = query.strip()
    upper = q.upper()
    exact_code = [r for r in rows
                  if r.get("ts_code", "").upper() == upper
                  or r.get("ts_code", "").split(".")[0] == q
                  or r.get("symbol") == q]
    exact_name = [r for r in rows if r.get("name") == q]
    contains = [r for r in rows if q and q in str(r.get("name", ""))]
    out: list[dict] = []
    seen: set = set()
    for group in (exact_code, exact_name, contains):
        for r in group:
            key = r.get("ts_code")
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out


@tool(
    name="entity.resolve",
    summary="把公司名/简称/各种代码写法解析成唯一标的(含代码、市场、行业、上市日)",
    covers=["A 股全部在市股票", "6 位代码/带前缀代码/带后缀代码", "公司全称或简称的精确/包含匹配"],
    not_covers=["港股/美股(暂无)", "已退市股票", "同名基金的歧义消解"],
    args_schema={
        "query": {"type": "string", "required": True, "description": "公司名、简称或代码",
                  "example": "茅台"},
        "limit": {"type": "integer", "min": 1, "max": 50, "description": "候选上限,默认 10"},
        "include_delisted": {"type": "boolean", "description": "是否包含已退市股票(默认 false)"},
    },
    returns=[
        {"field": "resolved", "desc": "唯一匹配时的标的(否则 null)"},
        {"field": "candidates[].ts_code", "desc": "如 600519.SH"},
        {"field": "candidates[].name / industry / market", "desc": "名称、行业、板块"},
    ],
    source="tushare_stock_basic", grade="L1", cache_ttl_s=0, cost="free",
    errors=["BAD_ARGUMENT", "NOT_FOUND", "AUTH_FAILED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"query": "茅台"}, "cmd": "ty call entity.resolve --json '{\"query\":\"茅台\"}'"}],
    latency_hint_ms=120,
)
def entity_resolve(args: dict) -> Result:
    query = str(args["query"]).strip()
    if not query:
        raise bad_arg("query 不能为空")
    limit = int(args.get("limit") or 10)
    warnings: list = []

    # A code-like query resolves without needing the full list (works even if Tushare is down).
    try:
        sym = resolve_symbol(query)
        is_code = True
    except ValueError:
        sym = None
        is_code = False

    listed, delisted, status = _stock_basic()
    include_delisted = bool(args.get("include_delisted"))
    rows = listed + delisted if include_delisted else listed
    status_text = status
    if status_text.startswith("stale-cache"):
        warnings.append({"type": "stale_reference_data",
                         "message": f"股票列表来自过期缓存({status_text}),可能缺新股"})
    if not rows:
        raise no_data("股票基础列表为空", hint="Tushare 权限或网络异常。")

    if is_code and sym is not None:
        hits = [r for r in rows if r.get("symbol") == sym.digits]
        if not hits and not include_delisted:
            gone = [r for r in delisted if r.get("symbol") == sym.digits]
            if gone:
                from ..errors import not_found
                raise not_found(
                    f"{sym.digits}({gone[0].get('name')}) 已退市,默认不返回退市股",
                    hint="若做历史/幸存者偏差研究,加 include_delisted=true 重试。",
                )
        if not hits:
            from ..errors import not_found
            hint = "确认代码;港股/美股不在覆盖范围内。"
            if sym.digits.startswith(("43", "83", "87")):
                hint = ("该代码属北交所老号段(43/83/87),存量标的多数已迁至 920xxx。"
                        "请改用公司名称查询(本工具支持按名称搜),或到北交所官网核对现行代码。")
            raise not_found(
                f"{sym.digits} 不在股票列表里(未上市/非 A 股/已迁移)",
                hint=hint,
            )
        candidates = hits[:limit]
    else:
        candidates = _match(rows, query)[:limit]

    if not candidates:
        from ..errors import not_found
        raise not_found(
            f"没有匹配「{query}」的 A 股标的",
            hint="试试公司简称(如 茅台→贵州茅台)、6 位代码,或确认是否为港股/美股。",
        )

    resolved = candidates[0] if len(candidates) == 1 else None
    if len(candidates) > 1:
        warnings.append({"type": "ambiguous_query",
                         "message": f"「{query}」有 {len(candidates)} 个候选,请用 ts_code 或更精确的名称消歧"})

    def slim(row: dict) -> dict:
        ts = row.get("ts_code", "")
        digits = ts.split(".")[0]
        return {
            "ts_code": ts,
            "code": digits,
            "prefixed": _prefixed(ts),
            "name": row.get("name"),
            "market": row.get("market") or row.get("exchange"),
            "industry": row.get("industry"),
            "area": row.get("area"),
            "list_date": row.get("list_date"),
            "delist_date": row.get("delist_date"),
            "list_status": row.get("list_status") or ("D" if row in delisted else "L"),
        }

    return Result(
        data={"query": query, "resolved": slim(resolved) if resolved else None,
              "candidates": [slim(c) for c in candidates]},
        rows=len(candidates),
        source="tushare_stock_basic", grade="L1",
        source_url=TUSHARE_API,
        as_of=None,
        warnings=warnings,
        next=[{"tool": "market.quote", "why": "确认代码后取实时行情"},
              {"tool": "filing.announcements", "why": "查该公司公告"}],
    )


def _prefixed(ts_code: str) -> str:
    code, _, market = ts_code.partition(".")
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(market.upper(), "")
    return f"{prefix}{code}"
