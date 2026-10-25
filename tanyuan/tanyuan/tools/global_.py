"""Global markets: Hong Kong / US quotes and K-lines (Tencent), plus SEC EDGAR filings.

Source choice (see research/global-stock-data-audit.md):
- Tencent front-end endpoints are C-level (personal research OK, no commercial redistribution).
  They cover HK/US quotes and — verified 2026-10-24 — HK/US K-lines too, which lets us avoid
  Yahoo's D-level terms for the K-line path.
- SEC EDGAR is S-level: free, scripted access explicitly allowed, 10 req/s cap, and requires
  a declared User-Agent with real contact info.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta

from .. import config, http
from ..envelope import Result
from ..errors import bad_arg, no_data, not_found, schema_changed, unsupported, unavailable
from ..registry import tool
from ..util import clamp, clean_text, now_cn, now_iso, num

GLOBAL_QUOTE_URL = "https://qt.gtimg.cn/q={}"
KLINE_HOSTS = [
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com/ifzqgtimg",
    "https://ifzq.gtimg.cn",
]
_HK_PATH = "/appstock/app/hkfqkline/get"
_US_PATH = "/appstock/app/usfqkline/get"
_US_QUOTE = "https://qt.gtimg.cn/q=us{ticker}"
_CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_SEC_FTS = "https://efts.sec.gov/LATEST/search-index"


def _sec_headers() -> dict:
    contact = config.secret("SEC_CONTACT") or "Tanyuan Research research@example.com"
    return {"User-Agent": contact, "Accept-Encoding": "gzip, deflate"}


# ── symbol parsing ──────────────────────────────────────────────────────────

class GlobalSymbol:
    def __init__(self, raw: str):
        text = str(raw).strip()
        low = text.lower()
        self.raw = text
        if re.fullmatch(r"\d{4,6}", text):
            digits = text.zfill(5)
            self.market, self.code = "hk", digits
            self.display = f"hk{digits}"
        elif low.startswith("hk") and re.fullmatch(r"hk\d{4,6}", low):
            self.market, self.code = "hk", low[2:].zfill(5)
            self.display = f"hk{self.code}"
        elif low.startswith("us") and re.fullmatch(r"us[a-z][a-z.\-]{0,9}", low):
            self.market, self.code = "us", text[2:].upper()
            self.display = f"us{self.code}"
        elif re.fullmatch(r"[A-Za-z][A-Za-z.\-]{0,9}", text):
            self.market, self.code = "us", text.upper()
            self.display = f"us{self.code}"
        else:
            raise ValueError(
                f"无法识别的全球市场代码 {raw!r};支持 00700 / hk00700 / AAPL / usAAPL"
            )

    @property
    def quote_key(self) -> str:
        return f"r_hk{self.code}" if self.market == "hk" else f"us{self.code}"


def _parse_global_quote(raw: str) -> dict | None:
    m = re.search(r'"(.+)"', raw, re.S)
    if not m:
        return None
    f = m.group(1).split("~")
    common = {
        "name": clean_text(f[1], 60) if len(f) > 1 else None,
        "price": num(f[3]) if len(f) > 3 else None,
        "prev_close": num(f[4]) if len(f) > 4 else None,
        "open": num(f[5]) if len(f) > 5 else None,
        "volume": num(f[6]) if len(f) > 6 else None,
        "change_pct": num(f[32]) if len(f) > 32 else None,
        "high": num(f[33]) if len(f) > 33 else None,
        "low": num(f[34]) if len(f) > 34 else None,
        "pe": num(f[39]) if len(f) > 39 else None,
        "float_mcap_yi": num(f[44]) if len(f) > 44 else None,
        "mcap_yi": num(f[45]) if len(f) > 45 else None,
        "high_52w": num(f[48]) if len(f) > 48 else None,
        "low_52w": num(f[49]) if len(f) > 49 else None,
        "timestamp": f[30] if len(f) > 30 else None,
    }
    if len(f) >= 76:            # HK layout (78 fields)
        common.update({
            "name_en": clean_text(f[46], 80) if len(f) > 46 else None,
            "amount": num(f[37]) if len(f) > 37 else None,
            "pb": num(f[58]) if len(f) > 58 else None,
            "currency": f[75] if len(f) > 75 else None,
            "market": "HK",
        })
        return common
    if len(f) >= 52:            # US layout (71 fields)
        common.update({
            "name_en": clean_text(f[46], 80) if len(f) > 46 else None,
            "amount": None,
            "pb": num(f[51]) if len(f) > 51 else None,
            "currency": f[35] if len(f) > 35 else None,
            "market": "US",
            "eps": num(f[47]) if len(f) > 47 else None,
        })
        return common
    return None


def _fetch_global_quotes(symbols: list[GlobalSymbol]) -> tuple[dict, list[dict]]:
    url = GLOBAL_QUOTE_URL.format(",".join(s.quote_key for s in symbols))
    text = http.get_text(url, headers={"Referer": "https://gu.qq.com/"}, encoding="gbk")
    if not text or "=" not in text:
        raise schema_changed("腾讯全球行情返回为空或格式异常", evidence=text[:200])
    by_key: dict[str, dict] = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key = line.split("=")[0].replace("v_", "", 1)   # v_r_hk00700 -> r_hk00700; v_usAAPL -> usAAPL
        if '"' not in line:
            by_key[key] = {}
            continue
        by_key[key] = _parse_global_quote(line) or {}
    out: dict[str, dict] = {}
    missing: list[dict] = []
    for sym in symbols:
        node = by_key.get(sym.quote_key)
        if not node:
            missing.append({"code": sym.raw, "reason": "上游未返回该代码"})
            continue
        item = dict(node)
        item["code"] = sym.display
        item["symbol"] = sym.code
        out[sym.raw] = item
    return out, missing


@tool(
    name="market.quote_global",
    summary="港股/美股实时行情(腾讯源,价格/涨跌幅/PE/PB/市值/52 周高低)",
    covers=["港股主板/GEM 个股与指数", "美股个股与指数(AAPL、TSLA 等)"],
    not_covers=["A 股(用 market.quote)", "日股/欧股(腾讯不覆盖;需 Yahoo,注意合规)", "盘口深度"],
    args_schema={
        "codes": {"type": "array[string]", "min_items": 1, "description": "代码列表,如 ['00700','AAPL']"},
        "code": {"type": "string", "min_length": 1, "description": "单个代码(与 codes 等价)", "example": "00700"},
    },
    arg_groups=[{"keys": ["code", "codes"], "min": 1, "max": 1}],
    returns=[
        {"field": "quotes.<code>.price/change_pct", "desc": "价格与涨跌幅"},
        {"field": "quotes.<code>.pe/pb", "desc": "估值(美股 pb 在另一字段,已归位)"},
        {"field": "quotes.<code>.mcap_yi", "desc": "总市值(亿,港币/美元)"},
        {"field": "quotes.<code>.currency", "desc": "HKD / USD"},
        {"field": "quotes.<code>.timestamp", "desc": "上游报价时间"},
    ],
    source="tencent_qt_global", grade="L1", cache_ttl_s=15, cost="free",
    errors=["BAD_ARGUMENT", "NOT_FOUND", "UNSUPPORTED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"codes": ["00700", "AAPL"]},
               "cmd": "ty call market.quote_global --json '{\"codes\":[\"00700\",\"AAPL\"]}'"}],
    latency_hint_ms=250,
)
def market_quote_global(args: dict) -> Result:
    raw_codes = args.get("codes") or ([args["code"]] if args.get("code") else None)
    if not raw_codes:
        raise bad_arg("需要 codes(数组)或 code(单值)")
    symbols = []
    for raw in raw_codes:
        text = str(raw).strip()
        if re.fullmatch(r"\d{6}", text):
            raise unsupported(
                f"{text} 是 6 位 A 股代码,请用 market.quote",
                hint="A 股在 market.quote;本工具只做港股(4–5 位)与美股(字母)。",
            )
        try:
            symbols.append(GlobalSymbol(text))
        except ValueError as exc:
            raise bad_arg(str(exc))
    quotes, missing = _fetch_global_quotes(symbols)
    if not quotes:
        raise not_found("所有代码都没有行情", hint="确认港股代码位数(00700)/美股 ticker 是否正确。")
    warnings = []
    if missing:
        warnings.append({"type": "partial_result", "message": f"{len(missing)} 个代码没取到",
                         "detail": missing[:10]})
    return Result(
        data={"quotes": quotes, "missing": missing}, rows=len(quotes),
        source="tencent_qt_global", grade="L1",
        source_url=GLOBAL_QUOTE_URL.format(",".join(s.quote_key for s in symbols)),
        as_of=None,
        warnings=warnings + [{"type": "license",
                              "message": "腾讯前端接口为 C 级:个人研究可用,商业/再分发需自评条款"}],
        units={"price": "本地币种(见 currency)", "market_cap": "亿"},
        next=[{"tool": "market.kline_global", "why": "需要历史走势"}],
    )


# ── K-line ─────────────────────────────────────────────────────────────────

def _kline_call(path: str, param: str) -> tuple[dict, str]:
    errors = []
    for host in KLINE_HOSTS:
        try:
            body = http.get_json(host + path, params={"param": param},
                                 headers={"Referer": "https://gu.qq.com/"}, timeout=(8, 25))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{host}: {type(exc).__name__}")
            continue
        data = (body or {}).get("data") if isinstance(body, dict) else None
        if isinstance(data, dict) and data:
            return data, host
        errors.append(f"{host}: 空响应")
    raise unavailable("腾讯全球 K 线入口均不可用", evidence="; ".join(errors))


def _us_exchange_suffix(ticker: str) -> str:
    """Tencent encodes the exchange in the quote symbol (AAPL.OQ / BRK.A)."""
    try:
        body = http.get_text(_US_QUOTE.format(ticker=ticker),
                             headers={"Referer": "https://gu.qq.com/"}, encoding="gbk")
    except Exception:  # noqa: BLE001
        return ""
    m = re.search(r'"(.+)"', body, re.S)
    if not m:
        return ""
    fields = m.group(1).split("~")
    if len(fields) > 2 and "." in fields[2]:
        return fields[2].split(".", 1)[1]
    return ""


@tool(
    name="market.kline_global",
    summary="港股/美股日/周/月 K 线(前复权/后复权/不复权)",
    covers=["港股个股与指数", "美股个股与指数"],
    not_covers=["A 股(用 market.kline)", "分钟线", "日韩台/欧洲股票"],
    args_schema={
        "code": {"type": "string", "required": True, "description": "00700 / AAPL", "example": "00700"},
        "period": {"type": "string", "enum": ["day", "week", "month"], "description": "默认 day"},
        "adjust": {"type": "string", "enum": ["qfq", "hfq", "none"], "description": "默认 qfq"},
        "count": {"type": "integer", "min": 1, "max": 640, "description": "默认 120"},
    },
    returns=[
        {"field": "bars[].date", "desc": "交易日"},
        {"field": "bars[].open/close/high/low", "desc": "价格(按 adjust 处理)"},
        {"field": "bars[].volume", "desc": "成交量(股)"},
    ],
    source="tencent_kline_global", grade="L1", cache_ttl_s=300, cost="free",
    errors=["BAD_ARGUMENT", "NOT_FOUND", "NO_DATA", "UNSUPPORTED", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"code": "00700", "count": 30},
               "cmd": "ty call market.kline_global --json '{\"code\":\"00700\",\"count\":30}'"}],
    latency_hint_ms=500,
)
def market_kline_global(args: dict) -> Result:
    try:
        sym = GlobalSymbol(args["code"])
    except ValueError as exc:
        raise bad_arg(str(exc))
    period = args.get("period") or "day"
    adjust = args.get("adjust") or "qfq"
    count = clamp(int(args.get("count") or 120), 1, 640)
    adjust_param = "" if adjust == "none" else adjust
    if sym.market == "hk":
        path, param_key = _HK_PATH, f"hk{sym.code}"
    else:
        suffix = _us_exchange_suffix(sym.code)
        if not suffix:
            raise not_found(f"取不到 {sym.code} 的美股交易所后缀", hint="确认 ticker;或先用 market.quote_global 验证。")
        path, param_key = _US_PATH, f"us{sym.code}.{suffix}"
    param = f"{param_key},{period},,,{count},{adjust_param}"
    data, host = _kline_call(path, param)
    node = data.get(param_key)
    if not isinstance(node, dict):
        raise schema_changed(f"腾讯全球 K 线返回里没有 {param_key}", evidence=str(data)[:300])
    series_key = f"{adjust_param}{period}"
    items = node.get(series_key)
    if items is None:
        items = node.get(period)
    if not isinstance(items, list) or not items:
        raise no_data(f"{sym.raw} 没有 {period} K 线", hint="确认代码;港股/美股均支持日周月。")
    bars = []
    for item in items:
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            raise schema_changed("腾讯全球 K 线行格式改变", evidence=str(item)[:200])
        bars.append({
            "date": str(item[0])[:10],
            "open": num(item[1]), "close": num(item[2]),
            "high": num(item[3]), "low": num(item[4]), "volume": num(item[5]),
        })
    bars.sort(key=lambda b: b["date"])
    return Result(
        data={"code": sym.display, "market": sym.market.upper(), "period": period,
              "adjust": adjust, "bars": bars},
        rows=len(bars), source="tencent_kline_global", grade="L1",
        source_url=f"{host}{path}?param={param}", as_of=bars[-1]["date"] if bars else None,
        warnings=[{"type": "license",
                   "message": "腾讯前端接口为 C 级:个人研究可用,商业/再分发需自评条款"}],
        units={"price": "本地币种", "volume": "股"},
    )


# ── SEC EDGAR ──────────────────────────────────────────────────────────────

_CIK_CACHE: dict[str, int] | None = None
_CIK_FETCHED_AT = 0.0
_CIK_TTL = 24 * 3600


def _cik_map() -> dict[str, int]:
    global _CIK_CACHE, _CIK_FETCHED_AT
    if _CIK_CACHE is not None and time.time() - _CIK_FETCHED_AT < _CIK_TTL:
        return _CIK_CACHE
    path = config.cache_dir() / "sec_cik_map.json"
    if path.is_file() and time.time() - path.stat().st_mtime < _CIK_TTL:
        try:
            mapping = json.loads(path.read_text(encoding="utf-8"))
            _CIK_CACHE, _CIK_FETCHED_AT = mapping, time.time()
            return mapping
        except (OSError, ValueError):
            pass
    body = http.get_json(_CIK_MAP_URL, headers=_sec_headers(), timeout=(8, 30))
    mapping = {}
    for row in (body or {}).values():
        ticker = str(row.get("ticker") or "").upper()
        cik = row.get("cik_str")
        if ticker and isinstance(cik, int):
            mapping[ticker] = cik
    try:
        path.write_text(json.dumps(mapping), encoding="utf-8")
    except OSError:
        pass
    _CIK_CACHE, _CIK_FETCHED_AT = mapping, time.time()
    return mapping


def _resolve_cik(query: str) -> tuple[int, str]:
    text = str(query).strip()
    if text.isdigit() and len(text) <= 10:
        return int(text), text
    ticker = text.upper().lstrip("$")
    mapping = _cik_map()
    cik = mapping.get(ticker)
    if cik is None:
        raise not_found(
            f"找不到 {ticker} 的 CIK",
            hint="美股 ticker 才能映射到 SEC CIK;港股/A 股不在 SEC 覆盖范围。",
        )
    return cik, ticker


@tool(
    name="filing.sec",
    summary="美股 SEC EDGAR 申报(10-K/10-Q/8-K/Form4 等,S 级一手源,附原件链接)",
    covers=["美股上市公司全部 SEC 申报列表", "按 form 类型过滤", "CIK 解析"],
    not_covers=["中概股境内文件", "港股/A 股公告(用 filing.announcements)", "申报正文全文解析(给 PDF/HTML 链接)"],
    args_schema={
        "ticker": {"type": "string", "required": True, "description": "美股 ticker 或 CIK", "example": "AAPL"},
        "forms": {"type": "array[string]", "description": "只看这些 form,如 ['10-K','8-K']"},
        "limit": {"type": "integer", "min": 1, "max": 100, "description": "默认 20"},
        "since": {"type": "string", "format": "date", "description": "YYYY-MM-DD,只看该日之后的申报"},
    },
    returns=[
        {"field": "filings[].form", "desc": "申报类型"},
        {"field": "filings[].filed_date", "desc": "申报日"},
        {"field": "filings[].primary_doc_url", "desc": "原件链接(L0 证据)"},
        {"field": "filings[].description", "desc": "文件说明"},
    ],
    source="sec_edgar", grade="L0", cache_ttl_s=3600, cost="free",
    errors=["BAD_ARGUMENT", "NOT_FOUND", "NO_DATA", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"ticker": "AAPL", "forms": ["10-K"], "limit": 5},
               "cmd": "ty call filing.sec --json '{\"ticker\":\"AAPL\",\"forms\":[\"10-K\"],\"limit\":5}'"}],
    latency_hint_ms=800,
)
def filing_sec(args: dict) -> Result:
    cik, label = _resolve_cik(args["ticker"])
    limit = clamp(int(args.get("limit") or 20), 1, 100)
    forms = {f.upper() for f in (args.get("forms") or [])}
    body = http.get_json(_SUBMISSIONS.format(cik=cik), headers=_sec_headers(), timeout=(8, 40))
    recent = ((body or {}).get("filings") or {}).get("recent") or {}
    if not recent:
        raise schema_changed("SEC submissions 返回里没有 filings.recent", evidence=str(body)[:300])
    keys = ["form", "filingDate", "accessionNumber", "primaryDocument", "primaryDocDescription",
            "reportDate", "acceptanceDateTime"]
    rows = []
    n = len(recent.get("form") or [])
    for i in range(n):
        row = {k: (recent.get(k) or [None] * n)[i] for k in keys}
        if forms and str(row["form"]).upper() not in forms:
            continue
        if args.get("since") and str(row["filingDate"]) < args["since"]:
            continue
        accession = str(row.get("accessionNumber") or "").replace("-", "")
        doc = row.get("primaryDocument") or ""
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{doc}"
               if accession and doc else None)
        rows.append({
            "form": row["form"],
            "filed_date": row["filingDate"],
            "report_date": row.get("reportDate") or None,
            "accession": row.get("accessionNumber"),
            "description": clean_text(row.get("primaryDocDescription"), 200),
            "primary_doc_url": url,
        })
        if len(rows) >= limit:
            break
    if not rows:
        raise no_data(
            f"{label} 没有符合条件(forms={sorted(forms) or '全部'})的申报",
            hint="放宽 forms/limit/since;SEC 申报频率低(10-K 一年一次)。",
        )
    return Result(
        data={"ticker": label, "cik": cik, "filings": rows},
        rows=len(rows), source="sec_edgar", grade="L0",
        source_url=f"https://data.sec.gov/submissions/CIK{cik:010d}.json", as_of=None,
        warnings=[{"type": "contact_required",
                   "message": "SEC 要求 UA 含真实联系方式;请设 SEC_CONTACT 环境变量,否则可能被限流"}],
        units={"time": "US/Eastern filing date"},
        next=[{"tool": "fetch.url", "why": "读原件正文(HTML/PDF)"}],
    )


# ── SEC daily filing index (event stream: Form 4 / 8-K / 13F by day) ────────

_SEC_DAILY_IDX = ("https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{q}/"
                  "form.{ymd}.idx")


def _parse_daily_idx(text: str) -> list[dict]:
    """Parse EDGAR's .idx.

    Fixed-width is unreliable here (the header wraps and column widths drift), so parse
    from the right: file name, date, CIK are the last three structural tokens; whatever
    remains after the form type is the company name.
    """
    rows = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 5 or not tokens[0] or tokens[0].startswith(("Description:", "Form", "---")):
            continue
        filename = tokens[-1]
        if "edgar/" not in filename:
            continue
        filed, cik = tokens[-2], tokens[-3]
        if not (filed.isdigit() and len(filed) == 8) or not cik.isdigit():
            continue
        company = " ".join(tokens[1:-3])
        rows.append({
            "form": tokens[0],
            "company": clean_text(company, 120),
            "cik": cik,
            "filed_date": f"{filed[:4]}-{filed[4:6]}-{filed[6:8]}",
            "url": f"https://www.sec.gov/Archives/{filename}",
        })
    return rows


@tool(
    name="filing.sec_index",
    summary="美股 SEC 每日申报流(某交易日全部 8-K/Form4/13F 等,S 级,L0)",
    covers=["某交易日 SEC 申报索引", "按 form 类型过滤与计数"],
    not_covers=["申报正文解析(给原件链接)", "非交易日(自动回退到最近工作日)"],
    args_schema={
        "date": {"type": "string", "format": "date", "description": "YYYY-MM-DD,默认最近工作日"},
        "forms": {"type": "array[string]", "description": "只看这些 form,如 ['8-K','4']"},
        "limit": {"type": "integer", "min": 1, "max": 200, "description": "返回条数,默认 40"},
        "company_contains": {"type": "string", "description": "公司名包含关键词(不区分大小写)"},
    },
    returns=[
        {"field": "counts", "desc": "按 form 的全量计数(与分页解耦)"},
        {"field": "filings[].form/company/cik/filed_date", "desc": "申报元数据"},
        {"field": "filings[].url", "desc": "SEC 原件链接(L0)"},
    ],
    source="sec_edgar_daily_index", grade="L0", cache_ttl_s=300, cost="free",
    errors=["BAD_ARGUMENT", "NO_DATA", "SOURCE_UNAVAILABLE"],
    examples=[{"args": {"forms": ["8-K"], "limit": 5},
               "cmd": "ty call filing.sec_index --json '{\"forms\":[\"8-K\"],\"limit\":5}'"}],
    latency_hint_ms=2500,
)
def filing_sec_index(args: dict) -> Result:
    from collections import Counter
    from datetime import date as _date, timedelta as _td
    if args.get("date"):
        try:
            target = datetime.strptime(str(args["date"])[:10], "%Y-%m-%d").date()
        except ValueError:
            raise bad_arg("date 格式应为 YYYY-MM-DD")
    else:
        target = now_cn().date()
    limit = clamp(int(args.get("limit") or 40), 1, 200)
    forms = {f.upper() for f in (args.get("forms") or [])}
    needle = (args.get("company_contains") or "").upper()

    rows: list[dict] = []
    used_date = None
    for offset in range(0, 7):          # daily index exists only for filing days
        day = target - _td(days=offset)
        url = _SEC_DAILY_IDX.format(year=day.year, q=(day.month - 1) // 3 + 1,
                                    ymd=day.strftime("%Y%m%d"))
        try:
            text = http.get_text(url, headers=_sec_headers(), timeout=(10, 60),
                                 allow_status=(404,), max_retries=0)
        except Exception:  # noqa: BLE001 - try the previous day
            continue
        if not text or "Form Type" not in text:
            continue
        rows = _parse_daily_idx(text)
        if rows:
            used_date = day.isoformat()
            break
    if not rows:
        raise no_data(f"{target} 起 7 天内没有可用的 SEC 每日索引(周末/假日/未发布)",
                      hint="指定最近交易日;SEC 每日索引在美东收盘后生成。")

    counts = Counter(r["form"] for r in rows)
    filtered = [r for r in rows if (not forms or r["form"].upper() in forms)]
    if needle:
        filtered = [r for r in filtered if needle in r["company"].upper()]
    if not filtered:
        raise no_data(f"{used_date} 没有符合条件的申报", hint="放宽 forms/company_contains")

    return Result(
        data={"date": used_date, "total_filings": len(rows),
              "counts": dict(counts.most_common(20)), "filings": filtered[:limit]},
        rows=len(filtered[:limit]), source="sec_edgar_daily_index", grade="L0",
        source_url=_SEC_DAILY_IDX.format(year=target.year, q=(target.month - 1) // 3 + 1,
                                         ymd=target.strftime("%Y%m%d")),
        as_of=None,
        warnings=[{"type": "counts_are_full_day",
                   "message": "counts 是当日全量;filings 受 limit 截断"},
                  {"type": "contact_required",
                   "message": "SEC 要求 UA 含真实联系方式;请设 SEC_CONTACT"}],
        units={"time": "US/Eastern"},
    )
