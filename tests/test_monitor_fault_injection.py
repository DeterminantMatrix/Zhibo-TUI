"""Offline fault-injection coverage for monitor scheduling and state safety."""

import asyncio
import time
from datetime import datetime, timedelta

import pytest

from zhibo.monitor import MonitorService
from zhibo.plugins import _plugins, register_plugin
from zhibo.plugins.base import LiveInfo, LiveStreamPlugin


CSV_HEADER = "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\n"


@pytest.fixture
def isolated_plugin_registry():
    previous_plugins = dict(_plugins)
    try:
        yield
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)


def make_service(tmp_path, rows: str) -> MonitorService:
    config_path = tmp_path / "followers.csv"
    config_path.write_text(CSV_HEADER + rows, encoding="utf-8")
    return MonitorService(config_path)


class NoopPlugin(LiveStreamPlugin):
    name = "fault_noop_plugin"

    async def check_live(self, url, **kwargs):
        return LiveInfo(is_live=False)

    async def get_stream_url(self, url, quality, **kwargs):
        raise AssertionError


def test_one_bilibili_timeout_is_room_scoped_with_real_cancel_path(
    tmp_path, isolated_plugin_registry
):
    rows = (
        "true,B站一,,fault_timeout_plugin,,bilibili,https://live.bilibili.com/1,best,\n"
        "true,B站二,,fault_timeout_plugin,,bilibili,https://live.bilibili.com/2,best,\n"
        "true,B站三,,fault_timeout_plugin,,bilibili,https://live.bilibili.com/3,best,\n"
    )
    service = make_service(tmp_path, rows)
    calls: list[str] = []

    class TimeoutPlugin(LiveStreamPlugin):
        name = "fault_timeout_plugin"

        async def check_live(self, url, **kwargs):
            calls.append(url)
            if url.endswith("/1"):
                await asyncio.Event().wait()
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(TimeoutPlugin())
    service.check_task_timeout = 0.01
    service.poll_deadline = 0.2

    async def scenario():
        await service.poll_all()
        assert not service._deadline_tasks
        await service.shutdown(timeout=0.1)

    asyncio.run(scenario())

    assert set(calls) == {
        "https://live.bilibili.com/1",
        "https://live.bilibili.com/2",
        "https://live.bilibili.com/3",
    }
    assert service.followers[0].check_state == "error"
    assert service.followers[1].check_state == "offline"
    assert service.followers[2].check_state == "offline"
    assert service.get_platform_health("bilibili").state == "healthy"


def test_platform_cooldown_marks_skipped_without_overwriting_confirmed_state(
    tmp_path, isolated_plugin_registry
):
    service = make_service(
        tmp_path,
        "true,主播,,fault_cooldown_plugin,,twitch,https://www.twitch.tv/room,best,\n",
    )
    calls: list[str] = []

    class ShouldNotRunPlugin(LiveStreamPlugin):
        name = "fault_cooldown_plugin"

        async def check_live(self, url, **kwargs):
            calls.append(url)
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(ShouldNotRunPlugin())
    status = service.followers[0]
    status.live_info = LiveInfo(is_live=True, stream_url="https://example.com/current")
    status.check_state = "online"
    status.last_confirmed_check = datetime.now()
    health = service.get_platform_health("twitch")
    health.state = "degraded"
    health.last_error = "connection timed out"
    health.next_allowed_monotonic = time.monotonic() + 60
    health.next_allowed_at = datetime.now() + timedelta(seconds=60)

    asyncio.run(service.poll_all())

    assert calls == []
    assert status.check_state == "skipped"
    assert status.live_info.is_live is True
    assert health.state == "degraded"


def test_confirmed_offline_state_survives_inconclusive_error_without_notification(
    tmp_path, isolated_plugin_registry
):
    service = make_service(
        tmp_path,
        "true,主播,,fault_offline_then_error,,huya,https://example.com/room,best,\n",
    )

    class OfflineThenErrorPlugin(LiveStreamPlugin):
        name = "fault_offline_then_error"

        def __init__(self):
            self.results = iter(
                [
                    LiveInfo(is_live=False),
                    LiveInfo(is_live=False, extra={"error": "temporary network failure"}),
                ]
            )

        async def check_live(self, url, **kwargs):
            return next(self.results)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(OfflineThenErrorPlugin())
    transitions: list[bool] = []
    service.on_status_change(lambda idx, status: transitions.append(status.live_info.is_live))

    async def scenario():
        await service.check_one(0)
        status = service.followers[0]
        confirmed_at = status.last_confirmed_check
        transitions.clear()
        await service.check_one(0)
        return status, confirmed_at

    status, confirmed_at = asyncio.run(scenario())

    assert status.live_info.is_live is False
    assert status.check_state == "error"
    assert status.last_confirmed_check == confirmed_at
    assert transitions == []


def test_fallback_order_is_deduplicated_and_preserved(tmp_path, isolated_plugin_registry):
    service = make_service(
        tmp_path,
        "true,主播,,fault_primary,fault_a;fault_primary;fault_b,huya,https://example.com/room,best,\n",
    )
    calls: list[str] = []

    class ResultPlugin(LiveStreamPlugin):
        def __init__(self, name: str, result: LiveInfo):
            self.name = name
            self.result = result

        async def check_live(self, url, **kwargs):
            calls.append(self.name)
            return self.result

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(ResultPlugin("fault_primary", LiveInfo(is_live=False, extra={"error": "primary failed"})))
    register_plugin(ResultPlugin("fault_a", LiveInfo(is_live=False, extra={"error": "fallback A failed"})))
    register_plugin(ResultPlugin("fault_b", LiveInfo(is_live=True, stream_url="https://example.com/fresh")))

    # Keep this explicit in case the CSV parser normalizes fallback separators
    # differently in a future version; the monitor contract is a list here.
    service.followers[0].follower.fallback_plugins = [
        "fault_a",
        "fault_primary",
        "fault_b",
    ]
    status = asyncio.run(service.check_one(0))

    assert calls == ["fault_primary", "fault_a", "fault_b"]
    assert status.check_state == "online"
    assert status.live_info.extra["plugin_used"] == "fault_b"


def test_shared_exception_stops_unnecessary_same_room_fallback(
    tmp_path, isolated_plugin_registry
):
    service = make_service(
        tmp_path,
        "true,主播,,fault_shared_error,fault_after_shared,huya,https://example.com/room,best,\n",
    )
    calls: list[str] = []

    class SharedErrorPlugin(LiveStreamPlugin):
        name = "fault_shared_error"

        async def check_live(self, url, **kwargs):
            calls.append(self.name)
            raise RuntimeError("connection timed out")

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    class FallbackPlugin(LiveStreamPlugin):
        name = "fault_after_shared"

        async def check_live(self, url, **kwargs):
            calls.append(self.name)
            return LiveInfo(is_live=True, stream_url="https://example.com/should-not-run")

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(SharedErrorPlugin())
    register_plugin(FallbackPlugin())

    status = asyncio.run(service.check_one(0))

    assert calls == ["fault_shared_error"]
    assert status.check_state == "error"
    assert "connection timed out" in status.error


def test_status_history_deduplicates_adjacent_entries_and_keeps_twenty(
    tmp_path, isolated_plugin_registry
):
    service = make_service(
        tmp_path,
        "true,主播,,fault_noop_plugin,,huya,https://example.com/room,best,\n",
    )
    register_plugin(NoopPlugin())
    status = service.followers[0]

    for _ in range(5):
        service._record_status_history(status, state="same", message="same", is_live=False)
    for index in range(25):
        service._record_status_history(
            status,
            state=f"state-{index}",
            message=str(index),
            is_live=False,
        )

    assert len(status.history) == 20
    assert status.history[0].state == "state-5"
    assert status.history[-1].state == "state-24"


def test_get_stream_info_does_not_reuse_previous_stream_url(
    tmp_path, isolated_plugin_registry
):
    service = make_service(
        tmp_path,
        "true,主播,,fault_fresh_stream,,huya,https://example.com/room,best,\n",
    )

    class FreshStreamPlugin(LiveStreamPlugin):
        name = "fault_fresh_stream"

        async def check_live(self, url, **kwargs):
            return LiveInfo(is_live=True, stream_url="https://example.com/fresh.m3u8")

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(FreshStreamPlugin())
    previous = "https://example.com/old.m3u8?old=1"
    service.followers[0].live_info = LiveInfo(is_live=True, stream_url=previous)
    service.followers[0].check_state = "online"

    info = asyncio.run(service.get_stream_info(0))

    assert info.stream_url == "https://example.com/fresh.m3u8"
    assert info.stream_url != previous
    assert service.followers[0].live_info.stream_url == previous


def test_offline_result_with_cdn_candidates_stays_offline_and_does_not_fallback(
    tmp_path, isolated_plugin_registry
):
    service = make_service(
        tmp_path,
        "true,主播,,fault_offline_candidates,,bilibili,https://live.bilibili.com/1,best,\n",
    )
    calls: list[str] = []

    class OfflineCandidatesPlugin(LiveStreamPlugin):
        name = "fault_offline_candidates"

        async def check_live(self, url, **kwargs):
            calls.append(self.name)
            return LiveInfo(
                is_live=False,
                extra={"stream_candidates": ["https://cdn.example.invalid/live.m3u8"]},
            )

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(OfflineCandidatesPlugin())
    status = asyncio.run(service.check_one(0))

    assert calls == ["fault_offline_candidates"]
    assert status.check_state == "offline"
    assert status.live_info.is_live is False
    assert status.failure_count == 0

