"""Config: paths, secrets, cache location.

Secrets never enter logs or JSON output. Lookup order:
  1. process env
  2. $TANYUAN_ENV_FILE
  3. selected workspace/.env (no user-global or parent-directory fallback)
"""
from __future__ import annotations

import os
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]          # .../tanyuan

def workspace_root() -> Path:
    # Installed layout: <workspace>/.tanyuan/plugin/skills/tanyuan.
    if (SKILL_ROOT.parent.name == "skills" and SKILL_ROOT.parents[1].name == "plugin"
            and SKILL_ROOT.parents[2].name == ".tanyuan"
            and (SKILL_ROOT.parents[1] / ".tanyuan-install.json").is_file()):
        return SKILL_ROOT.parents[3]
    return Path.cwd()


CACHE_DIR = Path(os.environ.get("TANYUAN_CACHE") or (workspace_root() / ".tanyuan" / "cache"))
USAGE_LOG = CACHE_DIR / "usage.jsonl"

_DOTENV_LOADED = False
_DOTENV_VALUES: dict[str, str] = {}


def _parse_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                out[key] = val
    except OSError:
        pass
    return out


def _bootstrap() -> None:
    global _DOTENV_LOADED, _DOTENV_VALUES
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    selected = os.environ.get("TANYUAN_ENV_FILE")
    path = Path(selected).expanduser() if selected else workspace_root() / ".env"
    _DOTENV_VALUES = _parse_dotenv(path)


def secret(name: str) -> str | None:
    _bootstrap()
    val = os.environ.get(name, _DOTENV_VALUES.get(name))
    return val.strip() if val and val.strip() else None


def require_secret(name: str, *, what: str, how: str) -> str:
    val = secret(name)
    if not val:
        from .errors import auth_failed
        raise auth_failed(
            f"缺少 {name}({what})",
            hint=f"{how};或在当前 tanyuan 安装工作区的 .env 写入 {name}=...",
        )
    return val


def cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def now_iso() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
