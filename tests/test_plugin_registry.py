"""测试插件注册系统"""
from unittest.mock import patch

from zhibo.plugins import BUILTIN_PLUGIN_MODULES, _plugins, register_plugin, get_plugin, list_plugins
from zhibo.plugins.base import LiveInfo, LiveStreamPlugin


class FakePlugin(LiveStreamPlugin):
    name = "fake"

    async def check_live(self, url, **kwargs):
        return LiveInfo(is_live=True)

    async def get_stream_url(self, url, quality, **kwargs):
        return "http://fake.stream/play.m3u8"


class TestPluginRegistry:
    def setup_method(self):
        # 清空前保存，结束后恢复：注册表是模块级全局，清空不恢复会
        # 污染后续依赖真实插件的测试（如 worker 的插件切换用例）。
        self._previous_plugins = dict(_plugins)
        _plugins.clear()

    def teardown_method(self):
        _plugins.clear()
        _plugins.update(getattr(self, "_previous_plugins", {}))

    def test_register_and_get(self):
        plugin = FakePlugin()
        register_plugin(plugin)
        assert get_plugin("fake") is plugin
        assert get_plugin("nonexistent") is None

    def test_list_plugins(self):
        register_plugin(FakePlugin())
        assert "fake" in list_plugins()

    def test_discover_plugins(self):
        """只加载受控的内置模块。"""
        with patch("zhibo.plugins.importlib.import_module") as mock_import:
            from zhibo.plugins import discover_plugins

            assert discover_plugins() == {}
            assert [call.args[0] for call in mock_import.call_args_list] == list(BUILTIN_PLUGIN_MODULES)

    def test_discover_plugins_reports_one_broken_module(self):
        from zhibo.plugins import discover_plugins

        def import_module(name):
            if name == BUILTIN_PLUGIN_MODULES[0]:
                raise ImportError("missing optional dependency")

        with patch("zhibo.plugins.importlib.import_module", side_effect=import_module):
            failures = discover_plugins()

        assert BUILTIN_PLUGIN_MODULES[0] in failures
        assert "ImportError" in failures[BUILTIN_PLUGIN_MODULES[0]]
