"""Content-addressed blob store for long text.

Long documents are stored once as content-addressed text. The response provides a
preview, path and character offsets for local reading or continuation calls.

Design rules:
- the blob is the source of truth for "full text"; stdout is only a preview;
- content-addressed (sha256) so repeated fetches dedupe;
- URL index lets `--offset` paging avoid re-hitting upstream;
- LRU size cap so the cache cannot grow unbounded.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from pathlib import Path

from . import cache, config

MAX_STORE_CHARS = 8_000_000          # safety valve, not a normal limit
GC_MAX_BYTES = 512 * 1024 * 1024     # total blob dir budget
GC_PROBABILITY = 0.02


def blob_dir() -> Path:
    path = config.cache_dir() / "blobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _conn():
    conn = cache.connection()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS blobs ("
        " sha TEXT PRIMARY KEY, url TEXT, path TEXT, chars INTEGER, bytes INTEGER,"
        " kind TEXT, title TEXT, created_at REAL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_blobs_url ON blobs(url, created_at)")
    conn.commit()
    return conn


def store(text: str, *, url: str | None = None, kind: str = "text",
          title: str | None = None) -> dict:
    """Persist text; returns {path, sha256, chars, bytes, clipped}. Never raises on IO."""
    original = len(text)
    clipped = False
    if original > MAX_STORE_CHARS:
        from .errors import unavailable
        raise unavailable("正文超过本地全文存储上限", hint="使用来源提供的分卷或较小文档")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    created_at = time.time()
    try:
        target = blob_dir() / digest[:2] / f"{digest}.txt"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO blobs (sha,url,path,chars,bytes,kind,title,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (digest, url, str(target), len(text), target.stat().st_size if target.is_file() else 0,
             kind, title, created_at),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - blob persistence must never break a tool call
        return {"path": None, "sha256": digest, "chars": len(text), "bytes": None,
                "clipped": clipped, "persisted": False, "created_at": created_at}
    if random.random() < GC_PROBABILITY:
        try:
            gc()
        except Exception:  # noqa: BLE001
            pass
    return {"path": str(target), "sha256": digest, "chars": len(text),
            "bytes": target.stat().st_size, "clipped": clipped, "persisted": True,
            "created_at": created_at}


def latest_for_url(url: str) -> dict | None:
    try:
        row = _conn().execute(
            "SELECT sha,path,chars,kind,title,created_at FROM blobs"
            " WHERE url=? ORDER BY created_at DESC LIMIT 1", (url,)
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    sha, path, chars, kind, title, created = row
    if not path or not Path(path).is_file():
        return None
    return {"sha256": sha, "path": path, "chars": chars, "kind": kind,
            "title": title, "created_at": created}


def read(sha_or_path: str, offset: int = 0, limit: int | None = None) -> tuple[str, int]:
    """Read a slice of a stored blob. Returns (slice, total_chars)."""
    path = Path(sha_or_path)
    if not path.is_file():
        row = _conn().execute("SELECT path FROM blobs WHERE sha=?", (sha_or_path,)).fetchone()
        if not row:
            raise FileNotFoundError(sha_or_path)
        path = Path(row[0])
    text = path.read_text(encoding="utf-8", errors="replace")
    total = len(text)
    offset = max(0, min(offset, total))
    if limit is None:
        return text[offset:], total
    return text[offset:offset + limit], total


def gc(max_bytes: int = GC_MAX_BYTES) -> dict:
    """Delete oldest blobs until the directory is under budget."""
    root = blob_dir()
    files = [(p.stat().st_mtime, p.stat().st_size, p) for p in root.rglob("*.txt") if p.is_file()]
    total = sum(size for _, size, _ in files)
    if total <= max_bytes:
        return {"removed": 0, "bytes": total}
    removed = 0
    for _, size, path in sorted(files):
        if total <= max_bytes:
            break
        try:
            path.unlink()
            removed += 1
            total -= size
        except OSError:
            pass
    try:
        conn = _conn()
        keep = {str(p) for p in root.rglob("*.txt") if p.is_file()}
        for sha, path in conn.execute("SELECT sha,path FROM blobs").fetchall():
            if path not in keep:
                conn.execute("DELETE FROM blobs WHERE sha=?", (sha,))
        conn.commit()
    except Exception:  # noqa: BLE001
        pass
    return {"removed": removed, "bytes": total}


def purge(*, older_than_days: float | None = None) -> dict:
    """Delete blobs (and their index rows). `older_than_days=None` purges everything."""
    cutoff = time.time() - older_than_days * 86400 if older_than_days is not None else None
    removed = 0
    freed = 0
    conn = _conn()
    for sha, path, created in conn.execute("SELECT sha,path,created_at FROM blobs").fetchall():
        if cutoff is not None and (created or 0) >= cutoff:
            continue
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = 0
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
        conn.execute("DELETE FROM blobs WHERE sha=?", (sha,))
        removed += 1
        freed += size
    conn.commit()
    result = {"removed": removed, "freed_mb": round(freed / 1024 / 1024, 3)}
    if (config.cache_dir() / "documents").exists():
        from . import documents
        result["documents"] = documents.purge(older_than_days=older_than_days)
    return result


def stats() -> dict:
    root = blob_dir()
    files = [p for p in root.rglob("*.txt") if p.is_file()]
    return {"count": len(files), "bytes": sum(p.stat().st_size for p in files)}
