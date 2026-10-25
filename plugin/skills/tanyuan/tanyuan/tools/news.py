"""News: per-stock news (Eastmoney) and market-wide 7x24 flashes (Wallstreetcn).

News is grade L2: good for *finding events and narrative*, never for numbers. Every item
keeps its source name and URL so a downstream agent can go verify against an L0 filing.
"""
from __future__ import annotations

from datetime import datetime

from .. import config, http
from ..envelope import Result
from ..errors import bad_arg, no_data, schema_changed, unavailable
from ..registry import tool
from ..util import CN_TZ, clamp, clean_text, clip_text, resolve_symbol

EM_SEARCH = "https://search-api-web.eastmoney.com/search/jsonp"
WSCN_LIVES = "https://api-one-wscn.awtmt.com/apiv1/content/lives"


@tool(
    name="news.stock",
    summary="个股新闻(东财全文检索,标题+摘要+时间+原文链接)",
    covers=["A 股个股相关新闻/资讯", "按关键词检索(代码或公司名)"],
    not_covers=["新闻正文全文(只给摘要,正文用 fetch.url)", "付费媒体正文", "公告(用 filing.announcements)"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "代码或公司名", "example": "600519"},
        "page_size": {"type": "integer", "min": 1, "max": 50, "description": "默认 20"},
        "keyword": {"type": "string", "description": "额外关键词过滤(可选)"},
    },
    returns=[
        {"field": "news[].title / summary", "desc": "标题与摘要"},
        {"field": "news[].published_at", "desc": "发布时间"},
        {"field": "news[].source", "desc": "媒体名"},
        {"field": "news[].url", "desc": "原文链接(可再用 fetch.url 取正文)"},
    ],
    source="eastmoney_news", grade="L2", cache_ttl_s=600, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED"],
    examples=[{"args": {"code": "600519", "page_size": 5},
               "cmd": "ty call news.stock --json '{\"code\":\"600519\",\"page_size\":5}'"}],
    latency_hint_ms=700,
)
def news_stock(args: dict) -> Result:
    raw = str(args["code"]).strip()
    try:
        sym = resolve_symbol(raw)
        keyword = sym.digits
        label = sym.prefixed
    except ValueError:
        keyword = raw
        label = raw
    if not keyword:
        raise bad_arg("code 不能为空")
    page_size = clamp(int(args.get("page_size") or 20), 1, 50)
    if args.get("keyword"):
        keyword = f"{keyword} {args['keyword']}"
    import json as _json
    inner = _json.dumps({
        "uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "default", "pageIndex": 1,
            "pageSize": page_size, "preTag": "", "postTag": "",
        }},
    }, ensure_ascii=False, separators=(",", ":"))
    text = http.get_text(
        EM_SEARCH,
        params={"cb": "jQuery_news", "param": inner},
        headers={"Referer": "https://so.eastmoney.com/"},
        timeout=(8, 25),
    )
    body = http.jsonp_to_json(text)
    articles = ((body.get("result") or {}).get("cmsArticleWebOld")) or []
    if not articles:
        raise no_data(f"没有检索到「{keyword}」的新闻", hint="换个关键词或确认代码;这不是源故障。")
    rows = []
    for item in articles:
        summary, trunc = clip_text(clean_text(item.get("content"), limit=None), 500)
        entry = {
            "title": clean_text(item.get("title"), 200),
            "summary": summary,
            "published_at": item.get("date"),
            "source": item.get("mediaName"),
            "url": item.get("url"),
        }
        if trunc:
            entry["summary_truncated"] = trunc
        rows.append(entry)
    return Result(
        data={"query": keyword, "news": rows},
        rows=len(rows), source="eastmoney_news", grade="L2",
        source_url=EM_SEARCH, as_of=None,
        warnings=[{"type": "news_is_lead_not_fact",
                   "message": "新闻只作线索;其中的数字与结论须回 L0/L1 源核验"}],
        next=[{"tool": "fetch.url", "why": "读某条新闻正文"},
              {"tool": "filing.announcements", "why": "核验公告类事实"}],
    )


@tool(
    name="news.flash",
    summary="全市场 7×24 快讯(华尔街见闻,可按频道,带重要度)",
    covers=["global-channel 要闻", "a-stock-channel A 股", "其他见闻频道(需已知频道名)"],
    not_covers=["个股定向新闻(用 news.stock)", "历史快讯回补(只可取最近 N 条)"],
    args_schema={
        "channel": {"type": "string", "description": "频道名,默认 global-channel"},
        "limit": {"type": "integer", "min": 1, "max": 100, "description": "默认 30"},
    },
    returns=[
        {"field": "items[].time / title / content", "desc": "时间、标题、正文(北京时间)"},
        {"field": "items[].importance", "desc": "见闻重要度:1=普通,2=头条级"},
        {"field": "items[].url", "desc": "原文链接"},
    ],
    source="wallstreetcn", grade="L2", cache_ttl_s=120, cost="free",
    errors=["BAD_ARGUMENT", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED"],
    examples=[{"args": {"channel": "a-stock-channel", "limit": 10},
               "cmd": "ty call news.flash --json '{\"channel\":\"a-stock-channel\",\"limit\":10}'"}],
    latency_hint_ms=300,
)
def news_flash(args: dict) -> Result:
    channel = args.get("channel") or "global-channel"
    if "-channel" not in channel:
        raise bad_arg(f"channel 形如 global-channel / a-stock-channel,收到 {channel!r}")
    limit = clamp(int(args.get("limit") or 30), 1, 100)
    body = http.get_json(WSCN_LIVES, params={"channel": channel, "limit": limit},
                         headers={"Referer": "https://wallstreetcn.com/"}, timeout=(8, 25))
    if not isinstance(body, dict) or body.get("code") != 20000:
        raise unavailable("华尔街见闻返回错误码", evidence=str(body)[:300])
    items_raw = ((body.get("data") or {}).get("items")) or []
    if not items_raw:
        raise no_data(f"频道 {channel} 返回 0 条(频道名可能不存在)", hint="换 global-channel 试试")
    items = []
    for item in items_raw:
        stamp = item.get("display_time")
        when = None
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            when = datetime.fromtimestamp(stamp, CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
        content, trunc = clip_text(clean_text(item.get("content_text"), limit=None), 2000)
        entry = {
            "time": when,
            "title": clean_text(item.get("title"), 200),
            "content": content,
            "importance": item.get("score"),
            "url": item.get("uri"),
        }
        if trunc:
            entry["content_truncated"] = trunc
        items.append(entry)
    return Result(
        data={"channel": channel, "sort": "time desc", "items": items,
              "next_cursor": (body.get("data") or {}).get("next_cursor")},
        rows=len(items), source="wallstreetcn", grade="L2",
        source_url=WSCN_LIVES, as_of=(items[0].get("time") if items else None),
        units={"time": "Asia/Shanghai"},
        warnings=[{"type": "news_is_lead_not_fact",
                   "message": "快讯为二手转述;数字需回一手源核验"}],
    )
