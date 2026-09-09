"""TUI 入口 — python -m tui [--smoke-test]。

启动前完成两件 Qt 版 main 同样做的事：插件发现（漏了会让
streamlink/streamget/yt-dlp 变成"未知插件"）和单实例守卫（防止
两个终端界面同时轮询、并发写 followers.csv）。
托盘：常驻图标，点击图标显示/隐藏终端窗口（隐藏后监控继续）。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zhibo.plugins import discover_plugins
from zhibo.single_instance import COMMAND_SHOW, SingleInstance, notify_existing_instance
from zhibo.single_instance import notify_existing_instance as _notify

INSTANCE_KEY = f"{str(PROJECT_ROOT).casefold()}::tui"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke = "--smoke-test" in argv

    instance = SingleInstance(f"{INSTANCE_KEY}::smoke" if smoke else INSTANCE_KEY)
    if not instance.acquire():
        # 已有实例（可能隐藏在托盘）：请求其显示窗口。
        notify_existing_instance(COMMAND_SHOW, key=INSTANCE_KEY)
        print("直播监控工具（TUI 版）已在运行，已请求显示窗口。")
        return 1

    failures = discover_plugins()
    startup_notes = [
        f"插件加载失败 {module_name}: {error}" for module_name, error in failures.items()
    ]

    from tui.app import ZhiboTui
    from tui.tray import (
        TrayController,
        hide_console,
        intercept_close_button,
        show_console,
    )

    app = ZhiboTui(startup_notes=startup_notes, smoke=smoke)

    tray = None
    if not smoke:
        # 托盘任何环节失败都只降级为"无托盘"，绝不能挡住监控主程序。
        try:
            tray = TrayController(
                icon_path=PROJECT_ROOT / "tui" / "assets" / "tray.ico",
                tooltip="直播监控工具 · TUI",
                on_show=show_console,
                on_hide=hide_console,
                on_quit=lambda: app.call_from_thread(app.shutdown_from_tray),
            )
            tray.start()
            intercept_close_button()
        except Exception:
            tray = None

    def handle_command(command: str) -> None:
        if command == COMMAND_SHOW:
            show_console()

    instance.set_command_handler(handle_command)

    try:
        app.run(headless=smoke)
    finally:
        if tray is not None:
            tray.stop()
        instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
