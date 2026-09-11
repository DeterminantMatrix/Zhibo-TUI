from dataclasses import dataclass
from datetime import datetime, timedelta

from zhibo.detail import (
    build_detail_view,
    format_confirmed_state,
    format_platform_health,
    format_recent_history,
    format_stream_availability,
    redact_detail_text,
    safe_url_hint,
)
from zhibo.monitor import FollowerStatus, PlatformHealth
from zhibo.models import Follower
from zhibo.plugins.base import LiveInfo


@dataclass
class HistoryEntry:
    at: datetime
    state: str
    message: str = ""
    is_live: bool | None = None


def make_status(**overrides) -> FollowerStatus:
    follower = Follower(
        name="测试主播",
        plugin="streamlink",
        platform="twitch",
        url="https://www.twitch.tv/test",
        quality="best",
    )
    defaults = {
        "follower": follower,
        "live_info": LiveInfo(is_live=False),
        "check_state": "unknown",
    }
    defaults.update(overrides)
    return FollowerStatus(**defaults)


def test_detail_marks_only_current_confirmed_live_stream_as_available():
    at = datetime(2026, 7, 10, 12, 0, 0)
    status = make_status(
        live_info=LiveInfo(
            is_live=True,
            stream_url="https://cdn.example.test/live.m3u8?token=stream-secret&signature=also-secret",
        ),
        last_check=at,
        last_confirmed_check=at,
        check_state="online",
    )

    view = build_detail_view(status)

    assert view.confirmed_live is True
    assert view.stream_available is True
    assert view.value_for("当前确认状态") == "已确认在线"
    assert "可尝试播放" in view.value_for("流地址")
    assert "stream-secret" not in view.value_for("流地址")
    assert "signature" not in view.value_for("流地址")
    assert "cdn.example.test" in view.value_for("流地址")


def test_detail_preserves_previous_live_result_but_never_marks_error_as_playable():
    confirmed_at = datetime(2026, 7, 10, 11, 0, 0)
    status = make_status(
        live_info=LiveInfo(is_live=True, stream_url="https://cdn.example.test/live?wsSecret=secret"),
        last_confirmed_check=confirmed_at,
        last_check=datetime(2026, 7, 10, 12, 0, 0),
        check_state="error",
        error="请求 https://api.example.test/check?token=api-secret 超时；Authorization: Bearer bearer-secret",
    )

    view = build_detail_view(status)

    assert view.confirmed_live is False
    assert view.stream_available is False
    assert view.value_for("当前确认状态") == "检测异常（上次确认：在线）"
    assert "已缓存但当前未确认在线" in view.value_for("流地址")
    assert "api-secret" not in view.value_for("检测错误")
    assert "bearer-secret" not in view.value_for("检测错误")
    assert "https://api.example.test/…（地址已隐藏）" in view.value_for("检测错误")


def test_detail_checking_state_does_not_allow_stale_stream_to_be_used():
    status = make_status(
        live_info=LiveInfo(is_live=True, stream_url="https://cdn.example.test/live?x=secret"),
        last_confirmed_check=datetime(2026, 7, 10, 10, 0, 0),
        check_state="online",
        is_checking=True,
    )

    state, tone = format_confirmed_state(status)
    stream, available, stream_tone = format_stream_availability(status)

    assert state == "正在检查（上次确认：在线）"
    assert tone == "warning"
    assert available is False
    assert stream_tone == "warning"
    assert "检测进行中" in stream


def test_platform_health_formats_backoff_against_injected_clock():
    now = datetime(2026, 7, 10, 12, 0, 0)
    health = PlatformHealth(
        state="outage",
        consecutive_failures=3,
        next_allowed_at=now + timedelta(seconds=45),
        last_error="token=platform-secret",
    )

    text, tone = format_platform_health(health, now=now)

    assert text == "熔断中：连续失败 3 次，约 45 秒后重试"
    assert tone == "error"


def test_platform_last_error_is_present_but_redacted_in_the_detail_view():
    health = PlatformHealth(
        state="degraded",
        last_error="连接 https://api.example.test/status?token=platform-secret 失败",
    )

    text = build_detail_view(make_status(), health).value_for("平台最近故障")

    assert "platform-secret" not in text
    assert "https://api.example.test/…（地址已隐藏）" in text


def test_history_supports_object_and_dict_entries_and_redacts_messages():
    at = datetime(2026, 7, 10, 12, 0, 0)
    history = [
        HistoryEntry(at, "online", "已确认 https://cdn.example.test/live?token=old-secret", True),
        {
            "at": at + timedelta(minutes=1),
            "state": "error",
            "message": "Cookie: session=history-secret",
            "is_live": True,
        },
    ]

    text = format_recent_history(history)

    assert "检测异常（上次确认在线）" in text
    assert "old-secret" not in text
    assert "history-secret" not in text
    assert "https://cdn.example.test/…（地址已隐藏）" in text


def test_history_is_optional_for_existing_status_objects():
    view = build_detail_view(make_status())

    assert view.value_for("最近事件") == "暂无状态事件"


def test_detail_url_helpers_never_show_path_or_query():
    raw = "rtmps://user:pass@media.example.test/private/path?token=secret&opaque=still-secret"

    assert safe_url_hint(raw) == "rtmps://media.example.test/…（地址已隐藏）"
    text = redact_detail_text(f"连接失败：{raw}")
    assert "private/path" not in text
    assert "still-secret" not in text
    assert "media.example.test" in text
