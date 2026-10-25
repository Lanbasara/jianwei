#!/usr/bin/env python3
"""Prepare Jianwei's user Python environment once, independently of the task directory."""
import argparse
import json
import subprocess
import sys

if sys.version_info < (3, 10):
    raise SystemExit("Jianwei requires Python 3.10+.")
sys.dont_write_bytecode = True
from runtime import setup, status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="离线检查环境与凭据名称，不显示密钥值")
    parser.add_argument("--python", help="复用已有依赖的 Python 绝对路径；省略则创建独立环境")
    parser.add_argument("--env-file", help="选用已有凭据文件；省略则保留选择或创建空白模板")
    args = parser.parse_args()
    try:
        if args.status:
            if args.python or args.env_file:
                parser.error("--status 不能与 --python 或 --env-file 同时使用")
            print(json.dumps(status(), ensure_ascii=False, indent=2))
            return 0
        setup(args.python, args.env_file)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("setup: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
