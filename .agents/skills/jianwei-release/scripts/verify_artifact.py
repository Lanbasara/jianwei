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
SOURCE_REPOSITORY = "https://github.com/Lanbasara/jianwei.git"
SOURCE_HOMEPAGE = "https://github.com/Lanbasara/jianwei"
INDEX_URL = (
    "https://raw.githubusercontent.com/Lanbasara/"
    "jianwei-marketplace/main/dsh/index.json"
)
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


def _parse_json(raw: bytes, label: str) -> dict[str, Any]:
    if len(raw) > MAX_METADATA_BYTES:
        raise VerificationError(f"metadata exceeds size limit: {label}")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid JSON: {label}") from error
    if not isinstance(value, dict):
        raise VerificationError(f"JSON root must be an object: {label}")
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


def _verify_zip(path: Path, *, candidate: bool) -> tuple[list[str], dict[str, bytes]]:
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
            contents: dict[str, bytes] = {}
            for info in infos:
                with archive.open(info) as stream:
                    if candidate:
                        contents[info.filename] = stream.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
                    else:
                        while stream.read(1024 * 1024):
                            pass
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        raise VerificationError(f"invalid ZIP: {path.name}") from error
    return names, contents


def _expected_readme(ref: str) -> str:
    return (
        "# 见微 Jianwei Marketplace\n\n"
        "Jianwei 专属目录，独立于其他 marketplace。市场名：`jianwei`。\n\n"
        f"产品与完整安装说明：[Jianwei]({SOURCE_HOMEPAGE}/blob/{ref}/plugin/INSTALL.md)。\n\n"
        "```bash\n"
        "claude plugin marketplace add Lanbasara/jianwei-marketplace\n"
        "claude plugin install jianwei@jianwei --scope user\n\n"
        "codex plugin marketplace add Lanbasara/jianwei-marketplace\n"
        "codex plugin add jianwei@jianwei\n"
        "```\n\n"
        "## DSH Desktop：添加市场索引\n\n"
        "打开「插件 → 发现 → 添加索引源」，填入以下完整 URL：\n\n"
        f"```text\n{INDEX_URL}\n```\n\n"
        "这是 DSH 原生 format 1 索引，安装来源固定到 Git commit 与 `plugin/` 子目录，不要求 npm 发布。\n"
        "仓库根目录仅保留 v0.1.1 兼容布局；0.2.0 及后续版本必须从 `plugin/` 安装。\n\n"
        "CLI 直接安装仍可使用（profile 按实际情况替换）：\n\n"
        "```bash\n"
        f"dsh plugin --profile web add 'git+{SOURCE_REPOSITORY}#{ref}&path:/plugin'\n"
        "```\n\n"
        "Python 环境和凭据需按产品说明首次配置。\n"
    )


def _verify_candidate_contents(
    contents: dict[str, bytes], *, version: str, commit: str, ref: str,
) -> None:
    claude = _exact_object(
        _parse_json(
            contents["jianwei-marketplace/.claude-plugin/marketplace.json"],
            "Claude marketplace",
        ),
        {"name", "description", "owner", "plugins"},
        "Claude marketplace",
    )
    if (
        claude["name"] != "jianwei"
        or claude["description"] != "见微 Jianwei 专属投资研究插件目录"
        or claude["owner"] != {"name": "Lanbasara"}
        or not isinstance(claude["plugins"], list)
        or len(claude["plugins"]) != 1
    ):
        raise VerificationError("Claude marketplace identity is invalid")
    claude_plugin = _exact_object(
        claude["plugins"][0], {"name", "source", "description"}, "Claude plugin",
    )
    if claude_plugin["name"] != "jianwei" or not isinstance(claude_plugin["description"], str):
        raise VerificationError("Claude plugin identity is invalid")
    expected_source = {
        "source": "git-subdir", "url": SOURCE_REPOSITORY,
        "path": "plugin", "sha": commit,
    }
    if ref != commit:
        expected_source["ref"] = ref
    if claude_plugin["source"] != expected_source:
        raise VerificationError("Claude plugin source is invalid")

    codex = _exact_object(
        _parse_json(
            contents["jianwei-marketplace/.agents/plugins/marketplace.json"],
            "Codex marketplace",
        ),
        {"name", "interface", "plugins"},
        "Codex marketplace",
    )
    if (
        codex["name"] != "jianwei"
        or codex["interface"] != {"displayName": "见微 Jianwei"}
        or not isinstance(codex["plugins"], list)
        or len(codex["plugins"]) != 1
    ):
        raise VerificationError("Codex marketplace identity is invalid")
    codex_plugin = _exact_object(
        codex["plugins"][0],
        {"name", "source", "description", "policy", "category"},
        "Codex plugin",
    )
    codex_source = {**expected_source, "path": "./plugin"}
    if (
        codex_plugin["name"] != "jianwei"
        or codex_plugin["source"] != codex_source
        or not isinstance(codex_plugin["description"], str)
        or codex_plugin["policy"] != {
            "installation": "AVAILABLE", "authentication": "ON_USE",
        }
        or codex_plugin["category"] != "Productivity"
    ):
        raise VerificationError("Codex plugin identity is invalid")

    index = _exact_object(
        _parse_json(contents["jianwei-marketplace/dsh/index.json"], "DSH index"),
        {"format", "title", "description", "plugins"},
        "DSH index",
    )
    if (
        index["format"] != 1
        or index["title"] != {"en": "Jianwei", "zh": "见微 Jianwei"}
        or not isinstance(index["description"], dict)
        or set(index["description"]) != {"en", "zh"}
        or not all(isinstance(item, str) for item in index["description"].values())
        or not isinstance(index["plugins"], list)
        or len(index["plugins"]) != 1
    ):
        raise VerificationError("DSH index identity is invalid")
    history = _exact_object(
        index["plugins"][0],
        {
            "name", "title", "description", "author", "homepage", "repository",
            "metadata", "dist-tags", "versions",
        },
        "DSH plugin history",
    )
    if (
        history["name"] != "jianwei"
        or history["title"] != {"en": "Jianwei", "zh": "见微 Jianwei"}
        or not isinstance(history["description"], str)
        or history["author"] != "Lanbasara"
        or history["homepage"] != SOURCE_HOMEPAGE
        or history["repository"] != {"url": SOURCE_HOMEPAGE}
        or not isinstance(history["metadata"], dict)
        or set(history["metadata"]) != {"keywords"}
        or not isinstance(history["metadata"]["keywords"], list)
        or not all(isinstance(item, str) for item in history["metadata"]["keywords"])
        or history["dist-tags"] != {"latest": version}
        or not isinstance(history["versions"], list)
        or not history["versions"]
    ):
        raise VerificationError("DSH plugin history is invalid")
    newest = _exact_object(
        history["versions"][0],
        {"version", "publishTimestamp", "engines", "source"},
        "DSH newest version",
    )
    if (
        newest["version"] != version
        or type(newest["publishTimestamp"]) is not int
        or newest["publishTimestamp"] < 0
        or not isinstance(newest["engines"], dict)
        or not newest["engines"]
        or newest["source"] != {
            "type": "git", "url": SOURCE_REPOSITORY, "commit": commit, "path": "plugin",
        }
    ):
        raise VerificationError("DSH newest version identity is invalid")
    if contents["jianwei-marketplace/README.md"] != _expected_readme(ref).encode("utf-8"):
        raise VerificationError("Marketplace README is not the deterministic contract")


def verify(
    directory: Path,
    *,
    expected_version: str,
    expected_commit: str,
    expected_marketplace_base: str,
    official: bool,
    expected_tag: str | None = None,
    expected_product_sha256: str | None = None,
    expected_marketplace_sha256: str,
    expected_marketplace_index_sha256: str,
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
    for value, label in (
        (expected_marketplace_sha256, "expected Marketplace candidate digest"),
        (expected_marketplace_index_sha256, "expected Marketplace index digest"),
    ):
        if HEX_64.fullmatch(value) is None:
            raise VerificationError(f"{label} must be lowercase SHA-256")
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
        or market["baseIndexSha256"] != expected_marketplace_index_sha256
    ):
        raise VerificationError("Marketplace provenance does not match the approved plan")
    if expected_product_sha256 is not None and product["sha256"] != expected_product_sha256:
        raise VerificationError("formal product differs from the verified preview")
    if market["sha256"] != expected_marketplace_sha256:
        raise VerificationError("Marketplace candidate differs from the approved plan")

    product_files, _ = _verify_zip(root / "jianwei-plugin.zip", candidate=False)
    candidate_files, candidate_contents = _verify_zip(
        root / "jianwei-marketplace-candidate.zip", candidate=True,
    )
    _verify_candidate_contents(
        candidate_contents, version=expected_version, commit=expected_commit,
        ref=expected_ref,
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
    parser.add_argument("--expected-marketplace-sha256", required=True)
    parser.add_argument("--expected-marketplace-index-sha256", required=True)
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
            expected_marketplace_sha256=args.expected_marketplace_sha256,
            expected_marketplace_index_sha256=args.expected_marketplace_index_sha256,
        )
    except (OSError, VerificationError) as error:
        print(f"artifact verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

