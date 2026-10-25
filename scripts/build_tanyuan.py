#!/usr/bin/env python3
"""Build the small, host-neutral delivery bundle. No tests or secrets are copied."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tanyuan"
# Explicit additions are reviewed here; never copy the repository recursively.
MODULES = """__init__ blobs cache cli config envelope errors garden http interface registry
downloads dns_worker documents pdf_worker fund_sources fund_data fund_metrics tools/fund
searchapi source_sets tushare util tools/__init__ tools/capital tools/entity
tools/events tools/filing tools/fundamental tools/global_ tools/history
tools/industry tools/macro tools/market tools/news tools/newsfeed tools/search""".split()
REFERENCES = """error-playbook evidence-rules news-layer news-recovery number-style
search-guide source-matrix workflows fund-workflow""".split()
PRODUCT_FILES = ["SKILL.md", "bin/ty", "requirements.txt"] + [
    f"tanyuan/{name}.py" for name in MODULES
] + [f"references/{name}.md" for name in REFERENCES] + [
    "references/source-sets/investment-news.json", "references/source-sets/policy-news.json"
]


def check_product(source: Path) -> None:
    allowed_imports = sys.stdlib_module_names | {"requests", "pypdf", "tanyuan"}
    for name in [*PRODUCT_FILES, "install.py", "README.md", ".env.example"]:
        path = source / name
        if any((source / Path(*Path(name).parts[:i])).is_symlink()
               for i in range(1, len(Path(name).parts) + 1)):
            raise ValueError(f"拒绝链接文件: {name}")
        if not path.is_file():
            raise ValueError(f"缺少交付文件: {name}")
        if name.endswith(".py") or name == "bin/ty":
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
            for node in ast.walk(tree):
                names = []
                segments = []
                if isinstance(node, ast.Import):
                    names = [a.name.split(".")[0] for a in node.names]
                    segments = [part for a in node.names for part in a.name.split(".")]
                elif isinstance(node, ast.ImportFrom):
                    segments = (node.module or "").split(".") + [a.name for a in node.names]
                    if node.level == 0 and node.module:
                        names = [node.module.split(".")[0]]
                if set(segments) & {"tests", "evals", "qa"}:
                    raise ValueError(f"交付代码依赖评测模块: {name}")
                if set(names) - allowed_imports:
                    raise ValueError(f"交付代码存在未声明依赖: {name}: {names}")
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if any(part in node.value for part in ("golden.jsonl", "truth_bundle.json", "qa/tanyuan/", "evals/")):
                        raise ValueError(f"交付代码引用评测数据: {name}")


def build(out: Path, source: Path = SOURCE) -> dict:
    out = out.absolute()
    if out.exists() or out.is_symlink():
        raise ValueError("输出目录已存在；请指定新的 --out，避免覆盖已有交付物。")
    check_product(source)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".tanyuan-build-", dir=out.parent) as tmp:
        stage = Path(tmp) / "bundle"
        skill = stage / "skills" / "tanyuan"
        for name in PRODUCT_FILES:
            dest = skill / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, dest)
        (skill / "bin" / "ty").chmod(0o755)
        for name in ("install.py", "README.md", ".env.example"):
            shutil.copyfile(source / name, stage / name)
        version = next(n.value.value for n in ast.parse(
            (source / "tanyuan" / "__init__.py").read_text(encoding="utf-8")
        ).body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__version__" for t in n.targets))
        for host in ("claude", "codex"):
            manifest = {"name": "tanyuan", "version": version,
                        "description": "投资研究数据工具：一份 Skill + ty CLI",
                        "author": {"name": "Tanyuan contributors"}}
            if host == "codex":
                manifest.update(skills="./skills/")
            dest = stage / f".{host}-plugin" / "plugin.json"
            dest.parent.mkdir()
            dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files = {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(stage.rglob("*")) if p.is_file()}
        inventory = {"name": "tanyuan", "version": version, "files": files}
        (stage / "manifest.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stage.rename(out)
    return {"bundle": str(out), "version": version, "files": len(files)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "tanyuan")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.out), ensure_ascii=False))
        return 0
    except (OSError, ValueError, SyntaxError) as exc:
        parser.exit(1, f"build: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
