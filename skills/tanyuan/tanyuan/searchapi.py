"""Search providers: Tavily (web/news/finance, good filters) and DeepSeek Search/Fetch.

Both are treated as *discovery* sources (grade L3): their snippets/answers are leads,
never numeric facts. Every normalised result carries url + published date + authority
so the caller can decide where to verify.
"""
from __future__ import annotations

import json

from . import config, http
from .errors import TanyuanError, auth_failed, bad_arg, unavailable
from .util import clean_text, num

TAVILY_SEARCH = "https://api.tavily.com/search"
TAVILY_EXTRACT = "https://api.tavily.com/extract"
DEEPSEEK_SEARCH = "https://api.deepseek.com/v1/tools/search"
DEEPSEEK_FETCH = "https://api.deepseek.com/v1/tools/fetch"


# ── Tavily ────────────────────────────────────────────────────────────────────

def tavily_search(
    query: str,
    *,
    count: int = 8,
    topic: str = "general",
    time_range: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    exact_match: bool = False,
    include_raw_content: bool = False,
    depth: str = "basic",
) -> dict:
    key = config.require_secret("TAVILY_API_KEY", what="Tavily API key",
                                how="export TAVILY_API_KEY=...")
    if not query or not query.strip():
        raise bad_arg("query 不能为空")
    if topic not in ("general", "news", "finance"):
        raise bad_arg(f"topic 只能是 general/news/finance,收到 {topic!r}")
    if depth not in ("basic", "advanced"):
        raise bad_arg(f"depth 只能是 basic/advanced,收到 {depth!r}")
    payload: dict = {
        "api_key": key,
        "query": query.strip(),
        "max_results": max(1, min(int(count), 20)),
        "topic": topic,
        "search_depth": depth,
        "include_answer": False,
        "include_raw_content": bool(include_raw_content),
        "include_usage": True,
        # Benchmark 2026-10-24: auto_parameters silently upgrades to advanced
        # (2 credits) even when search_depth=basic is explicit, with no relevance gain.
        "auto_parameters": False,
        # general topic does not return published_date unless this is set.
        "include_published_date": True,
    }
    if time_range:
        payload["time_range"] = time_range
    if start_date:
        payload["start_date"] = start_date
    if end_date:
        payload["end_date"] = end_date
    if include_domains:
        payload["include_domains"] = include_domains[:300]
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains[:150]
    if exact_match:
        payload["exact_match"] = True

    body = http.post_json(TAVILY_SEARCH, json_body=payload, timeout=(8, 40))
    if not isinstance(body, dict) or "results" not in body:
        from .errors import schema_changed
        raise schema_changed("Tavily 返回里没有 results", evidence=str(body)[:300])

    results = []
    for item in body.get("results") or []:
        results.append({
            "title": clean_text(item.get("title"), 300),
            "url": item.get("url"),
            "snippet": clean_text(item.get("content"), limit=None),
            "published": item.get("published_date"),
            "score": num(item.get("score")),
            "provider": "tavily",
        })
    return {
        "results": results,
        "usage": body.get("usage") or {},
        "request_id": body.get("request_id"),
    }


def tavily_extract(urls: list[str], *, depth: str = "basic") -> dict:
    key = config.require_secret("TAVILY_API_KEY", what="Tavily API key",
                                how="export TAVILY_API_KEY=...")
    if not urls:
        raise bad_arg("urls 不能为空")
    payload = {
        "api_key": key,
        "urls": urls[:20],
        "extract_depth": depth,
        "include_images": False,
    }
    body = http.post_json(TAVILY_EXTRACT, json_body=payload, timeout=(10, 60))
    out = []
    for item in body.get("results") or []:
        out.append({
            "url": item.get("url"),
            # full text: the caller decides how much to show and stores the rest
            "raw_content": (item.get("raw_content") or ""),
            "chars": len(item.get("raw_content") or ""),
            "provider": "tavily",
        })
    failed = [{"url": f.get("url"), "error": f.get("error")} for f in (body.get("failed_results") or [])]
    return {"results": out, "failed": failed, "usage": body.get("usage") or {}}


# ── DeepSeek Search / Fetch ──────────────────────────────────────────────────

def _deepseek_headers() -> dict:
    key = config.require_secret("DEEPSEEK_API_KEY", what="DeepSeek API key",
                                how="export DEEPSEEK_API_KEY=...")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def deepseek_search(
    query: str,
    *,
    count: int = 10,
    need_content: bool = False,
    ori_question: str | None = None,
    localization: str | None = None,
) -> dict:
    if not query or not query.strip():
        raise bad_arg("query 不能为空")
    if len(query) > 128:
        raise bad_arg(
            f"DeepSeek Search query 上限 128 字符,当前 {len(query)}(实测硬限制)",
            hint="压缩 query:去掉冗余限定词,用 site:/after:/before: 做过滤,把背景信息放到 ori_question。",
        )
    payload: dict = {
        "query": query.strip(),
        "count": max(1, min(int(count), 50)),
        "need_content": bool(need_content),
    }
    if ori_question:
        payload["ori_question"] = ori_question[:128]
    if localization:
        payload["localization"] = localization
    body = http.post_json(DEEPSEEK_SEARCH, json_body=payload, headers=_deepseek_headers(),
                          timeout=(8, 45))
    if not isinstance(body, dict) or "results" not in body:
        from .errors import schema_changed
        raise schema_changed("DeepSeek Search 返回里没有 results", evidence=str(body)[:300])
    results = []
    for item in body.get("results") or []:
        results.append({
            "title": clean_text(item.get("title"), 300),
            "url": item.get("url"),
            "snippet": clean_text(item.get("snippet"), limit=None),
            "summary": clean_text(item.get("summary"), limit=None),
            "content": clean_text(item.get("content"), limit=None) if need_content else None,
            "published": item.get("date"),
            "site_name": item.get("site_name"),
            "authority": item.get("authority"),
            "score": num(item.get("rerank_score")),
            "provider": "deepseek",
        })
    return {"results": results}


def deepseek_fetch(url: str) -> dict:
    if not url or not url.startswith(("http://", "https://")):
        raise bad_arg("url 必须是 http(s) 绝对地址")
    body = http.post_json(DEEPSEEK_FETCH, json_body={"url": url}, headers=_deepseek_headers(),
                          timeout=(10, 60))
    if not isinstance(body, dict):
        from .errors import schema_changed
        raise schema_changed("DeepSeek Fetch 返回不是对象", evidence=str(body)[:300])
    content = body.get("content") or ""
    return {
        "url": url,
        "title": clean_text(body.get("title"), 300),
        "content": content,          # full text; clipping is a presentation decision
        "chars": len(content),
        "provider": "deepseek",
    }
