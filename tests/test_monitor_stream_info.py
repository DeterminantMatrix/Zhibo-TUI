import asyncio
import tempfile
from pathlib import Path

from zhibo.monitor import MonitorService
from zhibo.plugins import _plugins, register_plugin
from zhibo.plugins.base import LiveInfo, LiveStreamPlugin


CSV_HEADER = "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\n"


def write_monitor_csv(row: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(CSV_HEADER + row)
    return f.name


class HeaderPlugin(LiveStreamPlugin):
    name = "header_plugin"

    async def check_live(self, url, **kwargs):
        return LiveInfo(
            is_live=True,
            stream_url="https://example.com/live.flv",
            extra={"headers": {"Referer": "https://example.com/"}},
        )

    async def get_stream_url(self, url, quality, **kwargs):
        raise AssertionError("get_stream_info should use check_live to preserve headers")


class ErrorPlugin(LiveStreamPlugin):
    name = "error_plugin"

    async def check_live(self, url, **kwargs):
        return LiveInfo(is_live=False, extra={"error": "primary failed"})

    async def get_stream_url(self, url, quality, **kwargs):
        raise AssertionError


class FallbackPlugin(HeaderPlugin):
    name = "fallback_plugin"


class StreamgetFallbackPlugin(HeaderPlugin):
    name = "streamget"


class QualityPlugin(LiveStreamPlugin):
    name = "quality_plugin"

    def __init__(self):
        self.qualities = []

    async def check_live(self, url, **kwargs):
        self.qualities.append(kwargs["quality"])
        return LiveInfo(is_live=False)

    async def get_stream_url(self, url, quality, **kwargs):
        raise AssertionError


class SlowPlugin(LiveStreamPlugin):
    name = "slow_plugin"

    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def check_live(self, url, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return LiveInfo(is_live=False)

    async def get_stream_url(self, url, quality, **kwargs):
        raise AssertionError


def test_get_stream_info_preserves_plugin_extra_headers():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,header_plugin,,huya,https://example.com/room,best,\n")

    try:
        register_plugin(HeaderPlugin())
        monitor = MonitorService(fpath)
        assert monitor.followers[0].live_info.stream_url == ""

        info = asyncio.run(monitor.get_stream_info(0))
        stream_url = asyncio.run(monitor.get_stream(0))

        assert info.stream_url == "https://example.com/live.flv"
        assert info.extra["headers"]["Referer"] == "https://example.com/"
        assert stream_url == "https://example.com/live.flv"
        assert monitor.followers[0].live_info.stream_url == ""
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_monitor_uses_configured_fallback_plugin():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,error_plugin,fallback_plugin,huya,https://example.com/room,best,\n")

    try:
        register_plugin(ErrorPlugin())
        register_plugin(FallbackPlugin())
        monitor = MonitorService(fpath)
        status = asyncio.run(monitor.check_one(0))

        assert status.live_info.is_live is True
        assert status.live_info.stream_url == "https://example.com/live.flv"
        assert status.live_info.extra["plugin_used"] == "fallback_plugin"
        assert status.failure_count == 0
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_monitor_adds_platform_fallback_for_twitch():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,error_plugin,,twitch,https://www.twitch.tv/example,best,\n")

    try:
        register_plugin(ErrorPlugin())
        register_plugin(StreamgetFallbackPlugin())
        monitor = MonitorService(fpath)
        status = asyncio.run(monitor.check_one(0))

        assert status.live_info.is_live is True
        assert status.live_info.stream_url == "https://example.com/live.flv"
        assert status.live_info.extra["plugin_used"] == "streamget"
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_monitor_reports_status_callback_errors():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,header_plugin,,huya,https://example.com/room,best,\n")
    errors = []

    try:
        register_plugin(HeaderPlugin())
        monitor = MonitorService(fpath)
        monitor.on_status_change(lambda idx, status: (_ for _ in ()).throw(RuntimeError("boom")))
        monitor.on_error(errors.append)

        asyncio.run(monitor.check_one(0))

        assert errors == ["回调异常: boom"]
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_monitor_passes_configured_quality_to_plugin():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,quality_plugin,,huya,https://example.com/room,HD,\n")

    try:
        plugin = QualityPlugin()
        register_plugin(plugin)
        monitor = MonitorService(fpath)
        asyncio.run(monitor.check_one(0))
        assert plugin.qualities == ["HD"]
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_monitor_serializes_overlapping_poll_requests():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,slow_plugin,,huya,https://example.com/room,best,\n")

    try:
        plugin = SlowPlugin()
        register_plugin(plugin)
        monitor = MonitorService(fpath)

        async def run_overlapping_polls():
            await asyncio.gather(monitor.poll_all(), monitor.poll_all())

        asyncio.run(run_overlapping_polls())
        assert plugin.max_active == 1
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_monitor_keeps_last_confirmed_state_when_a_check_errors():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv("true,主播,,sequence_plugin,,huya,https://example.com/room,best,\n")

    class SequencePlugin(LiveStreamPlugin):
        name = "sequence_plugin"

        def __init__(self):
            self.results = iter([
                LiveInfo(is_live=True, stream_url="https://example.com/live.flv"),
                LiveInfo(is_live=False, extra={"error": "temporary network failure"}),
                LiveInfo(is_live=True, stream_url="https://example.com/live.flv"),
                LiveInfo(is_live=False),
            ])

        async def check_live(self, url, **kwargs):
            return next(self.results)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        register_plugin(SequencePlugin())
        monitor = MonitorService(fpath)
        transitions = []
        monitor.on_status_change(lambda idx, status: transitions.append(status.live_info.is_live))

        asyncio.run(monitor.check_one(0))
        transitions.clear()
        confirmed_at = monitor.followers[0].last_confirmed_check

        status = asyncio.run(monitor.check_one(0))
        assert status.live_info.is_live is True
        assert status.check_state == "error"
        assert status.last_confirmed_check == confirmed_at
        assert transitions == []

        status = asyncio.run(monitor.check_one(0))
        assert status.live_info.is_live is True
        assert status.check_state == "online"
        assert transitions == []

        status = asyncio.run(monitor.check_one(0))
        assert status.live_info.is_live is False
        assert status.check_state == "offline"
        assert transitions == [False]
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)


def test_platform_skip_preserves_each_follower_confirmed_state():
    previous_plugins = dict(_plugins)
    fpath = write_monitor_csv(
        "true,主播一,,timeout_twitch,,twitch,https://www.twitch.tv/first,best,\n"
        "true,主播二,,timeout_twitch,,twitch,https://www.twitch.tv/second,best,\n"
    )

    class TimeoutTwitchPlugin(LiveStreamPlugin):
        name = "timeout_twitch"

        def __init__(self):
            self.fail = False

        async def check_live(self, url, **kwargs):
            if self.fail:
                return LiveInfo(is_live=False, extra={"error": "检测超时"})
            return LiveInfo(is_live=True, stream_url="https://example.com/live.flv")

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    try:
        plugin = TimeoutTwitchPlugin()
        register_plugin(plugin)
        monitor = MonitorService(fpath)
        transitions = []
        monitor.on_status_change(lambda idx, status: transitions.append((idx, status.live_info.is_live)))

        asyncio.run(monitor.check_one(0))
        asyncio.run(monitor.check_one(1))
        transitions.clear()

        plugin.fail = True
        asyncio.run(monitor.poll_all())

        statuses = [monitor.followers[0], monitor.followers[1]]
        assert all(status.live_info.is_live for status in statuses)
        assert {status.check_state for status in statuses} == {"error", "skipped"}
        assert transitions == []
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)
        Path(fpath).unlink(missing_ok=True)
