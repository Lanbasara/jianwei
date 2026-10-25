#!/usr/bin/env python
"""score.py — 探源 golden questions 判分器(可插拔被测对象)。

两种模式(同一个判分内核):
  A. 离线判分(默认): 已有答案文件, 直接打分
     .venv/bin/python qa/tanyuan/evals/score.py --answers answers.jsonl --out score.json
  B. 在线跑题: 给一条命令, 每题调一次, 由命令产出答案
     .venv/bin/python qa/tanyuan/evals/score.py --runner "python my_agent.py" --out score.json
     .venv/bin/python qa/tanyuan/evals/score.py --runner "python my_agent.py" --runner-mode batch

答案文件(JSONL, 一题一行; 缺题=0 分):
  {"qid":"M-01","answer":"2026-10-23 收盘价 1335.00 元...","sources":["https://qt.gtimg.cn/q=sh600519"],
   "latency_ms":812,"cost_usd":0.0004,"meta":{"model":"..."}}

runner 协议:
  per-question(默认): 每题起一个子进程, stdin 收题目 JSON, stdout 必须是一个答案 JSON
  batch: 起一个子进程, stdin 收全部题目 JSONL, stdout 每行一个答案 JSON

判分口径(详见 README.md):
  每题 = 加权 judge 通过率 ×100; 该拒答/该判空的题(refusal 类)判错 = 幻觉惩罚 -50;
  总分 = 各题得分的算术平均(允许负分), 另给 nonneg 版本。
"""
from __future__ import annotations

import argparse
import json
import hashlib
import re
import shlex
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GOLDEN_PATH = ROOT / "qa" / "tanyuan" / "evals" / "golden.jsonl"
SCORING_VERSION = "1.2-diagnostic"
HALLUCINATION_PENALTY = -50.0
AS_OF_YEAR = "2026"

# --------------------------------------------------------------------------- #
# 文本 / 数字 / 日期 解析
# --------------------------------------------------------------------------- #
# 先抹掉「像日期/时间/代码」的数字, 避免把 2026-10-23 解析成 2026、-10、-23
_STRIP_PATTERNS = [
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?",
    r"\d{4}年\d{1,2}月",
    r"\d{1,2}月\d{1,2}日",
    r"\d{4}-\d{1,2}-\d{1,2}[T ]\d{1,2}:\d{2}(?::\d{2})?[^\s，。;；]*",
    r"\d{4}-\d{2}-\d{2}",
    r"\d{8}",
    r"\d{1,2}:\d{2}(?::\d{2})?",
    r"\b\d{6}\.(?:SH|SZ|BJ|HK)\b",
    r"\b(?:0|3|6|8|9)\d{5}\b",
    r"\b\d{4}[EeHhQq]\d?\b",
    r"\bQ[1-4]\b",
    r"\bH[12]\b",
    r"\d{4}(?:年度|年上半年|年下半年|年中)",
]
_STRIP_RE = re.compile("|".join(_STRIP_PATTERNS))
_NUM_RE = re.compile(
    r"(?<![\d.,])"
    r"(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
    r"\s*(亿元|万元|千元|百万元|亿|万|%|％|倍|点|元|股|手|家|人|级|份|港元|港币|美元|HKD|USD)?"
)

UNIT_MAP = {
    "亿元": "亿元",
    "亿": "亿元",
    "万元": "万元",
    "万": "万元",
    "千元": "千元",
    "百万元": "百万元",
    "元": "元",
    "%": "%",
    "％": "%",
    "倍": "倍",
    "点": "点",
    "股": "股",
    "手": "手",
    "家": "家",
    "人": "人",
    "级": "级",
    "份": "份",
    "港元": "港元",
    "港币": "港元",
    "HKD": "港元",
    "美元": "美元",
    "USD": "美元",
    "": "",
}


def normalize_text(text: str) -> str:
    text = (text or "").replace("\u3000", " ")
    return text


def norm_unit(unit) -> str:
    if unit is None:
        return ""
    return UNIT_MAP.get(str(unit).strip(), str(unit).strip())


def extract_numbers(text: str) -> list[dict]:
    stripped = _STRIP_RE.sub(lambda m: " " * len(m.group(0)), normalize_text(text))
    out = []
    for m in _NUM_RE.finditer(stripped):
        raw = m.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        out.append({"value": value, "unit": norm_unit(m.group(2)), "pos": m.start(), "raw": m.group(0).strip()})
    return out


def num_close(a: float, b: float, tolerance: dict | None) -> bool:
    tolerance = tolerance or {"rel": 0.001}
    if "abs" in tolerance:
        return abs(a - b) <= tolerance["abs"] + 1e-12
    rel = tolerance.get("rel", 0.001)
    if b == 0:
        return abs(a) <= rel
    return abs(a - b) <= abs(b) * rel + 1e-12


def extract_dates(text: str) -> set[str]:
    text = normalize_text(text)
    found: set[str] = set()
    for m in re.finditer(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", text):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            found.add(f"{y}-{mo:02d}-{d:02d}")
    for m in re.finditer(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", text):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            found.add(f"{y}-{mo:02d}-{d:02d}")
    for m in re.finditer(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text):
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            found.add(f"{AS_OF_YEAR}-{mo:02d}-{d:02d}")
    for m in re.finditer(r"(?<![\d-])(\d{1,2})[-/](\d{1,2})(?![\d-])", text):
        mo, d = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            found.add(f"{AS_OF_YEAR}-{mo:02d}-{d:02d}")
    return found


def norm_date(value: str) -> str:
    value = str(value).strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", value)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", value)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return value


# --------------------------------------------------------------------------- #
# judges
# --------------------------------------------------------------------------- #
def judge_numeric(spec: dict, answer: str, _rec: dict) -> tuple[bool, str]:
    text = normalize_text(answer)
    tokens = extract_numbers(answer)
    keywords = spec.get("keywords") or []
    if keywords:
        hits = [m.start() for kw in keywords for m in re.finditer(re.escape(kw), normalize_text(answer))]
        if not hits:
            return False, "未找到指标上下文,不能仅按数字命中判正确"
        tokens = [t for t in tokens if any(abs(t["pos"] - p) <= 60 for p in hits)]
    tokens = [t for t in tokens if not re.search(
        r"(?:不是|并非|不为|不等于|错误(?:值)?(?:为|是)?|not\s*(?:equal to\s*)?)\s*$",
        text[max(0, t["pos"] - 20):t["pos"]], re.I)]
    unit_optional = spec.get("unit_optional", True)
    def matches(tok):
        return any(num_close(tok["value"], float(acc["value"]), spec.get("tolerance"))
                   and (tok["unit"] == norm_unit(acc.get("unit")) or (not tok["unit"] and unit_optional))
                   for acc in spec.get("accept", []))
    units = {norm_unit(a.get("unit")) for a in spec.get("accept", [])}
    relevant = [t for t in tokens if t["unit"] in units or (not t["unit"] and unit_optional)]
    if any(matches(t) for t in relevant) and any(not matches(t) for t in relevant):
        return False, "需人工复核:同一指标上下文存在多个不一致数值,不能按出现真值判正确"
    for acc in spec.get("accept", []):
        av = float(acc["value"])
        au = norm_unit(acc.get("unit"))
        for tok in tokens:
            if not num_close(tok["value"], av, spec.get("tolerance")):
                continue
            tu = tok["unit"]
            if not au:
                if tu == "" or unit_optional:
                    return True, f"命中 {av}{au or ''}（答案 {tok['raw']}）"
                continue
            if tu == au:
                return True, f"命中 {av}{au}（答案 {tok['raw']}）"
            if tu == "" and unit_optional:
                return True, f"命中 {av}{au}（答案未标单位, 允许）"
    return False, f"未命中期望值 {[str(a['value']) + str(a.get('unit') or '') for a in spec.get('accept', [])]}；答案数字: {[t['raw'] for t in tokens][:8]}"


def judge_text_any(spec: dict, answer: str, _rec: dict) -> tuple[bool, str]:
    text = normalize_text(answer).lower().replace(" ", "")
    for opt in spec.get("options", []):
        if normalize_text(str(opt)).lower().replace(" ", "") in text:
            return True, f"含「{opt}」"
    return False, f"未含任一: {spec.get('options')}"


def judge_text_all(spec: dict, answer: str, _rec: dict) -> tuple[bool, str]:
    text = normalize_text(answer).lower().replace(" ", "")
    missing = [o for o in spec.get("options", []) if normalize_text(str(o)).lower().replace(" ", "") not in text]
    return (not missing), ("全部命中" if not missing else f"缺: {missing}")


def judge_set_match(spec: dict, answer: str, _rec: dict) -> tuple[bool, str]:
    text = normalize_text(answer).lower().replace(" ", "")
    hits = [o for o in spec.get("options", []) if normalize_text(str(o)).lower().replace(" ", "") in text]
    need = int(spec.get("min_hits", 1))
    return len(hits) >= need, f"命中 {len(hits)}/{need}: {hits}"


def judge_date_in(spec: dict, answer: str, _rec: dict) -> tuple[bool, str]:
    want = {norm_date(d) for d in spec.get("dates", [])}
    got = extract_dates(answer)
    hit = want & got
    return bool(hit), f"期望 {sorted(want)}；答案日期 {sorted(got)}"


def judge_cite(spec: dict, answer: str, rec: dict) -> tuple[bool, str]:
    text = normalize_text(answer)
    urls = re.findall(r"https?://[^\s，。；;）)]+", text)
    urls += [str(u) for u in rec.get("sources", []) or []]
    domains = spec.get("domains") or []
    for url in urls:
        for dom in domains:
            if dom in url:
                return True, f"引用命中 {dom}: {url[:80]}"
    texts = [normalize_text(t).lower() for t in (spec.get("texts") or [])]
    low = text.lower()
    for t in texts:
        if t and t in low:
            return True, f"来源名命中「{t}」"
    for dom in domains:
        if dom and dom.lower() in low:
            return True, f"文中出现来源域「{dom}」"
    return False, f"未给出可回查来源(期望域名: {domains[:4]}...)"


def judge_refusal(spec: dict, answer: str, _rec: dict) -> tuple[bool, str]:
    text = normalize_text(answer).lower()
    hits = []
    for marker in spec.get("markers", []):
        needle = normalize_text(str(marker)).lower()
        for match in re.finditer(re.escape(needle), text):
            prefix = text[max(0, match.start() - 12):match.start()]
            if not re.search(r"(?:没有|并未|不是|并非|未|不|not\s*)\s*$", prefix):
                hits.append(marker)
                break
    return bool(hits), (f"命中拒答/否定标记 {hits[:3]}" if hits else f"未出现任何否定标记(期望: {spec.get('markers', [])[:4]}...)；疑似硬答")


def judge_forbid_numbers(spec: dict, answer: str, _rec: dict) -> tuple[bool, str]:
    text = normalize_text(answer)
    tokens = extract_numbers(text)
    units = {norm_unit(u) for u in (spec.get("units") or [])}
    keywords = spec.get("keywords") or []
    positive_only = spec.get("positive_only", False)
    window = int(spec.get("window", 80))
    for kw in keywords:
        for m in re.finditer(re.escape(kw), text):
            for tok in tokens:
                if abs(tok["pos"] - m.start()) > window:
                    continue
                if units and tok["unit"] not in units:
                    continue
                if positive_only and tok["value"] <= 0:
                    continue
                return False, f"在「{kw}」附近出现疑似编造数字 {tok['raw']}"
    return True, "未在敏感上下文附近发现编造数字"


def judge_forbid_text(spec: dict, answer: str, _rec: dict) -> tuple[bool, str]:
    low = normalize_text(answer).lower()
    hits = [t for t in spec.get("texts", []) if normalize_text(str(t)).lower() in low]
    return (not hits), ("未出现禁用表述" if not hits else f"出现禁用表述: {hits}")


JUDGES = {
    "numeric": judge_numeric,
    "text_any": judge_text_any,
    "text_all": judge_text_all,
    "set_match": judge_set_match,
    "date_in": judge_date_in,
    "cite": judge_cite,
    "refusal": judge_refusal,
    "forbid_numbers": judge_forbid_numbers,
    "forbid_text": judge_forbid_text,
}


# --------------------------------------------------------------------------- #
# 单题判分
# --------------------------------------------------------------------------- #
def grade_question(rec: dict, ans: dict | None) -> dict:
    if rec.get("validation_status") == "quarantined":
        return {"qid": rec["qid"], "category": rec["category"], "excluded": True,
                "score": None, "reason": rec.get("quarantine_reason")}
    judges_out = []
    if ans is None or not str(ans.get("answer", "")).strip():
        return {
            "qid": rec["qid"],
            "category": rec["category"],
            "score": 0.0,
            "answered": False,
            "hallucination": False,
            "latency_ms": None,
            "cost_usd": None,
            "sources": [],
            "judges": [{"type": "missing", "label": "未作答", "weight": 1, "pass": False, "role": "content", "reason": "答案文件缺少该题"}],
            "answer_excerpt": "",
        }
    answer = str(ans.get("answer", ""))
    total_w = 0.0
    passed_w = 0.0
    refusal_ok = None
    for judge in rec["judges"]:
        fn = JUDGES.get(judge["type"])
        if fn is None:
            continue
        ok, reason = fn(judge, answer, ans)
        total_w += judge["weight"]
        if ok:
            passed_w += judge["weight"]
        if judge["type"] == "refusal":
            refusal_ok = ok if refusal_ok is None else (refusal_ok and ok)
        judges_out.append(
            {
                "type": judge["type"],
                "label": judge["label"],
                "role": judge.get("role", "content"),
                "weight": judge["weight"],
                "pass": ok,
                "reason": reason,
            }
        )
    score = 100.0 * passed_w / total_w if total_w else 0.0
    hallucination = False
    if rec.get("expected_refusal") and refusal_ok is False:
        hallucination = True
        score = HALLUCINATION_PENALTY
    return {
        "qid": rec["qid"],
        "category": rec["category"],
        "trap": rec.get("trap", False),
        "score": round(score, 2),
        "answered": True,
        "hallucination": hallucination,
        "judges": judges_out,
        "requires_review": any(j["reason"].startswith("需人工复核") for j in judges_out),
        "answer_excerpt": answer[:300],
        "latency_ms": ans.get("latency_ms"),
        "cost_usd": ans.get("cost_usd"),
        "sources": ans.get("sources") or [],
    }


# --------------------------------------------------------------------------- #
# 取答案: 文件 / runner
# --------------------------------------------------------------------------- #
def load_answers(path: Path) -> dict[str, dict]:
    answers: dict[str, dict] = {}
    if not path.exists():
        raise SystemExit(f"答案文件不存在: {path}")
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{lineno} JSON 解析失败: {exc}") from exc
        qid = obj.get("qid")
        if not qid:
            raise SystemExit(f"{path}:{lineno} 缺 qid")
        answers[str(qid)] = obj
    return answers


def run_runner(cmd: str, golden: list[dict], mode: str, timeout: float) -> dict[str, dict]:
    golden = [r for r in golden if r.get("validation_status") != "quarantined"]
    answers: dict[str, dict] = {}
    if not golden:
        return answers
    argv = shlex.split(cmd)
    if mode == "batch":
        payload = "\n".join(
            json.dumps(
                {"qid": r["qid"], "question": r["question"], "category": r["category"], "as_of": r["as_of"]},
                ensure_ascii=False,
            )
            for r in golden
        )
        t0 = time.time()
        try:
            proc = subprocess.run(argv, input=payload, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            print(f"[runner] batch 超时({timeout}s)", file=sys.stderr)
            return answers
        elapsed = (time.time() - t0) * 1000
        if proc.returncode != 0:
            print(f"[runner] batch 退出码 {proc.returncode}; stderr={proc.stderr[-800:]}", file=sys.stderr)
        parsed = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("qid"):
                parsed.append(obj)
        per_q = elapsed / max(len(parsed), 1)  # batch 总耗时按题均摊, 避免把整批耗时记到每题
        for obj in parsed:
            obj.setdefault("latency_ms", per_q)
            answers[str(obj["qid"])] = obj
        return answers

    # per-question
    for rec in golden:
        q = {"qid": rec["qid"], "question": rec["question"], "category": rec["category"], "as_of": rec["as_of"]}
        t0 = time.time()
        try:
            proc = subprocess.run(argv, input=json.dumps(q, ensure_ascii=False), capture_output=True, text=True, timeout=timeout)
            elapsed = (time.time() - t0) * 1000
        except subprocess.TimeoutExpired:
            answers[rec["qid"]] = {"qid": rec["qid"], "answer": "", "latency_ms": timeout * 1000, "error": "timeout"}
            continue
        if proc.returncode != 0:
            answers[rec["qid"]] = {"qid": rec["qid"], "answer": "", "latency_ms": elapsed, "error": f"exit={proc.returncode} stderr={proc.stderr[-300:]}"}
            continue
        try:
            obj = json.loads(proc.stdout.strip())
        except json.JSONDecodeError:
            obj = {"answer": proc.stdout.strip()}
        obj["qid"] = rec["qid"]
        obj.setdefault("latency_ms", elapsed)
        answers[rec["qid"]] = obj
    return answers


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def summarize(results: list[dict], golden: list[dict]) -> dict:
    excluded = [r for r in results if r.get("excluded")]
    active = [(rec, res) for rec, res in zip(golden, results) if not res.get("excluded")]
    golden = [rec for rec, _ in active]
    results = [res for _, res in active]
    by_cat: dict[str, list[float]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r["score"])

    def cat_block(scores):
        return {
            "n": len(scores),
            "score": round(statistics.fmean(scores), 2),
            "perfect": sum(1 for s in scores if s >= 99.99),
            "zero": sum(1 for s in scores if s == 0),
            "hallucination": 0,
        }

    cats = {k: cat_block(v) for k, v in by_cat.items()}
    for r in results:
        if r["hallucination"]:
            cats[r["category"]]["hallucination"] += 1

    all_scores = [r["score"] for r in results]
    total = round(statistics.fmean(all_scores), 2) if all_scores else None

    # 指标
    numeric_total = numeric_pass = 0
    asof_total = asof_pass = 0
    src_total = src_pass = 0
    refusal_cases = refusal_pass = 0
    for rec, res in zip(golden, results):
        for j in res["judges"]:
            if j.get("type") == "missing":
                continue
            role = j.get("role")
            if j["type"] == "numeric":
                numeric_total += 1
                numeric_pass += 1 if j["pass"] else 0
            if role == "as_of":
                asof_total += 1
                asof_pass += 1 if j["pass"] else 0
            if j["type"] == "cite":
                src_total += 1
                src_pass += 1 if j["pass"] else 0
        if rec.get("expected_refusal"):
            refusal_cases += 1
            refusal_judges = [j for j in res["judges"] if j["type"] == "refusal"]
            if refusal_judges and all(j["pass"] for j in refusal_judges):
                refusal_pass += 1

    wrong = []
    for rec, res in zip(golden, results):
        if res["score"] >= 99.99:
            continue
        failed = [j for j in res["judges"] if not j["pass"]]
        wrong.append(
            {
                "qid": rec["qid"],
                "category": rec["category"],
                "score": res["score"],
                "hallucination": res["hallucination"],
                "expected": rec["expected"]["display"],
                "actual": res["answer_excerpt"],
                "reasons": [f"[{j['label']}/{j['type']}] {j['reason']}" for j in failed][:4],
            }
        )

    latencies = [a.get("latency_ms") for a in results if isinstance(a.get("latency_ms"), (int, float))]
    costs = [a.get("cost_usd") for a in results if isinstance(a.get("cost_usd"), (int, float))]

    return {
        "excluded_questions": excluded,
        "total_score": total,
        "total_score_nonneg": round(max(0.0, total), 2) if total is not None else None,
        "grade": grade_of(total) if total is not None else "未评分",
        "provisional": True,
        "acceptance_valid": False,
        "limitations": ["旧规则判分存在引用与否定语义误判，以及多指标数值误杀；只供诊断，不能证明产品能力。"],
        "by_category": cats,
        "metrics": {
            "n_questions": len(results),
            "n_requires_review": sum(bool(r.get("requires_review")) for r in results),
            "n_answered": sum(1 for r in results if r["answered"]),
            "n_perfect": sum(1 for r in results if r["score"] >= 99.99),
            "primary_source_hit_rate": round(src_pass / src_total, 4) if src_total else None,
            "numeric_accuracy": round(numeric_pass / numeric_total, 4) if numeric_total else None,
            "as_of_accuracy": round(asof_pass / asof_total, 4) if asof_total else None,
            "refusal_accuracy": round(refusal_pass / refusal_cases, 4) if refusal_cases else None,
            "refusal_cases": refusal_cases,
            "hallucination_count": sum(1 for r in results if r["hallucination"]),
            "avg_latency_ms": round(statistics.fmean(latencies), 1) if latencies else None,
            "avg_cost_usd": round(statistics.fmean(costs), 6) if costs else None,
            "judge_totals": {
                "numeric": [numeric_pass, numeric_total],
                "as_of": [asof_pass, asof_total],
                "source": [src_pass, src_total],
                "refusal": [refusal_pass, refusal_cases],
            },
        },
        "wrong_questions": wrong,
        "hallucinations": [w for w in wrong if w["hallucination"]],
    }


GRADES = [(95, "A+"), (90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F")]


def grade_of(score: float) -> str:
    for threshold, label in GRADES:
        if score >= threshold:
            return label
    return "F"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="探源 golden questions 判分器")
    ap.add_argument("--golden", default=str(GOLDEN_PATH))
    ap.add_argument("--answers", help="答案 JSONL 文件")
    ap.add_argument("--runner", help="被测对象命令(读 stdin 题目, 输出答案 JSON)")
    ap.add_argument("--runner-mode", choices=["per-question", "batch"], default="per-question")
    ap.add_argument("--out", required=True, help="评分生成物路径，放在题库之外的 reports/ 或工作区")
    ap.add_argument("--timeout", type=float, default=180.0, help="单题/整批超时秒数")
    ap.add_argument("--qids", help="只跑指定题号(逗号分隔), 用于子集冒烟; 缺省=全部")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    golden = [json.loads(line) for line in Path(args.golden).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not golden:
        raise SystemExit("golden.jsonl 为空")
    if args.qids:
        wanted = [q.strip() for q in args.qids.split(",") if q.strip()]
        by_qid = {r["qid"]: r for r in golden}
        unknown = [q for q in wanted if q not in by_qid]
        if unknown:
            raise SystemExit(f"--qids 里有不存在的题号: {unknown}")
        golden = [by_qid[q] for q in wanted]

    if args.answers:
        answers = load_answers(Path(args.answers))
        source = {"mode": "answers", "path": str(Path(args.answers))}
    elif args.runner:
        answers = run_runner(args.runner, golden, args.runner_mode, args.timeout)
        source = {"mode": "runner", "cmd": args.runner, "runner_mode": args.runner_mode}
    else:
        raise SystemExit("必须给 --answers <file> 或 --runner <cmd>")

    results = [grade_question(rec, answers.get(rec["qid"])) for rec in golden]
    summary = summarize(results, golden)

    score_doc = {
        "meta": {
            "scoring_version": SCORING_VERSION,
            "golden_sha256": hashlib.sha256(Path(args.golden).read_bytes()).hexdigest(),
            "scorer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "answers_sha256": hashlib.sha256(json.dumps(answers, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "scored_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
            "as_of": golden[0]["as_of"],
            "golden_file": str(Path(args.golden)),
            "answer_source": source,
            "hallucination_penalty": HALLUCINATION_PENALTY,
        },
        **summary,
        "details": [
            {
                "qid": r["qid"],
                "category": r["category"],
                "trap": r.get("trap", False),
                "score": r["score"],
                "answered": r["answered"],
                "hallucination": r["hallucination"],
                "judges": r["judges"],
                "requires_review": r.get("requires_review", False),
                "answer_excerpt": r["answer_excerpt"],
            }
            for r in results if not r.get("excluded")
        ],
    }
    out_path = Path(args.out) if args.out else Path(args.golden).parent / "score.json"
    out_path.write_text(json.dumps(score_doc, ensure_ascii=False, indent=1), encoding="utf-8")

    if not args.quiet:
        m = summary["metrics"]
        print("=" * 78)
        print("诊断结果：provisional=true，acceptance_valid=false；不能用于产品能力验收。")
        print(f"探源历史题库诊断 | {m['n_questions']} 题 | 已答 {m['n_answered']} | 规则满分 {m['n_perfect']}")
        print(f"总分 {summary['total_score']} ({summary['grade']})  |  非负 {summary['total_score_nonneg']}")
        print(
            "指标: 一手源命中率 {:.1%} | 数字精确 {:.1%} | as-of {:.1%} | 拒答正确 {:.1%} | 幻觉 {} 条 | 平均延迟 {} ms".format(
                m["primary_source_hit_rate"] or 0,
                m["numeric_accuracy"] or 0,
                m["as_of_accuracy"] or 0,
                m["refusal_accuracy"] or 0,
                m["hallucination_count"],
                m["avg_latency_ms"] if m["avg_latency_ms"] is not None else "-",
            )
        )
        print("-" * 78)
        for cat, blk in sorted(summary["by_category"].items()):
            print(f"  {cat:<12} n={blk['n']:<3} score={blk['score']:<7} 满分{blk['perfect']:<3} 0分{blk['zero']:<3} 幻觉{blk['hallucination']}")
        print("-" * 78)
        if summary["wrong_questions"]:
            print(f"错题 {len(summary['wrong_questions'])} 条:")
            for w in summary["wrong_questions"]:
                tag = " [幻觉]" if w["hallucination"] else ""
                print(f"  {w['qid']:<6} {w['score']:>7.2f}{tag}  {w['reasons'][0] if w['reasons'] else ''}"[:180])
        else:
            print("规则未检出错题；仍需独立语义核验。")
        print("=" * 78)
        print(f"score.json -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
