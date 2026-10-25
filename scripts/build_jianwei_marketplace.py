#!/usr/bin/env python3
"""Build local Jianwei Marketplace catalogs from an immutable Git release."""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable

REPOSITORY_URL = "https://github.com/Lanbasara/jianwei.git"
HOMEPAGE_URL = "https://github.com/Lanbasara/jianwei"
INDEX_URL = "https://raw.githubusercontent.com/Lanbasara/jianwei-marketplace/main/dsh/index.json"
DSH_ENGINE = ">=0.2.1-rc.3 <0.3.0"
SEMVER_NUMBER = r"(?:0|[1-9]\d*)"
TAG_PATTERN = re.compile(rf"v({SEMVER_NUMBER})\.({SEMVER_NUMBER})\.({SEMVER_NUMBER})\Z")
VERSION_PATTERN = re.compile(rf"({SEMVER_NUMBER})\.({SEMVER_NUMBER})\.({SEMVER_NUMBER})\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
MAX_JSON_BYTES = 1_000_000
LEGACY_ROOT_VERSIONS = {"0.1.0", "0.1.1"}
REGULAR_GIT_MODES = {"100644", "100755"}
AT_FDCWD = -100
RENAME_NOREPLACE = 1
RENAME_EXCL = 0x00000004


class MarketplaceError(ValueError):
    """Release metadata or Marketplace history is unsafe or inconsistent."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MarketplaceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_json_bytes(raw: bytes, label: str) -> Any:
    if len(raw) > MAX_JSON_BYTES:
        raise MarketplaceError(f"{label} exceeds 1 MB")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MarketplaceError(f"{label} is not valid UTF-8 JSON") from error


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise MarketplaceError(f"{label} must be a regular file")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise MarketplaceError(f"{label} exceeds 1 MB")
    return path


def _repository(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise MarketplaceError("--release-repo must be a real directory")
    resolved = path.resolve(strict=True)
    result = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "--is-inside-work-tree"],
        check=False, capture_output=True, text=True, shell=False, timeout=15,
    )
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise MarketplaceError("--release-repo must be a Git work tree")
    return resolved


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=text, shell=False, timeout=15,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise MarketplaceError("unable to read release metadata from Git") from error
    return result.stdout.strip() if text else result.stdout


def _safe_manifest_path(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 512
            or "\\" in value or any(ord(char) < 32 for char in value)):
        raise MarketplaceError("plugin manifest paths must be bounded POSIX strings")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts or path.as_posix() != value:
        raise MarketplaceError("plugin manifest paths must stay within plugin")
    return value


def _plugin_tree(repo: Path, commit: str) -> dict[str, bytes]:
    raw = _git(repo, "ls-tree", "-r", "-z", commit + ":plugin", text=False)
    if not isinstance(raw, bytes):
        raise MarketplaceError("unable to inspect plugin tree")
    blobs: dict[str, bytes] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split(" ")
            relative = _safe_manifest_path(raw_path.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise MarketplaceError("plugin tree contains an invalid entry") from error
        if mode not in REGULAR_GIT_MODES or object_type != "blob":
            raise MarketplaceError("plugin tree may contain only regular file blobs")
        if relative in blobs:
            raise MarketplaceError("plugin tree contains duplicate paths")
        content = _git(repo, "cat-file", "blob", object_id, text=False)
        if not isinstance(content, bytes):
            raise MarketplaceError("unable to read plugin blob")
        blobs[relative] = content
    return blobs


def _validate_plugin_tree(repo: Path, commit: str) -> tuple[dict[str, Any], dict[str, Any]]:
    blobs = _plugin_tree(repo, commit)
    if "manifest.json" not in blobs or "package.json" not in blobs:
        raise MarketplaceError("plugin tree must contain manifest.json and package.json")
    manifest = _parse_json_bytes(blobs["manifest.json"], "plugin/manifest.json")
    if (not isinstance(manifest, dict)
            or set(manifest) != {
                "name", "version", "files", "components", "entry_adaptation",
            }
            or manifest.get("name") != "jianwei"
            or not isinstance(manifest.get("version"), str)
            or not isinstance(manifest.get("files"), dict)
            or not isinstance(manifest.get("components"), dict)
            or not isinstance(manifest.get("entry_adaptation"), str)
            or not manifest["entry_adaptation"].strip()):
        raise MarketplaceError("plugin manifest has an unexpected schema")
    _version_tuple(manifest["version"])
    declared: dict[str, str] = {}
    for raw_path, raw_digest in manifest["files"].items():
        relative = _safe_manifest_path(raw_path)
        if (not isinstance(raw_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", raw_digest) is None):
            raise MarketplaceError("plugin manifest hashes must be lowercase SHA-256")
        declared[relative] = raw_digest
    actual_files = set(blobs) - {"manifest.json"}
    if set(declared) != actual_files:
        raise MarketplaceError("plugin manifest file set does not match the Git tree")
    for relative, digest in declared.items():
        if hashlib.sha256(blobs[relative]).hexdigest() != digest:
            raise MarketplaceError(f"plugin manifest hash mismatch: {relative}")
    package = _parse_json_bytes(blobs["package.json"], "plugin/package.json")
    if not isinstance(package, dict):
        raise MarketplaceError("plugin/package.json must be a JSON object")
    if package.get("name") != manifest["name"] or package.get("version") != manifest["version"]:
        raise MarketplaceError("plugin manifest identity does not match package.json")
    return package, manifest


def _version_tuple(value: str) -> tuple[int, int, int]:
    if len(value) > 64:
        raise MarketplaceError("version is too long")
    match = VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise MarketplaceError("versions must use stable three-part semantic versioning")
    return tuple(int(part) for part in match.groups())


def _validate_ref(value: str) -> tuple[str, str | None]:
    if len(value) > 64:
        raise MarketplaceError("--ref is too long")
    if TAG_PATTERN.fullmatch(value):
        return value, value
    if COMMIT_PATTERN.fullmatch(value):
        return value, None
    raise MarketplaceError("--ref must be vX.Y.Z or a lowercase full 40-character commit SHA")


def _exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise MarketplaceError(f"{label} has an unexpected schema")
    return value


def _release_metadata(repo: Path, ref: str) -> tuple[dict[str, Any], str, int, str | None]:
    _, tag = _validate_ref(ref)
    commit = str(_git(repo, "rev-parse", "--verify", ref + "^{commit}"))
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise MarketplaceError("resolved release commit is invalid")
    package, _ = _validate_plugin_tree(repo, commit)

    if package.get("name") != "jianwei":
        raise MarketplaceError("plugin package name must be jianwei")
    version = package.get("version")
    if not isinstance(version, str):
        raise MarketplaceError("plugin package version is missing")
    _version_tuple(version)
    if tag is not None and tag != "v" + version:
        raise MarketplaceError("tag does not match plugin package version")

    author = package.get("author")
    if author != {"name": "Lanbasara"}:
        raise MarketplaceError("plugin author identity is unexpected")
    if package.get("homepage") != HOMEPAGE_URL:
        raise MarketplaceError("plugin homepage is unexpected")
    repository = package.get("repository")
    if repository != {"type": "git", "url": "git+" + REPOSITORY_URL}:
        raise MarketplaceError("plugin repository identity is unexpected")
    if not isinstance(package.get("description"), str) or not package["description"].strip():
        raise MarketplaceError("plugin description is missing")
    if package.get("engines") != {"dsh": DSH_ENGINE}:
        raise MarketplaceError("plugin DSH engine range is unexpected")
    if package.get("dsh") != {"bundle": {"patch": "./cordis.patch.yml"}}:
        raise MarketplaceError("plugin DSH bundle declaration is unexpected")

    timestamp_raw = str(_git(repo, "show", "-s", "--format=%ct", commit))
    if not timestamp_raw.isascii() or not timestamp_raw.isdigit():
        raise MarketplaceError("release commit timestamp is invalid")
    timestamp = int(timestamp_raw)
    if not 0 <= timestamp < 10**11:
        raise MarketplaceError("release commit timestamp is out of range")
    return package, commit, timestamp, tag


def release_metadata(repo_arg: Path, ref: str) -> tuple[dict[str, Any], str, int, str | None]:
    """Read and validate release facts exclusively from ``commit:plugin``."""
    return _release_metadata(_repository(repo_arg), ref)


def _validate_source(source: Any, *, allow_root: bool) -> None:
    source = _exact_object(
        source,
        {"type", "url", "commit"} if allow_root else {"type", "url", "commit", "path"},
        "DSH source",
    )
    if source.get("type") != "git" or source.get("url") != REPOSITORY_URL:
        raise MarketplaceError("DSH history contains an unexpected repository")
    commit = source.get("commit")
    if not isinstance(commit, str) or COMMIT_PATTERN.fullmatch(commit) is None:
        raise MarketplaceError("DSH history must pin a full lowercase commit")
    if allow_root:
        return
    if source.get("path") != "plugin":
        raise MarketplaceError("DSH subdirectory releases must use path plugin")


def _localized_text(value: Any, label: str) -> dict[str, str]:
    localized = _exact_object(value, {"en", "zh"}, label)
    if any(not isinstance(text, str) or not text.strip() for text in localized.values()):
        raise MarketplaceError(f"{label} must contain non-empty en and zh strings")
    return localized


def _validate_history_release(
        repo: Path, entry: dict[str, Any], *, plugin_layout: bool,
) -> None:
    source = entry["source"]
    commit = source["commit"]
    resolved = str(_git(repo, "rev-parse", "--verify", commit + "^{commit}"))
    if resolved != commit:
        raise MarketplaceError("historical source does not resolve to its pinned commit")
    if plugin_layout:
        package, _ = _validate_plugin_tree(repo, commit)
    else:
        raw = _git(repo, "show", commit + ":package.json", text=False)
        if not isinstance(raw, bytes):
            raise MarketplaceError("unable to read historical root package")
        package = _parse_json_bytes(raw, "historical package.json")
        if not isinstance(package, dict):
            raise MarketplaceError("historical package.json must be an object")
    if (package.get("name") != "jianwei" or package.get("version") != entry["version"]
            or package.get("engines") != {"dsh": DSH_ENGINE}
            or package.get("dsh") != {"bundle": {"patch": "./cordis.patch.yml"}}):
        raise MarketplaceError("historical package identity or DSH contract is inconsistent")
    timestamp_raw = str(_git(repo, "show", "-s", "--format=%ct", commit))
    if not timestamp_raw.isascii() or not timestamp_raw.isdigit():
        raise MarketplaceError("historical commit timestamp is invalid")
    if int(timestamp_raw) != entry["publishTimestamp"]:
        raise MarketplaceError("historical publish timestamp does not match its commit")


def _validate_previous_index(
        data: Any, repo: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    top = _exact_object(data, {"format", "title", "description", "plugins"}, "previous index")
    if top.get("format") != 1 or not isinstance(top.get("plugins"), list) or len(top["plugins"]) != 1:
        raise MarketplaceError("previous index must contain one format-1 plugin")
    _localized_text(top.get("title"), "previous index title")
    _localized_text(top.get("description"), "previous index description")
    plugin = _exact_object(
        top["plugins"][0],
        {"name", "title", "description", "author", "homepage", "repository",
         "metadata", "dist-tags", "versions"},
        "previous Jianwei entry",
    )
    if (plugin.get("name") != "jianwei" or plugin.get("author") != "Lanbasara"
            or plugin.get("homepage") != HOMEPAGE_URL
            or plugin.get("repository") != {"url": HOMEPAGE_URL}):
        raise MarketplaceError("previous index has an unexpected Jianwei identity")
    _localized_text(plugin.get("title"), "previous plugin title")
    if not isinstance(plugin.get("description"), str) or not plugin["description"].strip():
        raise MarketplaceError("previous plugin description must be a non-empty string")
    metadata = _exact_object(plugin.get("metadata"), {"keywords"}, "previous metadata")
    keywords = metadata.get("keywords")
    if (not isinstance(keywords, list) or not keywords
            or not all(isinstance(value, str) and value.strip() for value in keywords)
            or len(set(keywords)) != len(keywords)):
        raise MarketplaceError("previous metadata keywords must be unique non-empty strings")
    dist_tags = _exact_object(plugin.get("dist-tags"), {"latest"}, "previous dist-tags")
    latest = dist_tags.get("latest")
    if not isinstance(latest, str):
        raise MarketplaceError("previous latest version is missing")
    _version_tuple(latest)
    versions = plugin.get("versions")
    if not isinstance(versions, list) or not versions:
        raise MarketplaceError("previous index must contain version history")
    seen: set[str] = set()
    ordered_versions: list[tuple[int, int, int]] = []
    layouts: list[bool] = []
    for entry in versions:
        item = _exact_object(
            entry, {"version", "publishTimestamp", "engines", "source"},
            "previous version",
        )
        version = item.get("version")
        if not isinstance(version, str):
            raise MarketplaceError("previous version is missing")
        parsed_version = _version_tuple(version)
        ordered_versions.append(parsed_version)
        if version in seen:
            raise MarketplaceError("previous index contains duplicate versions")
        seen.add(version)
        if type(item.get("publishTimestamp")) is not int or not 0 <= item["publishTimestamp"] < 10**11:
            raise MarketplaceError("previous publish timestamp is invalid")
        if item.get("engines") != {"dsh": DSH_ENGINE}:
            raise MarketplaceError("previous DSH engine range is unexpected")
        source = item.get("source")
        if isinstance(source, dict) and "path" not in source:
            _validate_source(source, allow_root=True)
            if version not in LEGACY_ROOT_VERSIONS:
                raise MarketplaceError("root-layout history is limited to legacy 0.1.0/0.1.1")
            plugin_layout = False
        else:
            _validate_source(source, allow_root=False)
            if parsed_version < (0, 2, 0):
                raise MarketplaceError("plugin-layout history requires version 0.2.0 or newer")
            plugin_layout = True
        layouts.append(plugin_layout)
    if latest not in seen:
        raise MarketplaceError("previous latest does not name a historical version")
    if ordered_versions != sorted(ordered_versions, reverse=True):
        raise MarketplaceError("previous versions must be in strict descending order")
    if latest != versions[0]["version"]:
        raise MarketplaceError("previous latest must equal the highest historical version")
    for item, plugin_layout in zip(versions, layouts):
        _validate_history_release(repo, item, plugin_layout=plugin_layout)
    return plugin, versions, latest


def _read_previous(
        path: Path | None, repo: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    if path is None:
        return None, [], None
    source = _regular_file(path, "--previous-index")
    data = _parse_json_bytes(source.read_bytes(), "previous index")
    plugin, versions, latest = _validate_previous_index(data, repo)
    return plugin, versions, latest


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_output(out: Path) -> Path:
    if out.exists() or out.is_symlink():
        raise MarketplaceError("output directory must not already exist")
    absolute = Path(os.path.abspath(out))
    parent = absolute.parent
    if not parent.exists() or not parent.is_dir():
        raise MarketplaceError("output parent must be an existing real directory")
    cursor = parent
    while True:
        if cursor.is_symlink():
            raise MarketplaceError("output path must not traverse symbolic links")
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    resolved_parent = parent.resolve(strict=True)
    resolved = resolved_parent / absolute.name
    if resolved.exists() or resolved.is_symlink():
        raise MarketplaceError("resolved output directory must not already exist")
    return resolved


def _atomic_publish_no_replace(stage: Path, out: Path) -> None:
    """Atomically rename ``stage`` to absent ``out`` without replacement."""
    libc = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(stage)
    target = os.fsencode(out)
    if sys.platform == "darwin":
        operation = getattr(libc, "renamex_np", None)
        if operation is None:
            raise MarketplaceError("atomic no-replace publish is unavailable")
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(source, target, RENAME_EXCL)
    elif sys.platform.startswith("linux"):
        operation = getattr(libc, "renameat2", None)
        if operation is None:
            raise MarketplaceError("atomic no-replace publish is unavailable")
        operation.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
            ctypes.c_uint,
        ]
        operation.restype = ctypes.c_int
        result = operation(AT_FDCWD, source, AT_FDCWD, target, RENAME_NOREPLACE)
    else:
        raise MarketplaceError("atomic no-replace publish is unsupported on this platform")
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error_number, "output appeared during publish", out)
        raise OSError(error_number, os.strerror(error_number), out)


def _transactional_output(out: Path, populate: Callable[[Path], None]) -> None:
    stage = Path(tempfile.mkdtemp(prefix=f".{out.name}.tmp-", dir=out.parent))
    try:
        populate(stage)
        stage.chmod(0o755)
        # Publishing is deliberately the final operation on the success path.
        # All fallible preparation, including the final directory mode, is
        # complete before the one atomic no-replace rename.
        _atomic_publish_no_replace(stage, out)
    except BaseException:
        if stage.exists() and not stage.is_symlink():
            shutil.rmtree(stage)
        raise


def build(out_arg: Path, ref: str, release_repo: Path, previous_index: Path | None = None) -> Path:
    out = _validate_output(out_arg)
    repo = _repository(release_repo)
    package, commit, timestamp, tag = _release_metadata(repo, ref)
    previous_plugin, previous_versions, previous_latest = _read_previous(previous_index, repo)
    version = package["version"]
    if any(item["version"] == version for item in previous_versions):
        raise MarketplaceError("new version conflicts with existing Marketplace history")
    if previous_latest is not None and _version_tuple(version) <= _version_tuple(previous_latest):
        raise MarketplaceError("new version must be greater than the previous latest")

    shared_source: dict[str, Any] = {
        "source": "git-subdir", "url": REPOSITORY_URL, "path": "plugin", "sha": commit,
    }
    if tag is not None:
        shared_source["ref"] = tag
    claude_entry = {
        "name": "jianwei", "source": shared_source,
        "description": package["description"],
    }
    codex_source = {**shared_source, "path": "./plugin"}
    codex_entry = {
        "name": "jianwei", "source": codex_source,
        "description": package["description"],
        "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
        "category": "Productivity",
    }
    new_version = {
        "version": version,
        "publishTimestamp": timestamp,
        "engines": package["engines"],
        "source": {"type": "git", "url": REPOSITORY_URL, "commit": commit, "path": "plugin"},
    }
    plugin_history = {
        "name": "jianwei",
        "title": {"en": "Jianwei", "zh": "见微 Jianwei"},
        "description": package["description"],
        "author": "Lanbasara",
        "homepage": HOMEPAGE_URL,
        "repository": {"url": HOMEPAGE_URL},
        "metadata": {"keywords": ["investment", "research", "tanyuan", "投资", "研究", "探源"]},
        "dist-tags": {"latest": version},
        "versions": [new_version, *previous_versions],
    }
    # Keep history version objects unchanged while refreshing top-level
    # descriptive metadata from the new release.
    if previous_plugin is not None:
        plugin_history["title"] = previous_plugin["title"]
        plugin_history["metadata"] = previous_plugin["metadata"]

    install_ref = tag or commit

    def populate(stage: Path) -> None:
        _write_json(stage / ".claude-plugin" / "marketplace.json", {
            "name": "jianwei", "description": "见微 Jianwei 专属投资研究插件目录",
            "owner": {"name": "Lanbasara"}, "plugins": [claude_entry],
        })
        _write_json(stage / ".agents" / "plugins" / "marketplace.json", {
            "name": "jianwei", "interface": {"displayName": "见微 Jianwei"},
            "plugins": [codex_entry],
        })
        _write_json(stage / "dsh" / "index.json", {
            "format": 1,
            "title": {"en": "Jianwei", "zh": "见微 Jianwei"},
            "description": {
                "en": "Investment data and research skills for native agents.",
                "zh": "见微专属市场：探源数据能力与专业投资研究 skills。",
            },
            "plugins": [plugin_history],
        })
        (stage / "README.md").write_text(
            "# 见微 Jianwei Marketplace\n\n"
            "Jianwei 专属目录，独立于其他 marketplace。市场名：`jianwei`。\n\n"
            f"产品与完整安装说明：[Jianwei]({HOMEPAGE_URL}/blob/{ref}/plugin/INSTALL.md)。\n\n"
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
            f"dsh plugin --profile web add 'git+{REPOSITORY_URL}#{install_ref}&path:/plugin'\n"
            "```\n\n"
            "Python 环境和凭据需按产品说明首次配置。\n",
            encoding="utf-8",
        )

    _transactional_output(out, populate)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--release-repo", type=Path, required=True)
    parser.add_argument("--previous-index", type=Path)
    args = parser.parse_args()
    try:
        result = build(args.out, args.ref, args.release_repo, args.previous_index)
    except (MarketplaceError, OSError) as error:
        parser.error(str(error))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
