#!/usr/bin/env python3
"""Verify one downloaded Jianwei release artifact directory fail-closed."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

RELEASE_FILES = {
    "SHA256SUMS",
    "jianwei-marketplace-candidate.zip",
    "jianwei-plugin.zip",
    "release.json",
}
CHECKSUM_FILES = RELEASE_FILES - {"SHA256SUMS"}
CANDIDATE_FILES = {
    "jianwei-marketplace/.agents/plugins/marketplace.json",
    "jianwei-marketplace/.claude-plugin/marketplace.json",
    "jianwei-marketplace/README.md",
    "jianwei-marketplace/dsh/index.json",
}
MARKETPLACE_REPOSITORY = "https://github.com/Lanbasara/jianwei-marketplace.git"
HEX_40 = re.compile(r"[0-9a-f]{40}\Z")
HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")
MAX_METADATA_BYTES = 1_000_000
MAX_ARCHIVE_BYTES = 100_000_000
MAX_ARCHIVE_MEMBER_BYTES = 50_000_000


class VerificationError(ValueError):
    """The downloaded artifact violates the release contract."""


def _exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise VerificationError(f"{label} has an unexpected schema")
    return value


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _regular_file(path: Path, limit: int) -> Path:
    if path.is_symlink() or not path.is_file():
        raise VerificationError(f"not a regular file: {path.name}")
    if path.stat().st_size > limit:
        raise VerificationError(f"file exceeds size limit: {path.name}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    raw = _regular_file(path, MAX_METADATA_BYTES).read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise VerificationError(f"JSON root must be an object: {path.name}")
    return value


def _read_checksums(path: Path) -> dict[str, str]:
    raw = _regular_file(path, MAX_METADATA_BYTES).read_bytes()
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as error:
        raise VerificationError("SHA256SUMS must be ASCII") from error
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if match is None or match.group(2) in values:
            raise VerificationError("SHA256SUMS has an invalid or duplicate entry")
        values[match.group(2)] = match.group(1)
    if set(values) != CHECKSUM_FILES:
        raise VerificationError("SHA256SUMS file set is not exact")
    return values


def _safe_member(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if not name or "\\" in name or name.startswith("/") or name.endswith("/"):
        raise VerificationError("ZIP contains an invalid path")
    path = PurePosixPath(name)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise VerificationError("ZIP path escapes its archive root")
    if info.flag_bits & 0x1 or info.compress_type != zipfile.ZIP_STORED:
        raise VerificationError("ZIP member encoding is not the deterministic contract")
    mode = (info.external_attr >> 16) & 0xFFFF
    if info.create_system != 3 or not stat.S_ISREG(mode):
        raise VerificationError("ZIP contains a non-regular member")
    if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
        raise VerificationError("ZIP member exceeds size limit")
    return name


def _verify_zip(path: Path, *, candidate: bool) -> list[str]:
    _regular_file(path, MAX_ARCHIVE_BYTES)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [_safe_member(info) for info in infos]
            if len(names) != len(set(names)):
                raise VerificationError("ZIP contains duplicate paths")
            if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
                raise VerificationError("ZIP expands beyond the total size limit")
            if candidate:
                if set(names) != CANDIDATE_FILES or len(names) != len(CANDIDATE_FILES):
                    raise VerificationError("Marketplace candidate file set is not exact")
            elif not names or any(
                not name.startswith("jianwei/") or ".agents/skills/" in name
                for name in names
            ):
                raise VerificationError("product ZIP has an invalid file inventory")
            for info in infos:
                with archive.open(info) as stream:
                    while stream.read(1024 * 1024):
                        pass
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise VerificationError(f"invalid ZIP: {path.name}") from error
    return names


def verify(
    directory: Path,
    *,
    expected_version: str,
    expected_commit: str,
    expected_marketplace_base: str,
    official: bool,
    expected_tag: str | None = None,
    expected_product_sha256: str | None = None,
) -> dict[str, Any]:
    if SEMVER.fullmatch(expected_version) is None:
        raise VerificationError("expected version must be stable SemVer")
    for value, label in (
        (expected_commit, "expected commit"),
        (expected_marketplace_base, "expected Marketplace base"),
    ):
        if HEX_40.fullmatch(value) is None:
            raise VerificationError(f"{label} must be a full lowercase SHA")
    if official:
        if expected_tag != "v" + expected_version:
            raise VerificationError("official artifact requires the exact version tag")
    elif expected_tag is not None:
        raise VerificationError("preview artifact may not have an expected tag")
    if expected_product_sha256 is not None and HEX_64.fullmatch(expected_product_sha256) is None:
        raise VerificationError("expected product digest must be lowercase SHA-256")

    if directory.is_symlink() or not directory.is_dir():
        raise VerificationError("artifact directory must be a real directory")
    root = directory.resolve(strict=True)
    children = {path.name for path in root.iterdir()}
    if children != RELEASE_FILES:
        raise VerificationError("artifact directory file set is not exact")

    sums = _read_checksums(root / "SHA256SUMS")
    actual: dict[str, str] = {}
    for name in sorted(CHECKSUM_FILES):
        path = _regular_file(
            root / name,
            MAX_METADATA_BYTES if name == "release.json" else MAX_ARCHIVE_BYTES,
        )
        actual[name] = _sha256(path)
        if actual[name] != sums[name]:
            raise VerificationError(f"checksum mismatch: {name}")

    data = _exact_object(
        _load_json(root / "release.json"),
        {
            "commit", "commitTimestamp", "format", "marketplace", "official",
            "product", "ref", "sourcePath", "tag", "version",
        },
        "release.json",
    )
    if (
        data["format"] != 1
        or data["official"] is not official
        or data["version"] != expected_version
        or data["commit"] != expected_commit
        or data["sourcePath"] != "plugin"
        or type(data["commitTimestamp"]) is not int
        or data["commitTimestamp"] < 0
    ):
        raise VerificationError("release identity does not match the approved plan")
    expected_ref = expected_tag if official else expected_commit
    if data["ref"] != expected_ref or data["tag"] != expected_tag:
        raise VerificationError("release ref/tag does not match the approved plan")

    product = _exact_object(data["product"], {"file", "sha256", "size"}, "product")
    market = _exact_object(
        data["marketplace"],
        {"baseCommit", "baseIndexSha256", "baseRepository", "file", "sha256", "size"},
        "marketplace",
    )
    expected_values = (
        (product, "jianwei-plugin.zip", actual["jianwei-plugin.zip"]),
        (market, "jianwei-marketplace-candidate.zip",
         actual["jianwei-marketplace-candidate.zip"]),
    )
    for record, filename, digest in expected_values:
        if (
            record["file"] != filename
            or record["sha256"] != digest
            or record["size"] != (root / filename).stat().st_size
        ):
            raise VerificationError(f"metadata mismatch: {filename}")
    if (
        market["baseRepository"] != MARKETPLACE_REPOSITORY
        or market["baseCommit"] != expected_marketplace_base
        or not isinstance(market["baseIndexSha256"], str)
        or HEX_64.fullmatch(market["baseIndexSha256"]) is None
    ):
        raise VerificationError("Marketplace provenance does not match the approved plan")
    if expected_product_sha256 is not None and product["sha256"] != expected_product_sha256:
        raise VerificationError("formal product differs from the verified preview")

    product_files = _verify_zip(root / "jianwei-plugin.zip", candidate=False)
    candidate_files = _verify_zip(
        root / "jianwei-marketplace-candidate.zip", candidate=True,
    )
    return {
        "candidateFiles": candidate_files,
        "commit": data["commit"],
        "marketplaceBase": market["baseCommit"],
        "marketplaceSha256": market["sha256"],
        "official": official,
        "productFiles": len(product_files),
        "productSha256": product["sha256"],
        "tag": data["tag"],
        "version": data["version"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--marketplace-base", required=True)
    parser.add_argument("--official", choices=("true", "false"), required=True)
    parser.add_argument("--tag")
    parser.add_argument("--expected-product-sha256")
    args = parser.parse_args()
    try:
        result = verify(
            args.directory,
            expected_version=args.version,
            expected_commit=args.commit,
            expected_marketplace_base=args.marketplace_base,
            official=args.official == "true",
            expected_tag=args.tag,
            expected_product_sha256=args.expected_product_sha256,
        )
    except (OSError, VerificationError) as error:
        print(f"artifact verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

