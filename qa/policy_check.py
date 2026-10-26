#!/usr/bin/env python3
"""Validate the machine-readable QA registry and entry-point coverage."""
from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "qa" / "suites.json"

TOP_FIELDS = {"version", "suites"}
SUITE_FIELDS = {"id", "kind", "execution", "gates", "network",
                "credentials", "runner", "files"}
KINDS = {"deterministic", "build-contract", "live", "ai-eval"}
EXECUTIONS = {"ci", "manual", "local"}
GATES = {"pull-request", "release"}
CREDENTIALS = {"none", "external-local"}
RUNNER_CAPABILITIES = {
    "policy-meta": {
        "network": False, "runtime": "python", "files": ("qa/meta/test_policy.py",),
    },
    "jianwei-unit": {
        "network": False, "runtime": "python", "files": ("qa/jianwei/test_distribution.py",),
    },
    "tanyuan-offline": {
        "network": False,
        "runtime": "python",
        "files": (
            "qa/tanyuan/tests/test_cli_boundaries.py",
            "qa/tanyuan/tests/test_contract.py",
            "qa/tanyuan/tests/test_documents.py",
            "qa/tanyuan/tests/test_eval_integrity.py",
            "qa/tanyuan/tests/test_fund.py",
            "qa/tanyuan/tests/test_garden.py",
            "qa/tanyuan/tests/test_portable.py",
            "qa/tanyuan/tests/test_semantics.py",
        ),
    },
    "evidence-security": {
        "network": False, "runtime": "python", "files": ("qa/tanyuan/security_path_tests.py",),
    },
    "judge-selftest": {
        "network": False,
        "runtime": "python",
        "files": ("qa/tanyuan/evals/selftest/test_judges.py",),
    },
    # This is the sole reviewed Node capability: fixed offline npm pack and
    # DSH registration on the verified payload after the Python build.
    # Registry data cannot add commands or select another JavaScript file.
    "build-contract": {
        "network": False,
        "runtime": "fixed-python-node",
        "files": ("qa/jianwei/check_dsh.mjs", "scripts/build_jianwei.py"),
    },
    "marketplace-contract": {
        "network": False,
        "runtime": "python",
        "files": (
            "qa/jianwei/test_marketplace.py",
            "scripts/build_jianwei_marketplace.py",
        ),
    },
    "release-artifact": {
        "network": False,
        "runtime": "python",
        "files": (
            "qa/jianwei/test_release.py",
            "scripts/build_jianwei_release.py",
            ".agents/skills/jianwei-release/scripts/verify_artifact.py",
        ),
    },
    "evidence-local": {
        "network": False,
        "runtime": "python",
        "files": ("qa/tanyuan/check_frozen.py", "qa/tanyuan/evals/verify_golden.py"),
    },
    "tanyuan-live": {
        "network": True,
        "runtime": "python",
        "files": ("qa/tanyuan/tests/test_live_contract.py",),
    },
}
RUNNERS = set(RUNNER_CAPABILITIES)

# Every common script file (plus any executable file) under qa is an entry
# point unless it is listed here.
# Keep exclusions exact and documented in qa/README.md.
EXCLUDED_ENTRY_POINTS = {
    "qa/jianwei/check_market.mjs": "external official Marketplace validator adapter",
    "qa/policy_check.py": "QA registry control plane",
    "qa/run.py": "QA runner control plane",
    "qa/no_network/sitecustomize.py": "best-effort defense-in-depth Python network guard",
    "qa/tanyuan/evidence.py": "local evidence path-resolution library",
    "qa/tanyuan/evals/build_golden.py": "frozen retired audit generator",
    "qa/tanyuan/evals/examples/ty_adapter.py": "frozen retired adapter",
    "qa/tanyuan/evals/fetch_truth.py": "frozen retired fetcher",
    "qa/tanyuan/evals/score.py": "caller-driven diagnostic scorer",
}


class PolicyError(ValueError):
    """The QA registry or its coverage violates policy."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise PolicyError("qa/suites.json must be a regular file smaller than 1 MB")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError("qa/suites.json is not valid UTF-8 JSON") from error


def _string_list(value: Any, label: str, allowed: set[str] | None = None) -> list[str]:
    if (not isinstance(value, list) or not all(isinstance(item, str) for item in value)
            or len(set(value)) != len(value)):
        raise PolicyError(f"{label} must be a list of unique strings")
    if allowed is not None and not set(value).issubset(allowed):
        raise PolicyError(f"{label} contains an unknown value")
    return value


def safe_repo_file(root: Path, value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value:
        raise PolicyError("suite file paths must be non-empty POSIX strings")
    if any(char in value for char in "*?["):
        raise PolicyError("suite file paths must be explicit, not globs")
    relative = PurePosixPath(value)
    if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
        raise PolicyError("suite file paths must stay within the repository")
    candidate = root.joinpath(*relative.parts)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise PolicyError(f"registered suite file does not exist: {value}") from error
    if not resolved.is_relative_to(resolved_root) or candidate.is_symlink() or not resolved.is_file():
        raise PolicyError("registered suite files must be regular files within the repository")
    return relative.as_posix()


def is_entry_point(path: Path) -> bool:
    if path.suffix.lower() in {".py", ".mjs", ".js", ".sh", ".bash", ".zsh"}:
        return True
    return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def scan_entry_points(root: Path) -> set[str]:
    qa_root = root / "qa"
    if not qa_root.is_dir():
        return set()
    return {path.relative_to(root).as_posix() for path in qa_root.rglob("*")
            if path.is_file() and not path.is_symlink() and is_entry_point(path)
            and path.relative_to(root).as_posix() not in EXCLUDED_ENTRY_POINTS}


def validate_registry_data(
        data: Any, root: Path = ROOT, *, scan: bool = True,
        capabilities: dict[str, dict[str, Any]] = RUNNER_CAPABILITIES,
) -> list[dict[str, Any]]:
    if (not isinstance(data, dict) or set(data) != TOP_FIELDS
            or type(data.get("version")) is not int or data.get("version") != 1):
        raise PolicyError("registry must contain exactly version=1 and suites")
    suites = data.get("suites")
    if not isinstance(suites, list) or not suites:
        raise PolicyError("registry suites must be a non-empty list")

    ids: set[str] = set()
    runners: set[str] = set()
    registered_files: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for raw in suites:
        if not isinstance(raw, dict) or set(raw) != SUITE_FIELDS:
            raise PolicyError("each suite must contain exactly the documented fields")
        suite_id = raw["id"]
        if not isinstance(suite_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", suite_id):
            raise PolicyError("suite id is invalid")
        if suite_id in ids:
            raise PolicyError(f"duplicate suite id: {suite_id}")
        ids.add(suite_id)
        if (not isinstance(raw["kind"], str) or raw["kind"] not in KINDS
                or not isinstance(raw["execution"], str)
                or raw["execution"] not in EXECUTIONS):
            raise PolicyError(f"suite {suite_id} has an unknown kind or execution")
        if not isinstance(raw["credentials"], str) or raw["credentials"] not in CREDENTIALS:
            raise PolicyError(f"suite {suite_id} has unknown credentials")
        if not isinstance(raw["network"], bool):
            raise PolicyError(f"suite {suite_id} network must be boolean")
        gates = _string_list(raw["gates"], f"suite {suite_id} gates", GATES)
        runner = raw["runner"]
        if not isinstance(runner, str) or runner not in capabilities:
            raise PolicyError(f"suite {suite_id} has unknown runner")
        if runner != suite_id:
            raise PolicyError(f"suite {suite_id} must use its identically named runner")
        if raw["network"] != capabilities[runner]["network"]:
            raise PolicyError(f"suite {suite_id} network capability disagrees with runner")
        if runner in runners:
            raise PolicyError(f"runner is assigned more than once: {runner}")
        runners.add(runner)
        files = _string_list(raw["files"], f"suite {suite_id} files")
        if not files:
            raise PolicyError(f"suite {suite_id} must register at least one file")
        clean_files = [safe_repo_file(root, value) for value in files]
        if tuple(clean_files) != capabilities[runner]["files"]:
            raise PolicyError(f"suite {suite_id} files differ from its runner manifest")
        overlap = registered_files.intersection(clean_files)
        if overlap:
            raise PolicyError(f"suite files overlap: {sorted(overlap)}")
        registered_files.update(clean_files)

        if raw["kind"] == "ai-eval" and not (
                raw["execution"] == "local" and gates == []
                and raw["credentials"] == "external-local"):
            raise PolicyError("ai-eval suites must be local, non-gating, and external-local")
        if raw["kind"] == "live" and not (
                raw["execution"] == "manual" and gates == [] and raw["network"] is True):
            raise PolicyError("live suites must be manual, non-gating, and network-enabled")
        if raw["network"] and raw["kind"] not in {"live", "ai-eval"}:
            raise PolicyError("networked suites must be classified as live or ai-eval")
        if raw["execution"] == "ci" and not (
                raw["network"] is False and raw["credentials"] == "none"):
            raise PolicyError("ci suites must be offline and credential-free")
        if gates and raw["execution"] != "ci":
            raise PolicyError("gated suites must use ci execution")
        normalized.append({**raw, "gates": gates, "files": clean_files})

    if scan:
        discovered = scan_entry_points(root)
        missing = discovered - registered_files
        if missing:
            raise PolicyError(f"unregistered QA entry points: {sorted(missing)}")
    return normalized


def load_registry(path: Path = REGISTRY, root: Path = ROOT) -> list[dict[str, Any]]:
    return validate_registry_data(read_json(path), root)


def main() -> int:
    try:
        suites = load_registry()
    except PolicyError as error:
        print(f"QA policy error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "suites": len(suites),
                      "entry_points": len(scan_entry_points(ROOT))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
