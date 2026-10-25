"""HTTP layer: throttling, retries, and translation of transport failures into
the contract's error codes.

Every upstream gets a minimum inter-request interval (Eastmoney rate-limits by IP:
>5 req/s or >=10 concurrent or >200/min triggers a temporary ban). Retries are for
*transient* failures only; a schema surprise is surfaced, never retried into silence.
"""
from __future__ import annotations

import random
import threading
import time
from urllib.parse import urlparse

import requests

from .errors import (
    Code, TanyuanError, auth_failed, not_found, rate_limited, schema_changed,
    unavailable,
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

DEFAULT_TIMEOUT = (8.0, 20.0)

# host -> minimum seconds between requests
_HOST_MIN_INTERVAL: dict[str, float] = {
    "api.fund.eastmoney.com": 0.50,
    "fundf10.eastmoney.com": 0.50,
    "fund.eastmoney.com": 0.50,
    "pdf.dfcfw.com": 0.30,
    "push2.eastmoney.com": 0.35,
    "push2his.eastmoney.com": 0.35,
    "push2ex.eastmoney.com": 0.35,
    "datacenter-web.eastmoney.com": 0.35,
    "reportapi.eastmoney.com": 0.35,
    "search-api-web.eastmoney.com": 0.35,
    "np-weblist.eastmoney.com": 0.35,
    "www.cninfo.com.cn": 0.40,
    "static.cninfo.com.cn": 0.20,
    "www.cls.cn": 0.50,
    "api-one-wscn.awtmt.com": 0.30,
    "qt.gtimg.cn": 0.10,
    "web.ifzq.gtimg.cn": 0.20,
    "proxy.finance.qq.com": 0.20,
    "ifzq.gtimg.cn": 0.20,
    "hq.sinajs.cn": 0.10,
    "basic.10jqka.com.cn": 0.50,
    "api.tushare.pro": 0.05,
    "api.tavily.com": 0.05,
    "api.deepseek.com": 0.02,
}

_EASTMONEY_HOSTS = tuple(h for h in _HOST_MIN_INTERVAL if "eastmoney" in h)

_THROTTLE_LOCK = threading.Lock()
_LAST_CALL: dict[str, float] = {}

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": UA, "Accept": "*/*"})


def _throttle(host: str) -> None:
    interval = _HOST_MIN_INTERVAL.get(host, 0.0)
    if interval <= 0:
        return
    with _THROTTLE_LOCK:
        now = time.monotonic()
        last = _LAST_CALL.get(host, 0.0)
        wait = last + interval - now
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[host] = time.monotonic()


def _retry_after(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def request(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    data=None,
    json_body=None,
    headers: dict | None = None,
    timeout=DEFAULT_TIMEOUT,
    max_retries: int = 2,
    allow_status: tuple[int, ...] = (),
) -> requests.Response:
    """Perform an HTTP request, raising TanyuanError on failure."""
    host = urlparse(url).hostname or ""
    merged_headers = {"User-Agent": UA}
    if headers:
        merged_headers.update(headers)
    attempts = max_retries + 1
    last_error: str | None = None

    for attempt in range(attempts):
        _throttle(host)
        try:
            resp = _SESSION.request(
                method,
                url,
                params=params,
                data=data,
                json=json_body,
                headers=merged_headers,
                timeout=timeout,
            )
        except requests.exceptions.SSLError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        except requests.exceptions.Timeout as exc:
            last_error = f"timeout: {exc}"
        except requests.exceptions.ConnectionError as exc:
            last_error = f"connection: {exc}"
        except requests.exceptions.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            status = resp.status_code
            if status in allow_status:
                return resp
            if status == 429:
                wait = _retry_after(resp)
                if attempt < attempts - 1:
                    time.sleep(wait if wait is not None else 0.8 * (2 ** attempt) + random.random() * 0.3)
                    continue
                raise rate_limited(f"{host} 返回 429(限流)", retry_after_s=wait)
            if status in (401, 403):
                if any(h in host for h in _EASTMONEY_HOSTS):
                    if attempt < attempts - 1:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    raise rate_limited(
                        f"{host} 返回 {status}(东财风控/限频)",
                        retry_after_s=30,
                    )
                raise auth_failed(f"{host} 返回 {status},鉴权失败或无权限")
            if status == 404:
                raise not_found(f"{host} 返回 404,资源不存在")
            if 500 <= status < 600:
                last_error = f"HTTP {status}"
            elif status >= 400:
                raise unavailable(
                    f"{host} 返回 HTTP {status}",
                    evidence=resp.text[:300] if resp.text else None,
                )
            else:
                return resp
        if attempt < attempts - 1:
            time.sleep(0.6 * (2 ** attempt) + random.random() * 0.3)
    raise unavailable(
        f"{host} 连续 {attempts} 次请求失败",
        evidence=last_error,
        hint="稍后重试;若持续失败,检查网络或切换备源。",
    )


def _decode(resp: requests.Response, encoding: str | None = None) -> str:
    if encoding:
        resp.encoding = encoding
    elif not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def get_json(url: str, *, params=None, headers=None, timeout=DEFAULT_TIMEOUT,
             encoding: str | None = None, allow_status=(), max_retries=2):
    resp = request("GET", url, params=params, headers=headers, timeout=timeout,
                   allow_status=allow_status, max_retries=max_retries)
    text = _decode(resp, encoding).strip()
    return _load_json(text, url)


def post_json(url: str, *, data=None, json_body=None, headers=None,
              timeout=DEFAULT_TIMEOUT, allow_status=(), max_retries=2):
    resp = request("POST", url, data=data, json_body=json_body, headers=headers,
                   timeout=timeout, allow_status=allow_status, max_retries=max_retries)
    return _load_json(_decode(resp).strip(), url)


def get_text(url: str, *, params=None, headers=None, timeout=DEFAULT_TIMEOUT,
             encoding: str | None = None, allow_status=(), max_retries=2) -> str:
    resp = request("GET", url, params=params, headers=headers, timeout=timeout,
                   allow_status=allow_status, max_retries=max_retries)
    return _decode(resp, encoding)


def _load_json(text: str, url: str):
    if not text:
        raise schema_changed(f"{url} 返回空 body", evidence="empty response")
    try:
        import json
        return json.loads(text)
    except ValueError as exc:
        raise schema_changed(
            f"{url} 返回的不是合法 JSON(可能被风控/改版)",
            evidence=f"{type(exc).__name__}: {exc} | body[:300]={text[:300]!r}",
        ) from exc


def jsonp_to_json(text: str) -> dict:
    """Eastmoney search endpoints wrap JSON in `cb(...)`. Reject anything else."""
    stripped = text.strip()
    start, end = stripped.find("("), stripped.rfind(")")
    if start < 0 or end <= start:
        raise schema_changed("JSONP 响应里找不到括号包裹体", evidence=stripped[:200])
    import json
    try:
        return json.loads(stripped[start + 1:end])
    except ValueError as exc:
        raise schema_changed("JSONP 内层不是合法 JSON", evidence=str(exc)) from exc
