import asyncio
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from zhibo import monitor as monitor_module
import pytest
from zhibo.monitor import MonitorService
from zhibo.plugins import _plugins, register_plugin
from zhibo.plugins.base import LiveInfo, LiveStreamPlugin


CSV_HEADER = "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\n"


def write_monitor_csv(rows: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(CSV_HEADER + rows)
    return f.name


def test_platform_connectivity_circuit_skips_then_recovers():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv(
        "true,主播一,,health_plugin,,twitch,https://www.twitch.tv/first,best,\n"
        "true,主播二,,health_plugin,,twitch,https://www.twitch.tv/second,best,\n"
    )

    class HealthPlugin(LiveStreamPlugin):
        name = "health_plugin"

        def __init__(self):
            self.mode = "failure"
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            if self.mode == "failure":
                return LiveInfo(is_live=False, extra={"error": "connection timed out"})
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        plugin = HealthPlugin()
        register_plugin(plugin)
        service = MonitorService(fpath)

        asyncio.run(service.poll_all())
        health = service.get_platform_health("Twitch")
        assert plugin.calls == 1
        assert health.state == "degraded"
        assert health.consecutive_failures == 1
        assert health.next_allowed_at is not None
        assert health.last_error
        assert service.followers[1].check_state == "skipped"

        asyncio.run(service.poll_all())
        assert plugin.calls == 1

        health.next_allowed_monotonic = time.monotonic() - 1
        plugin.mode = "offline"
        asyncio.run(service.poll_all())

        assert plugin.calls == 3
        assert health.state == "healthy"
        assert health.consecutive_failures == 0
        assert health.next_allowed_at is None
        assert health.last_error == ""
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_one_bilibili_room_timeout_does_not_skip_other_rooms():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv(
        "true,B站一,,bili_health_plugin,,bilibili,https://live.bilibili.com/1,best,\n"
        "true,B站二,,bili_health_plugin,,bilibili,https://live.bilibili.com/2,best,\n"
        "true,B站三,,bili_health_plugin,,bilibili,https://live.bilibili.com/3,best,\n"
    )

    class BiliHealthPlugin(LiveStreamPlugin):
        name = "bili_health_plugin"

        def __init__(self):
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return LiveInfo(is_live=False, extra={"error": "B站直播流接口超时"})
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        plugin = BiliHealthPlugin()
        register_plugin(plugin)
        service = MonitorService(fpath)

        asyncio.run(service.poll_all())

        assert plugin.calls == 3
        assert service.followers[0].check_state == "error"
        assert service.followers[1].check_state == "offline"
        assert service.followers[2].check_state == "offline"
        assert service.get_platform_health("bilibili").state == "healthy"
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_two_distinct_bilibili_room_timeouts_trigger_platform_backoff():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv(
        "true,B站一,,bili_outage_plugin,,bilibili,https://live.bilibili.com/1,best,\n"
        "true,B站二,,bili_outage_plugin,,bilibili,https://live.bilibili.com/2,best,\n"
        "true,B站三,,bili_outage_plugin,,bilibili,https://live.bilibili.com/3,best,\n"
    )

    class BiliOutagePlugin(LiveStreamPlugin):
        name = "bili_outage_plugin"

        def __init__(self):
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            return LiveInfo(is_live=False, extra={"error": "B站状态探测超时"})

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        plugin = BiliOutagePlugin()
        register_plugin(plugin)
        service = MonitorService(fpath)

        asyncio.run(service.poll_all())
        health = service.get_platform_health("bilibili")

        assert plugin.calls == 2
        assert service.followers[0].check_state == "error"
        assert service.followers[1].check_state == "error"
        assert service.followers[2].check_state == "skipped"
        assert health.state == "degraded"
        assert health.consecutive_failures == 1
        assert health.next_allowed_at is not None
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_bilibili_non_connectivity_error_breaks_timeout_sequence():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv(
        "true,B站一,,bili_sequence_plugin,,bilibili,https://live.bilibili.com/1,best,\n"
        "true,B站二,,bili_sequence_plugin,,bilibili,https://live.bilibili.com/2,best,\n"
        "true,B站三,,bili_sequence_plugin,,bilibili,https://live.bilibili.com/3,best,\n"
    )

    class BiliSequencePlugin(LiveStreamPlugin):
        name = "bili_sequence_plugin"

        def __init__(self):
            self.results = iter(
                [
                    LiveInfo(is_live=False, extra={"error": "B站状态探测超时"}),
                    LiveInfo(is_live=False, extra={"error": "B站响应格式无效"}),
                    LiveInfo(is_live=False, extra={"error": "B站直播流接口超时"}),
                ]
            )

        async def check_live(self, url, **kwargs):
            return next(self.results)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        register_plugin(BiliSequencePlugin())
        service = MonitorService(fpath)

        asyncio.run(service.poll_all())
        health = service.get_platform_health("bilibili")

        assert [service.followers[index].check_state for index in range(3)] == ["error"] * 3
        assert health.state == "healthy"
        assert health.consecutive_failures == 0
        assert health.failure_sources == {"2"}
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_fs1_expired_token_opens_long_platform_circuit_and_skips_other_rooms():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv(
        "true,FS一,,fs1_circuit_plugin,,fs1,room-1,best,1\n"
        "true,FS二,,fs1_circuit_plugin,,fs1,room-2,best,1\n"
        "true,FS三,,fs1_circuit_plugin,,fs1,room-3,best,1\n"
    )

    class Fs1CircuitPlugin(LiveStreamPlugin):
        name = "fs1_circuit_plugin"

        def __init__(self):
            self.mode = "expired"
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            if self.mode == "expired":
                return LiveInfo(is_live=False, extra={"error": "401 Unauthorized: token expired"})
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        plugin = Fs1CircuitPlugin()
        register_plugin(plugin)
        service = MonitorService(fpath)

        asyncio.run(service.poll_all())
        health = service.get_platform_health("fs1")

        assert plugin.calls == 1
        assert service.followers[0].check_state == "error"
        assert service.followers[1].check_state == "skipped"
        assert service.followers[2].check_state == "skipped"
        assert health.state == "degraded"
        assert health.retry_after_seconds >= monitor_module.FS1_PLATFORM_BACKOFF_MIN_SECONDS - 1

        service.reset_platform_health("fs1")
        plugin.mode = "offline"
        asyncio.run(service.poll_all())

        assert plugin.calls == 4
        assert all(service.followers[index].check_state == "offline" for index in range(3))
        assert health.state == "healthy"
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_fs1_confirmed_offline_rooms_do_not_open_platform_circuit():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv(
        "true,FS一,,fs1_offline_plugin,,fs1,room-1,best,1\n"
        "true,FS二,,fs1_offline_plugin,,fs1,room-2,best,1\n"
    )

    class Fs1OfflinePlugin(LiveStreamPlugin):
        name = "fs1_offline_plugin"

        def __init__(self):
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        plugin = Fs1OfflinePlugin()
        register_plugin(plugin)
        service = MonitorService(fpath)

        asyncio.run(service.poll_all())

        assert plugin.calls == 2
        assert service.get_platform_health("fs1").state == "healthy"
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_platform_backoff_is_exponential_jittered_and_bounded(monkeypatch):
    service = object.__new__(MonitorService)
    service.platform_health = {}
    service._logger = SimpleNamespace(warning=lambda *args, **kwargs: None, info=lambda *args, **kwargs: None)
    monkeypatch.setattr(monitor_module.random, "uniform", lambda low, high: high)

    first = service._record_platform_connectivity_failure("youtube", "connection timed out")
    first_delay = first.next_allowed_monotonic - time.monotonic()
    assert monitor_module.PLATFORM_BACKOFF_BASE_SECONDS <= first_delay <= (
        monitor_module.PLATFORM_BACKOFF_BASE_SECONDS * (1 + monitor_module.PLATFORM_BACKOFF_JITTER_RATIO) + 0.1
    )

    second = service._record_platform_connectivity_failure("youtube", "connection timed out")
    second_delay = second.next_allowed_monotonic - time.monotonic()
    assert second.state == "outage"
    assert second.consecutive_failures == 2
    assert second_delay > first_delay
    assert isinstance(second.next_allowed_at, datetime)

    for _ in range(20):
        service._record_platform_connectivity_failure("youtube", "connection timed out")
    assert second.retry_after_seconds <= monitor_module.PLATFORM_BACKOFF_MAX_SECONDS


def test_task_deadline_returns_without_waiting_for_delayed_cancellation():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,late_cancel_plugin,,huya,https://example.com/room,best,\n")

    class LateCancelPlugin(LiveStreamPlugin):
        name = "late_cancel_plugin"

        def __init__(self):
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # 模拟不立即结束的第三方协程；轮询仍必须按 deadline 返回。
                await asyncio.sleep(0.05)
                return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        plugin = LateCancelPlugin()
        register_plugin(plugin)
        service = MonitorService(fpath)
        service.check_task_timeout = 0.01
        service.poll_deadline = 0.2

        async def run_scenario():
            started = time.perf_counter()
            await service.poll_all()
            elapsed = time.perf_counter() - started
            assert service.followers[0].check_state == "error"
            assert service._deadline_tasks

            await service.poll_all()
            assert plugin.calls == 1
            await asyncio.sleep(0.08)
            return elapsed

        elapsed = asyncio.run(run_scenario())
        assert elapsed < 0.15
        assert not service._deadline_tasks
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_deadline_preserves_confirmed_live_state_but_records_platform_failure():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,hung_live_plugin,,twitch,https://www.twitch.tv/example,best,\n")

    class HungLivePlugin(LiveStreamPlugin):
        name = "hung_live_plugin"

        async def check_live(self, url, **kwargs):
            await asyncio.Event().wait()

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        register_plugin(HungLivePlugin())
        service = MonitorService(fpath)
        status = service.followers[0]
        status.live_info = LiveInfo(is_live=True, stream_url="https://example.com/previous")
        status.check_state = "online"
        status.last_confirmed_check = datetime.now()
        service.check_task_timeout = 0.01
        service.poll_deadline = 0.2

        asyncio.run(service.poll_all())

        assert status.live_info.is_live is True
        assert status.check_state == "error"
        assert "超时" in status.error
        assert service.get_platform_health("twitch").state == "degraded"
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_poll_deadline_caps_an_entire_round():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv(
        "true,主播一,,slow_deadline_plugin,,huya,https://example.com/first,best,\n"
        "true,主播二,,slow_deadline_plugin,,huya,https://example.com/second,best,\n"
    )

    class SlowPlugin(LiveStreamPlugin):
        name = "slow_deadline_plugin"

        async def check_live(self, url, **kwargs):
            await asyncio.sleep(10)
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        register_plugin(SlowPlugin())
        service = MonitorService(fpath)
        service.check_task_timeout = 1.0
        service.poll_deadline = 0.02

        started = time.perf_counter()
        asyncio.run(service.poll_all())
        elapsed = time.perf_counter() - started

        assert elapsed < 0.15
        statuses = list(service.followers.values())
        assert all(status.error for status in statuses)
        assert any("总时限" in status.error for status in statuses)
        assert sum(status.failure_count for status in statuses) == 1
        assert sum(status.check_state == "backoff" for status in statuses) == 1
        assert service.get_platform_health("huya").consecutive_failures == 1
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_status_history_records_confirmed_and_error_transitions():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,history_plugin,,huya,https://example.com/room,best,\n")

    class HistoryPlugin(LiveStreamPlugin):
        name = "history_plugin"

        def __init__(self):
            self.results = iter(
                [
                    LiveInfo(is_live=True, stream_url="https://example.com/live"),
                    LiveInfo(is_live=False, extra={"error": "temporary failure"}),
                    LiveInfo(is_live=False),
                ]
            )

        async def check_live(self, url, **kwargs):
            return next(self.results)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        register_plugin(HistoryPlugin())
        service = MonitorService(fpath)
        asyncio.run(service.check_one(0))
        asyncio.run(service.check_one(0))
        asyncio.run(service.check_one(0))

        history = service.followers[0].history
        assert [entry.state for entry in history] == ["online", "error", "offline"]
        assert "temporary failure" in history[1].message
        assert all(entry.at for entry in history)
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_disabled_followers_remain_available_to_the_editor_but_not_polling():
    fpath = write_monitor_csv(
        "false,已禁用,,streamget,,huya,https://example.com/disabled,best,\n"
        "true,启用,,streamget,,huya,https://example.com/enabled,best,\n"
    )
    try:
        service = MonitorService(fpath)

        assert [idx for idx, _ in service.get_by_tag(None)] == [1]
        visible = service.get_by_tag(None, include_disabled=True)
        assert [idx for idx, _ in visible] == [0, 1]
        assert visible[0][1].check_state == "disabled"
        assert visible[0][1].history[-1].state == "disabled"
    finally:
        Path(fpath).unlink(missing_ok=True)


def test_slow_status_callback_does_not_trip_platform_health():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,slow_callback_plugin,,twitch,https://www.twitch.tv/example,best,\n")

    class LivePlugin(LiveStreamPlugin):
        name = "slow_callback_plugin"

        async def check_live(self, url, **kwargs):
            return LiveInfo(is_live=True, stream_url="https://example.com/live.m3u8")

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        register_plugin(LivePlugin())
        service = MonitorService(fpath)
        service.check_task_timeout = 0.1
        # 比状态回调的短等待窗口更短，模拟整轮 deadline 在 UI 回调期间到期。
        service.poll_deadline = 0.01
        callbacks = []

        async def slow_callback(idx, status):
            await asyncio.sleep(0.05)
            callbacks.append(status.live_info.is_live)

        service.on_status_change(slow_callback)

        async def run_scenario():
            started = time.perf_counter()
            await service.poll_all()
            elapsed = time.perf_counter() - started
            assert service.followers[0].check_state == "online"
            assert service.get_platform_health("twitch").state == "healthy"
            await asyncio.sleep(0.08)
            return elapsed

        elapsed = asyncio.run(run_scenario())
        assert elapsed < 0.1
        assert callbacks == [True]
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_get_stream_info_uses_deadline_and_platform_circuit():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,slow_stream_plugin,,twitch,https://www.twitch.tv/example,best,\n")

    class SlowStreamPlugin(LiveStreamPlugin):
        name = "slow_stream_plugin"

        def __init__(self):
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            await asyncio.Event().wait()

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        plugin = SlowStreamPlugin()
        register_plugin(plugin)
        service = MonitorService(fpath)
        service.check_task_timeout = 0.01

        async def run_scenario():
            with pytest.raises(RuntimeError, match="获取直播流信息超时"):
                await service.get_stream_info(0)
            assert service.get_platform_health("twitch").state == "degraded"
            with pytest.raises(RuntimeError, match="平台暂不可用"):
                await service.get_stream_info(0)
            await service.shutdown(timeout=0.1)
            await asyncio.sleep(0)

        asyncio.run(run_scenario())
        assert plugin.calls == 1
        assert not service._stream_deadline_tasks
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_shutdown_waits_for_tracked_deadline_tasks():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,shutdown_plugin,,huya,https://example.com/room,best,\n")

    class LateCancelPlugin(LiveStreamPlugin):
        name = "shutdown_plugin"

        async def check_live(self, url, **kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)
                return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        register_plugin(LateCancelPlugin())
        service = MonitorService(fpath)
        service.check_task_timeout = 0.01
        service.poll_deadline = 0.2

        async def run_scenario():
            await service.poll_all()
            assert service._deadline_tasks
            await service.shutdown(timeout=0.1)
            await asyncio.sleep(0)

        asyncio.run(run_scenario())
        assert service._running is False
        assert not service._deadline_tasks
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_busy_pool_result_is_deferred_without_failure_backoff():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv(
        "true,主播一,,busy_plugin,,twitch,https://www.twitch.tv/first,best,\n"
    )

    class BusyPlugin(LiveStreamPlugin):
        name = "busy_plugin"

        async def check_live(self, url, **kwargs):
            return LiveInfo(is_live=False, extra={"error": "检测繁忙，请稍后重试", "busy": True})

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        register_plugin(BusyPlugin())
        service = MonitorService(fpath)

        asyncio.run(service.poll_all())

        status = service.followers[0]
        assert status.check_state == "backoff"
        assert "繁忙" in status.error
        # 资源压力不算主播失败：不累计失败、不进退避轮数、不触发平台熔断。
        assert status.failure_count == 0
        assert status.skip_polls == 0
        health = service.get_platform_health("twitch")
        assert health.state == "healthy"
        assert health.consecutive_failures == 0
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)
