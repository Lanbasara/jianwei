"""Tushare Pro client — thin, honest wrapper.

Tushare returns HTTP 200 even for permission/quota failures, so the only reliable
signal is `code != 0` plus the message. We translate those explicitly instead of
pretending "empty result" means "no data".
"""
from __future__ import annotations

import os
import time

from . import config, http
from .errors import Code, TanyuanError, auth_failed, bad_arg, rate_limited, unavailable

API_URL = "https://api.tushare.pro"
_MIN_INTERVAL = float(os.environ.get("TUSHARE_MIN_INTERVAL", "0.35"))
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    wait = _last_call + _MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def call(api_name: str, params: dict | None = None, fields: str = "") -> list[dict]:
    """Call a Tushare API and return rows as list[dict]."""
    token = config.require_secret(
        "TUSHARE_TOKEN", what="Tushare Pro token",
        how="export TUSHARE_TOKEN=... 或写进 ~/.config/tanyuan/.env",
    )
    _throttle()
    payload = {"api_name": api_name, "token": token, "params": params or {}, "fields": fields}
    try:
        body = http.post_json(API_URL, json_body=payload, timeout=(8, 30))
    except TanyuanError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise unavailable(f"Tushare 请求失败: {type(exc).__name__}", evidence=str(exc))

    code = body.get("code")
    msg = str(body.get("msg") or "")
    if code != 0:
        low = msg.lower()
        if "没有访问该接口的权限" in msg or "权限" in msg and "积分" in msg:
            raise auth_failed(
                f"Tushare 无权访问 {api_name}: {msg}",
                hint="该接口需要更高积分或单独购买权限;换免费源或升级账号。",
            )
        if "每分钟" in msg or "rate" in low or "限制" in msg:
            raise rate_limited(f"Tushare 限流: {msg}", retry_after_s=60)
        if "积分" in msg:
            raise auth_failed(f"Tushare 积分不足: {msg}")
        if "token" in low or "认证" in msg:
            raise auth_failed(f"Tushare token 无效: {msg}")
        raise bad_arg(f"Tushare 拒绝请求({api_name}): {msg}")

    data = body.get("data") or {}
    names = data.get("fields") or []
    items = data.get("items") or []
    rows = []
    for item in items:
        rows.append(dict(zip(names, item)))
    return rows


def probe(api_name: str = "stock_basic", params: dict | None = None) -> dict:
    """Used by diagnostics: returns ok/permission info without raising on permission."""
    try:
        rows = call(api_name, params or {"list_status": "L"}, "ts_code,name")
        return {"ok": True, "api": api_name, "rows": len(rows)}
    except TanyuanError as err:
        return {"ok": False, "api": api_name, "code": err.code.value, "message": err.message}
