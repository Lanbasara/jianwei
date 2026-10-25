"""Tool registry + dispatcher.

A tool is a small declarative object: name, what it covers (and *doesn't*), its args,
its upstream source, its evidence grade, its cache TTL. The dispatcher owns the
cross-cutting concerns (validation, cache, envelope, error mapping) so tool bodies stay
thin and honest.
"""
from __future__ import annotations

import difflib
import math
import json
import shlex
import re
from datetime import date
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import cache, config
from .envelope import Result, failure, success
from .errors import Code, TanyuanError, bad_arg, internal_error, unsupported

REGISTRY: dict[str, "ToolSpec"] = {}


@dataclass
class ToolSpec:
    name: str
    summary: str
    func: Callable[[dict], Result]
    covers: list[str] = field(default_factory=list)
    not_covers: list[str] = field(default_factory=list)
    args_schema: dict = field(default_factory=dict)
    arg_groups: list[dict] = field(default_factory=list)
    returns: list[dict] = field(default_factory=list)
    source: str = "unknown"
    grade: str = "L1"
    cache_ttl_s: int = 0
    cost: str = "free"
    errors: list[str] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    latency_hint_ms: int | None = None
    cache_when: Callable[[dict], bool] | None = None

    def describe(self) -> dict:
        return {
            "name": self.name,
            "summary": self.summary,
            "covers": self.covers,
            "not_covers": self.not_covers,
            "args": self.args_schema,
            "arg_groups": self.arg_groups,
            "help": {"response": "ty help response", "errors": "ty help errors"},
            "returns": self.returns,
            "source": self.source,
            "grade": self.grade,
            "cache_ttl_s": self.cache_ttl_s,
            "cost": self.cost,
            "errors": self.errors,
            "examples": [{**example, "cmd": "ty call " + shlex.quote(self.name) + " --json " +
                          shlex.quote(json.dumps(example["args"], ensure_ascii=False, separators=(',', ':')))}
                         for example in self.examples],
            "latency_hint_ms": self.latency_hint_ms,
        }


def tool(**kwargs) -> Callable:
    def wrap(fn: Callable[[dict], Result]) -> Callable[[dict], Result]:
        name = kwargs.pop("name", None) or fn.__name__.replace("_tool", "").replace("__", ".")
        spec = ToolSpec(name=name, func=fn, **kwargs)
        if name in REGISTRY:
            raise RuntimeError(f"duplicate tool: {name}")
        REGISTRY[name] = spec
        return fn
    return wrap


# ── validation ────────────────────────────────────────────────────────────────

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array[string]": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    "object": lambda v: isinstance(v, dict),
}


def _coerce(key: str, value, expected: str):
    """CLI --arg yields strings; coerce only when the declared schema says so."""
    if expected in ("integer", "number") and isinstance(value, str):
        try:
            return int(value) if expected == "integer" else float(value)
        except ValueError:
            raise bad_arg(f"参数 {key} 应为 {expected}",
                          hint="命令行数值可用 --arg k=8,精确 JSON 请用 --json")
    if expected == "boolean" and isinstance(value, str):
        if value.lower() in ("true", "1", "yes"):
            return True
        if value.lower() in ("false", "0", "no"):
            return False
        raise bad_arg(f"参数 {key} 应为 boolean")
    if expected in ("array[string]", "object") and isinstance(value, str):
        import json as _json
        try:
            parsed = _json.loads(value)
        except ValueError:
            raise bad_arg(f"参数 {key} 应为 JSON {expected}",
                          hint="例: --arg codes='[\"600519\"]' 或改用 --json")
        if not _TYPE_CHECKS[expected](parsed):
            raise bad_arg(f"参数 {key} 应为 {expected}")
        return parsed
    return value


def validate_args(spec: ToolSpec, args: dict) -> dict:
    schema = spec.args_schema or {}
    help_command = f"ty describe {spec.name}"
    if not isinstance(args, dict) or any(not isinstance(key, str) for key in args):
        raise bad_arg("参数必须是字符串键的对象。", f"运行 {help_command}。")
    unknown = set(args) - set(schema)
    if unknown:
        suggestions = sorted({close for key in unknown for close in
                              difflib.get_close_matches(key, sorted(schema), n=1, cutoff=.5)})
        raise TanyuanError(Code.BAD_ARGUMENT, "包含未声明的参数。",
                           hint=f"只使用 describe 中列出的参数；运行 `{help_command}`。",
                           details={"allowed": sorted(schema), "suggested_fields": suggestions,
                                    "help_command": help_command})

    def invalid(key, message):
        rule = schema[key]
        err = TanyuanError(Code.BAD_ARGUMENT, message,
                          hint=f"按 expected 修正 {key}；可省略的参数请直接省略，不用 null 代替。运行 `{help_command}`。",
                          details={"field": key, "expected": rule, "help_command": help_command})
        return err

    clean: dict = {}
    for key, rule in schema.items():
        if key not in args:
            if rule.get("required"):
                raise invalid(key, f"缺少必需参数 {key}。")
            continue
        expected = rule.get("type", "string")
        check = _TYPE_CHECKS.get(expected)
        if check is None:
            raise internal_error("参数 schema 包含未支持的类型。")
        try:
            value = _coerce(key, args[key], expected)
        except TanyuanError:
            raise invalid(key, f"参数 {key} 应为 {expected}。") from None
        if not check(value):
            raise invalid(key, f"参数 {key} 类型应为 {expected}，收到 {type(value).__name__}。")
        if isinstance(value, float) and not math.isfinite(value):
            raise invalid(key, f"参数 {key} 必须是有限数值。")
        if expected in ("integer", "number"):
            if "min" in rule and value < rule["min"]:
                raise invalid(key, f"参数 {key} 不能小于 {rule['min']}。")
            if "max" in rule and value > rule["max"]:
                raise invalid(key, f"参数 {key} 不能大于 {rule['max']}。")
        if expected == "array[string]":
            if len(value) < rule.get("min_items", 0) or len(value) > rule.get("max_items", float('inf')):
                raise invalid(key, f"参数 {key} 的数组长度不符合限制。")
            if any(not item.strip() for item in value):
                raise invalid(key, f"参数 {key} 的数组元素不能为空字符串。")
        if expected == "string" and "min_length" in rule and len(value.strip()) < rule['min_length']:
            raise invalid(key, f"参数 {key} 不能为空。")
        if expected == "string" and "max_length" in rule and len(value) > rule['max_length']:
            raise invalid(key, f"参数 {key} 超过长度上限。")
        if rule.get("format") == "date":
            text = value.strip()
            if not re.fullmatch(r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{8})", text):
                raise invalid(key, f"参数 {key} 需要 YYYY-MM-DD 或 YYYYMMDD 日期。")
            digits = text.replace('-', '')
            try:
                value = date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
            except ValueError:
                raise invalid(key, f"参数 {key} 不是有效日历日期。") from None
        if "enum" in rule and value not in rule["enum"]:
            raise invalid(key, f"参数 {key} 不在允许的枚举值中。")
        clean[key] = value
    for group in spec.arg_groups:
        present = sum(key in clean for key in group['keys'])
        if not group['min'] <= present <= group['max']:
            raise TanyuanError(Code.BAD_ARGUMENT, "参数组合不符合要求。",
                               hint=f"{group['keys']} 中提供 {group['min']}–{group['max']} 个参数；运行 `{help_command}`。",
                               details={"expected_group": group, "help_command": help_command})
    return clean


def _suggest_tools(name: str) -> list[dict]:
    names = list(REGISTRY)
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.4)
    return [{"tool": n, "summary": REGISTRY[n].summary} for n in close]


# ── dispatch ──────────────────────────────────────────────────────────────────

def dispatch(name: str, args: dict | None = None, *, use_cache: bool = True) -> tuple[dict, int]:
    args = {} if args is None else args
    started = time.monotonic()
    spec = REGISTRY.get(name)
    if spec is None:
        err = unsupported(
            "没有这个工具",
            hint="用 `ty tools` 列出全部工具;也许是参数写错了。",
            alternatives=_suggest_tools(name),
        )
        return failure(name if spec else "unknown", {}, err), err.exit_code

    try:
        clean = validate_args(spec, args)
    except TanyuanError as err:
        return failure(name if spec else "unknown", {}, err), err.exit_code

    refresh = clean.get("refresh") is True
    cache_allowed = use_cache and spec.cache_ttl_s > 0 and (spec.cache_when is None or spec.cache_when(clean))
    key_args = {k: v for k, v in clean.items() if k != "refresh"}
    key = cache.make_key(name, key_args)
    cache_warnings = []
    if cache_allowed and not refresh:
        try:
            cached, meta = cache.get(key)
            if cached is not None and not isinstance(cached.get("provenance"), dict):
                raise ValueError("invalid cached envelope")
        except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
            cached, meta = None, None
            cache_warnings.append({"type": "cache_unavailable", "operation": "read",
                                   "message": "响应缓存不可用,本次直接获取数据"})
        if cached is not None:
            duration = int((time.monotonic() - started) * 1000)
            cached["provenance"]["duration_ms"] = duration
            cached["provenance"]["cache"] = {"hit": True, "age_s": meta["age_s"]}
            cached["args"] = clean
            _log(name, clean, True, duration, spec, cache_hit=True)
            return cached, 0

    try:
        result = spec.func(clean)
        if not isinstance(result, Result):
            raise TypeError(f"{name} 返回了 {type(result).__name__},应返回 Result")
    except TanyuanError as err:
        duration = int((time.monotonic() - started) * 1000)
        _log(name, clean, False, duration, spec, error=err.code.value)
        return failure(name, args, err, duration_ms=duration, source=spec.source), err.exit_code
    except Exception as exc:  # noqa: BLE001 - last line of defence
        duration = int((time.monotonic() - started) * 1000)
        err = internal_error(
            f"{name} 内部错误: {type(exc).__name__}",
        )
        _log(name, clean, False, duration, spec, error="INTERNAL")
        return failure(name, args, err, duration_ms=duration, source=spec.source), err.exit_code

    duration = int((time.monotonic() - started) * 1000)
    envelope = success(name, clean, result, duration_ms=duration)
    if refresh:
        try:
            cache.invalidate(name, {"url": clean["url"]} if "url" in clean else key_args)
        except (OSError, sqlite3.Error):
            cache_warnings.append({"type": "cache_unavailable", "operation": "invalidate",
                                   "message": "已重新获取数据,但旧缓存未能清除;后续请使用 refresh=true"})
    if cache_warnings:
        envelope["provenance"].setdefault("warnings", []).extend(cache_warnings)
    if cache_allowed:
        try:
            cache.put(key, envelope, spec.cache_ttl_s)
        except (OSError, sqlite3.Error):
            envelope["provenance"].setdefault("warnings", []).append({
                "type": "cache_unavailable", "operation": "write",
                "message": "本次数据已取得,但响应缓存写入失败"})
    _log(name, clean, True, duration, spec)
    return envelope, 0


def _log(tool_name: str, args: dict, ok: bool, duration_ms: int, spec: ToolSpec,
         *, error: str | None = None, cache_hit: bool = False) -> None:
    cache.log_usage({
        "ts": config.now_iso(),
        "tool": tool_name,
        "args": args,
        "ok": ok,
        "error": error,
        "duration_ms": duration_ms,
        "source": spec.source,
        "cost": spec.cost,
        "cache_hit": cache_hit,
        "caller": "cli",
    })


def load_tools() -> None:
    """Import tool modules for their registration side effects."""
    from .tools import (  # noqa: F401
        capital, entity, events, filing, fund, fundamental, history, industry, macro, market,
        news, newsfeed, search,
    )
    from .tools import global_ as _global_tools  # noqa: F401
