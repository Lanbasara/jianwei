"""Deterministic, source-explicit fund statistics; never infer total return from accum_nav."""
from __future__ import annotations

import math
import statistics
from datetime import date

from . import fund_sources as free
from .errors import bad_arg


def select_series(rows, field="adj_nav"):
    """Keep latest announced revision, reject same-date/same-announcement conflicts."""
    if field not in ("adj_nav", "unit_nav"):
        raise bad_arg("指标只支持 adj_nav 或 unit_nav；累计净值不能作为再投资收益序列")
    grouped = {}
    for row in rows:
        grouped.setdefault(row["nav_date"], []).append(row)
    chosen, missing, conflicts, revisions = [], [], [], []
    for day, versions in sorted(grouped.items()):
        latest = max(r.get("ann_date") or "" for r in versions)
        newest = [r for r in versions if (r.get("ann_date") or "") == latest]
        values = {free.number(r.get(field)) for r in newest}
        if len(values) > 1:
            conflicts.append(day)
            continue
        value = next(iter(values))
        if value is None or value <= 0:
            missing.append(day)
            continue
        if len(versions) > 1:
            revisions.append({"date": day, "chosen_ann_date": latest or None, "versions": len(versions)})
        chosen.append({"date": day, "value": value, "ann_date": latest or None,
                       "source": newest[0].get("source")})
    return chosen, {"missing_or_nonpositive_dates": missing, "conflicting_dates": conflicts,
                    "revision_selection": revisions, "selection_rule": "latest_ann_date; reject same-ann conflicts"}


def statistics_for(points, periods_per_year=252):
    if not isinstance(periods_per_year, int) or isinstance(periods_per_year, bool) or not 1 <= periods_per_year <= 366:
        raise bad_arg("periods_per_year 必须为 1–366 的整数")
    if len(points) < 2:
        return {"status": "unavailable", "reason": "至少需要两个不同日期的正数观测值", "sample_count": len(points)}
    values = [p["value"] for p in points]
    if any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values):
        return {"status": "unavailable", "reason": "输入包含非有限数或非正数", "sample_count": len(points)}
    returns = [right / left - 1 for left, right in zip(values, values[1:])]
    ratio = values[-1] / values[0]
    if not math.isfinite(ratio) or any(not math.isfinite(r) for r in returns):
        return {"status": "unavailable", "reason": "输入比值超出有限数范围", "sample_count": len(points)}
    peak, drawdown, peak_day, trough_day = values[0], 0.0, points[0]["date"], points[0]["date"]
    current_peak_day = peak_day
    for p in points:
        if p["value"] > peak:
            peak, current_peak_day = p["value"], p["date"]
        dd = p["value"] / peak - 1
        if dd < drawdown:
            drawdown, peak_day, trough_day = dd, current_peak_day, p["date"]
    days = (date.fromisoformat(points[-1]["date"]) - date.fromisoformat(points[0]["date"])).days
    try:
        annualized = math.expm1(math.log(ratio) * 365.25 / days)
    except (OverflowError, ValueError, ZeroDivisionError):
        annualized = None
    if annualized is not None and not math.isfinite(annualized):
        annualized = None
    gaps = [(date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days for a, b in zip(points, points[1:])]
    try:
        deviation = statistics.stdev(returns) if len(returns) >= 2 else None
    except (OverflowError, ValueError):
        deviation = None
    if deviation is not None and not math.isfinite(deviation):
        deviation = None
    annual_vol = deviation * math.sqrt(periods_per_year) if deviation is not None else None
    if annual_vol is not None and not math.isfinite(annual_vol):
        annual_vol = None
    return {"status": "ok", "sample_count": len(points), "return_observations": len(returns),
            "start_date": points[0]["date"], "end_date": points[-1]["date"],
            "period_return": ratio - 1, "annualized_return": annualized,
            "max_drawdown": drawdown, "drawdown_peak_date": peak_day, "drawdown_trough_date": trough_day,
            "observation_return_stddev": deviation,
            "annualized_volatility_estimate": annual_vol,
            "unavailable_metrics": {k: "insufficient observations or arithmetic out of finite range" for k, v in
                                    (("annualized_return", annualized), ("observation_return_stddev", deviation),
                                     ("annualized_volatility_estimate", annual_vol)) if v is None},
            "annualization": {"periods_per_year": periods_per_year,
                              "assumption": "each adjacent observation treated as one equally spaced return period",
                              "calendar_verified": False, "max_calendar_gap_days": max(gaps)},
            "formulas": {"period_return": "last / first - 1", "annualized_return": "(last/first)^(365.25/calendar_days)-1",
                         "max_drawdown": "min(value/running_peak - 1)",
                         "observation_return_stddev": "sample stdev of adjacent simple returns (ddof=1)",
                         "annualized_volatility_estimate": "observation_return_stddev * sqrt(periods_per_year)"},
            "units": "ratios, not percentage points; multiply by 100 for %"}


def calculate(rows, field="adj_nav", periods_per_year=252):
    points, selection = select_series(rows, field)
    out = {"series_field": field, "basis": "provider_adjusted_nav" if field == "adj_nav" else "unit_nav_change_excludes_distribution_adjustments",
           "input_points": points, "selection": selection,
           "cost_scope": "NAV-based; excludes investor-specific subscription/redemption fees and taxes",
           "limitations": ["复权算法和再投资假设来自供应商，未独立重建；累计净值未用于收益计算" if field == "adj_nav"
                           else "单位净值变化不等于含分红/拆分调整的投资总收益",
                           "波动率年化仅为等间隔观测假设估算；未核对基金应披露日历，缺失观测可低估风险"]}
    if selection["conflicting_dates"] or selection["missing_or_nonpositive_dates"]:
        out["statistics"] = {"status": "unavailable", "reason": "所选序列存在缺失、非正数或同日冲突；不静默删点计算"}
    else:
        out["statistics"] = statistics_for(points, periods_per_year)
    return out


def benchmark_compare(fund_points, benchmark_rows, fund_basis, benchmark_basis,
                      currency_confirmed=False, periods_per_year=252):
    """Aligned descriptive comparison only when the caller confirms compatible bases.

    Caller declarations are exposed, never promoted to verified source facts.
    """
    mapping, conflicts = {}, set()
    for row in benchmark_rows:
        d, value = row.get("trade_date"), free.number(row.get("close"))
        if d in mapping and mapping[d] != value:
            conflicts.add(d)
        mapping[d] = value
    compatible = fund_basis == "adj_nav" and benchmark_basis == "total_return"
    if not compatible or not currency_confirmed or conflicts:
        return {"status": "unavailable", "reason": "需复权净值、明确的总收益基准及相同币种；价格指数/未知口径或冲突不能混算",
                "declared_benchmark_basis": benchmark_basis, "currency_confirmed_by_caller": currency_confirmed,
                "basis_verification": "caller_declaration_not_independently_verified",
                "conflicting_dates": sorted(conflicts)}
    aligned = [p for p in fund_points if p["date"] in mapping and mapping[p["date"]] is not None and mapping[p["date"]] > 0]
    if len(aligned) < 3:
        return {"status": "unavailable", "reason": "至少需要三个共同日期的正数观测"}
    fund_stats = statistics_for(aligned, periods_per_year)
    bp = [{"date": p["date"], "value": mapping[p["date"]]} for p in aligned]
    benchmark_stats = statistics_for(bp, periods_per_year)
    if fund_stats["status"] != "ok" or benchmark_stats["status"] != "ok":
        return {"status": "unavailable", "reason": "基金或基准计算超出有限数范围"}
    active = [(b["value"] / a["value"] - 1) - (mapping[b["date"]] / mapping[a["date"]] - 1)
              for a, b in zip(aligned, aligned[1:])]
    try:
        tracking_error = statistics.stdev(active) * math.sqrt(periods_per_year)
    except (OverflowError, ValueError):
        tracking_error = None
    if tracking_error is not None and not math.isfinite(tracking_error):
        tracking_error = None
    return {"status": "ok" if tracking_error is not None else "partial", "aligned_dates": [p["date"] for p in aligned],
            "fund_statistics": fund_stats, "benchmark_statistics": benchmark_stats,
            "period_return_difference": fund_stats["period_return"] - benchmark_stats["period_return"],
            "tracking_error_estimate": tracking_error,
            "tracking_error_missing_reason": None if tracking_error is not None else "arithmetic_out_of_finite_range",
            "formula": "sample stdev(fund adjacent return - benchmark adjacent return) * sqrt(periods_per_year)",
            "excluded_fund_dates": [p["date"] for p in fund_points if p not in aligned],
            "benchmark_basis": benchmark_basis, "currency_confirmed_by_caller": True,
            "basis_verification": "caller_declaration_not_independently_verified",
            "limitations": ["按共同观测日对齐，无填充；市场时区、收盘时点和估值滞后未消除", "不是官方跟踪误差或基金排名"]}
