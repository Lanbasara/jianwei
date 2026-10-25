"""Error taxonomy — the contract every tool must speak.

Why a taxonomy instead of exceptions: an agent's next action depends on *why* a call
failed. "No data" means report it. "Source down" means retry/fallback. "Schema changed"
means stop trusting the source. Collapsing these into one string is how agents hallucinate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Code(str, Enum):
    BAD_ARGUMENT = "BAD_ARGUMENT"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    NO_DATA = "NO_DATA"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    SOURCE_SCHEMA_CHANGED = "SOURCE_SCHEMA_CHANGED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_FAILED = "AUTH_FAILED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    BLOCKED = "BLOCKED"
    INTERNAL = "INTERNAL"


EXIT_CODES: dict[Code, int] = {
    Code.BAD_ARGUMENT: 2,
    Code.UNSUPPORTED: 8,
    Code.NOT_FOUND: 4,
    Code.AMBIGUOUS: 2,
    Code.NO_DATA: 3,
    Code.SOURCE_UNAVAILABLE: 4,
    Code.SOURCE_SCHEMA_CHANGED: 7,
    Code.RATE_LIMITED: 6,
    Code.AUTH_FAILED: 5,
    Code.QUOTA_EXCEEDED: 5,
    Code.BLOCKED: 8,
    Code.INTERNAL: 1,
}

RETRYABLE = {
    Code.SOURCE_UNAVAILABLE,
    Code.RATE_LIMITED,
}


# One source for help and the fallback hint on every typed error.
ERROR_GUIDANCE = {
    Code.BAD_ARGUMENT: "按 hint/details 修正参数，先 describe 确认类型和约束；原样重试无效。",
    Code.UNSUPPORTED: "查看 alternatives 或 ty tools 的覆盖边界；不要用其他市场或口径代替。",
    Code.NOT_FOUND: "确认标的或文档标识；必要时先消歧，不要原样重复查询。",
    Code.AMBIGUOUS: "根据用户意图从 candidates 中消歧；无法确定时询问用户，不自动选择。",
    Code.NO_DATA: "仅说明该来源、该查询范围返回为空；不能推断全网没有或事件不存在。",
    Code.SOURCE_UNAVAILABLE: "退避后有限重试，仍失败则寻找有覆盖能力的其他来源，并披露缺失。",
    Code.SOURCE_SCHEMA_CHANGED: "停止使用本次结果，报告源结构变化；换源时重新确认日期和口径。",
    Code.RATE_LIMITED: "有 retry_after_s 时等待指定时间，否则退避并降低频率；不要立即连续重试。",
    Code.AUTH_FAILED: "检查工作区凭据和数据源权限；修复配置前不重试，勿在聊天中提供密钥。",
    Code.QUOTA_EXCEEDED: "请用户检查余额或额度；额度恢复前不重试。",
    Code.BLOCKED: "换用合法可访问的来源，不绕过访问控制。",
    Code.INTERNAL: "停止原样重试；向维护者提供版本、脱敏后的调用方式和错误码。",
}


@dataclass
class TanyuanError(Exception):
    """A failure with a machine-readable code and a human/agent-usable next step."""

    code: Code
    message: str
    hint: str | None = None
    retryable: bool | None = None
    retry_after_s: float | None = None
    candidates: list[dict] | None = None
    alternatives: list[dict] | None = None
    evidence: str | None = None
    details: dict | None = None

    def __post_init__(self) -> None:
        if self.retryable is None:
            self.retryable = self.code in RETRYABLE
        super().__init__(self.message)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.code, 1)

    def to_dict(self) -> dict:
        out: dict = {
            "code": self.code.value,
            "message": self.message,
            "retryable": bool(self.retryable),
        }
        out["hint"] = self.hint or ERROR_GUIDANCE[self.code]
        if self.details:
            out["details"] = self.details
        if self.retry_after_s is not None:
            out["retry_after_s"] = self.retry_after_s
        if self.candidates:
            out["candidates"] = self.candidates
        if self.alternatives:
            out["alternatives"] = self.alternatives
        if self.evidence:
            out["evidence"] = self.evidence[:600]
        return out


# ── constructors: keep call sites terse and consistent ─────────────────────────

def bad_arg(message: str, hint: str | None = None) -> TanyuanError:
    return TanyuanError(Code.BAD_ARGUMENT, message, hint)


def unsupported(message: str, hint: str | None = None,
                alternatives: list[dict] | None = None) -> TanyuanError:
    return TanyuanError(Code.UNSUPPORTED, message, hint, alternatives=alternatives)


def not_found(message: str, hint: str | None = None) -> TanyuanError:
    return TanyuanError(Code.NOT_FOUND, message, hint)


def no_data(message: str, hint: str | None = None) -> TanyuanError:
    return TanyuanError(Code.NO_DATA, message, hint)


def unavailable(message: str, evidence: str | None = None,
                hint: str | None = None) -> TanyuanError:
    return TanyuanError(Code.SOURCE_UNAVAILABLE, message, hint, evidence=evidence)


def schema_changed(message: str, evidence: str | None = None) -> TanyuanError:
    return TanyuanError(
        Code.SOURCE_SCHEMA_CHANGED,
        message,
        hint="上游返回形状与预期不符,数据不可信。不要使用本次结果;报告该源已变更,并切换备源。",
        evidence=evidence,
    )


def rate_limited(message: str, retry_after_s: float | None = None) -> TanyuanError:
    return TanyuanError(Code.RATE_LIMITED, message,
                        retry_after_s=retry_after_s,
                        hint=ERROR_GUIDANCE[Code.RATE_LIMITED])


def auth_failed(message: str, hint: str | None = None) -> TanyuanError:
    return TanyuanError(Code.AUTH_FAILED, message,
                        hint=hint or "检查环境变量中的 API key 是否有效/有权限。")


def blocked(message: str, hint: str | None = None) -> TanyuanError:
    return TanyuanError(Code.BLOCKED, message, hint or "换合法来源;不要绕过访问控制。")


def internal_error(message: str, evidence: str | None = None) -> TanyuanError:
    return TanyuanError(
        Code.INTERNAL,
        message,
        hint=ERROR_GUIDANCE[Code.INTERNAL],
        evidence=evidence,
    )
