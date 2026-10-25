#!/usr/bin/env python3
"""Detect drift in exposed historical questions, grading rules and evidence."""
import hashlib
import json
from pathlib import Path

from evidence import EvidencePathError, evidence_root, golden_path, path_within

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    lock = json.loads((Path(__file__).parent / "frozen.json").read_text(encoding="utf-8"))
    try:
        local_golden = evidence_root()
    except EvidencePathError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2

    changed = []
    for name, digest in lock["files"].items():
        try:
            path = (golden_path(local_golden, name)
                    if Path(name).is_relative_to("artifacts/golden")
                    else path_within(ROOT, name))
        except EvidencePathError:
            changed.append(name)
            continue
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed.append(name)
    print(json.dumps({"ok": not changed, "checked": len(lock["files"]), "changed": changed}, ensure_ascii=False))
    return int(bool(changed))


if __name__ == "__main__":
    raise SystemExit(main())
