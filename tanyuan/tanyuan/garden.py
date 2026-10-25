"""Source-garden maintenance for the news subscription layer.

Contract (matches the product rule: judgement is autonomous, mutation needs a human):
- `check`  : read-only network probe. Writes nothing except an optional report file.
- `plan`   : proposes actions (disable / watch / keep / revive). Changes nothing.
- `apply`  : requires `--yes` (human approval) and a plan whose
             registry hash still matches. Always writes a timestamped backup first.
- `restore`: requires `--yes`; validate the snapshot and back up the target before replacing it.

Design rules:
- never delete a source entry: set `status` and keep the record for future revival;
- a plan is bound to the registry bytes it was computed from (sha256), so applying a stale
  plan against an edited registry is refused;
- official/institutional feeds are judged on a longer clock than high-volume media feeds
  (Fed publishes monthly-ish; CNBC publishes ~every 30 minutes).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from .errors import bad_arg, no_data
from .source_sets import registry_path as _registry_path, validate_registry

SKILL_ROOT = Path(__file__).resolve().parents[1]
SOURCE_SETS = SKILL_ROOT / "references" / "source-sets"
CN_TZ = ZoneInfo("Asia/Shanghai")

# freshness thresholds, in hours
MEDIA_STALE_H = 7 * 24
MEDIA_FROZEN_H = 30 * 24
INSTITUTIONAL_STALE_H = 45 * 24
INSTITUTIONAL_FROZEN_H = 120 * 24


def registry_path(source_set: str) -> Path:
    return _registry_path(SOURCE_SETS, source_set)


def load_registry(source_set: str) -> tuple[dict, Path, str]:
    path = registry_path(source_set)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    return validate_registry(json.loads(raw.decode("utf-8"))), path, digest


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _thresholds(source: dict) -> tuple[float, float]:
    if source.get("cadence") == "institutional" or source.get("official"):
        return INSTITUTIONAL_STALE_H, INSTITUTIONAL_FROZEN_H
    return MEDIA_STALE_H, MEDIA_FROZEN_H


def check_source(source: dict, *, timeout: float = 15, attempts: int = 2) -> dict:
    """Probe one feed. Read-only: no writes, no registry mutation."""
    from .tools.newsfeed import _fetch_one
    started = time.monotonic()
    _, items, error = _fetch_one(source, timeout, attempts)
    elapsed = round((time.monotonic() - started) * 1000)
    record = {
        "name": source.get("name"),
        "url": source.get("url"),
        "sector": source.get("sector"),
        "grade": source.get("grade"),
        "cadence": source.get("cadence") or ("institutional" if source.get("official") else "media"),
        "ok": items is not None and bool(items),
        "items": len(items or []),
        "latency_ms": elapsed,
        "error": error,
    }
    if record["ok"]:
        stamps = [i.get("published_ts") or 0 for i in items]
        stamps = [s for s in stamps if s]
        if stamps:
            latest = max(stamps)
            record["latest_item_at"] = datetime.fromtimestamp(latest, CN_TZ).isoformat(timespec="minutes")
            record["age_hours"] = round((time.time() - latest) / 3600, 1)
        else:
            record["latest_item_at"] = None
            record["age_hours"] = None
    return record


def check(source_set: str = "policy-news", *, workers: int = 8, limit: int | None = None) -> dict:
    """Probe every source in a source set (bounded concurrency). Returns a report dict."""
    from concurrent.futures import ThreadPoolExecutor
    registry, _, digest = load_registry(source_set)
    sources = [s for s in registry.get("sources", []) if s.get("status") != "disabled"]
    if limit:
        sources = sources[:limit]
    if not sources:
        raise no_data(f"{source_set} 没有可检查的源")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        records = list(pool.map(check_source, sources))
    counts = {"ok": sum(1 for r in records if r["ok"]),
              "failed": sum(1 for r in records if not r["ok"])}
    return {
        "source_set": source_set,
        "checked_at": _now_iso(),
        "registry_sha256": digest,
        "total": len(records),
        "counts": counts,
        "sources": records,
    }


def _verdict(record: dict, source: dict) -> tuple[str, str]:
    """Map a probe record to keep / watch / disable with a human-readable reason."""
    stale_h, frozen_h = _thresholds(source)
    if not record["ok"]:
        streak = int(source.get("fail_streak") or 0) + 1
        intermittent = bool(source.get("intermittent"))
        # one failed check is not enough evidence to disable: network hiccups are common
        # (NYT feeds flip between rounds). Require 2 consecutive failures, 3 if the source
        # was already known to be intermittent.
        needed = 3 if intermittent else 2
        if streak >= needed:
            return "disable", f"连续 {streak} 次抓取失败: {record.get('error') or '无内容'}"
        return "watch", (f"第 {streak} 次抓取失败(再失败 {needed - streak} 次才建议停用): "
                         f"{record.get('error') or '无内容'}")
    age = record.get("age_hours")
    if age is None:
        return "watch", "feed 无可用时间戳,无法判断新鲜度"
    if age > frozen_h:
        return "disable", f"疑似僵尸: 最新条目在 {age / 24:.0f} 天前(阈值 {frozen_h / 24:.0f} 天)"
    if age > stale_h:
        return "watch", f"偏旧: 最新条目在 {age / 24:.1f} 天前(阈值 {stale_h / 24:.0f} 天)"
    if record["items"] < 3:
        return "watch", f"条目偏少: 只有 {record['items']} 条"
    return "keep", f"健康: {record['items']} 条,最新 {age:.1f} 小时前"


def plan(source_set: str = "policy-news", *, workers: int = 8) -> dict:
    """Compute a change proposal. Pure function of the registry + current probe results."""
    report = check(source_set, workers=workers)
    registry, _, digest = load_registry(source_set)
    by_name = {s.get("name"): s for s in registry.get("sources", [])}
    actions = []
    for record in report["sources"]:
        source = by_name.get(record["name"]) or {}
        action, reason = _verdict(record, source)
        current = source.get("status") or "active"
        target = "disabled" if action == "disable" else ("watch" if action == "watch" else "active")
        if target != current:
            actions.append({"name": record["name"], "action": action, "from": current,
                            "to": target, "reason": reason,
                            "evidence": {"age_hours": record.get("age_hours"),
                                         "items": record.get("items"),
                                         "error": record.get("error")}})
    # sources currently disabled but healthy again → propose revive
    disabled = [s for s in registry.get("sources", []) if s.get("status") == "disabled"]
    revived = []
    if disabled:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(disabled))) as pool:
            for rec in pool.map(check_source, disabled):
                if rec["ok"] and (rec.get("age_hours") is not None):
                    src = by_name.get(rec["name"]) or {}
                    _, frozen_h = _thresholds(src)
                    if rec["age_hours"] <= frozen_h:
                        revived.append({"name": rec["name"], "action": "revive", "from": "disabled",
                                        "to": "active", "reason": f"已恢复: 最新 {rec['age_hours']:.1f} 小时前",
                                        "evidence": {"items": rec["items"]}})
    actions.extend(revived)
    probes = [{"name": r["name"], "ok": r["ok"], "items": r["items"],
               "age_hours": r.get("age_hours"), "latest_item_at": r.get("latest_item_at"),
               "error": r.get("error")} for r in report["sources"]]
    summary = {"keep": 0, "watch": 0, "disable": 0, "revive": 0}
    for a in actions:
        summary[a["action"]] = summary.get(a["action"], 0) + 1
    return {
        "version": 1,
        "source_set": source_set,
        "generated_at": _now_iso(),
        "registry_sha256": digest,
        "requires_human_approval": True,
        "summary": {"checked": report["total"], "unchanged": report["total"] - len(actions),
                    **{k: v for k, v in summary.items() if v}},
        "probes": probes,
        "actions": actions,
    }


def apply_plan(plan_path: str, *, yes: bool = False) -> dict:
    """Apply a plan to its registry. Requires explicit human approval (`--yes`)."""
    if not yes:
        raise bad_arg(
            "apply 会修改源清单,需要人类批准",
            hint="先把 plan 汇报给人;确认后加 --yes 再执行。",
        )
    path = Path(plan_path)
    if not path.is_file():
        raise bad_arg(f"plan 文件不存在: {plan_path}")
    proposal = json.loads(path.read_text(encoding="utf-8"))
    source_set = proposal.get("source_set")
    if not source_set:
        raise bad_arg("plan 缺少 source_set")
    registry, reg_path, digest = load_registry(source_set)
    if proposal.get("registry_sha256") != digest:
        raise bad_arg(
            "plan 已过期:源清单在生成 plan 之后被改过",
            hint="重新跑 `ty garden plan` 生成新方案,再给人确认。",
        )
    by_name = {s.get("name"): s for s in registry.get("sources", [])}
    applied, skipped = [], []
    # metadata-only updates (never status) for every source the plan actually probed
    stamped = 0
    for probe in proposal.get("probes", []):
        source = by_name.get(probe.get("name"))
        if source is None:
            continue
        source["last_checked"] = _now_iso()
        source["last_items"] = probe.get("items")
        if probe.get("latest_item_at"):
            source["last_item_at"] = probe["latest_item_at"]
        if probe.get("ok"):
            source["fail_streak"] = 0
        else:
            source["fail_streak"] = int(source.get("fail_streak") or 0) + 1
        stamped += 1
    for action in proposal.get("actions", []):
        source = by_name.get(action.get("name"))
        if source is None:
            skipped.append({"name": action.get("name"), "reason": "源不在清单里"})
            continue
        source["status"] = action["to"]
        source["last_checked"] = _now_iso()
        source["garden_note"] = action.get("reason")
        applied.append({"name": source.get("name"), "to": action["to"],
                        "reason": action.get("reason")})
    backup = reg_path.with_suffix(f".json.bak-{datetime.now(CN_TZ):%Y%m%d-%H%M%S}")
    shutil.copy2(reg_path, backup)
    reg_path.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"applied": applied, "skipped": skipped, "stamped": stamped, "backup": str(backup),
            "registry": str(reg_path), "applied_at": _now_iso()}


def restore(snapshot: str, *, source_set: str, yes: bool = False) -> dict:
    if not yes:
        raise bad_arg("restore 会覆盖源清单,需要人类批准", hint="确认备份后加 --yes 执行")
    target = registry_path(source_set)
    snap = Path(snapshot)
    if not snap.is_file():
        raise bad_arg(f"备份不存在: {snapshot}")
    try:
        raw = snap.read_text(encoding="utf-8")
        restored = validate_registry(json.loads(raw))
    except (OSError, ValueError) as exc:
        raise bad_arg("备份不是可读取的源清单 JSON") from exc
    if restored.get("source_set") not in (None, source_set):
        raise bad_arg("备份属于其他源集合")
    backup = target.with_suffix(f".json.bak-{datetime.now(CN_TZ):%Y%m%d-%H%M%S-%f}")
    shutil.copy2(target, backup)
    target.write_text(raw, encoding="utf-8")
    _, _, digest = load_registry(source_set)
    return {"restored": True, "registry": str(target), "from": snapshot, "backup": str(backup),
            "registry_sha256": digest}


def status(source_set: str) -> dict:
    """Current registry state: what's active / watch / disabled, and when it was last checked."""
    registry, path, digest = load_registry(source_set)
    sources = registry.get("sources", [])
    counts: dict[str, int] = {}
    never_checked = 0
    for source in sources:
        status_value = source.get("status") or "active"
        counts[status_value] = counts.get(status_value, 0) + 1
        if not source.get("last_checked"):
            never_checked += 1
    return {"source_set": source_set, "registry": str(path), "registry_sha256": digest,
            "total": len(sources), "by_status": counts, "never_checked": never_checked,
            "generated_at": _now_iso()}


def render_report(report: dict, proposal: dict | None = None) -> str:
    """Human-readable markdown report (additive file; no registry mutation)."""
    lines = [f"# 源花园体检报告 · {report.get('source_set')}", "",
             f"> 检查时间:{report.get('checked_at')} | 源数:{report.get('total')} | "
             f"正常 {report['counts']['ok']} / 失败 {report['counts']['failed']}", ""]
    bad = [r for r in report["sources"] if not r["ok"]]
    if bad:
        lines += ["## 抓取失败", "", "| 源 | 错误 |", "|---|---|"]
        lines += [f"| {r['name']} | {str(r.get('error'))[:80]} |" for r in bad]
        lines.append("")
    ages = [(r["name"], r.get("age_hours")) for r in report["sources"] if r.get("age_hours") is not None]
    ages.sort(key=lambda x: x[1], reverse=True)
    lines += ["## 最新鲜度排序(最旧在前)", "", "| 源 | 最新条目距今(小时) |", "|---|---|"]
    lines += [f"| {n} | {a:.1f} |" for n, a in ages[:15]]
    lines.append("")
    if proposal:
        lines += ["## 变更方案(需人类批准后才可执行)", "",
                  f"共 {len(proposal.get('actions', []))} 项:", ""]
        if proposal.get("actions"):
            lines += ["| 动作 | 源 | 原因 |", "|---|---|---|"]
            lines += [f"| {a['action']} | {a['name']} | {a['reason']} |" for a in proposal["actions"]]
        else:
            lines.append("无需变更。")
        lines.append("")
        lines += ["批准后执行:", "",
                  "```bash",
                  f"ty garden apply --plan <plan.json> --yes",
                  "```"]
    return "\n".join(lines) + "\n"
