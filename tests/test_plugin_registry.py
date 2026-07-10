"""测试插件注册系统"""
from unittest.mock import MagicMock, patch
from plugins import _plugins, register_plugin, get_plugin, list_plugins
from plugins.base import LiveInfo, LiveStreamPlugin


class FakePlugin(LiveStreamPlugin):
    name = "fake"

    async def check_live(self, url, **kwargs):
        return LiveInfo(is_live=True)

    async def get_stream_url(self, url, quality, **kwargs):
        return "http://fake.stream/play.m3u8"


class TestPluginRegistry:
    def setup_method(self):
        _plugins.clear()

    def test_register_and_get(self):
        plugin = FakePlugin()
        register_plugin(plugin)
        assert get_plugin("fake") is plugin
        assert get_plugin("nonexistent") is None

    def test_list_plugins(self):
        register_plugin(FakePlugin())
        assert "fake" in list_plugins()

    def test_discover_plugins(self):
        """模拟 discover_plugins 导入过程"""
        with patch("builtins.__import__") as mock_import:
            from plugins import discover_plugins
            discover_plugins()
            # 应该被调用多次（*_plugin.py 文件）
            assert mock_import.call_count >= 1
