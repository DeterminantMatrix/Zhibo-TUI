import asyncio
import time

from zhibo.plugins import streamget_plugin, streamlink_plugin
from zhibo.plugins.base import LiveInfo
from zhibo.plugins.base import stream_candidate_urls
from zhibo.plugins.streamget_plugin import resolve_platform
from zhibo.plugins.streamget_plugin import StreamgetPlugin
from zhibo.plugins.streamlink_plugin import StreamlinkPlugin


def test_twitch_platform_resolves_case_insensitively():
    assert resolve_platform("twitch") == "twitch"
    assert resolve_platform("Twitch") == "twitch"
    assert resolve_platform(" TWITCH ") == "twitch"


def test_stream_candidates_keep_primary_first_and_remove_duplicates():
    info = LiveInfo(
        is_live=True,
        stream_url="https://primary.example/live.m3u8",
        extra={"stream_candidates": [
            "https://primary.example/live.m3u8",
            "https://backup.example/live.m3u8",
        ]},
    )

    assert stream_candidate_urls(info) == [
        "https://primary.example/live.m3u8",
        "https://backup.example/live.m3u8",
    ]


def test_streamget_known_schema_error_is_compact():
    error = TypeError("StreamData.__init__() got an unexpected keyword argument 'play_url_list'")

    assert streamget_plugin._streamget_error(error) == "streamget 与当前平台响应结构不兼容（play_url_list）"


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


def test_streamlink_bilibili_offline_probe_skips_stream_resolution(monkeypatch):
    calls = []

    async def fake_status(url):
        calls.append(("status", url))
        return False

    async def unexpected_quality_lookup(url):
        raise AssertionError("offline room must not resolve a CDN stream")

    monkeypatch.setattr(streamlink_plugin, "fetch_bilibili_live_status", fake_status)
    monkeypatch.setattr(streamlink_plugin, "fetch_best_bilibili_stream", unexpected_quality_lookup)

    info = asyncio.run(
        StreamlinkPlugin().check_live(
            "https://live.bilibili.com/52032",
            platform="bilibili",
            quality="best",
        )
    )

    assert info.is_live is False
    assert info.extra["bilibili_status"] == "offline"
    assert "error" not in info.extra
    assert calls == [("status", "https://live.bilibili.com/52032")]


def test_streamlink_bilibili_best_uses_api_result_without_cdn_validation(monkeypatch):
    async def fake_status(url):
        return True

    async def fake_quality(url, **kwargs):
        return {
            "url": "https://example.com/live.m3u8",
            "qn": 10000,
            "codec": "av1",
            "format": "fmp4",
            "protocol": "http_hls",
            "label": "原画(10000)/AV1/fmp4",
            "used_cookie": True,
            "quality_warning": "",
            "candidates": [
                "https://example.com/live.m3u8",
                "https://backup.example.com/live.m3u8",
            ],
        }

    async def fake_metadata(url):
        return {"title": "测试直播标题"}

    monkeypatch.setattr(streamlink_plugin, "fetch_bilibili_live_status", fake_status)
    monkeypatch.setattr(streamlink_plugin, "fetch_best_bilibili_stream", fake_quality)
    monkeypatch.setattr(streamlink_plugin, "fetch_bilibili_room_metadata", fake_metadata)

    info = asyncio.run(
        StreamlinkPlugin().check_live(
            "https://live.bilibili.com/52032",
            platform="bilibili",
            quality="best",
        )
    )

    assert info.is_live is True
    assert info.stream_url == "https://example.com/live.m3u8"
    assert info.title == "测试直播标题"
    assert info.quality_name == "原画(10000)/AV1/fmp4"
    assert info.extra["bilibili_status"] == "live"
    assert info.extra["stream_candidates"][1] == "https://backup.example.com/live.m3u8"


def test_streamlink_bilibili_blue_quality_is_resolved_by_requested_qn(monkeypatch):
    captured = {}

    async def fake_status(url):
        return True

    async def fake_quality(url, **kwargs):
        captured.update(kwargs)
        return {
            "url": "https://example.com/blue.m3u8",
            "qn": 400,
            "codec": "avc",
            "format": "fmp4",
            "protocol": "http_hls",
            "label": "蓝光(400)/AVC/fmp4",
            "used_cookie": False,
            "quality_warning": "",
            "candidates": ["https://example.com/blue.m3u8"],
        }

    async def fake_metadata(url):
        return {}

    monkeypatch.setattr(streamlink_plugin, "fetch_bilibili_live_status", fake_status)
    monkeypatch.setattr(streamlink_plugin, "fetch_best_bilibili_stream", fake_quality)
    monkeypatch.setattr(streamlink_plugin, "fetch_bilibili_room_metadata", fake_metadata)

    info = asyncio.run(StreamlinkPlugin().check_live(
        "https://live.bilibili.com/52032", platform="bilibili", quality="蓝光"
    ))

    assert info.is_live is True
    assert info.extra["bilibili_qn"] == 400
    assert captured["quality"] == "蓝光"


def test_streamlink_bilibili_timeout_class_is_reported_as_timeout(monkeypatch):
    async def timeout_status(url):
        raise TimeoutError()

    monkeypatch.setattr(streamlink_plugin, "fetch_bilibili_live_status", timeout_status)

    info = asyncio.run(
        StreamlinkPlugin().check_live(
            "https://live.bilibili.com/52032",
            platform="bilibili",
            quality="best",
        )
    )

    assert info.is_live is False
    assert info.extra["error"] == "B站状态探测超时"


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


def test_streamget_bounds_both_stream_url_fetch_attempts(monkeypatch):
    calls = []

    class SlowTwitchStream:
        def __init__(self, proxy_addr=None):
            self.proxy_addr = proxy_addr

        async def fetch_web_stream_data(self, url, process_data=True):
            return {"anchor_name": "tester", "title": "live"}

        async def fetch_stream_url(self, data, quality="best"):
            calls.append(quality)
            await asyncio.sleep(1)

    monkeypatch.setitem(streamget_plugin.STREAMGET_PLATFORMS, "twitch", SlowTwitchStream)
    monkeypatch.setattr(streamget_plugin, "STREAM_URL_TIMEOUT", 0.01)

    started = time.monotonic()
    info = asyncio.run(
        StreamgetPlugin().check_live(
            "https://www.twitch.tv/example",
            platform="twitch",
            quality="720p60",
        )
    )

    assert time.monotonic() - started < 0.5
    assert calls == ["720p60", "best"]
    assert info.is_live is False
    assert info.extra["error"] == "获取流地址超时"


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
