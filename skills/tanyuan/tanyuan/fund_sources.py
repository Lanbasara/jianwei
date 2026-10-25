"""Bounded adapters for public fund disclosures, with no execution of remote scripts.

All requests use fixed HTTPS hosts. HTML/JSON are untrusted data; empty data and
changed schemas remain distinct. No credentials, filesystem paths or code come
from upstream payloads. Numeric cell headers are preserved with every table.
"""
from __future__ import annotations

import json
import math
import re
from html.parser import HTMLParser
from urllib.parse import urlencode

from . import config, downloads
from .errors import bad_arg, schema_changed, unavailable

DIRECTORY = "https://fund.eastmoney.com/js/fundcode_search.js"
F10 = "https://fundf10.eastmoney.com"
API = "https://api.fund.eastmoney.com/f10"
ARCHIVES = F10 + "/FundArchivesDatas.aspx"


def number(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").removesuffix("%")
    if text in ("", "--", "---", "None", "null"):
        return None
    try:
        n = float(text)
    except (ValueError, OverflowError):
        return None
    return n if math.isfinite(n) else None


def text(value, maximum=100_000):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise schema_changed("基金来源文本字段类型或长度异常")
    return value.strip() or None


def code_digits(code):
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise bad_arg("基金源代码须为六位数字，不含市场后缀")
    return code


def get(url, params=None):
    address = url + ("?" + urlencode(params) if params else "")
    body = downloads.get_text(address, headers={"Referer": F10 + "/"},
                              max_bytes=8 * 1024 * 1024, timeout=35)
    if len(body) > 8 * 1024 * 1024:
        raise schema_changed("基金来源响应超过大小限制")
    return body


def json_payload(body):
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        raise schema_changed("基金来源不是合法 JSON") from None
    if not isinstance(value, dict):
        raise schema_changed("基金来源 JSON 顶层不是对象")
    if value.get("ErrCode") not in (0, "0"):
        raise unavailable("免费基金来源返回业务错误")
    return value


def directory():
    body = get(DIRECTORY).lstrip("\ufeff").strip()
    match = re.fullmatch(r"(?:var\s+)?r\s*=\s*(\[.*\])\s*;?", body, re.S)
    if not match:
        raise schema_changed("基金目录的静态数组格式已变化")
    try:
        rows = json.loads(match.group(1))
    except (ValueError, RecursionError):
        raise schema_changed("基金目录不是合法静态 JSON 数组") from None
    if not isinstance(rows, list) or len(rows) > 100_000:
        raise schema_changed("基金目录规模或结构异常")
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 4 or not re.fullmatch(r"[0-9]{6}", str(row[0])):
            raise schema_changed("基金目录记录缺少有效代码")
        out.append({"code": row[0], "name": text(row[2], 300),
                    "fund_type": text(row[3], 100), "source_url": DIRECTORY})
    return out


class Tables(HTMLParser):
    """Extract visible table cells, headings and paragraphs, including omitted td ends.

    Does not execute JS or follow links; script/style data is discarded. Headings
    remain attached to tables so report dates cannot be detached from holdings.
    """
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.paragraphs = [], []
        self.table = self.row = self.cell = None
        self.heading, self.heading_buf, self.para = "", None, None
        self.hidden = 0

    def end_cell(self):
        if self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
        self.cell = None

    def end_row(self):
        self.end_cell()
        if self.row is not None and self.table is not None and self.row:
            self.table["rows"].append(self.row)
        self.row = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if self.hidden:
            return
        if tag == "h4":
            self.heading_buf = []
        elif tag == "p":
            self.para = []
        elif tag == "table":
            if self.table is not None:
                self.end_row()
                self.tables.append(self.table)
            self.table = {"heading": self.heading, "rows": []}
        elif tag == "tr":
            self.end_row()
            self.row = []
        elif tag in ("td", "th") and self.table is not None:
            self.end_cell()
            if self.row is None:
                self.row = []
            self.cell = []
        elif tag == "br":
            self.handle_data(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag in ("td", "th"):
            self.end_cell()
        elif tag == "tr":
            self.end_row()
        elif tag == "table" and self.table is not None:
            self.end_row()
            self.tables.append(self.table)
            self.table = None
        elif tag == "h4" and self.heading_buf is not None:
            self.heading = re.sub(r"\s+", " ", "".join(self.heading_buf)).strip()
            self.heading_buf = None
        elif tag == "p" and self.para is not None:
            value = re.sub(r"\s+", " ", "".join(self.para)).strip()
            if value:
                self.paragraphs.append({"heading": self.heading, "text": value})
            self.para = None

    def handle_data(self, data):
        if self.hidden:
            return
        for buf in (self.cell, self.heading_buf, self.para):
            if buf is not None:
                buf.append(data)


def parse_html(body):
    if not isinstance(body, str) or len(body) > 8 * 1024 * 1024:
        raise schema_changed("基金 HTML 大小或类型异常")
    parser = Tables()
    try:
        parser.feed(body)
        parser.close()
    except (ValueError, RecursionError):
        raise schema_changed("基金 HTML 无法解析") from None
    return parser


def archive_html(body):
    # Decode only a JSON double-quoted string. The surrounding JS is never run.
    match = re.search(r"\bcontent\s*:\s*", body)
    if not match:
        raise schema_changed("基金持仓响应缺少 content 字符串")
    try:
        value, _ = json.JSONDecoder().raw_decode(body[match.end():])
    except (ValueError, RecursionError):
        raise schema_changed("基金持仓 content 不是合法静态字符串") from None
    if not isinstance(value, str):
        raise schema_changed("基金持仓 content 不是文本")
    return value


def profile(code):
    code_digits(code)
    url = F10 + f"/jbgk_{code}.html"
    page = parse_html(get(url))
    fields = {}
    for table in page.tables:
        if any("基金全称" in row for row in table["rows"]):
            for row in table["rows"]:
                for pos in range(0, len(row) - 1, 2):
                    fields[row[pos]] = row[pos + 1]
    if not fields:
        raise schema_changed("基金档案页缺少基础资料表")
    sections = [p for p in page.paragraphs if p["heading"] in
                ("投资目标", "投资理念", "投资范围", "投资策略", "分红政策", "风险收益特征")]
    return {"fields": fields, "sections": sections, "effective_date": None,
            "source_url": url, "fee_scope": "页面列示费率含销售渠道优惠，非全部持有成本"}


def managers(code):
    code_digits(code)
    url = F10 + f"/jjjl_{code}.html"
    page = parse_html(get(url))
    found = [t for t in page.tables if t["rows"] and "起始期" in t["rows"][0]]
    if not found:
        raise schema_changed("基金经理页缺少任职表")
    return {"tables": found, "source_url": url, "date_basis": "来源网页任职历史，可能不同于当前合同任职起点"}


def fund_series_kind(fund_type):
    """Classify only explicit source labels, never codes or name prefixes."""
    if not isinstance(fund_type, str):
        return "unknown"
    if "货币" in fund_type:
        return "money_market"
    if any(label in fund_type.upper() for label in ("股票", "混合", "债券", "指数", "商品", "QDII", "FOF", "REIT")):
        return "nav"
    return "unknown"


def nav_page(code, start, end, page=1, size=20, fund_type=None):
    code_digits(code)
    obj = json_payload(get(API + "/lsjz", {"fundCode": code, "pageIndex": page,
                       "pageSize": size, "startDate": start, "endDate": end}))
    data = obj.get("Data")
    rows = data.get("LSJZList") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > size:
        raise schema_changed("基金净值列表结构或页长异常")
    if obj.get("PageSize") != size or obj.get("PageIndex") != page:
        raise schema_changed("基金净值来源实际分页与请求不一致，不能按请求页长推断完整")
    income_label = text(data.get("SYType"), 100)
    identity_kind = fund_series_kind(fund_type)
    if income_label and "收益" in income_label:
        series_kind = "money_market_yield" if income_label in ("每万份收益", "万份收益") else "income_unknown_unit"
    elif income_label == "单位净值" or identity_kind == "nav":
        series_kind = "nav"
    else:
        series_kind = "unknown"
    out = []
    for r in rows:
        if not isinstance(r, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(r.get("FSRQ"))):
            raise schema_changed("基金净值记录缺少日期")
        out.append({"nav_date": r["FSRQ"], "ann_date": None,
                    "unit_nav": number(r.get("DWJZ")) if series_kind == "nav" else None,
                    "accum_nav": number(r.get("LJJZ")) if series_kind == "nav" else None,
                    "adj_nav": None, "net_asset": None, "total_netasset": None,
                    "income_per_10000_shares": number(r.get("DWJZ")) if series_kind == "money_market_yield" else None,
                    "seven_day_annualized_yield_pct": number(r.get("LJJZ")) if series_kind == "money_market_yield" else None,
                    "series_kind": series_kind,
                    "source_type": {"fund_type_code": data.get("FundType"), "income_label": income_label,
                                    "confirmed_fund_type": fund_type},
                    "raw_values": {"DWJZ": text(r.get("DWJZ"), 100) if isinstance(r.get("DWJZ"), str) else r.get("DWJZ"),
                                   "LJJZ": text(r.get("LJJZ"), 100) if isinstance(r.get("LJJZ"), str) else r.get("LJJZ"),
                                   "NAVTYPE": text(r.get("NAVTYPE"), 100)},
                    "purchase_status": text(r.get("SGZT"), 300),
                    "redemption_status": text(r.get("SHZT"), 300),
                    "source": "eastmoney_lsjz"})
    total = obj.get("TotalCount")
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise schema_changed("基金净值分页总数异常")
    return out, total


def holdings(code, period, kind):
    code_digits(code)
    if kind not in ("stocks", "bonds"):
        raise bad_arg("持仓类别只能是 stocks 或 bonds")
    params = {"type": "jjcc" if kind == "stocks" else "zqcc", "code": code,
              "year": period[:4], "topline": 1000, "month": ""}
    content = archive_html(get(ARCHIVES, params))
    page = parse_html(content)
    groups = []
    expected = "股票代码" if kind == "stocks" else "债券代码"
    for table in page.tables:
        if not table["rows"] or expected not in table["rows"][0]:
            continue
        match = re.search(r"截止至[：:]?\s*(\d{4}-\d{2}-\d{2})", table["heading"])
        if not match:
            raise schema_changed("基金持仓表缺少报告日期")
        if match.group(1) != period:
            continue
        headers = table["rows"][0]
        rows = []
        for cells in table["rows"][1:]:
            if len(cells) != len(headers):
                raise schema_changed("基金持仓表的列数与表头不一致")
            raw = dict(zip(headers, cells))
            def find(key):
                return next((v for h, v in raw.items() if key in re.sub(r"\s+", "", h)), None)
            def in_ten_thousands(key, large_unit, small_unit):
                pair = next(((re.sub(r"\s+", "", h), v) for h, v in raw.items() if key in re.sub(r"\s+", "", h)), None)
                if pair is None:
                    return None
                header, value = pair
                n = number(value)
                if n is None:
                    return None
                if large_unit in header:
                    return n
                if re.search(r"[（(]\s*" + small_unit + r"(?:人民币)?\s*[）)]", header):
                    return n / 10_000
                # Unknown units are retained as raw text, never guessed.
                return None
            rows.append({"symbol": raw.get(expected), "name": find("名称"),
                         "nav_weight_pct": number(find("占净值比例")),
                         "shares_10k": in_ten_thousands("持股数", "万股", "股"),
                         "market_value_10k": in_ten_thousands("持仓市值", "万元", "元"),
                         "raw_cells": raw})
        groups.append({"asset_class": kind, "report_date": period, "ann_date": None,
                       "scope": "disclosed_subset" if kind == "stocks" else "mixed_disclosed_subset",
                       "complete_portfolio": False, "headers": headers, "rows": rows,
                       "units": {"nav_weight_pct": "% of fund NAV", "shares_10k": "万股",
                                 "market_value_10k": "万元，币种按原表头；未写明则未知"},
                       "source": "eastmoney_archives", "source_url": ARCHIVES + "?" + urlencode(params),
                       "limitations": ["债券表可能混合主要债券及转股期可转债，并非全部债券" if kind == "bonds"
                                       else "所取是披露子集，不能据条数推断完整持仓",
                                       "股/元与万股/万元按表头显式换算；未知单位保留raw_cells且标准化数值为null",
                                       "raw_cells 中最新价/涨跌幅不是报告日行情，不能参与历史估值"]})
    if content.strip() and "暂无" not in content and not any(
            t["rows"] and expected in t["rows"][0] for t in page.tables):
        raise schema_changed("基金持仓页面缺少预期表头")
    return groups


def allocation(code, period=None):
    code_digits(code)
    url = F10 + f"/zcpz_{code}.html"
    page = parse_html(get(url))
    found = []
    for table in page.tables:
        if not table["rows"] or "报告期" not in table["rows"][0] or "股票占净比" not in table["rows"][0]:
            continue
        headers = table["rows"][0]
        for row in table["rows"][1:]:
            if len(row) != len(headers):
                raise schema_changed("资产配置列数与表头不一致")
            data = dict(zip(headers, row))
            if period and data.get("报告期") != period:
                continue
            found.append({"report_date": data.get("报告期"), "ann_date": None,
                          "stock_nav_pct": number(data.get("股票占净比")),
                          "bond_nav_pct": number(data.get("债券占净比")),
                          "cash_nav_pct": number(data.get("现金占净比")),
                          "net_assets_100m": number(data.get("净资产（亿元）")), "raw_cells": data})
    if not any(t["rows"] and "股票占净比" in t["rows"][0] for t in page.tables):
        raise schema_changed("资产配置页面缺少预期表头")
    return {"rows": found, "source_url": url, "scope": "reported_asset_categories",
            "units": {"weights": "% of fund NAV (may sum above 100 with leverage)",
                      "net_assets_100m": "亿元，币种需回查报告"}}


def announcements(code, category, page, size):
    code_digits(code)
    kinds = {"all": "0", "report": "3", "issuance": "1", "dividend": "2",
             "personnel": "4", "sales": "5", "other": "6"}
    if category not in kinds:
        raise bad_arg("公告类别无效")
    params = {"fundcode": code, "pageIndex": page, "pageSize": size, "type": kinds[category]}
    obj = json_payload(get(API + "/JJGG", params))
    rows, total = obj.get("Data"), obj.get("TotalCount")
    if not isinstance(rows, list) or len(rows) > size or not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise schema_changed("基金公告列表或分页结构已变化")
    if obj.get("PageSize") != size or obj.get("PageIndex") != page:
        raise schema_changed("基金公告来源实际分页与请求不一致")
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("FUNDCODE") != code:
            raise schema_changed("基金公告返回标的不一致")
        ident = row.get("ID")
        if not isinstance(ident, str) or not re.fullmatch(r"AN\d{10,30}", ident):
            raise schema_changed("基金公告 ID 格式异常")
        title = text(row.get("TITLE"), 1000)
        published = text(row.get("PUBLISHDATE"), 60)
        if not published or not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[0-9:.+Z-]+)?", published):
            raise schema_changed("基金公告日期异常")
        out.append({"id": ident, "title": title, "published_at": published,
                    "published_date": published[:10], "report_period": None,
                    "source_category": row.get("NEWCATEGORY"),
                    "is_correction_title": bool(title and re.search("更正|修订|更新", title)),
                    "supersedes": None,
                    "url": f"https://fund.eastmoney.com/gonggao/{code},{ident}.html",
                    "pdf_url": f"https://pdf.dfcfw.com/pdf/H2_{ident}_1.pdf",
                    "pdf_link_status": "pattern_derived_unverified",
                    "source_role": "disclosure_mirror_not_verified_original"})
    return {"items": out, "page": page, "page_size": size, "total_count": total,
            "has_more": page * size < total, "next_page": page + 1 if page * size < total else None,
            "category": category, "source_url": API + "/JJGG?" + urlencode(params),
            "correction_coverage": "title_flags_only; no inferred supersession"}
