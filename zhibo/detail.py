"""主播详情的安全展示模型。

这个模块刻意不依赖任何具体 UI。界面只需要把选中的
``FollowerStatus`` 和对应 ``PlatformHealth`` 传给 ``build_detail_view``，
从而避免详情展示逻辑与表格、轮询调度逻辑互相耦合。
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from zhibo.app_logging import redact_sensitive_text
from zhibo.monitor import FollowerStatus, PlatformHealth


# Do not display a stream URL verbatim in the UI.  Stream URLs routinely carry
# time-limited signatures under provider-specific query keys, so merely
# redacting known ``token`` keys is not sufficient here.
_URL_IN_TEXT_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s<>'\"]+")
_URL_TRAILING_PUNCTUATION = ".,;:!?)]}"
_MAX_DETAIL_TEXT_LENGTH = 500


@dataclass(frozen=True)
class DetailRow:
    """一条可渲染、已经脱敏的详情字段。"""

    label: str
    value: str
    tone: str = "normal"  # normal / ok / warning / error / muted


@dataclass(frozen=True)
class DetailViewModel:
    """详情页的纯展示快照，适合不启动 UI 直接单测。"""

    title: str
    rows: tuple[DetailRow, ...]
    confirmed_live: bool
    stream_available: bool

    def value_for(self, label: str) -> str:
        """返回指定字段，供轻量 UI 或测试代码读取。"""
        for row in self.rows:
            if row.label == label:
                return row.value
        raise KeyError(label)


def format_datetime(value: object) -> str:
    """将状态时间转换成稳定、易读的本地时间文本。"""
    if not isinstance(value, datetime):
        return "尚无记录"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def safe_url_hint(value: object) -> str:
    """返回只包含协议和主机名的 URL 提示，不暴露路径、参数或凭据。"""
    text = redact_sensitive_text(value).strip()
    if not text:
        return "地址未提供"
    try:
        parsed = urlsplit(text)
        host = parsed.hostname
    except ValueError:
        host = None
        parsed = None

    if not parsed or not parsed.scheme or not host:
        return "地址已隐藏"
    return f"{parsed.scheme.casefold()}://{host}/…（地址已隐藏）"


def redact_detail_text(value: object, *, limit: int = _MAX_DETAIL_TEXT_LENGTH) -> str:
    """净化可能来自插件或网络错误的文本，避免详情页泄露会话信息。

    通用日志脱敏器会处理常见 token/header 字段；此处进一步将任何 URL
    缩为主机名提示，防止未知签名参数或路径型临时凭据被展示。
    """
    text = redact_sensitive_text(value)

    def replace_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        stripped = raw.rstrip(_URL_TRAILING_PUNCTUATION)
        trailing = raw[len(stripped):]
        return safe_url_hint(stripped) + trailing

    text = _URL_IN_TEXT_RE.sub(replace_url, text)
    text = " ".join(text.split())
    if limit > 0 and len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text or "未提供"


def _last_confirmed_result(status: FollowerStatus) -> str:
    if status.last_confirmed_check is None:
        return "尚无确认结果"
    return "在线" if status.live_info.is_live else "离线"


def is_currently_confirmed_live(status: FollowerStatus) -> bool:
    """只有本轮已确认在线、且没有新的检测进行中时才允许使用缓存流。"""
    return bool(
        status.check_state == "online"
        and status.live_info.is_live
        and not status.is_checking
    )


def format_confirmed_state(status: FollowerStatus) -> tuple[str, str]:
    """格式化当前状态，明确区分当前确认和保留的上次结果。"""
    previous = _last_confirmed_result(status)
    if status.is_checking:
        return f"正在检查（上次确认：{previous}）", "warning"

    state = (status.check_state or "unknown").casefold()
    if state == "online" and status.live_info.is_live:
        return "已确认在线", "ok"
    if state == "offline":
        return "已确认离线", "muted"
    if state == "error":
        return f"检测异常（上次确认：{previous}）", "error"
    if state == "skipped":
        return f"本轮跳过（上次确认：{previous}）", "warning"
    if state == "backoff":
        return f"退避等待（上次确认：{previous}）", "warning"
    if state == "disabled":
        return "已禁用", "muted"
    if state == "online":
        return "状态待重新确认", "warning"
    return "尚未确认", "muted"


def _seconds_until(value: object, now: datetime | None) -> int | None:
    if not isinstance(value, datetime):
        return None
    try:
        if now is None:
            reference = datetime.now(value.tzinfo) if value.tzinfo else datetime.now()
        elif value.tzinfo is not None and now.tzinfo is None:
            reference = now.replace(tzinfo=value.tzinfo)
        elif value.tzinfo is None and now.tzinfo is not None:
            reference = now.replace(tzinfo=None)
        else:
            reference = now
        return max(0, math.ceil((value - reference).total_seconds()))
    except (TypeError, OverflowError, ValueError):
        return None


def format_platform_health(
    health: PlatformHealth | object | None,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """将平台熔断状态转换为面向用户的摘要。"""
    if health is None:
        return "正常（尚无故障记录）", "ok"

    state = str(getattr(health, "state", "healthy") or "healthy").casefold()
    try:
        failures = max(0, int(getattr(health, "consecutive_failures", 0) or 0))
    except (TypeError, ValueError):
        failures = 0

    retry_seconds = _seconds_until(getattr(health, "next_allowed_at", None), now)
    if retry_seconds is None:
        try:
            retry_seconds = max(0, math.ceil(float(getattr(health, "retry_after_seconds", 0) or 0)))
        except (TypeError, ValueError):
            retry_seconds = 0
    retry = f"，约 {retry_seconds} 秒后重试" if retry_seconds > 0 else ""

    if state == "healthy":
        return "正常", "ok"
    if state == "degraded":
        return f"退化：连续失败 {failures} 次{retry}", "warning"
    if state == "outage":
        return f"熔断中：连续失败 {failures} 次{retry}", "error"
    return f"状态未知：{redact_detail_text(state, limit=80)}", "warning"


def format_platform_error(health: PlatformHealth | object | None) -> str:
    """返回已脱敏的平台级最近故障；它可能不同于当前主播的检测错误。"""
    if health is None:
        return "无"
    value = getattr(health, "last_error", "")
    return redact_detail_text(value, limit=240) if value else "无"


def _stream_url(status: FollowerStatus) -> str:
    info = status.live_info
    return str(info.flv_url or info.m3u8_url or info.stream_url or "")


def format_stream_availability(status: FollowerStatus) -> tuple[str, bool, str]:
    """返回流地址状态、是否可尝试播放和安全的地址提示。"""
    stream_url = _stream_url(status)
    hint = safe_url_hint(stream_url) if stream_url else ""
    if is_currently_confirmed_live(status):
        if stream_url:
            return f"可尝试播放（本轮已确认在线；{hint}）", True, "ok"
        return "不可用（已确认在线，但插件未返回流地址）", False, "warning"
    if stream_url:
        if status.is_checking:
            return "已缓存但检测进行中，暂不可直接使用（地址已隐藏）", False, "warning"
        return "已缓存但当前未确认在线，暂不可直接使用（地址已隐藏）", False, "warning"
    return "未取得流地址", False, "muted"


_HISTORY_STATE_LABELS = {
    "online": "确认在线",
    "offline": "确认离线",
    "error": "检测异常",
    "skipped": "本轮跳过",
    "backoff": "退避等待",
    "disabled": "已禁用",
    "unknown": "状态未知",
}


def _history_value(entry: object, name: str, default: Any = None) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(name, default)
    return getattr(entry, name, default)


def format_recent_history(history: object, *, limit: int = 3) -> str:
    """格式化最近状态事件，兼容 dataclass 与 dict 条目。

    ``FollowerStatus.history`` 是内存态；旧配置和旧会话没有它时保持空态
    兼容，不要求 UI 或迁移逻辑补写历史。
    """
    if limit <= 0 or isinstance(history, (str, bytes, Mapping)) or not isinstance(history, Iterable):
        return "暂无状态事件"

    entries = list(history)[-limit:]
    if not entries:
        return "暂无状态事件"

    lines: list[str] = []
    for entry in reversed(entries):
        at = format_datetime(_history_value(entry, "at"))
        raw_state = str(_history_value(entry, "state", "unknown") or "unknown").casefold()
        label = _HISTORY_STATE_LABELS.get(raw_state, redact_detail_text(raw_state, limit=80))
        is_live = _history_value(entry, "is_live", None)
        if raw_state not in {"online", "offline"} and isinstance(is_live, bool):
            label += "（上次确认在线）" if is_live else "（上次确认离线）"
        message = _history_value(entry, "message", "")
        detail = redact_detail_text(message, limit=180) if message else ""
        lines.append(f"{at} · {label}{f' · {detail}' if detail else ''}")
    return "\n".join(lines)


def build_detail_view(
    status: FollowerStatus,
    platform_health: PlatformHealth | object | None = None,
    *,
    now: datetime | None = None,
) -> DetailViewModel:
    """构建一个不会包含原始流地址或敏感错误文本的详情快照。"""
    follower = status.follower
    state_text, state_tone = format_confirmed_state(status)
    health_text, health_tone = format_platform_health(platform_health, now=now)
    stream_text, stream_available, stream_tone = format_stream_availability(status)
    error = redact_detail_text(status.error) if status.error else "无"
    platform_error = format_platform_error(platform_health)
    info = status.live_info
    history = format_recent_history(getattr(status, "history", []))

    rows = (
        DetailRow("当前确认状态", state_text, state_tone),
        DetailRow("上次确认", format_datetime(status.last_confirmed_check), "normal"),
        DetailRow("上次检查", format_datetime(status.last_check), "normal"),
        DetailRow("检测错误", error, "error" if status.error else "ok"),
        DetailRow("平台健康", health_text, health_tone),
        DetailRow(
            "平台最近故障",
            platform_error,
            "error" if platform_error != "无" else "ok",
        ),
        DetailRow("元数据健康", redact_detail_text(status.metadata_health or "-", limit=80), "normal"),
        DetailRow("流地址", stream_text, stream_tone),
        DetailRow("直播标题", redact_detail_text(info.title or "未提供", limit=200), "normal"),
        DetailRow("主播元数据", redact_detail_text(info.anchor_name or "未提供", limit=160), "normal"),
        DetailRow("画质", redact_detail_text(info.quality_name or follower.quality or "未提供", limit=100), "normal"),
        DetailRow(
            "平台 / 插件",
            f"{redact_detail_text(follower.platform or "未标注", limit=80)} / "
            f"{redact_detail_text(follower.plugin or "未标注", limit=80)}",
            "normal",
        ),
        DetailRow("最近事件", history, "muted"),
        DetailRow("安全说明", "流地址、URL 路径和查询参数不会在详情中原样显示。", "muted"),
    )
    return DetailViewModel(
        title=f"主播详情 · {redact_detail_text(follower.name or "未命名主播", limit=160)}",
        rows=rows,
        confirmed_live=is_currently_confirmed_live(status),
        stream_available=stream_available,
    )
