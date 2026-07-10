"""直播监控工具 — 主入口"""
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from plugins import discover_plugins
from single_instance import COMMAND_SHOW, SingleInstance, notify_existing_instance
from ui.app import ZhiboApp


def main():
    """启动直播监控 TUI"""
    if len(sys.argv) > 1 and sys.argv[1] == "fs1-update":
        from plugins.fs1_plugin import interactive_update

        raise SystemExit(interactive_update())

    instance = SingleInstance()
    if not instance.acquire():
        notify_existing_instance(COMMAND_SHOW)
        from desktop import show_already_running_message

        show_already_running_message()
        return 1

    # 自动发现并加载所有插件
    discover_plugins()

    config_path = None
    if len(sys.argv) > 1:
        config_path = sys.argv[1]

    app = ZhiboApp(config_path)
    instance.set_command_handler(app.handle_instance_command)
    try:
        app.run()
    finally:
        instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
