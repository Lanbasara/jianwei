"""Subscription feeds with explicit selection, partial failures and bounded output.

A source registry defines the scan scope; it cannot represent all news on the web.
Disabled/frozen sources are excluded by default and omissions are disclosed.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .. import config, http
from ..envelope import Result
from ..errors import bad_arg, no_data, unavailable
from ..registry import tool
from ..source_sets import registry_path, validate_registry
from ..util import CN_TZ, clamp, clean_text

SKILL_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SETS = SKILL_ROOT / "references" / "source-sets"
DEFAULT_SET = "investment-news"
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
             "utm_id", "spm", "fbclid", "gclid"}
_GRADE_ORDER = {"L1": 1, "L2": 2}


def canonical_url(url: str) -> str:
    """Drop tracking params + fragment; keep article-id params (dropping them merges stories)."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"),
                       "&".join(f"{k}={v}" for k, v in kept), ""))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        dt = parsedate_to_datetime(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S %z",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            cleaned = text.replace("Z", "+0000") if fmt.endswith("%z") else text
            dt = datetime.strptime(cleaned, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _local(tag: str) -> str:
    return tag.split("}")[-1].lower()


def _text(node) -> str:
    return clean_text("".join(node.itertext()), 900) if node is not None else ""


def parse_feed(xml_text: str, source: dict) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = link = published = summary = ""
        for child in node:
            name = _local(child.tag)
            if name == "title":
                title = _text(child)
            elif name == "link":
                link = (child.get("href") or (child.text or "")).strip()
            elif name in ("pubdate", "published", "updated", "date"):
                published = published or (child.text or "").strip()
            elif name in ("description", "summary", "content"):
                summary = summary or _text(child)
        if not title and not link:
            continue
        dt = _parse_time(published)
        items.append({
            "title": title,
            "url": canonical_url(link),
            "published": dt.astimezone(CN_TZ).isoformat(timespec="minutes") if dt else None,
            "published_ts": dt.timestamp() if dt else 0,
            "has_time": dt is not None,
            "summary": summary[:260],
            "summary_len": len(summary),
            "source": source["name"],
            "sector": source.get("sector"),
            "lang": source.get("lang"),
            "grade": source.get("grade") or "L2",
        })
    return items


def _fetch_one(source: dict, timeout: float, attempts: int = 2) -> tuple[dict, list[dict], str | None]:
    last_error = None
    for _ in range(attempts):
        try:
            text = http.get_text(
                source["url"], timeout=(6, timeout), max_retries=0,
                headers={"Accept": "application/rss+xml, application/atom+xml, "
                                    "application/xml, text/xml, */*"},
            )
            items = parse_feed(text, source)
            if not items:
                return source, [], "empty-or-not-feed"
            return source, items, None
        except Exception as exc:  # noqa: BLE001 - one dead feed must not kill the batch
            last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
    return source, [], last_error


def load_sources(source_set: str) -> dict:
    path = registry_path(SOURCE_SETS, source_set)
    try:
        return validate_registry(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise bad_arg(f"新闻源集合无法解析: {exc}")


def _parse_since(args: dict) -> float:
    """Accept `since` as 24h / 7d / ISO date, or legacy `since_hours`."""
    since = args.get("since")
    if since:
        text = str(since).strip().lower()
        m = re.fullmatch(r"(\d+)\s*([hdw])", text)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            hours = n * {"h": 1, "d": 24, "w": 168}[unit]
        else:
            dt = _parse_time(text)
            if dt is None:
                raise bad_arg(f"since 格式不支持: {since!r}", hint="用 24h / 7d,或 YYYY-MM-DD")
            hours = max(1.0, (datetime.now(CN_TZ) - dt.astimezone(CN_TZ)).total_seconds() / 3600)
        return float(hours)
    return float(int(args.get("since_hours") or 24))


@tool(
    name="news.feed",
    summary="订阅式新闻抓取:按源集、赛道和时间窗拉取 RSS,披露覆盖与截断",
    covers=["投资/科技赛道源集 investment-news",
            "时政/财经源集 policy-news",
            "按源集/赛道、时间窗、语言、证据等级过滤", "跨源 URL+标题去重"],
    not_covers=["A 股公司公告(用 filing.announcements)", "港股/美股个股新闻(用 news.stock/search.web)",
                "7×24 快讯(用 news.flash)", "全文抓取(用 fetch.url)"],
    args_schema={
        "source_set": {"type": "string",
                       "description": "源集合名:investment-news(科技产业)/ policy-news(时政财经),默认 investment-news"},
        "sector": {"type": "string",
                   "description": "单个赛道:sector 名随源集不同(见 source-set JSON 的 sectors)"},
        "sectors": {"type": "array[string]", "description": "多个赛道(与 sector 二选一)"},
        "since": {"type": "string", "description": "时间窗:24h / 7d / YYYY-MM-DD,默认 24h"},
        "since_hours": {"type": "integer", "min": 1, "max": 720,
                        "description": "等价于 since(小时数),兼容旧写法"},
        "lang": {"type": "string", "enum": ["any", "zh", "en"], "description": "默认 any"},
        "grade_min": {"type": "string", "enum": ["L2", "L1"], "description": "L1=只要官方机构源"},
        "include_stale": {"type": "boolean", "description": "是否包含长期不更新的源(默认 false)"},
        "per_source_limit": {"type": "integer", "min": 1, "max": 30, "description": "单源最多取几条,默认 10"},
        "limit": {"type": "integer", "min": 1, "max": 200, "description": "返回上限,默认 40"},
        "source_offset": {"type": "integer", "min": 0, "max": 100000,
                          "description": "符合条件的源按清单顺序分页,默认 0;每批最多 60 个,用 coverage.next_source_offset 续查"},
        "keyword": {"type": "string", "description": "标题/摘要关键词过滤"},
    },
    arg_groups=[{"keys": ["sector", "sectors"], "min": 0, "max": 1},
                {"keys": ["since", "since_hours"], "min": 0, "max": 1}],
    returns=[
        {"field": "items[].title / url / source", "desc": "标题、原文链接、来源"},
        {"field": "items[].published / age_hours", "desc": "发布时间(北京时间)与距今小时数"},
        {"field": "items[].sector / lang / grade", "desc": "赛道、语言、证据等级"},
        {"field": "items[].summary_len", "desc": "摘要长度(短摘要需 fetch.url 拉正文)"},
        {"field": "coverage", "desc": "请求源数/成功源数/失败明细/跳过源"},
        {"field": "warnings", "desc": "部分失败、跳过 frozen、丢弃未来时间戳、去重剔除数"},
    ],
    source="rss:investment-news", grade="L2", cache_ttl_s=300, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"sector": "ai", "since": "24h", "limit": 20},
               "cmd": "ty call news.feed --json '{\"sector\":\"ai\",\"since\":\"24h\"}'"},
              {"args": {"sector": "semi", "since": "7d", "lang": "en"},
               "cmd": "ty call news.feed --json '{\"sector\":\"semi\",\"since\":\"7d\",\"lang\":\"en\"}'"}],
    latency_hint_ms=4000,
)
def news_feed(args: dict) -> Result:
    payload = load_sources(args.get("source_set") or DEFAULT_SET)
    all_sources = payload.get("sources") or []
    known_sectors = set(payload.get("sectors") or {})
    sectors = set(args.get("sectors") or [])
    if args.get("sector"):
        sectors.add(str(args["sector"]))
    unknown = sectors - known_sectors
    if unknown:
        raise bad_arg(f"未知赛道 {sorted(unknown)}", hint=f"可用: {sorted(known_sectors)}")

    include_stale = bool(args.get("include_stale"))
    lang = args.get("lang") or "any"
    grade_min = args.get("grade_min") or "L2"
    if grade_min not in _GRADE_ORDER:
        raise bad_arg("grade_min 只能是 L1 / L2")

    skipped_stale: list[str] = []
    skipped_disabled: list[str] = []
    selected = []
    for src in all_sources:
        if src.get("status") == "disabled":
            skipped_disabled.append(src["name"])
            continue
        if src.get("status") == "watch":
            # watch sources still run, but the caller is told they are on probation
            pass
        if sectors and src.get("sector") not in sectors:
            continue
        if lang != "any" and src.get("lang") != lang:
            continue
        if _GRADE_ORDER.get(src.get("grade") or "L2", 2) > _GRADE_ORDER[grade_min]:
            continue
        if not include_stale and src.get("freshness_class") == "frozen":
            skipped_stale.append(src["name"])
            continue
        selected.append(src)
    if not selected:
        raise bad_arg("没有匹配的新闻源", hint="放宽 sector/lang/grade_min,或加 include_stale=true")
    available_sources = len(selected)
    source_offset = int(args.get("source_offset") or 0)
    if source_offset >= available_sources:
        raise bad_arg("source_offset 超出符合条件的源范围")
    selected = selected[source_offset:source_offset + 60]
    next_source_offset = source_offset + len(selected)
    if next_source_offset >= available_sources:
        next_source_offset = None

    hours = _parse_since(args)
    limit = clamp(int(args.get("limit") or 40), 1, 200)
    per_source = clamp(int(args.get("per_source_limit") or 10), 1, 30)
    cutoff = (datetime.now(CN_TZ) - timedelta(hours=hours)).timestamp()
    future_cutoff = (datetime.now(CN_TZ) + timedelta(hours=1)).timestamp()
    keyword = (args.get("keyword") or "").strip().lower()

    items: list[dict] = []
    failures: list[dict] = []
    ok_sources = 0
    omitted_by_source_limit = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_fetch_one, src, 15) for src in selected]
        for future in as_completed(futures):
            source, got, error = future.result()
            if error:
                failures.append({"source": source["name"], "reason": error})
                continue
            ok_sources += 1
            omitted_by_source_limit += max(0, len(got) - per_source)
            items.extend(got[:per_source])

    if ok_sources == 0:
        raise unavailable(
            f"{len(selected)} 个源全部抓取失败",
            evidence="; ".join(f"{f['source']}: {f['reason']}" for f in failures[:5]),
            hint="检查网络/代理;或缩小源范围后重试。",
        )

    dropped_future = 0
    fresh: list[dict] = []
    for item in items:
        ts = item.get("published_ts") or 0
        if ts > future_cutoff:
            dropped_future += 1
            continue
        if ts and ts < cutoff:
            continue
        if not ts:                      # no timestamp: keep only if window is generous
            if hours < 168:
                continue
        if keyword and keyword not in (item.get("title") or "").lower() \
                and keyword not in (item.get("summary") or "").lower():
            continue
        fresh.append(item)

    seen_url: set[str] = set()
    seen_title: set[str] = set()
    deduped: list[dict] = []
    removed_dup = 0
    for item in sorted(fresh, key=lambda i: i.get("published_ts") or 0, reverse=True):
        url_key = item.get("url") or ""
        title_key = (item.get("title") or "")[:40]
        if (url_key and url_key in seen_url) or (title_key and title_key in seen_title):
            removed_dup += 1
            continue
        if url_key:
            seen_url.add(url_key)
        if title_key:
            seen_title.add(title_key)
        deduped.append(item)

    matched_items = len(deduped)
    deduped = deduped[:limit]

    warnings: list[dict] = []
    if available_sources > len(selected):
        warnings.append({"type": "partial_source_coverage",
                         "message": "本次只查询符合条件的部分源;按 next_source_offset 续查,偏移前的源不在本次范围内"})
    if omitted_by_source_limit or matched_items > limit:
        warnings.append({"type": "items_truncated", "message": "条目受到单源或响应上限限制;可提高相应 limit 或缩小源范围"})
    if failures:
        warnings.append({"type": "partial_source_failure", "count": len(failures),
                         "sources": [f["source"] for f in failures[:10]],
                         "detail": failures[:5]})
    if skipped_disabled:
        warnings.append({"type": "skipped_disabled_sources", "count": len(skipped_disabled),
                         "sources": skipped_disabled[:10],
                         "message": "这些源已被园丁体检后人工批准停用;要恢复请跑 ty garden plan 并 revive"})
    if skipped_stale:
        warnings.append({"type": "skipped_frozen_sources", "count": len(skipped_stale),
                         "sources": skipped_stale[:10],
                         "message": "长期不更新的源已跳过;要包含请加 include_stale=true"})
    if dropped_future:
        warnings.append({"type": "dropped_future_timestamps", "count": dropped_future,
                         "message": "源里带未来发布时间的条目已剔除"})
    if removed_dup:
        warnings.append({"type": "deduped", "count": removed_dup})
    warnings.append({"type": "subscription_layer",
                     "message": "RSS 是订阅层:只覆盖已策展的源,不保证全网;找未订阅主题用 search.web"})
    warnings.append({"type": "license",
                     "message": "各源版权归原媒体;引用给原文链接,不要大段转载"})

    now_ts = datetime.now(CN_TZ).timestamp()
    for item in deduped:
        ts = item.pop("published_ts", 0) or 0
        item.pop("has_time", None)
        item["age_hours"] = round((now_ts - ts) / 3600, 1) if ts else None
        item["summary_truncated"] = item.get("summary_len", 0) > len(item.get("summary") or "")

    grades = sorted({i.get("grade") for i in deduped})
    return Result(
        data={"items": deduped,
              "coverage": {"available_sources": available_sources,
                           "selected_sources": len(selected), "requested_sources": len(selected),
                           "omitted_sources": available_sources - len(selected),
                           "source_offset": source_offset, "next_source_offset": next_source_offset,
                           "selection_order": "registry", "ok_sources": ok_sources,
                           "selected_names": [s["name"] for s in selected],
                           "matched_items": matched_items, "returned_items": len(deduped),
                           "omitted_by_source_limit": omitted_by_source_limit,
                           "omitted_by_response_limit": matched_items - len(deduped),
                           "failed_sources": len(failures), "skipped_frozen": len(skipped_stale),
                           "window_hours": hours}},
        rows=len(deduped), source=f"rss:{args.get('source_set') or DEFAULT_SET}",
        grade="L1" if grades == ["L1"] else "L2",
        source_url=args.get("source_set") or DEFAULT_SET,
        as_of=None, warnings=warnings,
        units={"time": "Asia/Shanghai"},
        next=([{"tool": "news.feed", "args": {**args, "source_offset": next_source_offset},
                "why": "继续查询本次未覆盖的新闻源"}] if next_source_offset is not None else []) +
             [{"tool": "fetch.url", "why": "摘要太短,需要正文"},
              {"tool": "search.web", "why": "主题不在源清单内时改用搜索发现"}],
    )
