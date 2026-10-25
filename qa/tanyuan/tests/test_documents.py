"""External synthetic checks; no experiment snapshots or model answers."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tanyuan"))
from tanyuan import blobs, cache, documents, downloads
from tanyuan.errors import Code, TanyuanError
from tanyuan.registry import dispatch, load_tools


def pdf_bytes(text="A reliable report", blank=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = content
    if blank:
        writer.add_blank_page(width=300, height=300)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.old_conn = cache._CONN
        cache._CONN = None
        self.patch_dir = patch("tanyuan.config.cache_dir", return_value=self.base)
        self.patch_dir.start()
        load_tools()

    def tearDown(self):
        if cache._CONN:
            cache._CONN.close()
        cache._CONN = self.old_conn
        self.patch_dir.stop()
        self.tmp.cleanup()

    def call(self, **extra):
        return documents.fetch({"url": "https://example.org/report.pdf", "provider": "local", **extra})

    def test_versions_refresh_and_continuation_keep_source_and_pages(self):
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes("Original"), "https://example.org/report.pdf")) as net:
            first = self.call()
            cached = self.call()
            self.assertEqual(net.call_count, 1)
            self.assertEqual(first.observed_at, cached.observed_at)
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes("Correction"), "https://example.org/report.pdf")):
            corrected = self.call(refresh=True)
        self.assertNotEqual(first.data["document_version"], corrected.data["document_version"])
        with patch.object(downloads, "get_bytes", side_effect=AssertionError("must not request")):
            old = self.call(document_version=first.data["document_version"], offset=1)
        self.assertIn("Original", old.data["content"])
        self.assertNotIn("Correction", old.data["content"])
        self.assertEqual(old.data["page_count"], 1)
        self.assertTrue(all(Path(a["path"]).is_file() for a in old.artifacts))

    def test_local_and_legacy_cache_are_separate(self):
        url = "https://example.org/report.pdf"
        blobs.store("Legacy extraction", url=url, kind="pdf")
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes("Local"), url)):
            local = self.call()
        self.assertIn("Local", local.data["content"])
        legacy = blobs.latest_for_url(url)
        self.assertEqual(blobs.read(legacy["sha256"])[0], "Legacy extraction")
        env, status = dispatch("fetch.url", {"url": url}, use_cache=False)
        self.assertEqual(status, 0)
        self.assertEqual(env["data"]["content"], "Legacy extraction")
        self.assertEqual(env["provenance"]["source"], "blob")

    def test_partial_text_and_malformed_pdf_are_honest(self):
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes(blank=True), "https://example.org/report.pdf")):
            result = self.call()
        self.assertEqual(result.data["extraction_status"], "partial")
        self.assertEqual(result.data["pages"][1]["status"], "no_text")
        with patch.object(downloads, "get_bytes", return_value=(b"<html>not PDF</html>", "https://example.org/report.pdf")):
            with self.assertRaises(TanyuanError) as caught:
                self.call(refresh=True)
        self.assertEqual(caught.exception.code.value, "NO_DATA")

    def test_version_cannot_be_rebound_or_used_with_refresh(self):
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes(), "https://example.org/report.pdf")):
            version = self.call().data["document_version"]
        for args in ({"url": "https://example.org/other.pdf", "document_version": version},
                     {"document_version": version, "refresh": True}, {"offset": 2},
                     {"document_version": "../private"}):
            with self.assertRaises(TanyuanError):
                self.call(**args)

    def test_same_content_at_two_urls_has_independent_records(self):
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes(), "https://example.org/report.pdf")):
            first = self.call()
            second = self.call(url="https://example.org/second.pdf")
        self.assertNotEqual(first.data["document_version"], second.data["document_version"])
        self.assertEqual(first.data["pdf_sha256"], second.data["pdf_sha256"])

    def test_timeout_and_dependency_failure_do_not_break_help_or_other_tools(self):
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes(), "https://example.org/report.pdf")), patch.object(
                subprocess, "run", side_effect=subprocess.TimeoutExpired("parser", 35)):
            env, status = dispatch("fetch.url", {"url": "https://example.org/report.pdf", "provider": "local"}, use_cache=False)
        self.assertEqual(env["error"]["code"], "SOURCE_UNAVAILABLE")
        self.assertNotEqual(status, 0)
        from tanyuan.registry import REGISTRY
        self.assertIn("market.quote", REGISTRY)
        self.assertIn("news.stock", REGISTRY)

    def test_document_storage_can_be_cleared_and_rejects_symlinks(self):
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes(), "https://example.org/report.pdf")):
            first = self.call()
        result = blobs.purge()
        self.assertEqual(result["documents"]["removed"], 1)
        with self.assertRaises(TanyuanError):
            self.call(document_version=first.data["document_version"])
        (self.base / "documents").rmdir()
        (self.base / "documents").symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(TanyuanError):
            documents._root()

    def test_dispatch_does_not_cache_deleted_document_artifacts(self):
        args = {"url": "https://example.org/report.pdf", "provider": "local"}
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes(), args["url"])) as net:
            first, _ = dispatch("fetch.url", args)
            blobs.purge()
            second, _ = dispatch("fetch.url", args)
        self.assertEqual(net.call_count, 2)
        self.assertNotEqual(first["data"]["document_version"], second["data"]["document_version"])
        self.assertTrue(all(Path(a["path"]).is_file() for a in second["artifacts"]))

    def test_deleted_or_changed_original_rejects_version(self):
        with patch.object(downloads, "get_bytes", return_value=(pdf_bytes(), "https://example.org/report.pdf")):
            result = self.call()
        original = next(Path(a["path"]) for a in result.artifacts if a["kind"] == "pdf")
        for operation in (lambda: original.write_bytes(b"%PDF-changed"), original.unlink):
            operation()
            with self.assertRaises(TanyuanError):
                self.call(document_version=result.data["document_version"])

    def test_missing_parser_dependency_is_actionable(self):
        for reason in ("dependency_missing", "dependency_version_mismatch"):
            with self.subTest(reason=reason):
                def missing(argv, **kwargs):
                    Path(argv[-1]).write_text(json.dumps({"ok": False, "reason": reason}))
                    raise subprocess.CalledProcessError(1, argv)
                with patch.object(downloads, "get_bytes", return_value=(b"%PDF-fake", "https://example.org/report.pdf")), patch.object(subprocess, "run", side_effect=missing):
                    with self.assertRaises(TanyuanError) as caught:
                        self.call()
                self.assertEqual(caught.exception.code.value, "SOURCE_UNAVAILABLE")
                self.assertIn("requirements.txt", caught.exception.hint)


class DownloadTests(unittest.TestCase):
    def test_tls_uses_remaining_total_deadline_and_original_hostname(self):
        from unittest.mock import MagicMock
        raw, secure, context = MagicMock(), MagicMock(), MagicMock()
        interrupted = threading.Event()
        secure.shutdown.side_effect = lambda *args: interrupted.set()
        def connect_delay(*args, **kwargs):
            time.sleep(0.7)
            return raw
        def handshake():
            if interrupted.wait(2):
                raise OSError("synthetic connection stopped")
        secure.do_handshake.side_effect = handshake
        context.wrap_socket.return_value = secure
        started = time.monotonic()
        with patch.object(downloads, "_target", return_value=("example.org", "/report.pdf", ["93.184.216.34"])), patch.object(
                socket, "create_connection", side_effect=connect_delay), patch.object(
                downloads.ssl, "create_default_context", return_value=context):
            with self.assertRaises(TanyuanError) as caught:
                downloads.get_bytes("https://example.org/report.pdf", timeout=1)
        self.assertEqual(caught.exception.code, Code.SOURCE_UNAVAILABLE)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(interrupted.is_set())
        self.assertLessEqual(raw.settimeout.call_args.args[0], 0.4)
        context.wrap_socket.assert_called_once_with(raw, server_hostname="example.org", do_handshake_on_connect=False)

    def test_public_dns_pinned_and_private_targets_rejected(self):
        with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("93.184.216.34", 443))]):
            self.assertEqual(downloads._target("https://example.org/a?b=1"),
                             ("example.org", "/a?b=1", ["93.184.216.34"]))
        with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(TanyuanError):
                downloads._target("https://example.org/a")
        for url in ("http://example.org/a", "https://a:b@example.org/a", "https://example.org:8080/a", "file:///tmp/a"):
            with self.assertRaises(TanyuanError):
                downloads._target(url)

    def test_redirect_private_target_and_size_limit(self):
        from unittest.mock import MagicMock
        response = MagicMock(status=302)
        response.getheader.side_effect = lambda name, default=None: "https://127.0.0.1/a" if name == "Location" else default
        conn = MagicMock()
        conn.getresponse.return_value = response
        with patch.object(downloads, "_target", side_effect=[("example.org", "/", ["93.184.216.34"]),
                                                           TanyuanError(Code.BLOCKED, "private")]), patch.object(downloads, "_PinnedHTTPS", return_value=conn):
            with self.assertRaises(TanyuanError):
                downloads.get_bytes("https://example.org/")
        response.status = 200
        response.getheader.side_effect = lambda name, default=None: "100" if name == "Content-Length" else default
        with patch.object(downloads, "_target", return_value=("example.org", "/", ["93.184.216.34"])), patch.object(downloads, "_PinnedHTTPS", return_value=conn):
            with self.assertRaises(TanyuanError):
                downloads.get_bytes("https://example.org/", max_bytes=10)

    def test_dns_subprocess_receives_remaining_deadline_and_no_secrets(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("resolver", 1)) as run:
            with self.assertRaises(TanyuanError):
                downloads.get_bytes("https://example.org/", timeout=1)
        kwargs = run.call_args.kwargs
        self.assertGreater(kwargs["timeout"], 0)
        self.assertLessEqual(kwargs["timeout"], 1)
        self.assertNotIn("TUSHARE_TOKEN", kwargs["env"])


if __name__ == "__main__":
    unittest.main()
