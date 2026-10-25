#!/usr/bin/env python3
"""Run registered QA suites through fixed, reviewable implementations."""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from policy_check import PolicyError, RUNNER_CAPABILITIES, load_registry

ROOT = Path(__file__).resolve().parents[1]
NO_NETWORK_PATH = ROOT / "qa" / "no_network"
TIMEOUT_SECONDS = 600

SAFE_INHERITED_ENV_NAMES = {
    "PATH", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_NUMERIC",
    "LC_TIME", "LC_COLLATE", "LC_MONETARY", "LC_MESSAGES", "LC_PAPER",
    "LC_NAME", "LC_ADDRESS", "LC_TELEPHONE", "LC_MEASUREMENT",
    "LC_IDENTIFICATION", "TZ", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
}


class RunError(RuntimeError):
    """A suite cannot safely be selected or executed."""


def _credential_free_environment(source: dict[str, str], isolation_root: Path) -> dict[str, str]:
    env = {name: value for name, value in source.items() if name in SAFE_INHERITED_ENV_NAMES}
    env.setdefault("PATH", os.defpath)
    for name in ("home", "config", "cache", "data", "state", "tmp"):
        (isolation_root / name).mkdir(mode=0o700)
    env.update({
        "HOME": str(isolation_root / "home"),
        "USERPROFILE": str(isolation_root / "home"),
        "XDG_CONFIG_HOME": str(isolation_root / "config"),
        "XDG_CACHE_HOME": str(isolation_root / "cache"),
        "XDG_DATA_HOME": str(isolation_root / "data"),
        "XDG_STATE_HOME": str(isolation_root / "state"),
        "APPDATA": str(isolation_root / "config"),
        "LOCALAPPDATA": str(isolation_root / "data"),
        "TMPDIR": str(isolation_root / "tmp"),
        "TEMP": str(isolation_root / "tmp"),
        "TMP": str(isolation_root / "tmp"),
    })
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    # This reduces accidental Python network use. It is not a security
    # boundary; fixed runners and exact capability manifests are the boundary.
    env["PYTHONPATH"] = str(NO_NETWORK_PATH)
    return env


@contextmanager
def gate_environment(source: dict[str, str] | None = None) -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="jianwei-qa-env-") as folder:
        yield _credential_free_environment(
            dict(os.environ if source is None else source), Path(folder),
        )


def select_gate(suites: list[dict[str, Any]], gate: str) -> list[dict[str, Any]]:
    selected = [suite for suite in suites if gate in suite["gates"]]
    if not selected:
        raise RunError(f"no suites registered for gate: {gate}")
    if any(suite["execution"] != "ci" or suite["network"]
           or suite["credentials"] != "none" for suite in selected):
        raise RunError("gate selection contained a non-CI, networked, or credentialed suite")
    return selected


def ensure_network_authorized(suite: dict[str, Any], allow_network: bool) -> None:
    if suite["network"] and not allow_network:
        raise RunError("network suite requires explicit --allow-network")
    if allow_network and not suite["network"]:
        raise RunError("--allow-network is valid only for a network suite")


@contextmanager
def suite_environment(
        suite: dict[str, Any], source: dict[str, str] | None = None,
) -> Iterator[dict[str, str]]:
    if suite["credentials"] == "none":
        with gate_environment(source) as env:
            yield env
        return
    inherited = dict(os.environ if source is None else source)
    inherited["PYTHONDONTWRITEBYTECODE"] = "1"
    if not suite["network"]:
        inherited["PYTHONPATH"] = str(NO_NETWORK_PATH)
    yield inherited


def run_command(argv: list[str], env: dict[str, str], timeout: int = TIMEOUT_SECONDS) -> None:
    subprocess.run(argv, cwd=ROOT, env=env, check=True, shell=False, timeout=timeout)


def capability_files(runner: str) -> tuple[str, ...]:
    capability = RUNNER_CAPABILITIES.get(runner)
    if capability is None:
        raise RunError(f"runner capability is not defined: {runner}")
    files = capability["files"]
    if not isinstance(files, tuple) or not files:
        raise RunError(f"runner capability has no fixed file manifest: {runner}")
    return files


def python_module(relative: str) -> str:
    path = PurePosixPath(relative)
    if path.suffix != ".py" or path.is_absolute() or ".." in path.parts:
        raise RunError(f"runner manifest entry is not a safe Python file: {relative}")
    return ".".join(path.with_suffix("").parts)


def python_modules(runner: str) -> list[str]:
    files = capability_files(runner)
    if not all(PurePosixPath(item).suffix == ".py" for item in files):
        raise RunError(f"runner requires an all-Python manifest: {runner}")
    return [python_module(item) for item in files]


def discover_args(runner: str) -> list[str]:
    files = capability_files(runner)
    if len(files) != 1 or PurePosixPath(files[0]).suffix != ".py":
        raise RunError(f"discover runner requires exactly one Python file: {runner}")
    entry = PurePosixPath(files[0])
    return ["-s", entry.parent.as_posix(), "-p", entry.name, "-v"]


def _single_file_with_suffix(runner: str, suffix: str) -> str:
    matches = [item for item in capability_files(runner)
               if PurePosixPath(item).suffix == suffix]
    if len(matches) != 1:
        raise RunError(f"runner {runner} must have exactly one {suffix} manifest entry")
    return matches[0]


def run_policy_meta(env: dict[str, str]) -> None:
    run_command([sys.executable, "-m", "unittest", "discover",
                 *discover_args("policy-meta")], env)


def run_jianwei_unit(env: dict[str, str]) -> None:
    child = dict(env)
    child["JIANWEI_TEST_PYTHON"] = sys.executable
    run_command([sys.executable, "-m", "unittest", "discover",
                 *discover_args("jianwei-unit")], child)


def run_tanyuan_offline(env: dict[str, str]) -> None:
    run_command([sys.executable, "-m", "unittest", "-v",
                 *python_modules("tanyuan-offline")], env)


def run_evidence_security(env: dict[str, str]) -> None:
    path = _single_file_with_suffix("evidence-security", ".py")
    run_command([sys.executable, path, "-v"], env)


def run_judge_selftest(env: dict[str, str]) -> None:
    path = _single_file_with_suffix("judge-selftest", ".py")
    run_command([sys.executable, path], env)


def _tree_files(root: Path) -> dict[str, Path]:
    return {path.relative_to(root).as_posix(): path for path in root.rglob("*") if path.is_file()}


def assert_same_tree(expected: Path, actual: Path) -> None:
    expected_files = _tree_files(expected)
    actual_files = _tree_files(actual)
    if set(expected_files) != set(actual_files):
        raise RunError("fresh build file set differs from tracked plugin")
    changed = [name for name in expected_files
               if not filecmp.cmp(expected_files[name], actual_files[name], shallow=False)]
    if changed:
        raise RunError(f"fresh build differs from tracked plugin: {changed}")


def run_build_contract(env: dict[str, str]) -> None:
    build_script = _single_file_with_suffix("build-contract", ".py")
    node_check = _single_file_with_suffix("build-contract", ".mjs")
    with tempfile.TemporaryDirectory(prefix="jianwei-qa-build-") as folder:
        fresh = Path(folder) / "plugin"
        run_command([sys.executable, build_script, "--out", str(fresh)], env)
        run_command(["node", node_check, str(fresh)], node_child_environment(env))
        assert_same_tree(ROOT / "plugin", fresh)
    print("PASS: fresh build matches tracked plugin")


def run_evidence_local(env: dict[str, str]) -> None:
    for path in capability_files("evidence-local"):
        if PurePosixPath(path).suffix != ".py":
            raise RunError("evidence-local manifest must contain only Python files")
        run_command([sys.executable, path], env)


def run_tanyuan_live(env: dict[str, str]) -> None:
    child = dict(env)
    child["TANYUAN_LIVE"] = "1"
    run_command([sys.executable, "-m", "unittest", "-v",
                 *python_modules("tanyuan-live")], child)


def node_child_environment(env: dict[str, str]) -> dict[str, str]:
    """Add only the runtime locator needed by the fixed Node contract step."""
    child = dict(env)
    node = shutil.which("node", path=child.get("PATH", os.defpath))
    if node is not None:
        node_path = Path(node)
        if node_path.parent.name == "bin" and node_path.parent.parent.name == ".volta":
            child["VOLTA_HOME"] = str(node_path.parent.parent)
    return child


RUNNERS: dict[str, Callable[[dict[str, str]], None]] = {
    "policy-meta": run_policy_meta,
    "jianwei-unit": run_jianwei_unit,
    "tanyuan-offline": run_tanyuan_offline,
    "evidence-security": run_evidence_security,
    "judge-selftest": run_judge_selftest,
    "build-contract": run_build_contract,
    "evidence-local": run_evidence_local,
    "tanyuan-live": run_tanyuan_live,
}


def execute_suite(suite: dict[str, Any], env: dict[str, str], allow_network: bool) -> None:
    ensure_network_authorized(suite, allow_network)
    runner = RUNNERS.get(suite["runner"])
    if runner is None:
        raise RunError(f"runner is not implemented: {suite['runner']}")
    print(f"==> {suite['id']} ({suite['execution']}/{suite['kind']})", flush=True)
    runner(env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--gate", choices=("pull-request",))
    mode.add_argument("--suite")
    mode.add_argument("--list", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args(argv)

    try:
        suites = load_registry()
        if args.list:
            if args.allow_network:
                raise RunError("--allow-network requires --suite")
            print(json.dumps(suites, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.gate:
            if args.allow_network:
                raise RunError("gate runs never accept --allow-network")
            selected = select_gate(suites, args.gate)
            print("gate suites: " + ", ".join(suite["id"] for suite in selected), flush=True)
            with gate_environment() as env:
                for suite in selected:
                    execute_suite(suite, env, False)
            return 0

        suite = next((item for item in suites if item["id"] == args.suite), None)
        if suite is None:
            raise RunError(f"unknown suite: {args.suite}")
        with suite_environment(suite) as env:
            execute_suite(suite, env, args.allow_network)
        return 0
    except (PolicyError, RunError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as error:
        print(f"QA run failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
