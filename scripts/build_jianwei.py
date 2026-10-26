#!/usr/bin/env python3
"""Assemble one release from independently maintained components; no QA is shipped."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from build_tanyuan import build as build_tanyuan

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "jianwei"
CONFIG = SOURCE / "distribution.json"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def copy(source: Path, target: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"交付源文件无效: {source.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def relative_file(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("交付文件路径必须是短的非空字符串。")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ValueError(f"交付文件路径必须位于声明根目录内: {value}")
    candidate = root.joinpath(*relative.parts)
    resolved_root = root.resolve()
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"交付文件越过声明根目录: {value}")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"交付文件不能经过符号链接: {value}")
    return resolved


def target_path(root: Path, value: object) -> Path:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("输出路径必须是短的非空字符串。")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise ValueError(f"输出路径必须位于发布包内: {value}")
    return root.joinpath(*relative.parts)


def string_map(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError(f"{label} 必须是有限的文件映射。")
    if not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ValueError(f"{label} 的路径必须是字符串。")
    return value


def load_distribution() -> dict:
    if CONFIG.is_symlink() or not CONFIG.is_file() or CONFIG.stat().st_size > 65536:
        raise ValueError("distribution.json 无效。")
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    expected = {"identity", "skills", "common_files", "runtime_files", "adapters"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("distribution.json 顶层字段无效。")
    identity = data["identity"]
    if not isinstance(identity, dict) or set(identity) != {"name", "description", "author", "repository"}:
        raise ValueError("distribution.json identity 无效。")
    if identity["name"] != "jianwei":
        raise ValueError("发布包名称必须为 jianwei。")
    if not isinstance(identity["description"], str) or not 1 <= len(identity["description"]) <= 4096:
        raise ValueError("发布包描述无效。")
    if identity["author"] != {"name": "Lanbasara"}:
        raise ValueError("发布包作者无效。")
    if (not isinstance(identity["repository"], str)
            or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
                                identity["repository"])):
        raise ValueError("发布仓库必须是无参数的 HTTPS GitHub URL。")
    skills = data["skills"]
    if (not isinstance(skills, list) or not 1 <= len(skills) <= 32
            or not all(isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name)
                       for name in skills) or len(set(skills)) != len(skills)):
        raise ValueError("skills 必须是唯一且有序的合法名称。")
    common_files = string_map(data["common_files"], "common_files")
    runtime_files = string_map(data["runtime_files"], "runtime_files")
    if any(target.startswith("skills/tanyuan/") for target in common_files.values()):
        raise ValueError("common_files 不能覆盖 Tanyuan 组件。")
    if (not all(source.startswith("runtime/") for source in runtime_files)
            or not all(target.startswith("scripts/") or target == "skills/tanyuan/bin/ty"
                       for target in runtime_files.values())):
        raise ValueError("runtime_files 必须从 runtime/ 映射到共享运行入口。")
    adapters = data["adapters"]
    if not isinstance(adapters, dict) or set(adapters) != {"claude", "codex", "dsh"}:
        raise ValueError("adapters 必须且只能声明 claude、codex、dsh。")
    adapter_targets = {"claude": {".claude-plugin/plugin.json"},
                       "codex": {".codex-plugin/plugin.json"},
                       "dsh": {"package.json", "cordis.patch.yml", "host/dsh.mjs"}}
    declared_targets = [*common_files.values(), *runtime_files.values()]
    for name, adapter in adapters.items():
        if not isinstance(adapter, dict) or set(adapter) != {"templates", "files"}:
            raise ValueError(f"{name} adapter 字段无效。")
        templates = string_map(adapter["templates"], f"{name}.templates")
        files = string_map(adapter["files"], f"{name}.files")
        sources = {*templates, *files}
        targets = {*templates.values(), *files.values()}
        if (not all(source.startswith(f"adapters/{name}/") for source in sources)
                or targets != adapter_targets[name]):
            raise ValueError(f"{name} adapter 必须只拥有其声明的宿主入口。")
        declared_targets.extend(templates.values())
        declared_targets.extend(files.values())
    if len(declared_targets) != len(set(declared_targets)):
        raise ValueError("交付映射不能重复写入同一目标。")
    reserved = {"plugin.json", "host/catalog.json", "manifest.json", ".env.example",
                "skills/tanyuan/bin/ty-upstream"}
    if reserved.intersection(declared_targets):
        raise ValueError("交付映射不能覆盖构建器生成或上游保留文件。")
    return data


def expand_template(value: object, tokens: dict[str, object]) -> object:
    if isinstance(value, dict):
        return {key: expand_template(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_template(item, tokens) for item in value]
    if isinstance(value, str) and value.startswith("$"):
        if value not in tokens:
            raise ValueError(f"未知 adapter 模板变量: {value}")
        return tokens[value]
    return value


def render_template(source: Path, target: Path, tokens: dict[str, object]) -> None:
    if source.stat().st_size > 65536:
        raise ValueError(f"adapter 模板过大: {source.name}")
    template = json.loads(source.read_text(encoding="utf-8"))
    write_json(target, expand_template(template, tokens))


def copy_declared(stage: Path, mapping: dict[str, str]) -> None:
    for source, target in mapping.items():
        copy(relative_file(SOURCE, source), target_path(stage, target))


def build(out: Path) -> dict:
    out = out.absolute()
    if out.exists() or out.is_symlink():
        raise ValueError("输出目录已存在；请使用新的 --out。")
    distribution = load_distribution()
    version = relative_file(SOURCE, "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("VERSION 必须是三段版本号。")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".jianwei-build-", dir=out.parent) as tmp:
        temp = Path(tmp)
        upstream = temp / "tanyuan"
        component = build_tanyuan(upstream)
        stage = temp / "jianwei"
        shutil.copytree(upstream / "skills", stage / "skills")
        binary = stage / "skills/tanyuan/bin/ty"
        binary.rename(binary.with_name("ty-upstream"))
        copy_declared(stage, distribution["common_files"])
        copy_declared(stage, distribution["runtime_files"])
        for adapter in distribution["adapters"].values():
            copy_declared(stage, adapter["files"])
        copy(upstream / ".env.example", stage / ".env.example")
        binary.chmod(0o755)
        (stage / "skills/tanyuan/bin/ty-upstream").chmod(0o755)
        (stage / "scripts/setup.py").chmod(0o755)

        identity = {"name": distribution["identity"]["name"], "version": version,
                    "description": distribution["identity"]["description"],
                    "author": distribution["identity"]["author"],
                    "repository": distribution["identity"]["repository"]}
        write_json(stage / "plugin.json", {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", **identity})
        catalog = []
        for name in distribution["skills"]:
            text = (stage / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            match = re.match(r"---\nname: ([a-z0-9-]+)\ndescription: ([^\n]+)\n---\n", text)
            if not match or match[1] != name:
                raise ValueError("当前发布器要求 name 与 description 使用单行 frontmatter。")
            catalog.append({"name": name, "description": match[2]})
        write_json(stage / "host/catalog.json", catalog)
        # Package only approved assembly outputs, including files still to be
        # rendered. Adapters must not maintain a second product allowlist.
        payload_files = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
        template_files = {target for adapter in distribution["adapters"].values()
                          for target in adapter["templates"].values()}
        tokens = {"$NAME": identity["name"], "$VERSION": identity["version"],
                  "$DESCRIPTION": identity["description"], "$AUTHOR": identity["author"],
                  "$REPOSITORY": identity["repository"],
                  "$GIT_REPOSITORY": "git+" + identity["repository"] + ".git",
                  "$FILES": sorted(payload_files | template_files | {"manifest.json"})}
        for adapter in distribution["adapters"].values():
            for source, target in adapter["templates"].items():
                render_template(relative_file(SOURCE, source), target_path(stage, target), tokens)
        upstream_files = json.loads((upstream / "manifest.json").read_text(encoding="utf-8"))["files"]
        hashes = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(stage.rglob("*")) if p.is_file()}
        write_json(stage / "manifest.json", {
            "name": "jianwei", "version": version, "files": hashes,
            "components": {"tanyuan": {"version": component["version"],
                                       "source_files": {k: v for k, v in upstream_files.items() if k.startswith("skills/")}},
                           **{name: {"sha256": hashes[f"skills/{name}/SKILL.md"]}
                              for name in distribution["skills"] if name != "tanyuan"}},
            "entry_adaptation": "skills/tanyuan/bin/ty is the Jianwei launcher; ty-upstream preserves the original entry."
        })
        stage.rename(out)
    return {"bundle": str(out), "version": version, "tanyuan": component["version"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.out), ensure_ascii=False))
