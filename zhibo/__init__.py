"""ZHIBO 直播监控后端核心包。

包含轮询调度（monitor）、配置持久化（config）、导入预览、平台代理、
插件系统（plugins）、便携工具更新与 mpv 播放支持。前端界面在
``qt_quick`` 包中，通过本包提供的 MonitorService 与插件注册表驱动。
"""
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """用户数据根目录：打包后为 exe 所在目录，源码运行为项目根。

    followers.csv / settings.csv 等可写数据都锚定在这里；打包后
    ``__file__`` 指向临时解包目录，不能作为数据位置。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    """只读资源根（QML/图标）：打包后为 PyInstaller 解包目录。"""
    return Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent.parent)
