#!/usr/bin/env python3
"""Retired oracle adapter. This entry intentionally cannot manufacture answers."""
import sys


def main() -> int:
    print("旧 ty_adapter 已撤下：其题目专属结论污染了评测证据。"
          "请使用真实 agent 在隔离工作区加载交付包，保存答案后交给 score.py 作诊断；"
          "数据层行为请运行 qa/tanyuan/tests。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
