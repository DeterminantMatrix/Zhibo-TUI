"""TUI 入口 — python -m tui [--smoke-test]。

启动前完成两件 Qt 版 main 同样做的事：插件发现（漏了会让
streamlink/streamget/yt-dlp 变成"未知插件"）和单实例守卫（防止
两个终端界面同时轮询、并发写 followers.csv）。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from zhibo.plugins import discover_plugins
from zhibo.single_instance import SingleInstance

INSTANCE_KEY = f"{str(PROJECT_ROOT).casefold()}::tui"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke = "--smoke-test" in argv

    instance = SingleInstance(f"{INSTANCE_KEY}::smoke" if smoke else INSTANCE_KEY)
    if not instance.acquire():
        print("直播监控工具（TUI 版）已在运行，请先退出已有实例。")
        return 1

    failures = discover_plugins()
    startup_notes = [
        f"插件加载失败 {module_name}: {error}" for module_name, error in failures.items()
    ]

    from tui.app import ZhiboTui

    try:
        app = ZhiboTui(startup_notes=startup_notes, smoke=smoke)
        app.run(headless=smoke)
    finally:
        instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
