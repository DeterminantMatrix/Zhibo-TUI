"""Web 前端入口 — pywebview 窗口 + pystray 托盘 + 单实例锁。"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import webview

from zhibo.plugins import discover_plugins
from zhibo.private_data import ensure_private_parent, user_data_dir
from zhibo.single_instance import COMMAND_SHOW, SingleInstance, notify_existing_instance
from webui.api import ZhiboApi
from webui.events import EventPusher
from webui.notify import Notifier, ensure_protocol_registered, parse_notify_uri
from webui.service import WebMonitorService
from webui.smoke import run_dialog_probe, verdict
from webui.tray import TrayController


INSTANCE_KEY = f"{str(PROJECT_ROOT).casefold()}::webui"
GEOMETRY_FILE = user_data_dir() / "webui_window.json"


def load_window_geometry() -> dict:
    """读取上次窗口位置/尺寸；损坏或缺失时返回空 dict（用默认值）。"""
    try:
        data = json.loads(GEOMETRY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _extract_notify_play(argv: list[str]) -> int:
    """--notify-play zhibo://play/N → N；没有或非法返回 -1。"""
    if "--notify-play" not in argv:
        return -1
    index = argv.index("--notify-play")
    if index + 1 >= len(argv):
        return -1
    return parse_notify_uri(argv[index + 1])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke = "--smoke-test" in argv

    # 通知点击路径：转发给运行中的实例后直接退出；没有实例则正常启动。
    notify_idx = _extract_notify_play(argv)
    if notify_idx >= 0:
        if notify_existing_instance(f"play:{notify_idx}", key=INSTANCE_KEY):
            return 0
        # 没有运行中的实例：忽略播放请求，按普通启动走。
        while "--notify-play" in argv:
            index = argv.index("--notify-play")
            del argv[index:index + 2]
        notify_idx = -1

    instance = SingleInstance(f"{INSTANCE_KEY}::smoke" if smoke else INSTANCE_KEY)
    if not instance.acquire():
        notify_existing_instance(COMMAND_SHOW, key=instance.key)
        print("直播监控工具（Web 版）已在运行，已请求显示现有窗口。")
        return 1

    pusher = EventPusher()
    service = WebMonitorService(pusher)
    api = ZhiboApi()
    # 加载全部内置插件（streamlink/streamget/yt-dlp 等）；漏掉会让
    # 使用这些插件的关注项全部"未知插件"，下载也不可用。
    plugin_failures = discover_plugins() or {}
    for module_name, error in plugin_failures.items():
        service._log(f"插件加载失败 {module_name}: {error}")

    # 窗口几何记忆：启动恢复，moved/resized 防抖落盘。
    geometry = load_window_geometry()
    geometry_x = geometry.get("x")
    geometry_y = geometry.get("y")
    window = webview.create_window(
        "直播监控工具",
        url=str(Path(__file__).with_name("static") / "index.html"),
        js_api=api,
        x=geometry_x if isinstance(geometry_x, int) else None,
        y=geometry_y if isinstance(geometry_y, int) else None,
        width=geometry.get("width") if isinstance(geometry.get("width"), int) else 1280,
        height=geometry.get("height") if isinstance(geometry.get("height"), int) else 840,
        min_size=(960, 600),
        frameless=True,
        # 无边框窗口创建瞬间的底色：Win98 桌面青。
        background_color="#008080",
    )
    api.attach(window)
    api.attach_service(service)
    pusher.attach(window)

    # 开播 toast + zhibo:// 协议（点击通知 = 唤起窗口并播放）。
    notifier = Notifier()
    service.notify_hook = notifier.notify_live
    if not ensure_protocol_registered():
        service._log("zhibo:// 协议注册失败：通知点击将无法唤起播放")

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

    # 移动/缩放停止 0.8 秒后落盘，避免拖动期间高频写文件。
    geometry_timer: dict = {}

    def save_window_geometry() -> None:
        try:
            data = {
                "x": int(window.x),
                "y": int(window.y),
                "width": int(window.width),
                "height": int(window.height),
            }
            ensure_private_parent(GEOMETRY_FILE)
            GEOMETRY_FILE.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass  # 几何记忆是锦上添花，任何失败都静默。

    def schedule_geometry_save(*_args) -> None:
        previous = geometry_timer.get("t")
        if previous is not None:
            previous.cancel()
        timer = threading.Timer(0.8, save_window_geometry)
        timer.daemon = True
        geometry_timer["t"] = timer
        timer.start()

    window.events.resized += schedule_geometry_save
    window.events.moved += schedule_geometry_save

    def handle_command(command: str) -> None:
        if command == COMMAND_SHOW:
            window.show()
        elif command.startswith("play:"):
            # 通知点击转发：唤起窗口并直接播放对应直播间。
            window.show()
            try:
                service.play(int(command[5:]))
            except ValueError:
                pass

    instance.set_command_handler(handle_command)

    probe_result: dict = {}

    if smoke:
        # 无人值守自检：页面就绪后注入对话框交互探针（真实按钮点击全链路），
        # 拿到结果即退出；兜底定时器防止探针挂死拖住进程。
        safety = threading.Timer(40.0, api.quit_from_tray)
        safety.daemon = True
        safety.start()

        def start_probe() -> None:
            def collect() -> None:
                probe_result.update(run_dialog_probe(window))
                api.quit_from_tray()

            threading.Thread(target=collect, name="zhibo-webui-smoke", daemon=True).start()

        window.events.loaded += start_probe

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

    if smoke:
        ok, summary = verdict(probe_result)
        print(f"SMOKE_DIALOGS {'PASS' if ok else 'FAIL'}: {summary}")
        return 0 if ok else 1
    return 0
