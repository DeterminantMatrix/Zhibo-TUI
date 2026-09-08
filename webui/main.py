"""Web 前端入口 — pywebview 窗口 + pystray 托盘 + 单实例锁。"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import webview

from zhibo.single_instance import COMMAND_SHOW, SingleInstance, notify_existing_instance
from webui.api import ZhiboApi
from webui.events import EventPusher
from webui.service import WebMonitorService
from webui.tray import TrayController


INSTANCE_KEY = f"{str(PROJECT_ROOT).casefold()}::webui"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke = "--smoke-test" in argv

    instance = SingleInstance(f"{INSTANCE_KEY}::smoke" if smoke else INSTANCE_KEY)
    if not instance.acquire():
        notify_existing_instance(COMMAND_SHOW, key=instance.key)
        print("直播监控工具（Web 版）已在运行，已请求显示现有窗口。")
        return 1

    pusher = EventPusher()
    service = WebMonitorService(pusher)
    api = ZhiboApi()
    window = webview.create_window(
        "直播监控工具",
        url=str(Path(__file__).with_name("static") / "index.html"),
        js_api=api,
        width=1280,
        height=840,
        min_size=(960, 600),
        frameless=True,
        # 无边框窗口创建瞬间的底色：Win98 桌面青。
        background_color="#008080",
    )
    api.attach(window)
    api.attach_service(service)
    pusher.attach(window)

    tray = TrayController(
        icon_path=PROJECT_ROOT / "qt_quick" / "assets" / "tray.ico",
        tooltip="直播监控工具 · Web",
        on_show=window.show,
        on_hide=window.hide,
        on_quit=api.quit_from_tray,
    )
    tray.start()

    # 关闭请求 = 隐藏到托盘（与 Qt 版语义一致）；退出时放行真关闭。
    # 注意：closing 返回 False 也会拦截 destroy()，因此退出必须先置标志。
    def on_closing():
        if api.quitting:
            return True
        if tray.available:
            window.hide()
            return False
        return True

    window.events.closing += on_closing
    instance.set_command_handler(
        lambda command: window.show() if command == COMMAND_SHOW else None
    )

    if smoke:
        # 无人值守自检：加载后由前端定时调用退出，外部再兜底强杀。
        timer = threading.Timer(8.0, api.quit_from_tray)
        timer.daemon = True
        timer.start()

    def start_after_gui() -> None:
        # evaluate_js 只能在 GUI 启动后调用；推送与监控线程随 GUI 起动。
        pusher.start()
        service.start()

    # 主题验证钩子：ZHIBO_WEB_THEME=dark 时启动即切暗色（走真实切换函数）。
    env_theme = os.environ.get("ZHIBO_WEB_THEME", "").strip()
    if env_theme in {"dark", "classic"}:
        window.events.loaded += lambda: window.evaluate_js(
            f"window.zhibo && window.zhibo.setTheme({env_theme!r})"
        )

    webview.start(start_after_gui)
    pusher.stop()
    service.stop()
    tray.stop()
    instance.close()
    return 0
