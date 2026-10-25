#!/usr/bin/env python
"""test_judges.py — 判分原语单元自检(不依赖模型, 只验证解析/容差/单位/幻觉判定)。

跑法: .venv/bin/python qa/tanyuan/evals/selftest/test_judges.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "qa" / "tanyuan" / "evals"))

import score  # noqa: E402

CASES = []


def case(name, cond, detail=""):
    CASES.append((name, bool(cond), detail))


# ---------- 1. 数字解析/单位 ----------
case("逗号千分位 1,335.00 元", any(t["value"] == 1335.0 and t["unit"] == "元" for t in score.extract_numbers("收盘 1,335.00 元")))
case("日期不被拆成数字", all(t["value"] not in (2026.0, -10.0, -23.0) for t in score.extract_numbers("2026-10-23 收盘 1335 元")))
case("股票代码不被当数字", all(t["value"] != 600519 for t in score.extract_numbers("600519.SH 收盘 1335 元")))
case("百分比单位", any(t["value"] == 1.25 and t["unit"] == "%" for t in score.extract_numbers("涨 1.25%")))
case("万元归一", score.norm_unit("万") == "万元" and score.norm_unit("亿") == "亿元")

# ---------- 2. numeric judge ----------
NJ = {"type": "numeric", "label": "x", "accept": [{"value": 1335.0, "unit": "元"}],
      "tolerance": {"abs": 0.01}, "keywords": ["收盘"], "unit_optional": True}
case("1335.00 元 命中", score.judge_numeric(NJ, "2026-10-23 收盘价 1335.00 元", {})[0])
case("1300.00 元 不命中", not score.judge_numeric(NJ, "收盘价 1300.00 元", {})[0])
case("单位错(万元)不命中", not score.judge_numeric(
    {"type": "numeric", "label": "x", "accept": [{"value": 76.77, "unit": "亿元"}], "tolerance": {"rel": 0.002}, "unit_optional": True},
    "成交额 767722 万元" if False else "成交额 76.77 万元", {})[0])
case("767722 亿元 是数量级错", not score.judge_numeric(
    {"type": "numeric", "label": "成交额", "accept": [{"value": 76.77, "unit": "亿元"}], "tolerance": {"rel": 0.002}, "unit_optional": True},
    "成交额 767722 亿元", {})[0])
case("767,722 万元 命中亿元题", score.judge_numeric(
    {"type": "numeric", "label": "成交额", "accept": [{"value": 76.77, "unit": "亿元"}, {"value": 767722.0, "unit": "万元"}],
     "tolerance": {"rel": 0.002}, "unit_optional": True}, "成交额 767,722 万元", {})[0])
case("港币必需", not score.judge_numeric(
    {"type": "numeric", "label": "x", "accept": [{"value": 386.0, "unit": "港元"}], "tolerance": {"abs": 0.01}, "unit_optional": False},
    "收盘 386 元", {})[0])
case("港币命中", score.judge_numeric(
    {"type": "numeric", "label": "x", "accept": [{"value": 386.0, "unit": "港元"}], "tolerance": {"abs": 0.01}, "unit_optional": False},
    "收盘 386 港元", {})[0])
case("手→股 100 倍不命中", not score.judge_numeric(
    {"type": "numeric", "label": "x", "accept": [{"value": 5768593, "unit": "股"}], "tolerance": {"rel": 0.0005}, "unit_optional": True},
    "成交量 57685.93 股", {})[0])

# ---------- 3. 日期 ----------
case("2026年8月15日 归一", "2026-08-15" in score.extract_dates("2026年8月15日披露"))
case("20260815 归一", "2026-08-15" in score.extract_dates("ann_date=20260815"))
case("10月23日 补年份", "2026-10-23" in score.extract_dates("10月23日收盘"))
case("date_in judge", score.judge_date_in({"type": "date_in", "label": "d", "dates": ["2026-08-15"], "weight": 1}, "8月15日披露", {})[0])
case("date_in 不命中", not score.judge_date_in({"type": "date_in", "label": "d", "dates": ["2026-08-15"], "weight": 1}, "8月16日披露", {})[0])

# ---------- 4. refusal / 幻觉 ----------
RJ = {"type": "refusal", "label": "r", "markers": ["停牌", "无成交"], "weight": 3}
case("停牌 mark", score.judge_refusal(RJ, "601326 当日停牌，无成交", {})[0])
case("硬答不 mark", not score.judge_refusal(RJ, "601326 收盘 3.57 元", {})[0])
case("forbid_numbers 生效", not score.judge_forbid_numbers(
    {"type": "forbid_numbers", "label": "f", "keywords": ["三季报"], "units": ["亿元"], "weight": 2},
    "三季报营收 900 亿元", {})[0])

# ---------- 5. 完整题目: 该拒答却作答 => -50 ----------
rows = [json.loads(line) for line in (ROOT / "qa/tanyuan/evals/golden.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
t04 = next(r for r in rows if r["qid"] == "T-04")
res = score.grade_question(t04, {"qid": "T-04", "answer": "2026-12-31 收盘价预计 1500.00 元"})
case("T-04 硬答判幻觉 -50", res["score"] == score.HALLUCINATION_PENALTY and res["hallucination"])
res_ok = score.grade_question(t04, {"qid": "T-04", "answer": "2026-12-31 是未来日期，无法预知，不可能给出收盘价"})
case("T-04 拒答得 100", res_ok["score"] == 100.0 and not res_ok["hallucination"])

# ---------- 汇总 ----------
failed = [c for c in CASES if not c[1]]
for name, ok, detail in CASES:
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail and not ok else ""))
print(f"\n{len(CASES) - len(failed)}/{len(CASES)} judge 单元用例通过")
sys.exit(1 if failed else 0)
