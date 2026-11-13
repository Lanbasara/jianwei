"""Explicit reading views over completed tool responses; no I/O or summarisation.

Only declared collections and text fields are shortened. External text remains
data for the CLI's JSON encoder. Projection never changes the cached response,
source coverage, timestamps, warnings, continuation hints, or failure semantics.
"""
from __future__ import annotations

from .errors import bad_arg, internal_error


BRIEF_COLLECTIONS = {
    "news.feed": "items",
    "news.flash": "items",
    "search.web": "results",
}
BRIEF_ITEM_LIMIT = 8
BRIEF_TEXT_LIMIT = 280
_TEXT_FIELDS = ("summary", "snippet", "content")


def validate_view(tool: str, mode: str) -> None:
    """Reject unsupported views before dispatch can perform a paid request."""
    if mode not in ("full", "brief"):
        raise bad_arg("未知阅读视图。", "--view 只支持 full 或 brief。")
    if mode == "brief" and tool not in BRIEF_COLLECTIONS:
        raise bad_arg(
            "该工具尚不支持 brief 视图。",
            "使用 --view full；brief 支持 " + "、".join(BRIEF_COLLECTIONS) + "。",
        )


def render_view(envelope: dict, mode: str) -> dict:
    if mode == "full" or envelope.get("ok") is not True:
        return envelope

    tool = envelope.get("tool")
    validate_view(tool, mode)
    collection = BRIEF_COLLECTIONS[tool]
    data = envelope.get("data")
    records = data.get(collection) if isinstance(data, dict) else None
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise internal_error("精简视图无法识别工具返回结构；可使用 --view full 读取原始响应。")

    shown = []
    shortened = []
    for index, record in enumerate(records[:BRIEF_ITEM_LIMIT]):
        item = dict(record)
        for field in _TEXT_FIELDS:
            value = item.get(field)
            if isinstance(value, str) and len(value) > BRIEF_TEXT_LIMIT:
                item[field] = value[:BRIEF_TEXT_LIMIT]
                shortened.append({
                    "path": f"data.{collection}[{index}].{field}",
                    "returned_chars": len(value),
                    "shown_chars": BRIEF_TEXT_LIMIT,
                })
        shown.append(item)

    # Keep tool-level truncation separate from this view's omissions. `rows`
    # still describes the tool response, not the shortened reading view.
    view = {
        "mode": "brief",
        "collection": f"data.{collection}",
        "returned_items": len(records),
        "shown_items": len(shown),
        "omitted_items": len(records) - len(shown),
        "shortened_fields": shortened,
    }
    # Put provenance and continuation before potentially long external content.
    out = {key: envelope[key] for key in ("ok", "tool", "args", "provenance", "rows")
           if key in envelope}
    out["view"] = view
    out.update({key: value for key, value in envelope.items()
                if key not in out and key != "data"})
    out["data"] = {key: value for key, value in data.items() if key != collection}
    out["data"][collection] = shown
    return out
