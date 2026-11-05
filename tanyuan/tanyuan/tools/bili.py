"""Declarative bili-source tools. Credentials, acquisition and caches stay outside args."""
from ..bili_source import BiliSourceClient, MAX_DELIVERY_BYTES, _encoded, result_for, validate_get_args, validate_query_sources
from ..envelope import success
from ..errors import Code, TanyuanError
from ..registry import tool


def _client():
    return BiliSourceClient.from_config()


def _result(name, args, client, data):
    result = result_for(data, client.requests)
    envelope = success(name, args, result, duration_ms=999999999)
    if max(len(_encoded(envelope)), len(_encoded(envelope, pretty=True))) + 1 > MAX_DELIVERY_BYTES:
        raise TanyuanError(Code.QUOTA_EXCEEDED, "单次服务结果超出 256 KiB 交付预算。", retryable=False,
                           hint="单条元数据本身过大时请联系服务维护者，不通过重复空游标重试。",
                           details={"service_code": "CLIENT_DELIVERY_LIMIT", "handle": data.get("handle")})
    return result


_WAIT = {"type": "integer", "min": 0, "max": 60000, "description": "有限等待毫秒；默认20000，0立即返回。首请求另有10秒上限，网络重试最多一次。"}
_ERRORS = ["BAD_ARGUMENT", "AUTH_FAILED", "BLOCKED", "NOT_FOUND", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_CHANGED", "RATE_LIMITED", "QUOTA_EXCEEDED"]
_RETURNS = [
    {"field": "handle.id / handle.idempotency_key / handle.cursor", "desc": "原任务、提交标识和不透明同版本续读位置；原样用于下一次调用"},
    {"field": "query.state / query.coverage / query.unknown_remaining", "desc": "服务状态与目录覆盖证据，partial或等待不等于完整资料"},
    {"field": "read_state / read_reason", "desc": "pending、read_partial、read_complete、expired；read_complete只表示本次固定结果读到末尾"},
    {"field": "items[].content.text / text_offset / next_offset", "desc": "本次交付文字及UTF-16不透明位置；用原cursor接续，不按Python字符数重算"},
    {"field": "items[].source_url / published_at / content.observed_at", "desc": "来源、发布时间和服务取得时间"},
    {"field": "items[].content.images / query.capabilities", "desc": "图片引用与受控资源能力；未下载/识别图片，不代表已读图"},
]


@tool(name="bili.pair", summary="首次连接或重新授权 bili-source 客户端；已有可用连接无需配对",
      covers=["不复制token的机器客户端配对", "私有凭据持久恢复", "显式restart重新连接"],
      not_covers=["B站扫码或Cookie", "自动批准来源池", "撤销旧客户端（由管理页操作）"],
      args_schema={"label": {"type": "string", "min_length": 1, "max_length": 80, "description": "客户端标签，默认tanyuan；标签不是身份凭证"},
                   "resume": {"type": "boolean", "description": "使用已保存凭据轮询；首次配对直接返回批准地址，不等待"},
                   "restart": {"type": "boolean", "description": "显式替换本地连接并创建新配对；不会撤销服务上的旧客户端"}, "wait_ms": _WAIT},
      returns=[{"field": "state / verification_uri / code / expires_at", "desc": "配对状态与给用户核对的批准入口；永不返回设备token"}],
      source="bili-source", grade="L3", cache_ttl_s=0, cost="service API only", errors=_ERRORS,
      examples=[{"args": {"label": "tanyuan"}}, {"args": {"resume": True, "wait_ms": 20000}}])
def bili_pair(args):
    client = _client()
    return _result("bili.pair", args, client, client.pair(**args))


@tool(name="bili.pools", summary="列出当前 bili-source 客户端获准的来源池与UP快照",
      covers=["当前客户端可发现的池"], not_covers=["管理或扩张来源池", "未授权池"], args_schema={},
      returns=[{"field": "pools[].id / label / revision / sources", "desc": "服务当前授权池；提交时服务固定池快照"}],
      source="bili-source", grade="L3", cache_ttl_s=0, cost="service API only", errors=_ERRORS, examples=[{"args": {}}])
def bili_pools(args):
    client = _client()
    return _result("bili.pools", args, client, client.pools())


@tool(name="bili.query", summary="按发布时间查询已配置 Bilibili UP 的动态和已有字幕，可选择池内 UID",
      covers=["所选池内指定UP或全部UP的图文和已有字幕", "每次调用独立传入时间范围", "同键受理恢复", "有界正文和同版本续读"],
      not_covers=["B站全站搜索或任意URL/池外UP", "自动同步平台关注列表", "全站查全承诺", "视频媒体/ASR", "图片OCR", "后台自动唤醒agent"],
      args_schema={"pool_id": {"type": "string", "required": True, "min_length": 1, "max_length": 64},
                   "source_uids": {"type": "array[string]", "min_items": 1, "max_items": 20,
                                   "description": "可选：从bili.pools中选择的UID；省略查整个池。只查某位UP时传其UID，不能扩大到池外。"},
                   "idempotency_key": {"type": "string", "required": True, "min_length": 8, "max_length": 128,
                                       "description": "先保留稳定key；同次重试原样使用，不改成新任务"},
                   "last_days": {"type": "integer", "min": 1, "max": 90},
                   "from": {"type": "string", "max_length": 40, "description": "带时区ISO发布时间下界（含），与until同用"},
                   "until": {"type": "string", "max_length": 40, "description": "带时区ISO发布时间上界（不含），与from同用"}, "wait_ms": _WAIT},
      arg_groups=[{"keys": ["last_days", "from"], "min": 1, "max": 1}, {"keys": ["last_days", "until"], "min": 1, "max": 1}],
      returns=_RETURNS, source="bili-source", grade="L3", cache_ttl_s=0, cost="service API; upstream budget owned by service", errors=_ERRORS, validate=validate_query_sources,
      examples=[{"args": {"pool_id": "default", "last_days": 7, "idempotency_key": "research-window-001", "wait_ms": 20000}}])
def bili_query(args):
    client = _client()
    return _result("bili.query", args, client, client.query(args))


@tool(name="bili.get", summary="用原任务ID和可选同版本游标有限续读，保留pending/partial/expired证据",
      covers=["原查询状态", "长正文及字幕的精确分块续读", "中途到期与已交付文字"],
      not_covers=["自动替换过期结果", "新建采集任务", "用本地缓存掩盖撤销或到期"],
      args_schema={"id": {"type": "string", "required": True, "max_length": 36, "description": "原handle.id"},
                   "cursor": {"type": "object", "description": "原handle.cursor，含id/version/result_offset/content_offset；原样复制，不计算或修改"}, "wait_ms": _WAIT},
      returns=_RETURNS, source="bili-source", grade="L3", cache_ttl_s=0, cost="service API only", errors=_ERRORS, validate=validate_get_args,
      examples=[{"args": {"id": "00000000-0000-4000-8000-000000000001", "wait_ms": 20000}}])
def bili_get(args):
    client = _client()
    return _result("bili.get", args, client, client.get(args))
