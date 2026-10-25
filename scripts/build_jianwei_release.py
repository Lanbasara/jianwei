#!/usr/bin/env python3
"""Build deterministic Jianwei release and Marketplace candidate artifacts."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import build_jianwei_marketplace as marketplace

MARKETPLACE_REPOSITORY_URL = "https://github.com/Lanbasara/jianwei-marketplace.git"
MARKETPLACE_ORIGIN_URLS = {
    MARKETPLACE_REPOSITORY_URL,
    MARKETPLACE_REPOSITORY_URL.removesuffix(".git"),
}
OUTPUT_FILES = (
    "jianwei-plugin.zip",
    "jianwei-marketplace-candidate.zip",
    "release.json",
)
MAX_INDEX_BYTES = 1_000_000


class ReleaseError(ValueError):
    """A release input or artifact violates the deterministic release contract."""


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=text, shell=False, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ReleaseError("unable to verify committed release input") from error
    return result.stdout.strip() if text else result.stdout


def _commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ReleaseError(f"{label} must be a lowercase full commit SHA")
    return value


def _marketplace_base(repo_arg: Path) -> tuple[str, bytes, str]:
    if repo_arg.is_symlink() or not repo_arg.is_dir():
        raise ReleaseError("--marketplace-repo must be a real Git directory")
    repo = repo_arg.resolve(strict=True)
    if _git(repo, "rev-parse", "--is-inside-work-tree") != "true":
        raise ReleaseError("--marketplace-repo must be a Git work tree")
    if _git(repo, "remote", "get-url", "origin") not in MARKETPLACE_ORIGIN_URLS:
        raise ReleaseError("Marketplace origin is unexpected")
    head = _commit(_git(repo, "rev-parse", "HEAD"), "Marketplace HEAD")
    main = _commit(
        _git(repo, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"),
        "Marketplace origin/main",
    )
    if head != main:
        raise ReleaseError("Marketplace HEAD must equal committed origin/main")
    raw = _git(repo, "show", head + ":dsh/index.json", text=False)
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_INDEX_BYTES:
        raise ReleaseError("committed Marketplace index is missing or too large")
    return head, raw, hashlib.sha256(raw).hexdigest()


def _plugin_modes(repo: Path, commit: str, files: dict[str, bytes]) -> dict[str, int]:
    raw = _git(repo, "ls-tree", "-r", "-z", commit + ":plugin", text=False)
    if not isinstance(raw, bytes):
        raise ReleaseError("unable to inspect committed plugin modes")
    modes: dict[str, int] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, _ = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise ReleaseError("committed plugin tree contains an invalid entry") from error
        if object_type != "blob" or mode not in marketplace.REGULAR_GIT_MODES:
            raise ReleaseError("committed plugin tree contains a non-regular file")
        modes[relative] = 0o755 if mode == "100755" else 0o644
    if set(modes) != set(files):
        raise ReleaseError("plugin mode inventory differs from validated files")
    return modes


def _zip_timestamp(timestamp: int) -> tuple[int, int, int, int, int, int]:
    try:
        value = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ReleaseError("commit timestamp is outside the supported ZIP range") from error
    if value.year < 1980:
        return (1980, 1, 1, 0, 0, 0)
    if value.year > 2107:
        raise ReleaseError("commit timestamp exceeds the ZIP year 2107 limit")
    return (value.year, value.month, value.day, value.hour, value.minute,
            value.second - value.second % 2)


def _write_zip(
        target: Path, files: dict[str, bytes], modes: dict[str, int],
        *, prefix: str, timestamp: int,
) -> None:
    if set(files) != set(modes):
        raise ReleaseError("archive file and mode inventories differ")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED,
                         allowZip64=False) as archive:
        for relative in sorted(files):
            info = zipfile.ZipInfo(prefix + relative, _zip_timestamp(timestamp))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | modes[relative]) << 16
            archive.writestr(info, files[relative])


def _candidate_files(root: Path) -> tuple[dict[str, bytes], dict[str, int]]:
    files: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReleaseError("Marketplace candidate may not contain links")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            files[relative] = path.read_bytes()
            modes[relative] = 0o644
    if not files:
        raise ReleaseError("Marketplace candidate is empty")
    return files, modes


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build(
        out_arg: Path, ref: str, release_repo_arg: Path,
        marketplace_repo_arg: Path, *, expected_commit: str | None = None,
        required_ancestor: str | None = None,
) -> Path:
    repo = marketplace._repository(release_repo_arg)
    package, commit, timestamp, tag = marketplace._release_metadata(repo, ref)
    if expected_commit is not None and commit != _commit(expected_commit, "expected commit"):
        raise ReleaseError("release ref does not match the expected workflow commit")
    if tag is not None:
        if required_ancestor != "origin/main":
            raise ReleaseError("official tag builds require --required-ancestor origin/main")
        result = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor",
             commit, required_ancestor],
            check=False, capture_output=True, text=True, shell=False, timeout=30,
        )
        if result.returncode != 0:
            raise ReleaseError("official release commit is not contained in required main")
    marketplace_commit, base_index, index_digest = _marketplace_base(marketplace_repo_arg)
    plugin_files = marketplace._plugin_tree(repo, commit)
    plugin_modes = _plugin_modes(repo, commit, plugin_files)
    version = package["version"]
    out = marketplace._validate_output(out_arg)

    def populate(stage: Path) -> None:
        product = stage / "jianwei-plugin.zip"
        _write_zip(
            product, plugin_files, plugin_modes,
            prefix="jianwei/", timestamp=timestamp,
        )
        previous = stage / ".marketplace-base-index.json"
        previous.write_bytes(base_index)
        candidate = stage / ".marketplace-candidate"
        marketplace.build(candidate, ref, repo, previous)
        candidate_files, candidate_modes = _candidate_files(candidate)
        candidate_zip = stage / "jianwei-marketplace-candidate.zip"
        _write_zip(
            candidate_zip, candidate_files, candidate_modes,
            prefix="jianwei-marketplace/", timestamp=timestamp,
        )
        shutil.rmtree(candidate)
        previous.unlink()
        release_data = {
            "format": 1,
            "official": tag is not None,
            "version": version,
            "ref": ref,
            "tag": tag,
            "commit": commit,
            "commitTimestamp": timestamp,
            "sourcePath": "plugin",
            "product": {
                "file": product.name,
                "sha256": _sha256(product),
                "size": product.stat().st_size,
            },
            "marketplace": {
                "file": candidate_zip.name,
                "sha256": _sha256(candidate_zip),
                "size": candidate_zip.stat().st_size,
                "baseRepository": MARKETPLACE_REPOSITORY_URL,
                "baseCommit": marketplace_commit,
                "baseIndexSha256": index_digest,
            },
        }
        release_json = stage / "release.json"
        _write_json(release_json, release_data)
        checksums = [
            f"{_sha256(stage / name)}  {name}"
            for name in OUTPUT_FILES
        ]
        (stage / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="ascii")

    marketplace._transactional_output(out, populate)
    return out


def _github_context() -> tuple[str, str]:
    event = os.environ.get("GITHUB_EVENT_NAME")
    sha = _commit(os.environ.get("GITHUB_SHA"), "GITHUB_SHA")
    if event == "push":
        if os.environ.get("GITHUB_REF_TYPE") != "tag":
            raise ReleaseError("push release context must be a tag")
        ref = os.environ.get("GITHUB_REF_NAME")
        if not isinstance(ref, str) or marketplace.TAG_PATTERN.fullmatch(ref) is None:
            raise ReleaseError("release tag must use vX.Y.Z")
        return ref, sha
    if event == "workflow_dispatch":
        return sha, sha
    raise ReleaseError("unsupported GitHub release event")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--release-repo", type=Path, required=True)
    parser.add_argument("--marketplace-repo", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ref")
    mode.add_argument("--github-context", action="store_true")
    parser.add_argument("--expected-commit")
    parser.add_argument("--required-ancestor")
    args = parser.parse_args()
    try:
        ref = args.ref
        expected = args.expected_commit
        if args.github_context:
            ref, expected = _github_context()
        result = build(
            args.out, ref, args.release_repo, args.marketplace_repo,
            expected_commit=expected, required_ancestor=args.required_ancestor,
        )
    except (ReleaseError, marketplace.MarketplaceError, OSError) as error:
        parser.error(str(error))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
