"""直播流插件系统 — 自动发现与注册"""

from pathlib import Path
from .base import LiveStreamPlugin, LiveInfo

_plugins: dict[str, LiveStreamPlugin] = {}


def register_plugin(plugin: LiveStreamPlugin) -> None:
    """注册插件实例"""
    _plugins[plugin.name] = plugin


def get_plugin(name: str) -> LiveStreamPlugin | None:
    """获取已注册的插件"""
    return _plugins.get(name)


def list_plugins() -> list[str]:
    """列出所有已注册的插件名"""
    return list(_plugins.keys())


def discover_plugins() -> None:
    """自动发现并导入 plugins/*_plugin.py"""
    plugin_dir = Path(__file__).parent
    for py_file in plugin_dir.glob("*_plugin.py"):
        if py_file.name == "__init__.py":
            continue
        mod_name = f"plugins.{py_file.stem}"
        __import__(mod_name)


__all__ = ["LiveStreamPlugin", "LiveInfo", "register_plugin", "get_plugin", "list_plugins", "discover_plugins"]
