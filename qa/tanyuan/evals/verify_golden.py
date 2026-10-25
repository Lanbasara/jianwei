#!/usr/bin/env python
"""verify_golden.py — golden.jsonl 完整性 / 真值可回溯性检查。

检查项:
  1. 结构: qid 唯一、必填字段齐全、单位/容差存在;
  2. 取证: 每题 evidence 里的原始文件都存在(artifacts/golden/raw/*.json);
  3. 数值可回溯: 每个 numeric judge 的期望值, 至少有一个「原始单位」形式能在
     对应 raw 快照里字面找到 (例如 1335.0 / 7677216.283 / 57525672087.0);
     仅由原始字段算出来的派生值(亿元换算、买卖差等)记为 derived, 单独统计。

用法: .venv/bin/python qa/tanyuan/evals/verify_golden.py [--strict]
退出码: 0 = 通过; 1 = 有结构/取证错误(--strict 时 derived 也算失败)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "qa" / "tanyuan"))

from evidence import EvidencePathError, evidence_root, golden_path, path_within  # noqa: E402

GOLDEN = ROOT / "qa" / "tanyuan" / "evals" / "golden.jsonl"

REQUIRED = ["qid", "category", "question", "as_of", "answerable", "expected", "unit", "tolerance",
            "judges", "verify_by", "source_url", "evidence", "match_method"]
VALID_JUDGES = {"numeric", "text_any", "text_all", "set_match", "date_in", "cite", "refusal", "forbid_numbers", "forbid_text"}


def numeric_forms(value: float) -> list[str]:
    forms = {str(value), repr(float(value))}
    if float(value).is_integer():
        forms.add(str(int(value)))
        forms.add(f"{int(value)}.0")
    else:
        forms.add(f"{value:.2f}")
        forms.add(f"{value:.4f}")
        forms.add(f"{value:.6f}")
        forms.add(f"{value:g}")
    return [f for f in forms if f and f != "None"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="derived 值也算错误")
    args = ap.parse_args()

    try:
        local_golden = evidence_root()
        bundle_path = path_within(local_golden, "truth_bundle.json")
    except EvidencePathError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    rows = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not bundle_path.is_file():
        print("ERROR: local golden directory is missing truth_bundle.json", file=sys.stderr)
        return 2
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    raw_manifest = bundle["raw_files"]

    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    numeric_total = numeric_verified = numeric_derived = 0

    raw_cache: dict[str, str] = {}
    evidence_files: dict[str, Path] = {}

    def raw_text(paths: list[str]) -> str:
        text = ""
        for rel in paths:
            if rel not in raw_cache:
                raw_cache[rel] = evidence_files[rel].read_text(encoding="utf-8", errors="replace")
            text += raw_cache[rel]
        return text

    for rec in rows:
        qid = rec.get("qid", "<no-qid>")
        if qid in seen:
            errors.append(f"{qid}: qid 重复")
        seen.add(qid)
        for field in REQUIRED:
            if field not in rec:
                errors.append(f"{qid}: 缺字段 {field}")
        for j in rec.get("judges", []):
            if j.get("type") not in VALID_JUDGES:
                errors.append(f"{qid}: 未知 judge 类型 {j.get('type')}")
            if "weight" not in j:
                errors.append(f"{qid}: judge {j.get('label')} 缺 weight")
        # evidence 存在性
        if not rec.get("evidence"):
            errors.append(f"{qid}: evidence 为空")
        for rel in rec.get("evidence", []):
            try:
                path = golden_path(local_golden, rel)
            except EvidencePathError:
                errors.append(f"{qid}: evidence 路径越界 {rel}")
                continue
            if not path.is_file():
                errors.append(f"{qid}: evidence 文件不存在 {rel}")
                continue
            evidence_files[rel] = path
        # 数值可回溯
        text = raw_text([r for r in rec.get("evidence", []) if r in evidence_files])
        for j in rec.get("judges", []):
            if j.get("type") != "numeric":
                continue
            numeric_total += 1
            found = False
            for acc in j["accept"]:
                try:
                    value = float(acc["value"])
                except (TypeError, ValueError):
                    continue
                for form in numeric_forms(value):
                    if form in text:
                        found = True
                        break
                if found:
                    break
            if found:
                numeric_verified += 1
            else:
                numeric_derived += 1
                warnings.append(f"{qid}/{j['label']}: 期望值 {[a['value'] for a in j['accept']]} 未在 raw 快照字面出现(派生值)")

    # 分类覆盖
    cats: dict[str, int] = {}
    for rec in rows:
        cats[rec["category"]] = cats.get(rec["category"], 0) + 1
    traps = [r["qid"] for r in rows if r.get("trap")]

    print("隔离题:", [r["qid"] for r in rows if r.get("validation_status") == "quarantined"])
    print(f"golden: {len(rows)} 题 | categories={cats}")
    print(f"trap 题: {len(traps)} 道 -> {traps}")
    print(f"numeric judge: {numeric_total} 个 | raw 字面可回溯 {numeric_verified} | 派生 {numeric_derived}")
    print(f"evidence 文件: {len(raw_manifest)} 个 raw 快照, {sum(len(r.get('evidence', [])) for r in rows)} 条引用")
    print(f"expected_refusal 题: {[r['qid'] for r in rows if r.get('expected_refusal')]}")

    if warnings:
        print("\n派生值(由 raw 字段计算, 非字面出现) —— 允许, 但需在 verify_by 里写清算法:")
        for w in warnings:
            print("  -", w)
    if errors:
        print("\nERRORS:")
        for e in errors:
            print("  -", e)
        return 1
    if args.strict and numeric_derived:
        print(f"\n--strict: {numeric_derived} 个派生值未字面回溯 -> fail")
        return 1
    print("\nOK: 结构 / 文件存在性 / 数值字面回溯检查通过;不证明单位或指标语义正确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
