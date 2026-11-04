"""Offline bili-source consumer contract: synthetic transport only, no sockets."""
from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tanyuan"))
from tanyuan import bili_source, blobs, cache, cli, config, registry  # noqa: E402
from tanyuan.bili_source import BiliSourceClient  # noqa: E402
from tanyuan.errors import TanyuanError  # noqa: E402
from tanyuan.tools import bili  # noqa: E402

ORIGIN = "https://bili-source.example.test"
TOKEN = "a" * 64  # Synthetic credential, never a real account value.
CODE = "ABCDEF2345"
JOB = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
VERSION = "33333333-3333-4333-8333-333333333333"
CONTENT = "44444444-4444-4444-8444-444444444444"
STAMP = "2026-11-05T00:00:00.000Z"
EXPIRY = "2099-11-05T00:00:00.000Z"
SOURCE = "https://www.bilibili.com/opus/10001"
DELIVERY_BYTES = 256 * 1024


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


class Service:
    """Independent service-side fixture; persist admissions, not client results."""
    def __init__(self):
        self.calls = []
        self.token = TOKEN
        self.now = 1793836800.0
        self.approved = True
        self.revoked = False
        self.expired = False
        self.complete = False
        self.query_state = "waiting_budget"
        self.admissions = {}
        self.drop_acceptance_once = False
        self.fail_content_at = None
        self.metadata_title = "合成标题"
        self.chunks = ["合成正文🙂【末尾】"]
        self.override = None

    def sleep(self, seconds):
        self.now += seconds

    def query_view(self):
        return {"id": JOB, "state": "partial" if self.complete else self.query_state,
                "reason": None if self.complete else "request_schedule", "created_at": STAMP,
                "deadline_at": EXPIRY, "next_attempt_at": None if self.complete else STAMP,
                "pool": {"id": "default", "label": "合成来源池", "revision": 1,
                         "sources": [{"uid": "123", "label": "合成 UP"}]},
                "interval": {"from": "2024-07-01T00:00:00.000Z", "until": "2024-07-03T00:00:00.000Z"},
                "coverage": [{"uid": "123", "coverage": "partial", "done": self.complete, "exhausted": self.complete,
                              "cursor": None, "seen": [], "pages": 1, "observed_at": STAMP,
                              "gaps": [{"code": "upstream_snapshot_not_guaranteed"}]}],
                "counts": [], "unknown_remaining": True,
                "result_version": VERSION if self.complete else None,
                "result_expires_at": EXPIRY if self.complete else None,
                "result_state": "expired" if self.expired else "available" if self.complete else "pending",
                "idempotency_expires_at": EXPIRY if self.complete else None,
                "retention": {"result_ttl_ms": 60000, "record_ttl_ms": 120000},
                "capabilities": {"image": "controlled_resources", "video_text": "existing_subtitles"}}

    def transport(self, method, url, *, headers, body, timeout, max_bytes):
        if not url.startswith(ORIGIN + "/"):
            raise AssertionError("fixture refuses a different origin")
        if not (0 < timeout <= 60 and 0 < max_bytes <= 4 * 1024 * 1024):
            raise AssertionError("transport must be bounded")
        request = None if body is None else json.loads(body.decode("utf-8"))
        path = urlsplit(url).path
        self.calls.append({"method": method, "path": path, "request": request, "headers": dict(headers)})
        if any(key.lower() == "cookie" for key in headers):
            raise AssertionError("Bilibili cookies never belong in this client")
        if self.override:
            result = self.override(method, path, request)
            if result is not None:
                return result
        if path == "/api/auth/pair":
            return 202, {"content-type": "application/json"}, encode({"ok": True, "device_token": self.token,
                "code": CODE, "expires_at": EXPIRY, "interval_seconds": 5, "verification_uri": ORIGIN + "/login"})
        if path == "/api/auth/pair-status":
            if request != {"device_token": self.token}:
                raise AssertionError("pair polling must use the durable device credential")
            return 200, {"content-type": "application/json"}, encode({"ok": True, **(
                {"state": "approved", "client_id": CLIENT, "pool_ids": ["default"], "expires_at": EXPIRY}
                if self.approved else {"state": "pending"})})
        authorization = next((value for key, value in headers.items() if key.lower() == "authorization"), None)
        if authorization != "Bearer " + self.token:
            raise AssertionError("consumer request must use its own paired service credential")
        if self.revoked:
            return self.error(401, "AUTH_CLIENT")
        if path == "/api/v1/pools":
            if method != "GET":
                raise AssertionError("machine clients only discover pools")
            return self.reply({"pools": [self.query_view()["pool"]]})
        if path == "/api/v1/queries":
            key = request["idempotency_key"]
            self.admissions.setdefault(key, JOB)
            if self.drop_acceptance_once:
                self.drop_acceptance_once = False
                raise OSError("synthetic transport exception with private marker " + TOKEN)
            return 202, {"content-type": "application/json"}, encode({"ok": True, "query": self.query_view()})
        if path == "/api/v1/query":
            return self.reply({"query": self.query_view()})
        if path == "/api/v1/result":
            if self.expired:
                return self.error(410, "RESULT_EXPIRED")
            if request["version"] != VERSION:
                return self.error(409, "RESULT_VERSION")
            items = [] if request.get("offset", 0) else [{"id": "10001", "uid": "123", "state": "partial",
                "content_version": CONTENT, "source_url": SOURCE, "published_at": STAMP,
                "error": None, "title": self.metadata_title}]
            return self.reply({"result": {"version": VERSION, "items": items, "next_offset": None}})
        if path == "/api/v1/content":
            if self.expired:
                return self.error(410, "RESULT_EXPIRED")
            offset = request["offset"]
            if offset == self.fail_content_at:
                raise OSError("synthetic unread-chunk failure " + TOKEN)
            position = 0
            for index, chunk in enumerate(self.chunks):
                if position <= offset < position + utf16_length(chunk):
                    # A client may stop inside a service response when its own
                    # delivery budget is exhausted. Decode strictly so a split
                    # surrogate is a failed fixture contract, never repaired.
                    remaining = chunk.encode("utf-16-le")[(offset-position)*2:].decode("utf-16-le")
                    return self.reply({"content": {"content_version": CONTENT, "text": remaining,
                        "next_offset": None if index == len(self.chunks) - 1 else position + utf16_length(chunk),
                        "observed_at": STAMP, "source_url": SOURCE, "content_state": "detail_received_unverified",
                        "published_at": STAMP, "images": ["https://i0.hdslb.com/bfs/new_dyn/synthetic.png"],
                        "gaps": [{"code": "original_not_verified"}], "image_state": "links_only_not_downloaded_or_read"}})
                position += utf16_length(chunk)
            raise AssertionError("client sent an out-of-range UTF-16 content cursor")
        raise AssertionError("unexpected service route")

    @staticmethod
    def reply(data):
        return 200, {"content-type": "application/json"}, encode({"ok": True, **data})

    @staticmethod
    def error(status, code):
        return status, {"content-type": "application/json"}, encode({"ok": False, "error": {
            "code": code, "message": "synthetic upstream detail must be discarded " + TOKEN}})


class BiliClientTests(unittest.TestCase):
    def setUp(self):
        registry.load_tools()
        self.tmp = tempfile.TemporaryDirectory(prefix="tanyuan-bili-qa-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.base.chmod(0o700)
        self.state_file = self.base / "credential.json"
        self.service = Service()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(config, "CACHE_DIR", self.base / "cache"))
        self.stack.enter_context(patch.object(config, "USAGE_LOG", self.base / "usage.jsonl"))
        self.stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("real network forbidden")))
        self.stack.enter_context(patch.object(bili, "_client", side_effect=self.client))

    def client(self):
        return BiliSourceClient(ORIGIN, str(self.state_file), transport=self.service.transport,
            now=lambda: self.service.now, clock=lambda: self.service.now, sleep=self.service.sleep)

    def invoke(self, tool, args=None):
        out, err = io.StringIO(), io.StringIO()
        argv = ["call", tool, "--json", json.dumps(args or {}, ensure_ascii=False)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(argv)
        self.assertEqual(err.getvalue(), "")
        raw = out.getvalue()
        result = json.loads(raw)  # Also rejects a second JSON or prose prefix/suffix.
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn(self.service.token, raw)
        self.assertLessEqual(len(raw.encode("utf-8")), DELIVERY_BYTES)
        return status, result

    def approve(self):
        status, first = self.invoke("bili.pair", {"wait_ms": 0})
        self.assertEqual(status, 0, first)
        self.service.now += 5
        status, second = self.invoke("bili.pair", {"resume": True, "wait_ms": 1000})
        self.assertEqual(status, 0, second)
        self.assertEqual(second["data"]["state"], "approved")

    @staticmethod
    def query_args():
        return {"pool_id": "default", "idempotency_key": "synthetic-stable-key", "last_days": 2, "wait_ms": 0}

    def test_discovery_is_offline_and_never_reads_credentials(self):
        with patch.object(config, "secret", side_effect=AssertionError("credentials read")), \
                patch.object(bili, "_client", side_effect=AssertionError("client constructed")):
            for args in [["tools"], *[["describe", name] for name in ("bili.pair", "bili.pools", "bili.query", "bili.get")]]:
                with self.subTest(args=args), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(cli.main(args), 0)
                self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertFalse(self.state_file.exists())
        for name in ("bili.pair", "bili.pools", "bili.query", "bili.get"):
            spec = registry.REGISTRY[name]
            self.assertEqual(spec.grade, "L3")
            self.assertEqual(spec.cache_ttl_s, 0)
            self.assertFalse({"url", "origin", "token", "cookie", "state_file"}.intersection(spec.args_schema))

    def test_pair_returns_code_before_approval_and_persists_credential_privately(self):
        self.service.approved = False
        status, result = self.invoke("bili.pair")
        self.assertEqual(status, 0, result)
        self.assertIn(CODE, json.dumps(result))
        self.assertIn(ORIGIN + "/login", json.dumps(result))
        self.assertEqual([call["path"] for call in self.service.calls], ["/api/auth/pair"])
        self.assertEqual(stat.S_IMODE(self.state_file.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.state_file.parent.stat().st_mode), 0o700)
        self.assertIn(TOKEN, self.state_file.read_text(encoding="utf-8"))
        self.service.now += 5
        self.invoke("bili.pair", {"resume": True, "wait_ms": 0})
        self.assertEqual(len([call for call in self.service.calls if call["path"] == "/api/auth/pair"]), 1)

    def test_202_and_waiting_states_are_pending_observations_not_failures(self):
        self.approve()
        for state in ("queued", "waiting_login", "waiting_budget", "cooling_down"):
            self.service.query_state = state
            status, result = self.invoke("bili.query", self.query_args())
            self.assertEqual(status, 0, result)
            self.assertEqual(result["data"]["read_state"], "pending")
            self.assertEqual(result["data"]["handle"]["id"], JOB)
            self.assertEqual(result["data"]["query"]["state"], state)
            self.assertEqual(result["provenance"]["source_grade"], "L3")
        self.assertEqual(len(self.service.admissions), 1)

    def test_explicit_restart_replaces_local_pairing_without_inheriting_previous_approval(self):
        self.approve()
        self.assertNotEqual(self.invoke("bili.pair")[0], 0)
        self.service.token = "b" * 64
        status, result = self.invoke("bili.pair", {"restart": True})
        self.assertEqual(status, 0, result)
        self.assertEqual(result["data"]["state"], "pending")
        saved = self.state_file.read_text(encoding="utf-8")
        self.assertIn(self.service.token, saved); self.assertNotIn(TOKEN, saved)
        before = len(self.service.calls)
        self.assertNotEqual(self.invoke("bili.pools")[0], 0)
        self.assertEqual(len(self.service.calls), before)
        self.service.now += 5
        self.assertEqual(self.invoke("bili.pair", {"resume": True, "wait_ms": 1000})[0], 0)
        self.assertEqual(self.invoke("bili.pools")[0], 0)
        self.assertEqual(len([call for call in self.service.calls if call["path"] == "/api/auth/pair"]), 2)

    def test_lost_acceptance_retries_the_same_idempotency_key_without_leaking_transport_details(self):
        self.approve(); self.service.drop_acceptance_once = True
        self.invoke("bili.query", self.query_args())
        status, result = self.invoke("bili.query", self.query_args())
        self.assertEqual(status, 0, result)
        self.assertEqual(result["data"]["handle"]["id"], JOB)
        requests = [call["request"] for call in self.service.calls if call["path"] == "/api/v1/queries"]
        self.assertGreaterEqual(len(requests), 2)
        self.assertEqual({item["idempotency_key"] for item in requests}, {"synthetic-stable-key"})
        self.assertEqual(len(self.service.admissions), 1)

    def test_long_emoji_content_continues_across_invocations_using_exact_utf16_cursors(self):
        self.approve(); self.service.complete = True
        self.service.chunks = [("文🙂" * 4000) + f"【段{index}尾】" for index in range(14)]
        expected = "".join(self.service.chunks)
        args = {"id": JOB}; received = []; cursor_states = []
        for _ in range(8):
            status, result = self.invoke("bili.get", args)
            self.assertEqual(status, 0, result)
            data = result["data"]
            received.extend(item["content"]["text"] for item in data["items"] if item.get("content"))
            self.assertEqual(data["query"]["state"], "partial")
            self.assertTrue(data["query"]["unknown_remaining"])
            for item in data["items"]:
                if item.get("content"):
                    self.assertEqual(item["content"]["content_state"], "detail_received_unverified")
                    self.assertEqual(item["content"]["source_url"], SOURCE)
            if data["read_state"] == "read_complete":
                break
            self.assertEqual(data["read_state"], "read_partial")
            cursor = data["handle"]["cursor"]
            self.assertEqual(cursor["version"], VERSION)
            self.assertNotIn(cursor, cursor_states)
            cursor_states.append(cursor)
            args = {"id": JOB, "cursor": cursor}
        else:
            self.fail("bounded continuation never completed")
        self.assertTrue(cursor_states, "fixture must exceed one delivery budget")
        self.assertEqual("".join(received), expected)
        offsets = [call["request"]["offset"] for call in self.service.calls if call["path"] == "/api/v1/content"]
        valid_offsets, position = {0}, 0
        for character in expected:
            position += utf16_length(character)
            valid_offsets.add(position)
        self.assertTrue(all(offset in valid_offsets for offset in offsets))

    def test_mid_read_failure_preserves_obtained_text_and_resume_starts_at_unread_offset(self):
        self.approve(); self.service.complete = True
        self.service.chunks = ["已取得🙂【第一段末尾】", "恢复读取🙂【第二段末尾】"]
        self.service.fail_content_at = utf16_length(self.service.chunks[0])
        status, result = self.invoke("bili.get", {"id": JOB})
        self.assertEqual(status, 0, result)
        self.assertEqual(result["data"]["read_state"], "read_partial")
        self.assertEqual("".join(item["content"]["text"] for item in result["data"]["items"] if item.get("content")), self.service.chunks[0])
        cursor = result["data"]["handle"]["cursor"]
        self.assertEqual(cursor["content_offset"], self.service.fail_content_at)
        self.service.fail_content_at = None
        status, resumed = self.invoke("bili.get", {"id": JOB, "cursor": cursor})
        self.assertEqual(status, 0, resumed)
        self.assertEqual(resumed["data"]["read_state"], "read_complete")
        self.assertEqual("".join(item["content"]["text"] for item in resumed["data"]["items"] if item.get("content")), self.service.chunks[1])

    def test_request_budget_is_independent_of_byte_budget_and_retains_a_progressing_cursor(self):
        self.approve(); self.service.complete = True
        self.service.chunks = [f"{index}🙂" for index in range(70)]
        before = len(self.service.calls)
        status, result = self.invoke("bili.get", {"id": JOB})
        self.assertEqual(status, 0, result)
        self.assertLessEqual(len(self.service.calls) - before, 64)
        self.assertEqual(result["data"]["read_state"], "read_partial")
        cursor = result["data"]["handle"]["cursor"]
        self.assertGreater(cursor["content_offset"], 0)
        first = "".join(item["content"]["text"] for item in result["data"]["items"] if item.get("content"))
        status, second = self.invoke("bili.get", {"id": JOB, "cursor": cursor})
        self.assertEqual(status, 0, second); self.assertEqual(second["data"]["read_state"], "read_complete")
        tail = "".join(item["content"]["text"] for item in second["data"]["items"] if item.get("content"))
        self.assertEqual(first + tail, "".join(self.service.chunks))

    def test_expiration_between_content_chunks_keeps_delivered_text_and_the_original_version(self):
        self.approve(); self.service.complete = True
        self.service.chunks = ["到期前已取得🙂【末尾】", "到期后不能读取"]
        unread = utf16_length(self.service.chunks[0])
        self.service.override = lambda method, path, body: Service.error(410, "RESULT_EXPIRED") \
            if path == "/api/v1/content" and body["offset"] == unread else None
        status, result = self.invoke("bili.get", {"id": JOB})
        self.assertEqual(status, 0, result)
        self.assertEqual(result["data"]["read_state"], "expired")
        self.assertEqual(result["data"]["query"]["result_version"], VERSION)
        self.assertEqual(result["data"]["handle"]["cursor"]["content_offset"], unread)
        self.assertEqual("".join(item["content"]["text"] for item in result["data"]["items"] if item.get("content")), self.service.chunks[0])
        self.assertFalse(any(call["path"] == "/api/v1/queries" for call in self.service.calls))

    def test_result_denials_without_delivered_items_keep_error_classification(self):
        self.approve(); self.service.complete = True
        cases = [(401, "AUTH_CLIENT", "AUTH_FAILED"), (403, "POOL_DENIED", "BLOCKED"),
                 (404, "JOB_NOT_FOUND", "NOT_FOUND"), (429, "CAPACITY", "QUOTA_EXCEEDED"),
                 (409, "RESULT_VERSION", "BAD_ARGUMENT")]
        for http_status, code, category in cases:
            self.service.override = lambda method, path, body, s=http_status, c=code: \
                Service.error(s, c) if path == "/api/v1/result" else None
            status, result = self.invoke("bili.get", {"id": JOB})
            self.assertNotEqual(status, 0, result)
            self.assertEqual(result["error"]["code"], category)
            self.assertEqual(result["error"]["details"]["handle"]["id"], JOB)

    def test_nonretryable_midread_errors_preserve_text_without_automatic_next(self):
        self.approve(); self.service.complete = True
        self.service.chunks = ["已取得🙂【末尾】", "不应取得"]
        unread = utf16_length(self.service.chunks[0])
        for http_status, code, category in [(401, "AUTH_CLIENT", "AUTH_FAILED"), (403, "POOL_DENIED", "BLOCKED"),
                                          (404, "JOB_NOT_FOUND", "NOT_FOUND"), (409, "RESULT_VERSION", "BAD_ARGUMENT")]:
            self.service.override = lambda method, path, body, s=http_status, c=code: Service.error(s, c) \
                if path == "/api/v1/content" and body["offset"] == unread else None
            status, result = self.invoke("bili.get", {"id": JOB})
            self.assertEqual(status, 0, result)
            self.assertEqual(result["data"]["read_state"], "read_partial")
            self.assertEqual(result["data"]["items"][0]["content"]["text"], self.service.chunks[0])
            self.assertEqual(result["data"]["handle"]["cursor"]["content_offset"], unread)
            self.assertEqual(result["data"]["read_error"]["code"], category)
            self.assertFalse(result["data"]["read_error"]["retryable"])
            self.assertTrue(result["data"]["read_error"]["hint"])
            self.assertFalse(result.get("next"))

    def test_published_legacy_coverage_up_to_202_gaps_remains_readable(self):
        self.approve()
        for count in [200, 201, 202]:
            view = self.service.query_view()
            view["coverage"][0]["gaps"] = [{"code": "item_gap"} for _ in range(count)]
            self.service.override = lambda method, path, body, q=view: Service.reply({"query": q}) \
                if path == "/api/v1/query" else None
            status, result = self.invoke("bili.get", {"id": JOB, "wait_ms": 0})
            self.assertEqual(status, 0, result)
            self.assertEqual(len(result["data"]["query"]["coverage"][0]["gaps"]), count)

    def test_revocation_and_expiration_are_observed_again_without_response_cache_or_blob_writes(self):
        self.approve(); self.service.complete = True
        with patch.object(cache, "get", side_effect=AssertionError("response cache read")), \
                patch.object(cache, "put", side_effect=AssertionError("response cache write")), \
                patch.object(blobs, "store", side_effect=AssertionError("body blob write")):
            self.assertEqual(self.invoke("bili.get", {"id": JOB})[0], 0)
            self.service.expired = True
            status, expired = self.invoke("bili.get", {"id": JOB, "wait_ms": 0})
            self.assertEqual(status, 0, expired)
            self.assertEqual(expired["data"]["read_state"], "expired")
            self.service.revoked = True
            status, revoked = self.invoke("bili.pools")
            self.assertNotEqual(status, 0)
            self.assertEqual(revoked["error"]["code"], "AUTH_FAILED")
        self.assertEqual(len([call for call in self.service.calls if call["path"] == "/api/auth/pair"]), 1)
        for path in self.base.rglob("*"):
            if path.is_file() and path != self.state_file:
                self.assertNotIn(TOKEN, path.read_text(encoding="utf-8"))
                self.assertNotIn("合成正文", path.read_text(encoding="utf-8"))

    def test_redirect_response_is_not_followed_and_sensitive_error_body_is_not_echoed(self):
        self.approve(); before = len(self.service.calls)
        self.service.override = lambda *_: (302, {"location": "https://evil.example.test/steal", "content-type": "text/html"}, TOKEN.encode())
        status, result = self.invoke("bili.pools")
        self.assertNotEqual(status, 0)
        self.assertEqual(len(self.service.calls), before + 1)
        self.assertNotIn("evil.example", json.dumps(result))

    def test_source_errors_keep_distinct_existing_taxonomy(self):
        self.approve()
        cases = [(403, "POOL_DENIED", "BLOCKED"), (404, "JOB_NOT_FOUND", "NOT_FOUND"),
                 (409, "RESULT_VERSION", "BAD_ARGUMENT"), (429, "CAPACITY", "QUOTA_EXCEEDED")]
        for status, service_code, expected in cases:
            self.service.override = lambda *_, s=status, c=service_code: Service.error(s, c)
            exit_code, result = self.invoke("bili.get", {"id": JOB, "wait_ms": 0})
            self.assertNotEqual(exit_code, 0)
            self.assertEqual(result["error"]["code"], expected)
            self.assertEqual(result["error"]["details"]["service_code"], service_code)

    def test_malformed_and_excessive_responses_fail_as_protocol_errors_without_body_echoes(self):
        self.approve()
        for raw in [b'{"ok":true,"ok":false}', encode({"ok": True, "unexpected": "x" * (1024 * 1024)})]:
            self.service.override = lambda *_, body=raw: (200, {"content-type": "application/json"}, body)
            status, result = self.invoke("bili.pools")
            self.assertNotEqual(status, 0)
            self.assertEqual(result["error"]["code"], "SOURCE_SCHEMA_CHANGED")
            self.assertNotIn("unexpected", json.dumps(result))

    def test_oversized_item_metadata_fails_instead_of_repeating_an_empty_continuation(self):
        self.approve(); self.service.complete = True; self.service.metadata_title = "中" * 100000
        status, result = self.invoke("bili.get", {"id": JOB})
        self.assertNotEqual(status, 0)
        self.assertEqual(result["error"]["code"], "QUOTA_EXCEEDED")

    def test_credentials_refuse_broad_permissions_symlinks_hardlinks_and_origin_rebinding(self):
        self.approve()
        before = len(self.service.calls)
        self.state_file.chmod(0o644)
        self.assertNotEqual(self.invoke("bili.pools")[0], 0)
        self.state_file.chmod(0o600)
        link = self.base / "linked.json"; os.link(self.state_file, link)
        self.assertNotEqual(self.invoke("bili.pools")[0], 0)
        link.unlink()
        saved = self.base / "saved.json"; self.state_file.rename(saved); self.state_file.symlink_to(saved)
        self.assertNotEqual(self.invoke("bili.pools")[0], 0)
        self.state_file.unlink(); saved.rename(self.state_file)
        with self.assertRaises(TanyuanError):
            BiliSourceClient("https://other.example.test", str(self.state_file), transport=self.service.transport).pools()
        self.assertEqual(len(self.service.calls), before)

    def test_tool_input_cannot_supply_destination_credentials_or_conflicting_intervals(self):
        self.approve()
        before = len(self.service.calls)
        for args in [dict(self.query_args(), token="private-input-sentinel"), dict(self.query_args(), origin="https://evil.test"),
                     dict(self.query_args(), **{"from": "2024-07-01T00:00:00Z", "until": "2024-07-03T00:00:00Z"}),
                     {"pool_id": "default", "idempotency_key": "stable", "from": "2024-07-01", "until": "2024-07-03"}]:
            status, result = self.invoke("bili.query", args)
            self.assertEqual(status, 2, result)
            self.assertEqual(result["error"]["code"], "BAD_ARGUMENT")
            self.assertNotIn("private-input-sentinel", json.dumps(result))
        self.assertEqual(len(self.service.calls), before)

    def test_rejected_nested_cursor_credentials_never_reach_output_or_usage_log(self):
        self.approve(); before = len(self.service.calls)
        sentinel = "PRIVATE_CURSOR_CREDENTIAL_MUST_NOT_APPEAR"
        cursor = {"id": JOB, "version": VERSION, "result_offset": 0, "content_offset": 0, "token": sentinel}
        status, result = self.invoke("bili.get", {"id": JOB, "cursor": cursor})
        self.assertEqual(status, 2)
        self.assertEqual(result["error"]["code"], "BAD_ARGUMENT")
        self.assertNotIn(sentinel, json.dumps(result))
        self.assertEqual(len(self.service.calls), before)
        usage = self.base / "usage.jsonl"
        if usage.exists():
            self.assertNotIn(sentinel, usage.read_text(encoding="utf-8"))


class NativeTransportTests(unittest.TestCase):
    @contextlib.contextmanager
    def peer(self, respond):
        """Exercise http.client framing with AF_UNIX only; never bind/connect IP."""
        client, server = socket.socketpair()
        client.settimeout(2); server.settimeout(2)
        stopped = threading.Event()
        requests = []
        original = bili_source._Connection

        class Connection(original):
            def connect(self):
                self._remember_socket(client)
                client.settimeout(self.timeout)

        def serve():
            try:
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    part = server.recv(4096)
                    if not part:
                        return
                    request.extend(part)
                requests.append(bytes(request))
                respond(server, stopped)
            except OSError:
                pass  # Expected when the deadline shuts down the socketpair.

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            with patch.object(bili_source, "_resolve", return_value="127.0.0.1"), \
                    patch.object(bili_source, "_Connection", Connection):
                yield requests
        finally:
            stopped.set()
            for sock in (client, server):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
            thread.join(1)
            self.assertFalse(thread.is_alive(), "native fixture worker did not stop")

    def test_native_host_matches_origin_with_implicit_and_explicit_ports(self):
        def respond(peer, _):
            peer.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 11\r\n\r\n{"ok":true}')
        for origin in ["https://bili-source.example.test", "https://bili-source.example.test:443", "http://127.0.0.1:8787"]:
            with self.peer(respond) as requests:
                status, _, raw = bili_source._http_request("GET", origin + "/api/v1/pools", headers={},
                                                          body=None, timeout=1, max_bytes=1024)
                self.assertEqual((status, raw), (200, b'{"ok":true}'))
                self.assertIn(("Host: " + urlsplit(origin).netloc + "\r\n").encode(), requests[0])

    def test_native_deadline_interrupts_slow_chunk_extension_after_connection_close(self):
        self.assert_slow_framing_interrupted(trailer=False)

    def test_native_deadline_interrupts_slow_chunk_trailer_after_connection_close(self):
        self.assert_slow_framing_interrupted(trailer=True)

    def assert_slow_framing_interrupted(self, *, trailer):
        def respond(peer, stopped):
            peer.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\nTransfer-Encoding: chunked\r\n\r\n")
            peer.sendall(b'b\r\n{"ok":true}\r\n0\r\nX-Slow: ' if trailer else b"b;")
            # Each byte arrives before socket inactivity timeout, but total framing
            # would take 1.6s without a transport-wide shutdown deadline.
            for _ in range(80):
                if stopped.wait(0.02):
                    return
                peer.sendall(b"x")
            peer.sendall(b"\r\n\r\n" if trailer else b'\r\n{"ok":true}\r\n0\r\n\r\n')

        with self.peer(respond):
            started = time.monotonic()
            with self.assertRaises(TanyuanError) as raised:
                bili_source._http_request("GET", ORIGIN + "/api/v1/pools", headers={}, body=None,
                                          timeout=0.2, max_bytes=1024)
            elapsed = time.monotonic() - started
        self.assertEqual(raised.exception.details["service_code"], "CLIENT_NETWORK")
        self.assertLess(elapsed, 0.65, "deadline was lost when HTTPResponse took ownership of the socket")

    def test_dns_worker_output_and_elapsed_time_are_bounded_before_parsing(self):
        from subprocess import Popen
        # Fixed local fixture program replaces only the process launch; it never
        # executes dns_worker or contacts a resolver, even outside the QA guard.
        programs = ["import sys;sys.stdout.buffer.write(b'x'*65536);sys.stdout.flush()",
                    "import time;time.sleep(10)"]
        for program in programs:
            children = []
            def launch(*args, **kwargs):
                child = Popen([sys.executable, "-c", program], stdout=kwargs["stdout"], stderr=kwargs["stderr"],
                              env=kwargs["env"], shell=False)
                children.append(child)
                return child
            with patch.object(bili_source.subprocess, "Popen", side_effect=launch):
                started = time.monotonic()
                with self.assertRaises(TanyuanError) as raised:
                    bili_source._resolve("bili-source.example.test", 0.2)
                self.assertLess(time.monotonic() - started, 0.8)
                self.assertEqual(raised.exception.details["service_code"], "CLIENT_NETWORK")
            self.assertTrue(children)
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(children[0].stdout.closed)


if __name__ == "__main__":
    unittest.main()
