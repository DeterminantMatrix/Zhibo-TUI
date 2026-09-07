from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QMetaObject, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon


# 打包后数据（实例锁 key）锚定 exe 目录；QML/图标等只读资源在解包目录，
# 源码模式下二者都相对本模块所在目录。
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS")) / "qt_quick"
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    RESOURCE_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 冒烟测试会和用户正在运行的实例共用日志文件，Windows 上轮转重命名
# 会因文件锁失败并刷屏 Logging error；改用独立临时日志。
if "--smoke-test" in sys.argv:
    import tempfile

    os.environ.setdefault(
        "ZHIBO_LOG_FILE",
        str(Path(tempfile.gettempdir()) / "zhibo-smoke.log"),
    )

from zhibo.plugins import discover_plugins
from zhibo.single_instance import COMMAND_SHOW, SingleInstance, notify_existing_instance

from .controller import QuickController
from .worker import QuickMonitorThread


def _invoke(root, method: str) -> None:
    QMetaObject.invokeMethod(root, method, Qt.ConnectionType.QueuedConnection)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    smoke_test = "--smoke-test" in argv
    positional = [arg for arg in argv[1:] if not arg.startswith("-")]

    QQuickStyle.setStyle("FluentWinUI3")
    app = QApplication(argv)
    app.setApplicationName("Zhibo Quick")
    app.setQuitOnLastWindowClosed(False)

    icon_path = RESOURCE_ROOT / "assets" / "tray.ico"
    icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
    app.setWindowIcon(icon)

    # Smoke tests must coexist with a user's already running desktop instance.
    # A separate key keeps the verification process from opening a modal
    # "second instance" message box and blocking automated shutdown.
    instance_key = f"{str(PROJECT_ROOT).casefold()}::qt-quick"
    instance = SingleInstance(f"{instance_key}::smoke" if smoke_test else instance_key)
    if not instance.acquire():
        delivered = notify_existing_instance(COMMAND_SHOW)
        text = "直播监控工具已在运行，已请求显示现有窗口。" if delivered else "无法连接现有实例，本机通信端口可能被占用。"
        QMessageBox.information(None, "不能打开第二个实例", text)
        return 1

    monitor: QuickMonitorThread | None = None
    tray: QSystemTrayIcon | None = None
    controller: QuickController | None = None
    try:
        failures = discover_plugins() or {}
        config_path = positional[0] if positional else None
        monitor = QuickMonitorThread(config_path)
        controller = QuickController(monitor)

        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("controller", controller)
        engine.rootContext().setContextProperty("streamModel", controller.table_model)
        qml_path = RESOURCE_ROOT / "qml" / "Main.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        if not engine.rootObjects():
            raise RuntimeError("QML 主界面加载失败")
        root = engine.rootObjects()[0]

        tray = QSystemTrayIcon(icon, app)
        tray.setToolTip("直播监控工具 · Qt Quick")
        tray_menu = QMenu()
        show_action = QAction("显示窗口", tray_menu)
        hide_action = QAction("隐藏至托盘", tray_menu)
        quit_action = QAction("退出", tray_menu)
        tray_menu.addAction(show_action)
        tray_menu.addAction(hide_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)
        tray.setContextMenu(tray_menu)
        show_action.triggered.connect(lambda: _invoke(root, "restoreWindow"))
        hide_action.triggered.connect(lambda: _invoke(root, "hideWindow"))

        def shutdown() -> None:
            if monitor:
                monitor.stop()
            if tray:
                tray.hide()
            root.setProperty("allowClose", True)
            app.quit()

        quit_action.triggered.connect(shutdown)
        controller.quitRequested.connect(shutdown)
        controller.hideRequested.connect(lambda: _invoke(root, "hideWindow"))
        controller.showRequested.connect(lambda: _invoke(root, "restoreWindow"))
        tray.activated.connect(
            lambda reason: _invoke(root, "restoreWindow")
            if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}
            else None
        )
        controller.notificationRequested.connect(
            lambda title, message: tray.showMessage(
                title,
                message,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )
        )
        if QSystemTrayIcon.isSystemTrayAvailable():
            tray.show()
        else:
            # 没有托盘时窗口隐藏等于失联：允许关窗直接退出，并禁用隐藏入口。
            controller.setTrayAvailable(False)
            app.setQuitOnLastWindowClosed(True)
            controller.append_log("系统托盘不可用；关闭窗口将直接退出程序")

        instance.set_command_handler(controller.request_show)
        for module_name, error in failures.items():
            controller.append_log(f"插件加载失败 {module_name}: {error}")
        monitor.start()
        screenshot_path = os.environ.get("ZHIBO_QT_SCREENSHOT", "").strip()
        if screenshot_path:
            screenshot_size = os.environ.get("ZHIBO_QT_SCREENSHOT_SIZE", "").lower().split("x", 1)
            if len(screenshot_size) == 2 and all(part.isdigit() for part in screenshot_size):
                root.setWidth(max(1040, int(screenshot_size[0])))
                root.setHeight(max(680, int(screenshot_size[1])))
            screenshot_action = os.environ.get("ZHIBO_QT_SCREENSHOT_ACTION", "").strip()
            if screenshot_action:
                QTimer.singleShot(500, lambda: controller.action(screenshot_action))
            QTimer.singleShot(1100, lambda: root.grabWindow().save(screenshot_path))
        if smoke_test:
            QTimer.singleShot(2500, shutdown)
        return app.exec()
    finally:
        if controller:
            controller.shutdown()
        elif monitor:
            monitor.stop()
        if tray:
            tray.hide()
        instance.close()
