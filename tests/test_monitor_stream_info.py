import asyncio
import tempfile
from pathlib import Path

from monitor import MonitorService
from plugins import _plugins, register_plugin
from plugins.base import LiveInfo, LiveStreamPlugin


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
    yaml_content = """
followers:
  - name: "主播"
    plugin: header_plugin
    platform: huya
    url: "https://example.com/room"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name

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
    yaml_content = """
followers:
  - name: "主播"
    plugin: error_plugin
    fallback_plugins: [fallback_plugin]
    platform: huya
    url: "https://example.com/room"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name

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
    yaml_content = """
followers:
  - name: "主播"
    plugin: error_plugin
    platform: twitch
    url: "https://www.twitch.tv/example"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name

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
    yaml_content = """
followers:
  - name: "主播"
    plugin: header_plugin
    platform: huya
    url: "https://example.com/room"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name
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
    yaml_content = """
followers:
  - name: "主播"
    plugin: quality_plugin
    platform: huya
    url: "https://example.com/room"
    quality: HD
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name

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
    yaml_content = """
followers:
  - name: "主播"
    plugin: slow_plugin
    platform: huya
    url: "https://example.com/room"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
    fpath = f.name

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
