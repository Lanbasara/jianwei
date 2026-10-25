#!/usr/bin/env python
"""fetch_truth.py — 为 golden questions 采集「真值」并留原始取证。

设计:
  1. 每个 *dataset* 是一次原始取数(一手源), 原样落盘到 artifacts/golden/raw/<name>.json;
  2. 由原始数据推导出 truth_bundle(每题期望值), 落盘 artifacts/golden/truth_bundle.json;
  3. 每次取数写 artifacts/golden/fetch_log.jsonl(时间/source/ok/rows), 保证可回溯。

可复跑: `set -a; source .env; set +a; .venv/bin/python qa/tanyuan/evals/fetch_truth.py`
注意: as_of 固定为 2026-10-24T11:40+08:00(最近交易日 2026-10-23)。快照会随日期漂移,
      但本题集的 as_of 已写死; 重跑用于「同一 as_of 的复核」, 不是刷新答案。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "audit_astock"))

AS_OF = "2026-10-24T11:40:00+08:00"
LAST_TRADE_DATE = "20261023"
RAW_DIR = ROOT / "artifacts" / "golden" / "raw"
OUT_DIR = ROOT / "artifacts" / "golden"
TUSHARE_URL = "https://api.tushare.pro"

LOG_PATH = OUT_DIR / "fetch_log.jsonl"

_TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
_askd = None


def now_cn() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def log(entry: dict) -> None:
    entry.setdefault("fetched_at", now_cn())
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def save_raw(name: str, payload) -> str:
    path = RAW_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return str(path.relative_to(ROOT))


# --------------------------------------------------------------------------- #
# 取数器
# --------------------------------------------------------------------------- #
def tushare(api_name: str, params: dict, *, note: str = "", fields: str = "", retries: int = 3) -> dict:
    """调 Tushare, 返回 {rows, fields, raw}. 失败抛 RuntimeError(不静默给空)。"""
    if not _TUSHARE_TOKEN:
        raise RuntimeError("TUSHARE_TOKEN 未设置(先 set -a; source .env; set +a)")
    body = json.dumps({"api_name": api_name, "token": _TUSHARE_TOKEN, "params": params, "fields": fields}).encode()
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(TUSHARE_URL, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as resp:
                payload = json.loads(resp.read().decode())
            break
        except Exception as exc:  # network hiccup
            last_err = exc
            time.sleep(1.0 + attempt)
    else:
        log({"dataset": note or api_name, "source": "tushare", "api": api_name, "params": params, "ok": False, "error": str(last_err)})
        raise RuntimeError(f"tushare {api_name} failed: {last_err}")

    if payload.get("code") != 0:
        log({"dataset": note or api_name, "source": "tushare", "api": api_name, "params": params, "ok": False, "error": payload.get("msg")})
        raise RuntimeError(f"tushare {api_name} code={payload.get('code')} msg={payload.get('msg')}")
    data = payload["data"]
    rows = [dict(zip(data["fields"], item)) for item in data["items"]]
    log({"dataset": note or api_name, "source": "tushare", "api": api_name, "params": params, "ok": True, "rows": len(rows)})
    time.sleep(0.16)
    return {"api": api_name, "params": params, "fields": data["fields"], "rows": rows}


def askd():
    global _askd
    if _askd is None:
        import askd as mod  # noqa: PLC0415  (lazy import; module runs network on import? no)

        _askd = mod
    return _askd


def wrap_askd(name: str, fn, *args, **kwargs):
    """调 a-stock-data 抽出的函数, 把 DataFrame 转成可落盘的 records。"""
    try:
        out = fn(*args, **kwargs)
    except Exception as exc:
        log({"dataset": name, "source": "askd", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        raise
    if hasattr(out, "to_dict"):
        payload = out.to_dict(orient="records")
        shape = {"kind": "dataframe", "rows": len(payload)}
    else:
        payload = out
        shape = {"kind": type(out).__name__}
    log({"dataset": name, "source": "askd", **shape, "ok": True})
    return payload


def http_raw(name: str, url: str, *, encoding: str = "utf-8", timeout: int = 20) -> str:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout) as resp:
            raw = resp.read()
        text = raw.decode(encoding, errors="replace")
        log({"dataset": name, "source": "http", "url": url, "ok": True, "bytes": len(raw)})
        return text
    except Exception as exc:
        log({"dataset": name, "source": "http", "url": url, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        raise


def cninfo_search(keyword: str, sdate: str, edate: str, page_size: int = 20) -> list[dict]:
    """巨潮全文检索(用于「某段时间哪些公司发了某类公告」这类题)。"""
    body = (
        f"searchkey={urllib.parse.quote(keyword)}&sdate={sdate}&edate={edate}"
        f"&isfulltext=false&sortName=pubdate&sortType=desc&pageNum=1&pageSize={page_size}"
    ).encode()
    req = urllib.request.Request(
        "http://www.cninfo.com.cn/new/fulltextSearch/full",
        data=body,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log({"dataset": f"cninfo_search:{keyword}", "source": "cninfo", "ok": False, "error": str(exc)})
        raise
    anns = payload.get("announcements") or []
    rows = [
        {
            "secCode": a.get("secCode"),
            "secName": a.get("secName"),
            "title": (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", ""),
            "date": datetime.fromtimestamp(a.get("announcementTime", 0) / 1000, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d"),
            "url": "http://static.cninfo.com.cn/" + (a.get("adjunctUrl") or ""),
        }
        for a in anns
    ]
    log({"dataset": f"cninfo_search:{keyword}", "source": "cninfo", "ok": True, "rows": len(rows)})
    return rows


# --------------------------------------------------------------------------- #
# 采集(按域)
# --------------------------------------------------------------------------- #
def fetch_all() -> dict:
    import urllib.parse  # noqa: F401  (used by cninfo_search, imported here to keep top tidy)

    ds: dict[str, object] = {}
    A = askd()

    # ---- 行情: 日线 + 估值 + 指数 ----
    ds["daily_20261023"] = tushare("daily", {"trade_date": LAST_TRADE_DATE}, note="daily_20261023")
    ds["daily_basic_20261023"] = tushare("daily_basic", {"trade_date": LAST_TRADE_DATE}, note="daily_basic_20261023")
    ds["index_daily_20261023"] = tushare("index_daily", {"trade_date": LAST_TRADE_DATE}, note="index_daily_20261023")
    ds["index_global_20261023"] = tushare("index_global", {"trade_date": LAST_TRADE_DATE}, note="index_global_20261023")
    ds["kline_600519"] = tushare("daily", {"ts_code": "600519.SH", "start_date": "20261015", "end_date": LAST_TRADE_DATE}, note="kline_600519")
    ds["tencent_quote"] = wrap_askd(
        "tencent_quote",
        A.tencent_quote,
        ["600519", "000001", "sh000001", "000300", "300750", "601326", "000004", "300104"],
    )
    ds["tencent_quote_bj"] = wrap_askd("tencent_quote_bj", A.tencent_quote, ["920992", "bj920992"])
    ds["tencent_kline_600519"] = wrap_askd("tencent_kline_600519", A.tencent_kline, "600519", count=4)
    ds["tencent_raw_hk_us"] = http_raw("tencent_raw_hk_us", "https://qt.gtimg.cn/q=hk00700,usAAPL", encoding="gbk")

    # ---- 停牌/涨跌停/资金/两融/龙虎榜/行业 ----
    ds["suspend_d_20261023"] = tushare("suspend_d", {"trade_date": LAST_TRADE_DATE}, note="suspend_d_20261023")
    ds["limit_list_20261023"] = {
        "U": tushare("limit_list_d", {"trade_date": LAST_TRADE_DATE, "limit_type": "U", "limit": 1000}, note="limit_list_U"),
        "D": tushare("limit_list_d", {"trade_date": LAST_TRADE_DATE, "limit_type": "D", "limit": 1000}, note="limit_list_D"),
    }
    ds["moneyflow_20261023"] = tushare("moneyflow", {"trade_date": LAST_TRADE_DATE}, note="moneyflow_20261023")
    ds["margin_20261023"] = tushare("margin_detail", {"trade_date": LAST_TRADE_DATE}, note="margin_20261023")
    ds["hsgt_20261023"] = tushare("moneyflow_hsgt", {"trade_date": LAST_TRADE_DATE}, note="hsgt_20261023")
    ds["ggt_daily_20261023"] = tushare("ggt_daily", {"trade_date": LAST_TRADE_DATE}, note="ggt_daily_20261023")
    ds["top_list_20261023"] = tushare("top_list", {"trade_date": LAST_TRADE_DATE, "limit": 300}, note="top_list_20261023")
    ds["sw_daily_20261023"] = tushare("sw_daily", {"trade_date": LAST_TRADE_DATE}, note="sw_daily_20261023")
    ds["index_classify_L1"] = tushare("index_classify", {"level": "L1", "src": "SW2021"}, note="index_classify_L1")
    ds["bak_daily_20261023"] = tushare("bak_daily", {"trade_date": LAST_TRADE_DATE}, note="bak_daily_20261023")

    # ---- 财务 / 披露 ----
    ds["income_600519_h1"] = tushare("income", {"ts_code": "600519.SH", "period": "20260630"}, note="income_600519_h1")
    ds["fina_600519_h1"] = tushare("fina_indicator", {"ts_code": "600519.SH", "period": "20260630"}, note="fina_600519_h1")
    ds["balancesheet_600519_h1"] = tushare("balancesheet", {"ts_code": "600519.SH", "period": "20260630"}, note="balancesheet_600519_h1")
    ds["income_600519_prev_h1"] = tushare("income", {"ts_code": "600519.SH", "period": "20250630"}, note="income_600519_prev_h1")
    ds["q3_income"] = {
        code: tushare("income", {"ts_code": code, "period": "20260930"}, note=f"income_{code}_q3")
        for code in ["300750.SZ", "601899.SH", "002415.SZ", "601088.SH", "000001.SZ", "920992.BJ", "600519.SH"]
    }
    ds["q3_fina"] = {
        code: tushare("fina_indicator", {"ts_code": code, "period": "20260930"}, note=f"fina_{code}_q3")
        for code in ["300750.SZ", "601899.SH", "002415.SZ", "000001.SZ"]
    }
    ds["q3_income_prev"] = {
        code: tushare("income", {"ts_code": code, "period": "20250930"}, note=f"income_{code}_prev_q3")
        for code in ["300750.SZ", "601899.SH", "002415.SZ", "000001.SZ", "601088.SH"]
    }
    ds["forecast_301563"] = tushare("forecast", {"ts_code": "301563.SZ", "period": "20260930"}, note="forecast_301563")
    ds["dividend_600519"] = tushare("dividend", {"ts_code": "600519.SH"}, note="dividend_600519")
    ds["disclosure_date_20261024"] = tushare("disclosure_date", {"end_date": "20260930", "actual_date": "20261024"}, note="disclosure_date_20261024")
    ds["stock_basic_delisted"] = tushare("stock_basic", {"list_status": "D"}, note="stock_basic_delisted")

    # ---- 宏观 ----
    ds["macro"] = {
        "cn_cpi": tushare("cn_cpi", {"start_m": "202609", "end_m": "202609"}, note="cn_cpi_202609"),
        "cn_ppi": tushare("cn_ppi", {"start_m": "202609", "end_m": "202609"}, note="cn_ppi_202609"),
        "cn_gdp": tushare("cn_gdp", {"start_q": "2026Q1", "end_q": "2026Q3"}, note="cn_gdp_2026q3"),
        "cn_m": tushare("cn_m", {"start_m": "202609", "end_m": "202609"}, note="cn_m_202609"),
        "cn_pmi": tushare(
            "cn_pmi",
            {"start_m": "202609", "end_m": "202609"},
            fields="month,pmi010000,pmi010100,pmi020100,pmi030000",
            note="cn_pmi_202609",
        ),
        "shibor": tushare("shibor", {"start_date": "20261020", "end_date": LAST_TRADE_DATE}, note="shibor_1023"),
    }
    ds["lpr"] = wrap_askd("lpr_history", A.lpr_history)
    ds["trading_calendar_2026_10"] = wrap_askd("trading_calendar_2026_10", A.trading_calendar, 2026, 10)
    ds["chinabond_20261023"] = wrap_askd("chinabond_20261023", A.chinabond_yield_curve, "20261023", "20261023")

    # ---- 公告 / 研报 / 新闻 (东财系串行 + 间隔) ----
    ds["cninfo_600519"] = wrap_askd("cninfo_600519", A.cninfo_announcements, "600519", 30)
    ds["cninfo_000858"] = wrap_askd("cninfo_000858", A.cninfo_announcements, "000858", 30)
    ds["cninfo_601899"] = wrap_askd("cninfo_601899", A.cninfo_announcements, "601899", 30)
    ds["cninfo_300937"] = wrap_askd("cninfo_300937", A.cninfo_announcements, "300937", 10)
    ds["cninfo_600735"] = wrap_askd("cninfo_600735", A.cninfo_announcements, "600735", 10)
    ds["cninfo_920992"] = wrap_askd("cninfo_920992", A.cninfo_announcements, "920992", 10)
    for code in ["300750", "002415", "601088", "000001"]:
        time.sleep(0.4)
        ds[f"cninfo_{code}"] = wrap_askd(f"cninfo_{code}", A.cninfo_announcements, code, 10)
    ds["cninfo_search_restate"] = cninfo_search("会计差错更正", "2026-10-20", "2026-10-24")

    ds["em_reports"] = {}
    for idx, code in enumerate(["600519", "300750", "601899", "002415"]):
        time.sleep(0.8)  # 东财风控: 串行 + 间隔
        ds["em_reports"][code] = wrap_askd(f"em_reports_{code}", A.eastmoney_reports, code, 1)

    ds["cctv_20261023"] = wrap_askd("cctv_20261023", A.cctv_news, "2026-10-23")
    ds["cctv_20261022"] = wrap_askd("cctv_20261022", A.cctv_news, "2026-10-22")
    ds["cls_telegraph"] = wrap_askd("cls_telegraph", A.cls_telegraph, 50)

    return ds


# --------------------------------------------------------------------------- #
# 从原始数据推导真值
# --------------------------------------------------------------------------- #
def first_row(ds_obj, **match) -> dict | None:
    rows = ds_obj["rows"] if isinstance(ds_obj, dict) and "rows" in ds_obj else ds_obj
    for row in rows:
        if all(str(row.get(k)) == str(v) for k, v in match.items()):
            return row
    return None


def build_truth(ds: dict) -> dict:
    T: dict[str, object] = {}

    # ===== 行情 =====
    def daily(code):
        return first_row(ds["daily_20261023"], ts_code=code)

    def dbasic(code):
        return first_row(ds["daily_basic_20261023"], ts_code=code)

    def index(code):
        return first_row(ds["index_daily_20261023"], ts_code=code)

    d = daily("600519.SH")
    b = dbasic("600519.SH")
    tq = ds["tencent_quote"]["600519"]
    T["m_600519"] = {
        "trade_date": "2026-10-23",
        "close": d["close"],                    # 元
        "pct_chg": d["pct_chg"],                # %
        "amount_qian": d["amount"],             # 千元
        "amount_yi": round(d["amount"] / 1e5, 4),
        "vol_shou": d["vol"],                   # 手
        "vol_gu": int(round(d["vol"] * 100)),   # 股
        "pe_ttm": b["pe_ttm"],
        "pb": b["pb"],
        "tencent_price": tq["price"],
        "tencent_change_pct": tq["change_pct"],
        "tencent_amount_wan": tq["amount_wan"],
        "limit_up": d.get("limit_up") or ds["tencent_quote"]["600519"].get("limit_up"),
    }
    d1 = daily("000001.SZ")
    T["m_000001"] = {"close": d1["close"], "pct_chg": d1["pct_chg"]}
    d750 = daily("300750.SZ")
    T["m_300750"] = {"close": d750["close"], "pct_chg": d750["pct_chg"]}
    for label, code in [("sh000001", "000001.SH"), ("hs300", "000300.SH"), ("sz399001", "399001.SZ"), ("cyb", "399006.SZ"), ("zz500", "000905.SH")]:
        row = index(code)
        T[f"m_{label}"] = {"close": row["close"], "pct_chg": row["pct_chg"]}
    hsi = first_row(ds["index_global_20261023"], ts_code="HSI")
    T["m_hsi"] = {"close": hsi["close"], "pct_chg": hsi["pct_chg"]}
    T["m_000002_pe"] = {"pe_ttm": dbasic("000002.SZ")["pe_ttm"], "close": dbasic("000002.SZ")["close"]}

    # 港股 / 美股: 腾讯原始行情
    hk_line = [ln for ln in ds["tencent_raw_hk_us"].split(";") if ln.startswith("v_hk00700")]
    us_line = [ln for ln in ds["tencent_raw_hk_us"].split(";") if ln.startswith("v_usAAPL")]
    if hk_line:
        f = hk_line[0].split('="')[1].rstrip('"').split("~")
        T["m_hk00700"] = {"name": f[1], "code": f[2], "price_hkd": float(f[3]), "prev_close_hkd": float(f[4]), "change_pct": float(f[32]) if len(f) > 32 else None, "quote_time": f[30] if len(f) > 30 else None}
    if us_line:
        f = us_line[0].split('="')[1].rstrip('"').split("~")
        T["m_usAAPL"] = {"name": f[1], "code": f[2], "price_usd": float(f[3]), "change_pct": float(f[32]) if len(f) > 32 else None}

    # ===== 财务 =====
    inc = ds["income_600519_h1"]["rows"][0]
    fin = ds["fina_600519_h1"]["rows"][0]
    bs = ds["balancesheet_600519_h1"]["rows"][0]
    T["f_600519_h1"] = {
        "ann_date": inc["ann_date"],
        "end_date": inc["end_date"],
        "total_revenue": inc["total_revenue"],
        "revenue": inc["revenue"],
        "n_income": inc["n_income"],
        "n_income_attr_p": inc["n_income_attr_p"],
        "basic_eps": inc["basic_eps"],
        "gross_margin_pct": fin["grossprofit_margin"],
        "net_margin_pct": fin["netprofit_margin"],
        "contract_liab": bs["contract_liab"],
    }
    T["f_600519_h1"].update(
        total_revenue_yi=round(inc["total_revenue"] / 1e8, 4),
        revenue_yi=round(inc["revenue"] / 1e8, 4),
        n_income_yi=round(inc["n_income"] / 1e8, 4),
        n_income_attr_p_yi=round(inc["n_income_attr_p"] / 1e8, 4),
        contract_liab_yi=round(bs["contract_liab"] / 1e8, 4),
    )
    prev = ds["income_600519_prev_h1"]["rows"][0]
    T["f_600519_h1"]["total_revenue_yoy_pct"] = round((inc["total_revenue"] / prev["total_revenue"] - 1) * 100, 4)

    def q3(code):
        rows = ds["q3_income"].get(code, {}).get("rows", [])
        return rows[0] if rows else None

    def q3f(code):
        rows = ds["q3_fina"].get(code, {}).get("rows", [])
        return rows[0] if rows else None

    def prev_q3(code):
        rows = ds["q3_income_prev"].get(code, {}).get("rows", [])
        return rows[0] if rows else None

    for code, key in [("300750.SZ", "f_300750_q3"), ("601899.SH", "f_601899_q3"), ("002415.SZ", "f_002415_q3"), ("601088.SH", "f_601088_q3"), ("000001.SZ", "f_000001_q3"), ("920992.BJ", "f_920992_q3")]:
        row = q3(code)
        prev_row = prev_q3(code)
        item = {"ts_code": code, "has_q3": bool(row)}
        if row:
            item.update(
                ann_date=row["ann_date"],
                end_date=row["end_date"],
                total_revenue=row["total_revenue"],
                n_income=row["n_income"],
                n_income_attr_p=row["n_income_attr_p"],
                basic_eps=row["basic_eps"],
                report_type=row["report_type"],
                total_revenue_yi=round(row["total_revenue"] / 1e8, 4),
                n_income_yi=round(row["n_income"] / 1e8, 4),
                n_income_attr_p_yi=round(row["n_income_attr_p"] / 1e8, 4),
            )
            if prev_row:
                item["total_revenue_yoy_pct"] = round((row["total_revenue"] / prev_row["total_revenue"] - 1) * 100, 4)
                item["np_attr_yoy_pct"] = round((row["n_income_attr_p"] / prev_row["n_income_attr_p"] - 1) * 100, 4)
        item["has_q3"] = bool(row)
        frow = q3f(code)
        if frow:
            item.update(
                eps=frow["eps"],
                roe=frow["roe"],
                grossprofit_margin=frow["grossprofit_margin"],
                or_yoy=frow["or_yoy"],
                netprofit_yoy=frow["netprofit_yoy"],
            )
        T[key] = item

    # 未披露三季报(600519 在 as_of 时点)
    T["f_600519_q3_status"] = {"has_q3_in_tushare": bool(ds["q3_income"]["600519.SH"]["rows"])}

    fc = ds["forecast_301563"]["rows"][0]
    T["f_301563_forecast"] = {k: fc[k] for k in ["ann_date", "end_date", "type", "p_change_min", "p_change_max", "net_profit_min", "net_profit_max", "last_parent_net"]}

    div_rows = [r for r in ds["dividend_600519"]["rows"] if r.get("div_proc") == "实施" and r.get("end_date") == "20251231"]
    div = div_rows[0]
    T["f_600519_dividend"] = {"end_date": div["end_date"], "ann_date": div["ann_date"], "cash_div_tax": div["cash_div_tax"], "ex_date": div["ex_date"], "record_date": div["record_date"]}

    # ===== 披露状态 =====
    anns = ds["cninfo_600519"]
    T["f_600519_ann_latest"] = {"latest_date": anns[0]["date"], "latest_title": anns[0]["title"], "count_page": len(anns)}
    day0815 = [a for a in anns if a["date"] == "2026-08-15"]
    T["f_600519_ann_0815"] = {"titles": [a["title"] for a in day0815], "count": len(day0815), "urls": [a["url"] for a in day0815]}

    # ===== 宏观 =====
    cpi = ds["macro"]["cn_cpi"]["rows"][0]
    ppi = ds["macro"]["cn_ppi"]["rows"][0]
    gdp = [r for r in ds["macro"]["cn_gdp"]["rows"] if r["quarter"] == "2026Q3"][0]
    m = ds["macro"]["cn_m"]["rows"][0]
    pmi_rows = [r for r in ds["macro"]["cn_pmi"]["rows"] if r["month"] == "202609"]
    pmi = pmi_rows[0] if pmi_rows else {}
    shib = [r for r in ds["macro"]["shibor"]["rows"] if str(r["date"]) == LAST_TRADE_DATE][0]
    lpr_rows = sorted(ds["lpr"], key=lambda r: r["date"])
    T["c_macro"] = {
        "lpr_20261020": {"lpr_1y": float(lpr_rows[-1]["lpr_1y"]), "lpr_5y": float(lpr_rows[-1]["lpr_5y"]), "date": str(lpr_rows[-1]["date"])[:10]},
        "cpi_202609": {"nt_yoy": cpi["nt_yoy"], "nt_mom": cpi["nt_mom"], "nt_val": cpi["nt_val"]},
        "ppi_202609": {"ppi_yoy": ppi["ppi_yoy"]},
        "gdp_2026q3": {"gdp_yi": gdp["gdp"], "gdp_yoy": gdp["gdp_yoy"]},
        "m2_202609": {"m2_yi": m["m2"], "m2_yoy": m["m2_yoy"], "m1_yoy": m["m1_yoy"]},
        "pmi_202609": {"manufacturing_pmi": pmi.get("pmi010000")},
        "shibor_20261023": {"on": shib["on"], "w1": shib["1w"]},
    }
    cb = [r for r in ds["chinabond_20261023"] if r["curve"] == "中债国债收益率曲线"][0]
    T["c_macro"]["bond_10y_20261023"] = {"y10": cb["10y"], "curve": cb["curve"]}

    cal = [r for r in ds["trading_calendar_2026_10"] if str(r["date"])[:10] in ("2026-10-23", "2026-10-24")]
    T["c_calendar"] = {str(r["date"])[:10]: bool(r["is_open"]) for r in cal}

    # ===== 行业 / 资金 =====
    l1 = {r["index_code"]: r["industry_name"] for r in ds["index_classify_L1"]["rows"]}
    sw_l1 = [r for r in ds["sw_daily_20261023"]["rows"] if r["ts_code"] in l1]
    sw_l1.sort(key=lambda r: -r["pct_change"])
    T["i_sw_rank"] = {
        "top3": [{"name": r["name"], "pct_change": r["pct_change"], "code": r["ts_code"]} for r in sw_l1[:3]],
        "bottom3": [{"name": r["name"], "pct_change": r["pct_change"]} for r in sw_l1[-3:]],
        "n_l1": len(sw_l1),
    }
    ups = ds["limit_list_20261023"]["U"]["rows"]
    downs = ds["limit_list_20261023"]["D"]["rows"]
    T["i_limit"] = {"limit_up": len(ups), "limit_down": len(downs), "limit_up_names_first3": [r["name"] for r in ups[:3]]}
    hs = ds["hsgt_20261023"]["rows"][0]
    T["i_north"] = {"raw": dict(hs), "validation_status": "quarantined",
                    "reason": "旧净流入题混淆指标与单位;原始快照不自动生成语义真值"}
    ggt = ds["ggt_daily_20261023"]["rows"][0]
    T["i_ggt"] = {"buy_yi": ggt["buy_amount"], "sell_yi": ggt["sell_amount"], "net_yi": round(ggt["buy_amount"] - ggt["sell_amount"], 2)}
    mf = first_row(ds["moneyflow_20261023"], ts_code="600519.SH")
    main_wan = (mf["buy_lg_amount"] + mf["buy_elg_amount"]) - (mf["sell_lg_amount"] + mf["sell_elg_amount"])
    T["i_600519_flow"] = {
        "main_wan": round(main_wan, 2),
        "main_yi": round(main_wan / 1e4, 4),
        "net_mf_wan": mf["net_mf_amount"],
        "net_mf_yi": round(mf["net_mf_amount"] / 1e4, 4),
        "buy_lg_wan": mf["buy_lg_amount"],
        "buy_elg_wan": mf["buy_elg_amount"],
        "sell_lg_wan": mf["sell_lg_amount"],
        "sell_elg_wan": mf["sell_elg_amount"],
    }
    mg = first_row(ds["margin_20261023"], ts_code="600519.SH")
    T["i_600519_margin"] = {"rzye_yuan": mg["rzye"], "rzye_yi": round(mg["rzye"] / 1e8, 4), "rqye_yuan": mg["rqye"]}
    tops = ds["top_list_20261023"]["rows"]
    top_net = max(tops, key=lambda r: r["net_amount"] or 0)
    T["i_top_list"] = {"max_net_amount_yuan": top_net["net_amount"], "max_net_amount_yi": round((top_net["net_amount"] or 0) / 1e8, 4), "max_net_name": top_net["name"], "max_net_code": top_net["ts_code"], "count": len(tops)}

    # ===== 公告/重述 =====
    T["t_restate"] = {
        "hits": [
            {"code": r["secCode"], "name": r["secName"], "title": r["title"], "date": r["date"]}
            for r in ds["cninfo_search_restate"]
            if "差错" in r["title"] and ("更正" in r["title"])
        ][:6],
    }
    T["t_restate_300937"] = [{"date": a["date"], "title": a["title"], "url": a["url"]} for a in ds["cninfo_300937"] if "更正" in a["title"]]
    T["t_restate_600735"] = [{"date": a["date"], "title": a["title"], "url": a["url"]} for a in ds["cninfo_600735"] if "更正" in a["title"]]

    # ===== 研报 =====
    def report(code):
        reps = ds["em_reports"][code]
        reps = sorted(reps, key=lambda r: r["publishDate"], reverse=True)
        r = reps[0]
        return {
            "publish_date": r["publishDate"][:10],
            "org": r["orgSName"],
            "rating": r["emRatingName"],
            "eps_this_year": r["predictThisYearEps"],
            "eps_next_year": r["predictNextYearEps"],
            "title": r["title"],
            "url_code": r.get("infoCode"),
            "n_total": len(reps),
        }

    T["r_reports"] = {c: report(c) for c in ["600519", "300750", "601899", "002415"]}

    # ===== 新闻 =====
    cctv23 = ds["cctv_20261023"]
    cctv22 = ds["cctv_20261022"]
    eq = [r for r in cctv23 if "地震" in r["title"]]
    # 把「伤亡数字」那篇排最前(题面要求截至 10/23 13 时的伤亡)
    eq.sort(key=lambda r: 0 if "遇难" in (r.get("content") or "") else 1)
    T["n_earthquake"] = [{"title": r["title"], "url": r["url"], "content": r["content"][:400]} for r in eq[:3]]
    longmarch = [r for r in cctv22 if "长征胜利90周年" in r["title"] and "大会" in r["title"]]
    T["n_longmarch"] = [{"title": r["title"], "url": r["url"]} for r in longmarch[:2]]
    cls = ds["cls_telegraph"]
    pdd = [r for r in cls if "拼多多" in r["title"] or "拼多多" in (r.get("content") or "")]
    T["n_pdd"] = [{"title": r["title"], "time": r["time"], "url": r.get("url"), "content": (r.get("content") or "")[:300]} for r in pdd[:2]]
    donate = [a for a in ds["cninfo_601899"] if "地震" in a["title"] or "捐赠" in a["title"]]
    T["n_601899_donate"] = [{"date": a["date"], "title": a["title"], "url": a["url"]} for a in donate[:3]]

    # ===== 停牌 / 退市 / 北交所 =====
    susp = ds["suspend_d_20261023"]["rows"]
    T["t_suspend"] = {"n": len(susp), "codes": [r["ts_code"] for r in susp], "has_601326": any(r["ts_code"] == "601326.SH" for r in susp)}
    T["t_601326"] = {
        "daily_row_1023": bool(daily("601326.SH")),
        "tencent": ds["tencent_quote"].get("601326"),
    }
    T["t_delisted"] = {
        "000004": ds["tencent_quote"].get("000004"),
        "300104": ds["tencent_quote"].get("300104"),
        "in_tushare_delist": [c for c in ["000004.SZ", "300104.SZ"] if any(r["ts_code"] == c for r in ds["stock_basic_delisted"]["rows"])],
    }
    T["t_bj"] = {
        "quote_920992_no_prefix": ds["tencent_quote_bj"].get("920992"),
        "quote_920992_bj": ds["tencent_quote_bj"].get("bj920992"),
    }

    # ===== 歧义 / 指数同码 =====
    T["t_ambiguous"] = {
        "sh000001_index": {"name": ds["tencent_quote"]["sh000001"]["name"], "price": ds["tencent_quote"]["sh000001"]["price"], "pct": ds["tencent_quote"]["sh000001"]["change_pct"]},
        "sz000001_stock": {"name": ds["tencent_quote"]["000001"]["name"], "price": ds["tencent_quote"]["000001"]["price"], "pct": ds["tencent_quote"]["000001"]["change_pct"]},
    }
    return T


NESTED_DATASETS = {"macro", "q3_income", "q3_fina", "q3_income_prev", "em_reports", "limit_list_20261023"}


def flush_raw(ds: dict) -> dict[str, str]:
    """把每个 dataset 原样落盘; 返回 dataset -> 相对路径 的映射(供 golden.jsonl 的 evidence 引用)。"""
    files: dict[str, str] = {}
    for key, value in ds.items():
        if key in NESTED_DATASETS and isinstance(value, dict):
            for sub, payload in value.items():
                name = f"{key}__{sub}".replace(":", "_").replace("/", "_")
                files[f"{key}.{sub}"] = save_raw(name, payload)
        else:
            files[key] = save_raw(key, value)
    return files


MANIFEST_PATH = OUT_DIR / "raw_manifest.json"


def load_raw_from_disk() -> tuple[dict, dict[str, str]]:
    """用已落盘的 raw 快照重建 datasets(不联网), 便于只改推导逻辑时快速复跑。"""
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"缺少 {MANIFEST_PATH}, 先完整跑一次 fetch_truth.py")
    files = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    ds: dict = {}
    for key, rel in files.items():
        payload = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        if "." in key:
            parent, sub = key.split(".", 1)
            ds.setdefault(parent, {})[sub] = payload
        else:
            ds[key] = payload
    return ds, files


def main() -> int:
    raise SystemExit("历史真值快照已冻结，禁止用当前上游覆盖旧 as_of。新题库请独立采集并审核新的快照。")


def _historical_main() -> int:
    """Retained for audit of the historical collection procedure, not a CLI."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--from-raw", action="store_true", help="不联网, 用 artifacts/golden/raw 已有快照重建 truth_bundle")
    args = parser.parse_args()

    print(f"[fetch_truth] as_of={AS_OF} last_trade={LAST_TRADE_DATE} from_raw={args.from_raw}")
    if args.from_raw:
        raw, raw_files = load_raw_from_disk()
    else:
        LOG_PATH.write_text("", encoding="utf-8")  # 只在真正联网取数时重置日志
        raw = fetch_all()
        raw_files = flush_raw(raw)
        MANIFEST_PATH.write_text(json.dumps(raw_files, ensure_ascii=False, indent=1), encoding="utf-8")
    truth = build_truth(raw)
    bundle_path = OUT_DIR / "truth_bundle.json"
    bundle = {
        "as_of": AS_OF,
        "last_trade_date": LAST_TRADE_DATE,
        "generated_at": now_cn(),
        "raw_dir": str(RAW_DIR.relative_to(ROOT)),
        "raw_files": raw_files,
        "truth": truth,
    }
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"[fetch_truth] raw -> {RAW_DIR.relative_to(ROOT)}/  truth -> {bundle_path.relative_to(ROOT)}")
    print(f"[fetch_truth] datasets={len(raw_files)}  truth_keys={len(truth)}  log_lines={len(LOG_PATH.read_text().splitlines())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
