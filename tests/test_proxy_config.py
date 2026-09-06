import asyncio

from zhibo.plugins import streamget_plugin
from zhibo.plugins.streamget_plugin import StreamgetPlugin
from zhibo.proxy_config import DEFAULT_PROXY_URL, proxy_for_platform, set_platform_proxies


def test_foreign_platforms_use_default_proxy(monkeypatch):
    monkeypatch.delenv("ZHIBO_PLATFORM_PROXY", raising=False)

    for platform in ["twitch", "youtube", "tiktok", "chzzk", "twitcasting", "kick"]:
        assert proxy_for_platform(platform) == DEFAULT_PROXY_URL


def test_domestic_and_fs_platforms_are_direct(monkeypatch):
    monkeypatch.setenv("ZHIBO_PLATFORM_PROXY", "http://127.0.0.1:7890")

    for platform in ["fs1", "bilibili", "douyu", "huya", "douyin", "rednote"]:
        assert proxy_for_platform(platform) is None


def test_proxy_can_be_disabled_with_env(monkeypatch):
    monkeypatch.setenv("ZHIBO_PLATFORM_PROXY", "direct")

    assert proxy_for_platform("twitch") is None
    assert proxy_for_platform("youtube") is None


def test_streamget_passes_proxy_only_for_foreign_platforms(monkeypatch):
    captured = []

    class FakeStreamObject:
        is_live = False
        anchor_name = ""
        title = ""
        flv_url = ""
        m3u8_url = ""
        quality = "best"

    class FakeStream:
        def __init__(self, proxy_addr=None):
            captured.append(proxy_addr)

        async def fetch_web_stream_data(self, url, process_data=True):
            return {}

        async def fetch_stream_url(self, data, quality="best"):
            return FakeStreamObject()

    monkeypatch.setenv("ZHIBO_PLATFORM_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setitem(streamget_plugin.STREAMGET_PLATFORMS, "twitch", FakeStream)
    monkeypatch.setitem(streamget_plugin.STREAMGET_PLATFORMS, "huya", FakeStream)

    asyncio.run(StreamgetPlugin().check_live("https://www.twitch.tv/example", platform="twitch"))
    asyncio.run(StreamgetPlugin().check_live("https://www.huya.com/example", platform="huya"))

    assert captured == ["http://127.0.0.1:7890", None]


def test_platform_proxy_override_accepts_port_and_direct(monkeypatch):
    monkeypatch.delenv("ZHIBO_TWITCH_PROXY", raising=False)
    monkeypatch.delenv("ZHIBO_YOUTUBE_PROXY", raising=False)
    set_platform_proxies({"twitch": "7897", "youtube": "direct"})

    try:
        assert proxy_for_platform("twitch") == "http://127.0.0.1:7897"
        assert proxy_for_platform("youtube") is None
    finally:
        set_platform_proxies({})
