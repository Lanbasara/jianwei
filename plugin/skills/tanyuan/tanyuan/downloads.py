"""Bounded public HTTPS downloads. No credentials, proxies or executable content.

DNS answers are checked and the connection pins a checked address, while TLS
still verifies the original hostname. Existing JSON request behavior is unchanged.
"""
from __future__ import annotations

import http.client as http_client
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urljoin, urlsplit

from . import http
from .errors import bad_arg, blocked, not_found, rate_limited, unavailable

MAX_BYTES = 32 * 1024 * 1024


def _target(url: str, *, deadline: float | None = None) -> tuple[str, str, list[str]]:
    if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 for c in url):
        raise bad_arg("下载 URL 无效或过长")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443) or parts.fragment):
            raise ValueError
        host = parts.hostname.encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        raise bad_arg("仅支持不含凭据和片段的公共 HTTPS 地址（443端口）") from None
    try:
        if deadline is None:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(a[4][0] for a in answers))
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OSError
            resolved = subprocess.run([sys.executable, str(Path(__file__).with_name("dns_worker.py")), host],
                                      check=True, capture_output=True, text=True, timeout=remaining,
                                      env={k: v for k, v in os.environ.items() if k in {"PATH", "SYSTEMROOT"}})
            addresses = json.loads(resolved.stdout)
            if not isinstance(addresses, list) or len(addresses) > 256:
                raise ValueError
    except (OSError, ValueError, subprocess.SubprocessError):
        raise unavailable("下载来源域名解析失败") from None
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise blocked("下载目标必须是公共网络地址，不访问本地或私有网络")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        path.encode("ascii")
    except UnicodeError:
        raise bad_arg("URL 路径与查询中的非ASCII字符需百分号编码") from None
    return host, path, addresses


class _PinnedHTTPS(http_client.HTTPSConnection):
    def __init__(self, host: str, address: str, timeout: float, *, deadline: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.deadline = deadline

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        self.sock = raw
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            raw.settimeout(min(self.timeout, remaining))
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            self.sock.do_handshake()
        except Exception:
            raw.close()
            raise


def get_bytes(url: str, *, headers: dict | None = None,
              max_bytes: int = MAX_BYTES, timeout: float = 45) -> tuple[bytes, str]:
    if not 1 <= max_bytes <= MAX_BYTES or not 1 <= timeout <= 120:
        raise bad_arg("下载大小或时间上限无效")
    request_headers = {"User-Agent": http.UA, "Accept-Encoding": "identity", "Accept": "*/*"}
    for key, value in (headers or {}).items():
        if not isinstance(key, str) or key.lower() not in {"referer", "accept", "user-agent"} or not isinstance(value, str) or len(value) > 4096 or any(
                ord(c) < 32 for c in value):
            raise bad_arg("下载请求不接受凭据或非白名单请求头")
        request_headers[key] = value
    started = time.monotonic()
    current = url
    for hop in range(4):
        host, path, addresses = _target(current, deadline=started + timeout)
        http._throttle(host)
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise unavailable("下载超过总时间上限")
        conn = _PinnedHTTPS(host, addresses[0], min(15, remaining), deadline=started + timeout)
        def abort():
            try:
                if conn.sock:
                    conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        timer = threading.Timer(remaining, abort)
        timer.daemon = True
        timer.start()
        try:
            conn.request("GET", path, headers=request_headers)
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location or hop == 3:
                    raise unavailable("下载重定向缺失或超过上限")
                current = urljoin(current, location)
                continue
            if response.status == 404:
                raise not_found("下载来源未找到该文件")
            if response.status in (401, 403):
                raise blocked("下载来源拒绝访问，不绕过访问控制")
            if response.status == 429:
                raise rate_limited("下载来源限流", retry_after_s=30)
            if response.status != 200:
                raise unavailable(f"下载来源返回 HTTP {response.status}")
            if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise unavailable("下载来源返回不支持的压缩传输，未解析")
            size = response.getheader("Content-Length")
            if size and (not size.isdigit() or int(size) > max_bytes):
                raise unavailable("下载文件超过大小上限或长度无效")
            chunks, total = [], 0
            while True:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise unavailable("下载超过总时间上限")
                if conn.sock:
                    conn.sock.settimeout(min(15, remaining))
                chunk = response.read1(min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise unavailable("下载文件超过大小上限")
                chunks.append(chunk)
            if size and total != int(size):
                raise unavailable("下载文件不完整")
            return b"".join(chunks), current
        except (OSError, http_client.HTTPException):
            raise unavailable("下载传输失败或超时") from None
        finally:
            timer.cancel()
            conn.close()
    raise unavailable("下载重定向超过上限")


def get_text(url: str, *, headers: dict | None = None, max_bytes: int = 8 * 1024 * 1024,
             timeout: float = 45, encoding: str = "utf-8") -> str:
    raw, _ = get_bytes(url, headers=headers, max_bytes=max_bytes, timeout=timeout)
    try:
        return raw.decode(encoding)
    except (UnicodeError, LookupError):
        raise unavailable("下载文本编码无效") from None
