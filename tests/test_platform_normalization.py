import asyncio

from plugins import streamget_plugin
from plugins.base import LiveInfo
from plugins.streamget_plugin import resolve_platform
from plugins.streamget_plugin import StreamgetPlugin
from plugins.streamlink_plugin import StreamlinkPlugin


def test_twitch_platform_resolves_case_insensitively():
    assert resolve_platform("twitch") == "twitch"
    assert resolve_platform("Twitch") == "twitch"
    assert resolve_platform(" TWITCH ") == "twitch"


def test_rednote_aliases_resolve_when_supported():
    assert resolve_platform("rednote") == "rednote"
    assert resolve_platform("xiaohongshu") == "rednote"
    assert resolve_platform("xhs") == "rednote"
    assert resolve_platform("小红书") == "rednote"


def test_streamlink_streamget_meta_accepts_twitch_case(monkeypatch):
    class FakeTwitchStream:
        def __init__(self, proxy_addr=None):
            self.proxy_addr = proxy_addr

        async def fetch_web_stream_data(self, url, process_data=True):
            return {"anchor_name": "tester", "title": "live"}

    monkeypatch.setitem(streamget_plugin.STREAMGET_PLATFORMS, "twitch", FakeTwitchStream)

    info = asyncio.run(
        StreamlinkPlugin()._streamget_meta(
            "https://www.twitch.tv/example",
            {"platform": "Twitch"},
        )
    )

    assert info is not None
    assert info.anchor_name == "tester"
    assert info.title == "live"


def test_streamlink_streamget_meta_uses_stream_object_metadata(monkeypatch):
    class FakeStreamObject:
        anchor_name = "huya-anchor"
        title = "huya-title"

    class FakeHuyaStream:
        def __init__(self, proxy_addr=None):
            self.proxy_addr = proxy_addr

        async def fetch_web_stream_data(self, url, process_data=True):
            return {"data": {}}

        async def fetch_stream_url(self, data, quality="best"):
            return FakeStreamObject()

    monkeypatch.setitem(streamget_plugin.STREAMGET_PLATFORMS, "huya", FakeHuyaStream)

    info = asyncio.run(
        StreamlinkPlugin()._streamget_meta(
            "https://www.huya.com/example",
            {"platform": "huya", "quality": "best"},
        )
    )

    assert info is not None
    assert info.anchor_name == "huya-anchor"
    assert info.title == "huya-title"


def test_streamget_get_stream_url_passes_requested_quality(monkeypatch):
    captured = {}
    plugin = StreamgetPlugin()

    async def fake_check_live(url, **kwargs):
        captured.update(kwargs)
        return LiveInfo(is_live=True, stream_url="http://example.com/live.m3u8")

    monkeypatch.setattr(plugin, "check_live", fake_check_live)

    stream_url = asyncio.run(
        plugin.get_stream_url(
            "https://www.twitch.tv/example",
            "720p60",
            platform="Twitch",
        )
    )

    assert stream_url == "http://example.com/live.m3u8"
    assert captured["quality"] == "720p60"
    assert captured["platform"] == "Twitch"


def test_streamget_uses_record_url_when_stream_urls_are_empty(monkeypatch):
    class FakeStreamObject:
        is_live = True
        anchor_name = "bili-anchor"
        title = "bili-title"
        flv_url = ""
        m3u8_url = ""
        record_url = "https://example.com/live.flv"
        quality = "BEST"

    class FakeBilibiliStream:
        def __init__(self, proxy_addr=None):
            self.proxy_addr = proxy_addr

        async def fetch_web_stream_data(self, url, process_data=True):
            return {"anchor_name": "bili-anchor", "title": "bili-title"}

        async def fetch_stream_url(self, data, quality="best"):
            return FakeStreamObject()

    monkeypatch.setitem(streamget_plugin.STREAMGET_PLATFORMS, "bilibili", FakeBilibiliStream)
    monkeypatch.setattr(streamget_plugin, "fetch_best_bilibili_stream", lambda url: None)

    info = asyncio.run(
        StreamgetPlugin().check_live(
            "https://live.bilibili.com/7777",
            platform="bilibili",
            quality="HD",
        )
    )

    assert info.is_live is True
    assert info.stream_url == "https://example.com/live.flv"


def test_streamget_accepts_app_stream_data_fetcher(monkeypatch):
    class FakeStreamObject:
        is_live = True
        anchor_name = "rednote-anchor"
        title = "rednote-title"
        flv_url = "http://example.com/live.flv"
        m3u8_url = "http://example.com/live.m3u8"
        quality = "OD"

    class FakeRedNoteStream:
        def __init__(self, proxy_addr=None):
            self.proxy_addr = proxy_addr

        async def fetch_app_stream_data(self, url, process_data=True):
            return {"anchor_name": "rednote-anchor", "title": "rednote-title"}

        async def fetch_stream_url(self, data, quality="best"):
            return FakeStreamObject()

    async def fake_reachable(url, headers=None):
        return True

    monkeypatch.setitem(streamget_plugin.STREAMGET_PLATFORMS, "rednote", FakeRedNoteStream)
    monkeypatch.setattr(streamget_plugin, "_stream_url_is_reachable", fake_reachable)

    info = asyncio.run(
        StreamgetPlugin().check_live(
            "https://www.xiaohongshu.com/user/profile/example",
            platform="xhs",
            quality="OD",
        )
    )

    assert info.is_live is True
    assert info.anchor_name == "rednote-anchor"
    assert info.title == "rednote-title"
    assert info.stream_url == "http://example.com/live.m3u8"


def test_rednote_unreachable_stream_is_reported(monkeypatch):
    class FakeStreamObject:
        is_live = True
        anchor_name = "rednote-anchor"
        title = "rednote-title"
        flv_url = "http://example.com/live.flv"
        m3u8_url = "http://example.com/live.m3u8"
        quality = "OD"

    class FakeRedNoteStream:
        def __init__(self, proxy_addr=None):
            self.proxy_addr = proxy_addr

        async def fetch_app_stream_data(self, url, process_data=True):
            return {"anchor_name": "rednote-anchor", "title": "rednote-title"}

        async def fetch_stream_url(self, data, quality="best"):
            return FakeStreamObject()

    async def fake_unreachable(url, headers=None):
        return False

    monkeypatch.setitem(streamget_plugin.STREAMGET_PLATFORMS, "rednote", FakeRedNoteStream)
    monkeypatch.setattr(streamget_plugin, "_stream_url_is_reachable", fake_unreachable)

    info = asyncio.run(
        StreamgetPlugin().check_live(
            "https://www.xiaohongshu.com/livestream/example",
            platform="rednote",
            quality="OD",
        )
    )

    assert info.is_live is False
    assert info.anchor_name == "rednote-anchor"
    assert info.title == "rednote-title"
    assert "不可达" in info.extra["error"]
    assert info.extra["candidate_m3u8_url"] == "http://example.com/live.m3u8"
    assert info.extra["candidate_flv_url"] == "http://example.com/live.flv"
