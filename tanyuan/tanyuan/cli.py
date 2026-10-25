"""Public CLI: one JSON response, strict arguments, offline discovery."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from . import __version__
from .errors import Code, TanyuanError, bad_arg, internal_error, unsupported
from .interface import HelpRequested, build_parser, topic_help


def _print(obj: dict, *, pretty: bool = False) -> None:
    text = json.dumps(obj, ensure_ascii=False, allow_nan=False,
                      indent=2 if pretty else None, separators=None if pretty else (',', ':'))
    sys.stdout.write(text + '\n')


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise bad_arg('JSON 对象有重复键。', '每个键只出现一次；不会用后值覆盖前值。')
        result[key] = value
    return result


def _reject_constant(value):
    raise bad_arg('JSON 中的 NaN/Infinity 不合法。', '使用有限数字；未知值是否允许 null 请看 describe。')


def _load_json(text):
    try:
        result = json.loads(text, object_pairs_hook=_object, parse_constant=_reject_constant)
        _finite(result)
        return result
    except (ValueError, RecursionError):
        raise bad_arg('JSON 格式无效或嵌套过深。', '键与字符串用双引号；去掉尾逗号；用 --json 传一个完整对象。') from None


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise bad_arg('数值超出有限范围。', '缩小数值范围，不能使用 NaN 或 Infinity。')
    if isinstance(value, dict):
        for v in value.values():
            _finite(v)
    elif isinstance(value, list):
        for v in value:
            _finite(v)


def _parse_value(text: str):
    stripped = text.strip()
    if stripped in ('true', 'false', 'null') or stripped[:1] in ('{', '[', '"'):
        return _load_json(stripped)
    return text  # Preserve leading zeros; only the declared schema may coerce.


def _tool(name):
    from . import registry
    registry.load_tools()
    spec = registry.REGISTRY.get(name)
    if spec is None:
        raise unsupported('没有这个工具。', '运行 ty tools，或选择 alternatives 中符合任务的工具。',
                          alternatives=registry._suggest_tools(name))
    return spec


def _show_help(parser, tool_name=None):
    data = {'usage': parser.format_help(), 'topics': ['ty help config', 'ty help response', 'ty help errors']}
    if tool_name:
        data['tool'] = _tool(tool_name).describe()
    _print({'ok': True, 'tool': 'help', 'data': data}, pretty=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(list(sys.argv[1:] if argv is None else argv))
    except TanyuanError as err:
        _print({'ok': False, 'tool': 'cli', 'error': err.to_dict()})
        return err.exit_code
    except Exception as exc:
        err = internal_error('CLI 无法完成请求：' + type(exc).__name__)
        _print({'ok': False, 'tool': 'cli', 'error': err.to_dict()})
        return err.exit_code


def _main(argv):
    parser, parsers = build_parser()
    try:
        ns = parser.parse_args(argv)
    except HelpRequested as request:
        name = argv[1] if len(argv) > 1 and argv[0] in ('call', 'describe') and not argv[1].startswith('-') else None
        return _show_help(request.parser, name)
    if ns.version:
        if ns.command:
            raise bad_arg('--version 不能与子命令混用。', '单独运行 ty --version。')
        _print({'ok': True, 'version': __version__})
        return 0
    if ns.command is None:
        return _show_help(parser)
    if ns.command == 'help':
        path = tuple(ns.topic)
        if path in parsers:
            return _show_help(parsers[path])
        if len(path) == 1:
            data = topic_help(path[0])
            if data is not None:
                _print({'ok': True, 'tool': 'help', 'data': data}, pretty=True)
                return 0
            return _show_help(parsers[('call',)], path[0])
        raise bad_arg('没有这个帮助路径。', '运行 ty --help，或 ty help garden check 等命令路径。')
    if ns.command == 'version':
        _print({'ok': True, 'version': __version__})
        return 0
    if ns.command == 'describe':
        _print({'ok': True, 'tool': 'describe', 'data': _tool(ns.tool).describe()}, pretty=True)
        return 0
    if ns.command == 'tools':
        from . import registry
        registry.load_tools()
        entries = []
        for spec in sorted(registry.REGISTRY.values(), key=lambda s: s.name):
            entry = {k: v for k, v in spec.describe().items() if k in
                     ('name', 'summary', 'grade', 'source', 'cost', 'covers', 'not_covers')}
            if ns.all:
                entry.update(args=spec.args_schema, arg_groups=spec.arg_groups)
            entries.append(entry)
        _print({'ok': True, 'tool': 'catalog', 'data': {'count': len(entries), 'tools': entries}})
        return 0
    if ns.command == 'call':
        from . import registry
        _tool(ns.tool)
        args = _load_json(ns.json_args) if ns.json_args is not None else {}
        if not isinstance(args, dict):
            raise bad_arg('--json 必须是 JSON 对象。', '单个参数也要放在对象里；按 ty describe <tool> 的 args 构造。')
        for pair in ns.arg:
            key, separator, value = pair.partition('=')
            if not separator or not key:
                raise bad_arg('--arg 需要非空的 KEY=VALUE。', '例如 --arg code=600036；复杂参数可用 --json。')
            if key in args:
                raise bad_arg('参数键重复，已拒绝覆盖。', '--json 和所有 --arg 之间，每个键只提供一次。')
            args[key] = _parse_value(value)
        envelope, status = registry.dispatch(ns.tool, args, use_cache=not ns.no_cache)
        _print(envelope, pretty=ns.pretty or sys.stdout.isatty())
        return status
    if ns.command == 'cache':
        return _cache_command(ns)
    if ns.command == 'garden':
        return _garden_command(ns)
    if ns.command == 'doctor':
        result = _doctor()
        _print(result, pretty=True)
        return 0 if result['ok'] else 4
    raise bad_arg('没有这个子命令。', '运行 ty --help。')


def _cache_command(ns):
    action = ns.cache_action or 'stats'
    # Validate before importing/initializing storage or deleting any data.
    if action == 'clear':
        selected = any(getattr(ns, name) for name in ('kv', 'blobs', 'usage', 'reference'))
        if not ns.all and not selected:
            raise bad_arg('必须指定清理类别。', '运行 ty cache clear --help，明确选择要删除的类别。')
        if ns.all and selected:
            raise bad_arg('--all 与单独类别不能混用。', '选择单独类别，或在用户确认后使用 --all --yes。')
        if ns.all and not ns.yes:
            raise bad_arg('全量清理需要用户确认。', '确认后加 --yes；否则只选择要清理的类别。')
    from . import blobs, cache
    if action == 'stats':
        result = cache.stats()
        result['blobs'] = blobs.stats()
    elif action == 'prune':
        result = cache.prune(max_age_days=ns.days)
        result['blobs'] = blobs.purge(older_than_days=ns.blobs_days) if ns.blobs_days is not None else None
    else:
        result = cache.clear_all(**{name: ns.all or getattr(ns, name) for name in ('kv', 'blobs', 'usage', 'reference')})
    _print({'ok': True, 'tool': 'cache', 'data': result}, pretty=True)
    return 0


def _garden_command(ns):
    from . import garden
    action = ns.garden_action or 'status'
    source = getattr(ns, 'source_set', 'policy-news')
    output = getattr(ns, 'report', None) or getattr(ns, 'out', None)
    if output and not Path(output).parent.is_dir():
        raise bad_arg('输出目录不存在。', '先创建目录，再使用其中的新文件路径。')
    if output and (Path(output).exists() or Path(output).is_symlink()):
        raise bad_arg('输出文件已存在，未覆盖。', '为 --report/--out 选择新的文件路径。')
    if action == 'status':
        result = garden.status(source)
    elif action in ('check', 'plan'):
        result = getattr(garden, action)(source, workers=ns.workers)
        if output:
            text = garden.render_report(result) if action == 'check' else json.dumps(result, ensure_ascii=False, indent=2)
            try:
                with Path(output).open('x', encoding='utf-8') as stream:
                    stream.write(text)
            except OSError:
                raise bad_arg('无法创建输出文件。', '选择已存在的可写目录内的新文件路径。') from None
            result['report_written_to' if action == 'check' else 'written_to'] = str(Path(output).resolve())
    elif action == 'apply':
        result = garden.apply_plan(ns.plan, yes=ns.yes)
    else:
        result = garden.restore(ns.snapshot, source_set=source, yes=ns.yes)
    _print({'ok': True, 'tool': 'garden', 'data': result}, pretty=True)
    return 0


def _doctor() -> dict:
    """Cheap liveness checks: what works right now, with a fix hint per failure."""
    checks = []

    def record(name: str, fn):
        try:
            detail = fn()
            checks.append({"check": name, "ok": True, "detail": detail})
        except Exception as exc:  # noqa: BLE001
            checks.append({
                "check": name,
                "ok": False,
                "error": exc.to_dict() if isinstance(exc, TanyuanError) else internal_error("检查失败：" + type(exc).__name__).to_dict(),
            })

    def tencent():
        from .tools.market import fetch_quotes
        payload = fetch_quotes(["600519"])
        quote = payload["quotes"]["600519"]
        return {"name": quote.get("name"), "price": quote.get("price")}

    def tushare_check():
        from . import tushare
        from datetime import date, timedelta
        today = date.today()
        rows = tushare.call("trade_cal", {"exchange": "SSE", "start_date": (today-timedelta(days=7)).strftime("%Y%m%d"),
                                         "end_date": today.strftime("%Y%m%d")}, "cal_date,is_open")
        return {"rows": len(rows), "last": rows[-1] if rows else None}

    def cninfo():
        from .tools.filing import _org_map
        mapping = _org_map()
        return {"org_map_size": len(mapping)}

    def tavily():
        from . import searchapi
        payload = searchapi.tavily_search("贵州茅台", count=1)
        return {"results": len(payload["results"])}

    def deepseek():
        from . import searchapi
        payload = searchapi.deepseek_search("贵州茅台 三季报", count=1)
        return {"results": len(payload["results"])}

    for name, fn in [
        ("tencent_quote", tencent),
        ("cninfo_orgmap", cninfo),
        ("tushare", tushare_check),
        ("tavily", tavily),
        ("deepseek_search", deepseek),
    ]:
        record(name, fn)

    try:
        from . import blobs
        checks.append({"check": "blob_store", "ok": True, "detail": blobs.stats()})
    except Exception as exc:  # noqa: BLE001
        checks.append({"check": "blob_store", "ok": False, "error": internal_error("本地正文检查失败：" + type(exc).__name__).to_dict()})

    ok_count = sum(1 for c in checks if c["ok"])
    disk = None
    try:
        from . import blobs as _blobs
        from . import cache as _cache
        stats = _cache.stats()
        disk = {"dir": stats["dir"], "total_mb": stats["total_mb"],
                "cache_mb": stats["cache_mb"], "cache_rows": stats["cache_rows"],
                "expired_rows": stats["expired_rows"], "usage_mb": stats["usage_mb"],
                "blobs": _blobs.stats()}
    except Exception as exc:  # noqa: BLE001 - diagnostics must not fail
        disk = {"error": internal_error("本地缓存检查失败：" + type(exc).__name__).to_dict()}
    return {
        "ok": ok_count == len(checks),
        "version": __version__,
        "checks": checks,
        "disk": disk,
        "summary": f"{ok_count}/{len(checks)} sources reachable",
    }


if __name__ == "__main__":
    sys.exit(main())
