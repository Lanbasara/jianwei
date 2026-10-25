"""Envelope: the single response shape every tool returns.

Two properties matter more than elegance:
1. the agent can always tell *what it got, from where, as of when*;
2. on failure it learns *what to do next*, not just that something broke.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import config
from .errors import TanyuanError


@dataclass
class Result:
    """What a tool implementation returns (before provenance is attached)."""

    data: Any
    rows: int | None = None
    source: str = "unknown"
    grade: str = "L1"
    source_url: str | None = None
    as_of: str | None = None
    observed_at: str | None = field(default_factory=config.now_iso)
    warnings: list[Any] = field(default_factory=list)
    next: list[dict] = field(default_factory=list)
    cost: dict | None = None
    units: dict | None = None
    # Long content lives in blobs; the envelope exposes stable paths instead of
    # pretending a truncated string is the whole document.
    artifacts: list[dict] = field(default_factory=list)


def success(
    tool: str,
    args: dict,
    result: Result,
    *,
    duration_ms: int,
    cache_meta: dict | None = None,
) -> dict:
    provenance = {
        "source": result.source,
        "source_grade": result.grade,
        "as_of": result.as_of,
        "observed_at": result.observed_at,
        "duration_ms": duration_ms,
        "cache": {
            "hit": bool(cache_meta and cache_meta.get("hit")),
            "age_s": (cache_meta or {}).get("age_s", 0),
        },
        "cost": result.cost or {"unit": "request", "amount": 1},
    }
    if result.source_url:
        provenance["source_url"] = result.source_url
    if result.warnings:
        provenance["warnings"] = result.warnings
    if result.units:
        provenance["units"] = result.units

    out = {
        "ok": True,
        "tool": tool,
        "args": args,
        "data": result.data,
        "rows": result.rows if result.rows is not None else _count(result.data),
        "provenance": provenance,
    }
    if result.artifacts:
        out["artifacts"] = result.artifacts
    if result.next:
        out["next"] = result.next
    return out


def failure(
    tool: str,
    args: dict,
    err: TanyuanError,
    *,
    duration_ms: int | None = None,
    source: str | None = None,
) -> dict:
    provenance: dict = {"observed_at": config.now_iso()}
    if source:
        provenance["source"] = source
    if duration_ms is not None:
        provenance["duration_ms"] = duration_ms
    return {
        "ok": False,
        "tool": tool,
        "args": args,
        "error": err.to_dict(),
        "provenance": provenance,
    }


def _count(data: Any) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("bars", "rows", "items", "results", "announcements", "reports", "news"):
            val = data.get(key)
            if isinstance(val, list):
                return len(val)
        return 1
    return None
