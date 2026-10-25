"""Bound source-set names to the installed registry directory."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from .errors import bad_arg


def registry_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name):
        raise bad_arg("source_set 必须是源集合名", hint="使用 ty garden status --source-set <集合名>")
    root = root.resolve()
    path = (root / f"{name}.json").resolve()
    if path.parent != root:
        raise bad_arg("源集合必须位于源清单目录中")
    if not path.is_file():
        raise bad_arg(f"源集合不存在: {name}", hint=f"可用: {sorted(p.stem for p in root.glob('*.json'))}")
    return path


def validate_registry(payload: object) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        raise bad_arg("源清单必须是包含 sources 数组的对象")
    if not isinstance(payload.get("sectors", {}), dict):
        raise bad_arg("源清单 sectors 必须是对象")
    names = set()
    for source in payload["sources"]:
        if not isinstance(source, dict) or not isinstance(source.get("name"), str) or not source["name"]:
            raise bad_arg("每个新闻源必须有非空名称")
        if source["name"] in names:
            raise bad_arg("新闻源名称重复")
        names.add(source["name"])
        url = source.get("url")
        if not isinstance(url, str):
            raise bad_arg("新闻源 URL 必须是字符串")
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
        except ValueError:
            valid = False
        if not valid:
            raise bad_arg("新闻源必须使用不含凭据的 HTTP(S) 地址")
    return payload
