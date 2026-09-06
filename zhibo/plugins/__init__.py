"""直播流插件系统 — 自动发现与注册"""

import importlib

from .base import LiveStreamPlugin, LiveInfo

_plugins: dict[str, LiveStreamPlugin] = {}

# 发布版只加载项目自带的模块。扫描可写目录会让同步冲突或误放文件在启动时执行。
BUILTIN_PLUGIN_MODULES = (
    "zhibo.plugins.fs1_plugin",
    "zhibo.plugins.streamget_plugin",
    "zhibo.plugins.streamlink_plugin",
    "zhibo.plugins.yt_dlp_plugin",
)


def register_plugin(plugin: LiveStreamPlugin) -> None:
    """注册插件实例"""
    _plugins[plugin.name] = plugin


def get_plugin(name: str) -> LiveStreamPlugin | None:
    """获取已注册的插件"""
    return _plugins.get(name)


def list_plugins() -> list[str]:
    """列出所有已注册的插件名"""
    return list(_plugins.keys())


def discover_plugins() -> dict[str, str]:
    """Load built-in plugins and report failures without preventing startup."""
    failures: dict[str, str] = {}
    for module_name in BUILTIN_PLUGIN_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failures[module_name] = f"{type(exc).__name__}: {exc}"
    return failures


__all__ = [
    "LiveStreamPlugin",
    "LiveInfo",
    "register_plugin",
    "get_plugin",
    "list_plugins",
    "discover_plugins",
]
