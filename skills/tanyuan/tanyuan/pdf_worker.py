"""Isolated, time/size bounded PDF text extraction; never executes PDF actions."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path


def main() -> int:
    logging.disable(logging.CRITICAL)
    source, destination = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        resource.setrlimit(resource.RLIMIT_FSIZE, (40 * 1024 * 1024, 40 * 1024 * 1024))
        # RLIMIT_AS is effective on supported Unix kernels; wall time remains
        # enforced by the parent even where macOS does not support this limit.
        try:
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        except (ValueError, OSError):
            pass
        import pypdf
        if pypdf.__version__ != "6.21.2":
            destination.write_text(json.dumps({"ok": False, "reason": "dependency_version_mismatch"}), encoding="utf-8")
            return 1
        from pypdf import PdfReader
        reader = PdfReader(str(source), strict=False)
        if reader.is_encrypted:
            raise ValueError("encrypted")
        if len(reader.pages) > 1500:
            raise ValueError("page_limit")
        pages, text, position = [], [], 0
        for number, page in enumerate(reader.pages, 1):
            body = page.extract_text() or ""
            part = f"\n--- PDF page {number} ---\n{body}\n"
            if position + len(part) > 8_000_000:
                raise ValueError("text_limit")
            pages.append({"page": number, "start": position, "end": position + len(part),
                          "text_chars": len(body.strip()),
                          "status": "text" if body.strip() else "no_text"})
            text.append(part)
            position += len(part)
        result = {"ok": True, "text": "".join(text), "pages": pages}
    except ImportError:
        result = {"ok": False, "reason": "dependency_missing"}
    except Exception:
        result = {"ok": False, "reason": "invalid_encrypted_or_oversized_pdf"}
    destination.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
