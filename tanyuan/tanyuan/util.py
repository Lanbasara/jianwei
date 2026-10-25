"""Small pure helpers: tickers, numbers, dates, text hygiene.

Ticker handling is the single most error-prone surface for an investment tool
(000001 is both 上证指数 and 平安银行; 920xxx is the new BSE segment; SH/SZ/BJ prefixes
arrive in a dozen spellings). Everything funnels through `resolve_symbol`.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")

SH_INDEX_WHITELIST = {"000010", "000016", "000300", "000688", "000852", "000905"}
# 000001 is genuinely ambiguous: sh000001=上证指数, sz000001=平安银行.
AMBIGUOUS_CODES = {"000001"}

_TICKER_RE = re.compile(
    r"^(?:(sh|sz|bj)(\d{6})|(\d{6})(?:[.\-](sh|sz|bj|xshg|xshe))?)$", re.IGNORECASE
)


@dataclass(frozen=True)
class Symbol:
    digits: str          # 600519
    market: str          # sh / sz / bj
    kind: str            # stock / index / etf / fund
    explicit: bool       # caller spelled the market out

    @property
    def prefixed(self) -> str:
        return f"{self.market}{self.digits}"

    @property
    def ts_code(self) -> str:
        return f"{self.digits}.{self.market.upper()}"


def classify(digits: str, market: str = "sz") -> str:
    if digits in SH_INDEX_WHITELIST or digits.startswith(("399", "899")):
        return "index"
    if digits == "000001" and market == "sh":
        return "index"          # 上证指数;裸代码由 resolve_symbol 拒绝
    if digits.startswith(("600", "601", "603", "605", "688", "689")):
        return "stock"
    if digits.startswith(("000", "001", "002", "003", "300", "301")):
        return "stock"
    if digits.startswith(("920", "920", "43", "83", "87", "88")):
        return "stock"
    if digits.startswith(("51", "50", "52", "56", "58", "159", "16")):
        return "etf"
    return "stock"


def resolve_symbol(raw: str) -> Symbol:
    """Parse any common spelling into a Symbol.

    Raises ValueError with a usable message on nonsense input (never silently guesses).
    """
    text = str(raw).strip()
    m = _TICKER_RE.match(text)
    if not m:
        hint = "支持 600519 / sh600519 / 600519.SH / 600519.XSHG(A 股)"
        if re.fullmatch(r"\d{4,5}", text):
            hint = "4–5 位数字看起来像港股代码(如 00700);本工具只覆盖 A 股,港股/美股不在能力范围"
        elif re.fullmatch(r"[A-Za-z.\-]{1,10}", text):
            hint = "字母代码看起来像美股/港股;本工具只覆盖 A 股,港股/美股不在能力范围"
        raise ValueError(f"无法识别的代码/写法: {raw!r}。{hint}")
    if m.group(1):
        market, digits = m.group(1).lower(), m.group(2)
        explicit = True
    else:
        digits, market, explicit = m.group(3), (m.group(4) or "").lower(), bool(m.group(4))
    if market == "xshg":
        market = "sh"
    elif market == "xshe":
        market = "sz"
    if not market:
        if digits in AMBIGUOUS_CODES:
            from .errors import Code, TanyuanError
            raise TanyuanError(
                Code.AMBIGUOUS, f"{digits} 同时对应股票和指数,请指定市场",
                hint="使用 sh000001(上证指数)或 sz000001(平安银行)",
                candidates=[{"code": "sh000001", "name": "上证指数", "kind": "index"},
                            {"code": "sz000001", "name": "平安银行", "kind": "stock"}],
            )
        market = natural_market(digits)
    return Symbol(digits=digits, market=market, kind=classify(digits, market), explicit=explicit)


def natural_market(digits: str) -> str:
    if digits.startswith("92"):
        return "bj"
    if digits.startswith(("43", "83", "87", "88")):
        return "bj"
    if digits == "000001":
        return "sz"          # 平安银行; sh000001(上证指数)必须显式前缀
    if digits in SH_INDEX_WHITELIST or digits.startswith(("5", "6", "9")):
        return "sh"
    if digits.startswith("399"):
        return "sz"
    return "sz"


def looks_like_foreign_symbol(raw: str) -> bool:
    """HK/US code shapes that this layer deliberately does not support."""
    text = str(raw).strip()
    if re.fullmatch(r"\d{4,5}", text):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z.\-]{0,9}", text):
        return True
    return False


def num(value, default=None):
    """Lenient numeric parse: '' / '-' / '--' / None -> default. Never raises."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "-", "--", "None", "null", "nan", "NaN"):
        return default
    try:
        return float(text)
    except ValueError:
        return default


def integer(value, default=None):
    got = num(value, default=None)
    return default if got is None else int(got)


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def now_iso() -> str:
    return now_cn().isoformat(timespec="seconds")


def date_only(value: str | None) -> str | None:
    """Accept YYYY-MM-DD / YYYYMMDD / YYYY-MM-DD HH:MM:SS -> YYYY-MM-DD."""
    if not value:
        return None
    text = str(value).strip()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", text)
    if not m:
        raise ValueError(f"日期格式不对: {value!r},应为 YYYY-MM-DD 或 YYYYMMDD")
    return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()


def to_plain_text(value: str | None, *, preserve_lines: bool = True) -> str:
    """HTML/markdown -> plain text WITHOUT destroying document structure.

    `clean_text` collapses all whitespace, which turns a 118k-char annual report into a
    single line: the information is still there but neither a human nor an agent can read
    or grep it. Body text must keep paragraph breaks.
    """
    if not value:
        return ""
    text = str(value)
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|table)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    if not preserve_lines:
        return re.sub(r"\s+", " ", text).strip()
    lines = [re.sub(r"[ \t\u3000]+", " ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line:
            out.append(line)
            blank = 0
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


def clip_text(text: str, limit: int | None) -> tuple[str, dict | None]:
    """Clip to `limit` chars and return structured truncation metadata.

    Structured, not ellipsis: the caller must be able to tell "this is the whole thing"
    from "this is the first 12k of 118k" and know how to get the rest.
    """
    if text is None:
        return "", None
    total = len(text)
    if limit is None or total <= limit:
        return text, None
    limit = max(0, int(limit))
    return text[:limit], {
        "truncated": True,
        "returned_chars": limit,
        "total_chars": total,
        "next_offset": limit,
    }


def clean_text(value: str | None, limit: int = 1200) -> str:
    """Strip HTML, collapse whitespace, truncate. External text is untrusted data."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", "", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def dedupe_rows(rows: list[dict], key: str) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for row in rows:
        k = row.get(key)
        if k in seen:
            continue
        seen.add(k)
        out.append(row)
    return out


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def date_context_hint(date_str: str | None) -> str | None:
    """Explain *why* a date query is empty — weekend, future, or just non-trading."""
    if not date_str:
        return None
    try:
        target = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    today = now_cn().date()
    if target > today:
        return f"{date_str} 是未来日期,数据尚不存在(不可能取到)"
    if target.weekday() >= 5:
        return f"{date_str} 是周末,休市;请用最近交易日(通常为其前的周五)"
    return None


def latest_weekday(date_str: str) -> str:
    """Best-effort previous weekday (holidays ignored)."""
    target = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    cursor = target
    while cursor.weekday() >= 5:
        cursor = cursor - timedelta(days=1)
    return cursor.isoformat()
