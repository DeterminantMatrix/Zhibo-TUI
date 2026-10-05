"""测试插件基类"""
import pytest
from zhibo.plugins.base import LiveInfo, LiveStreamPlugin


class TestLiveInfo:
    def test_defaults(self):
        info = LiveInfo(is_live=False)
        assert info.is_live is False
        assert info.anchor_name == ""
        assert info.title == ""
        assert info.stream_url == ""
        assert info.extra == {}

    def test_full_init(self):
        info = LiveInfo(
            is_live=True,
            anchor_name="测试主播",
            title="游戏直播",
            stream_url="http://example.com/stream.flv",
            m3u8_url="http://example.com/stream.m3u8",
            flv_url="http://example.com/stream.flv",
            quality_name="HD",
            extra={"key": "value"},
        )
        assert info.is_live is True
        assert info.anchor_name == "测试主播"
        assert info.extra == {"key": "value"}


class TestLiveStreamPlugin:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            LiveStreamPlugin()  # type: ignore

    def test_subclass_must_implement_methods(self):
        class IncompletePlugin(LiveStreamPlugin):
            name = "incomplete"

        with pytest.raises(TypeError):
            IncompletePlugin()  # type: ignore

    def test_valid_subclass(self):
        class ValidPlugin(LiveStreamPlugin):
            name = "valid"

            async def check_live(self, url, **kwargs):
                return LiveInfo(is_live=True)

            async def get_stream_url(self, url, quality, **kwargs):
                return "http://example.com/stream.m3u8"

        plugin = ValidPlugin()
        assert plugin.name == "valid"
