"""SQLite TTL cache + usage ledger.

Design notes:
- Cache key includes the *tool arguments*, so a PIT replay with a different as-of
  cannot accidentally reuse a newer answer.
- A cache hit must not refresh `as_of`: provenance keeps the original observation time.
- The usage ledger is append-only JSONL so cost/audit analysis needs no DB.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from . import config

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_LAST_PRUNE = 0.0

# Retention / size guards. Overridable by env so a long-running deployment can tune them.
CACHE_MAX_MB = int(os.environ.get("TANYUAN_CACHE_MAX_MB", "200"))
CACHE_MAX_VALUE_KB = int(os.environ.get("TANYUAN_CACHE_MAX_VALUE_KB", "512"))
USAGE_MAX_MB = float(os.environ.get("TANYUAN_USAGE_MAX_MB", "2"))
PRUNE_INTERVAL_S = 300


def _db() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        path = config.cache_dir() / "cache.sqlite3"
        _CONN = sqlite3.connect(str(path), check_same_thread=False)
        _CONN.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL,"
            " created_at REAL NOT NULL, expires_at REAL NOT NULL)"
        )
        _CONN.execute("CREATE INDEX IF NOT EXISTS idx_expires ON kv(expires_at)")
        _CONN.commit()
    return _CONN


def _maybe_auto_prune() -> None:
    """Cheap housekeeping: drop long-expired rows at most once every few minutes.

    Without this, expired entries are only removed when someone re-queries the same key,
    so a long-running deployment leaks disk (observed: 663 dead rows after one afternoon).
    """
    global _LAST_PRUNE
    now = time.time()
    if now - _LAST_PRUNE < PRUNE_INTERVAL_S:
        return
    _LAST_PRUNE = now
    try:
        conn = _db()
        conn.execute("DELETE FROM kv WHERE expires_at < ?", (now - 3600,))  # keep 1h grace
        conn.commit()
    except sqlite3.Error:
        pass


# Bump when tool semantics change (not just args), so old cached envelopes stop being
# served as if they came from the current implementation.
CACHE_VERSION = "v4"


def connection() -> sqlite3.Connection:
    """Shared connection (also used by blobs, so both live in one sqlite file)."""
    return _db()


def make_key(tool: str, args: dict) -> str:
    payload = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"{CACHE_VERSION}:{tool}:{digest}"


def get(key: str) -> tuple[dict | None, dict | None]:
    """Returns (cached_envelope_payload, cache_meta) or (None, None)."""
    conn = _db()
    row = conn.execute(
        "SELECT value, created_at, expires_at FROM kv WHERE key=?", (key,)
    ).fetchone()
    if not row:
        return None, None
    value, created_at, expires_at = row
    if expires_at < time.time():
        try:
            conn.execute("DELETE FROM kv WHERE key=?", (key,))
            conn.commit()
        except sqlite3.Error:
            pass
        return None, None
    payload = json.loads(value)
    payload["_cache_hit"] = True
    payload["_cache_created_at"] = created_at
    return payload, {"hit": True, "age_s": round(time.time() - created_at, 1)}


def put(key: str, value: dict, ttl_s: int) -> None:
    if ttl_s <= 0:
        return
    _maybe_auto_prune()
    payload = json.dumps(value, ensure_ascii=False, default=str)
    if len(payload) > CACHE_MAX_VALUE_KB * 1024:
        return                      # never let one huge response bloat the cache DB
    now = time.time()
    conn = _db()
    conn.execute(
        "INSERT OR REPLACE INTO kv (key, value, created_at, expires_at) VALUES (?,?,?,?)",
        (key, payload, now, now + ttl_s),
    )
    conn.commit()
    _enforce_size_cap(conn)


def invalidate(tool: str, matching_args: dict) -> None:
    """Discard cached views of a refreshed resource, including other page offsets."""
    conn = _db()
    rows = conn.execute("SELECT key,value FROM kv WHERE key LIKE ?",
                        (f"{CACHE_VERSION}:{tool}:%",)).fetchall()
    keys = []
    for key, value in rows:
        try:
            args = json.loads(value).get("args", {})
        except (ValueError, AttributeError):
            keys.append((key,))
            continue
        if all(args.get(k) == v for k, v in matching_args.items()):
            keys.append((key,))
    conn.executemany("DELETE FROM kv WHERE key=?", keys)
    conn.commit()


def _enforce_size_cap(conn: sqlite3.Connection) -> None:
    path = config.cache_dir() / "cache.sqlite3"
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
    except OSError:
        return
    if size_mb <= CACHE_MAX_MB:
        return
    conn.execute(
        "DELETE FROM kv WHERE key IN (SELECT key FROM kv ORDER BY created_at ASC LIMIT 200)"
    )
    conn.commit()


def prune(*, max_age_days: float | None = None, vacuum: bool = True) -> dict:
    """Delete expired rows (and optionally rows older than N days), then compact the DB."""
    conn = _db()
    now = time.time()
    expired = conn.execute("DELETE FROM kv WHERE expires_at < ?", (now,)).rowcount
    aged = 0
    if max_age_days is not None:
        aged = conn.execute("DELETE FROM kv WHERE created_at < ?",
                            (now - max_age_days * 86400,)).rowcount
    conn.commit()
    before = stats()["cache_mb"]
    if vacuum and (expired + aged) > 0:
        try:
            conn.execute("VACUUM")
            conn.commit()
        except sqlite3.Error:
            pass
    after = stats()["cache_mb"]
    return {"deleted_expired": expired, "deleted_aged": aged,
            "size_mb_before": before, "size_mb_after": after}


def stats() -> dict:
    """Footprint of everything this skill writes, for `ty doctor` / `ty cache stats`."""
    base = config.cache_dir()
    conn = _db()
    now = time.time()
    rows = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(value)),0) FROM kv").fetchone()
    expired = conn.execute("SELECT COUNT(*) FROM kv WHERE expires_at < ?", (now,)).fetchone()[0]
    usage = base / "usage.jsonl"
    files = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        files.append({"path": str(path.relative_to(base)),
                      "mb": round(path.stat().st_size / (1024 * 1024), 3)})
    total_mb = round(sum(f["mb"] for f in files), 3)
    return {
        "dir": str(base),
        "total_mb": total_mb,
        "cache_mb": round((base / "cache.sqlite3").stat().st_size / 1024 / 1024, 3)
        if (base / "cache.sqlite3").exists() else 0,
        "cache_rows": rows[0],
        "cache_value_mb": round(rows[1] / 1024 / 1024, 3),
        "expired_rows": expired,
        "usage_mb": round(usage.stat().st_size / 1024 / 1024, 3) if usage.exists() else 0,
        "limits": {"cache_max_mb": CACHE_MAX_MB, "max_value_kb": CACHE_MAX_VALUE_KB,
                   "usage_max_mb": USAGE_MAX_MB},
        "files": files[:20],
    }


def clear_all(*, kv: bool = True, blobs: bool = True, usage: bool = True,
              reference: bool = False) -> dict:
    """One-shot cleanup used by `ty cache clear`.

    `reference` covers the regenerable JSON maps (stock list, CIK map, org map) — they cost
    one network call each to rebuild.
    """
    result: dict = {}
    if kv:
        result["kv_rows"] = clear()
    if blobs:
        from . import blobs as _blobs
        result["blobs"] = _blobs.purge()
    if usage:
        path = config.cache_dir() / "usage.jsonl"
        removed = 0
        for candidate in (path, path.with_suffix(".jsonl.1")):
            if candidate.exists():
                try:
                    candidate.unlink()
                    removed += 1
                except OSError:
                    pass
        result["usage_files"] = removed
    if reference:
        removed = 0
        for name in ("stock_basic.json", "stock_basic_v2.json", "cninfo_orgmap.json",
                     "sec_cik_map.json"):
            path = config.cache_dir() / name
            if path.exists():
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        result["reference_files"] = removed
    return result


def clear(prefix: str | None = None) -> int:
    conn = _db()
    if prefix:
        cur = conn.execute("DELETE FROM kv WHERE key LIKE ?", (prefix + "%",))
    else:
        cur = conn.execute("DELETE FROM kv")
    conn.commit()
    return cur.rowcount


def log_usage(record: dict) -> None:
    """Append one call to the ledger. Never raises (audit must not break the call).

    The ledger rotates at USAGE_MAX_MB (keeps one previous file), so it cannot grow forever.
    """
    try:
        path = config.cache_dir() / "usage.jsonl"
        if path.exists() and path.stat().st_size > USAGE_MAX_MB * 1024 * 1024:
            rotated = path.with_suffix(".jsonl.1")
            try:
                if rotated.exists():
                    rotated.unlink()
                path.rename(rotated)
            except OSError:
                pass
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass
