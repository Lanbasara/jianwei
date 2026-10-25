#!/usr/bin/env python3
"""Install one skill and CLI, registering it with selected local agent hosts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile

HOST_DIRS = {"claude": ".claude/skills", "codex": ".agents/skills", "dsh": ".dsh/skills"}
MARKER = ".tanyuan-install.json"


def inventory(bundle: Path) -> dict:
    if (bundle / "manifest.json").is_symlink():
        raise ValueError("交付清单不能是符号链接。")
    data = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("交付清单必须是对象。")
    files = data.get("files")
    if data.get("name") != "tanyuan" or not isinstance(files, dict) or not files:
        raise ValueError("不是 tanyuan 交付包；请使用构建后的包运行 install.py。")
    required = {".env.example", "skills/tanyuan/SKILL.md", "skills/tanyuan/bin/ty", "skills/tanyuan/requirements.txt"}
    if not required <= files.keys():
        raise ValueError("交付包缺少必要文件。")
    for name, digest in files.items():
        part = PurePosixPath(name)
        if part.is_absolute() or ".." in part.parts or "\\" in name or part.as_posix() != name:
            raise ValueError("交付包包含非法路径。")
        path = bundle / name
        if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents) if p != bundle.parent):
            raise ValueError(f"交付包缺失文件或包含链接: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"交付包文件校验失败: {name}")
    return data


def run(argv: list[str]) -> None:
    subprocess.run(argv, check=True)


def install(bundle: Path, workspace: Path, hosts: list[str], python: str | None) -> dict:
    bundle = bundle.resolve()
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError("--workspace 必须是用户选择的已存在工作区目录。")
    prefix = workspace / ".tanyuan" / "plugin"
    bin_dir = workspace / ".tanyuan" / "bin"
    # Prevent a workspace-local installation from escaping through existing links.
    for relative in (".tanyuan", ".tanyuan/plugin", ".tanyuan/bin", *HOST_DIRS.values()):
        part = workspace
        for segment in Path(relative).parts:
            part = part / segment
            if part.is_symlink():
                raise ValueError(f"安装目录不能通过链接指向工作区外: {part}")
    data = inventory(bundle)
    skill = prefix / "skills" / "tanyuan"
    links = [(workspace / HOST_DIRS[h] / "tanyuan", skill) for h in sorted(set(hosts))]
    links.append((bin_dir / "ty", skill / "bin" / "ty"))
    if any(link.is_relative_to(prefix) or prefix.is_relative_to(link) for link, _ in links):
        raise ValueError("安装目录与宿主技能/命令注册位置必须分开。")
    # Check all destinations before doing work; never replace unrelated user files.
    if prefix.is_symlink() or bundle == prefix or bundle.is_relative_to(prefix) or prefix.is_relative_to(bundle):
        raise ValueError("安装位置必须独立于交付包，且不能是符号链接。")
    existing = prefix.exists()
    if existing:
        marker = prefix / MARKER
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != data:
            raise ValueError("安装位置已占用或版本不同；请保留现有安装并另选工作区，或明确移除旧安装后再安装。")
        inventory(prefix)
    for link, target in links:
        if link.exists() or link.is_symlink():
            if not link.is_symlink() or link.resolve() != target.resolve():
                raise ValueError(f"注册位置已占用，未覆盖: {link}")
    interpreter = Path(python).expanduser().absolute() if python else skill / ".venv" / "bin" / "python"
    if python:
        if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
            raise ValueError("--python 必须指向可执行的 Python 文件。")
        run([str(interpreter), "-c", "import sys, requests; assert sys.version_info >= (3, 10)"])
    if not existing:
        prefix.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".tanyuan-install-", dir=prefix.parent) as tmp:
            stage = Path(tmp) / "payload"
            for name in data["files"]:
                dest = stage / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(bundle / name, dest)
            shutil.copyfile(bundle / "manifest.json", stage / "manifest.json")
            (stage / "skills/tanyuan/bin/ty").chmod(0o755)
            (stage / MARKER).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            stage.rename(prefix)
    if not python:
        if not interpreter.is_file():
            run([sys.executable, "-m", "venv", str(skill / ".venv")])
        run([str(interpreter), "-m", "pip", "install", "--no-cache-dir", "--index-url", "https://pypi.org/simple",
             "-r", str(skill / "requirements.txt")])
    (skill / ".python").write_text(str(interpreter) + "\n", encoding="utf-8")
    run([str(interpreter), str(skill / "bin" / "ty"), "--version"])
    for link, target in links:
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.is_symlink():
            link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=target.is_dir())
    example = workspace / ".env.example"
    if not example.exists() and not example.is_symlink():
        with example.open("x", encoding="utf-8") as stream:
            stream.write((bundle / ".env.example").read_text(encoding="utf-8"))
    return {"workspace": str(workspace), "installed": str(prefix), "skill": str(skill), "command": str(bin_dir / "ty"),
            "hosts": sorted(set(hosts)), "python": str(interpreter),
            "env_file": str(workspace / ".env"),
            "note": "在所选工作区启动宿主；凭据放工作区 .env；CLI 为 ./.tanyuan/bin/ty。"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", nargs="+", choices=tuple(HOST_DIRS), required=True)
    parser.add_argument("--workspace", type=Path, required=True, help="用户选择的工作区；只在此目录安装")
    parser.add_argument("--python", help="复用已安装 requests 的 Python；省略则创建独立 venv 并安装依赖")
    args = parser.parse_args()
    try:
        result = install(Path(__file__).parent, args.workspace, args.host, args.python)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"install: {exc}\n未自动覆盖已有文件；失败后可用相同参数重试。\n")


if __name__ == "__main__":
    raise SystemExit(main())
