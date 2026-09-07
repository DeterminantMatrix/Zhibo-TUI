import os
import asyncio
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPoint, QSettings, Signal, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from qt_quick.controller import QuickController
from qt_quick import controller as quick_controller_module
from qt_quick.model import HEADERS, StreamTableModel
from qt_quick.worker import QuickMonitorThread
from zhibo.config import ConfigManager
from zhibo.models import Follower
from zhibo.monitor import MonitorService


class _Bridge(QObject):
    snapshot = Signal(object)
    log = Signal(str)
    polling = Signal(bool, int)
    fatal = Signal(str)
    streamReady = Signal(str, object)
    detailData = Signal(object)
    dialogData = Signal(str, object)
    operationFinished = Signal(str, bool, str, object)
    progress = Signal(str, float, str)
    liveEvent = Signal(bool, str, str, bool)
    stopped = Signal()


class _Monitor:
    def __init__(self):
        self.bridge = _Bridge()
        self.refresh_count = 0
        self.notification_count = 0
        self.stream_requests = []
        self.stop_count = 0
        self.start_count = 0
        self.dialog_requests = []
        self.update_status_requests = 0
        self.update_check_requests = []
        self.update_check_all_requests = 0
        self.settings_previews = []
        self.proxy_tests = []
        self.delete_requests = []
        self.quality_requests = []
        self.enabled_requests = []

    def start(self):
        self.start_count += 1

    def toggle_enabled(self, follower_index):
        self.enabled_requests.append(follower_index)

    def request_refresh(self):
        self.refresh_count += 1

    def toggle_notifications(self):
        self.notification_count += 1

    def request_stream(self, follower_index, purpose):
        self.stream_requests.append((follower_index, purpose))

    def request_details(self, follower_index):
        self.dialog_requests.append(("details", follower_index))

    def request_edit(self, follower_index):
        self.dialog_requests.append(("edit", follower_index))

    def preview_delete(self, follower_index):
        self.delete_requests.append(follower_index)

    def set_quality(self, follower_index, quality):
        self.quality_requests.append((follower_index, quality))

    def request_settings(self):
        self.dialog_requests.append(("settings", None))

    def preview_settings(self, values):
        self.settings_previews.append(dict(values))

    def request_proxy(self):
        self.dialog_requests.append(("proxy", None))

    def test_proxy(self, values):
        self.proxy_tests.append(dict(values))

    def request_update_status(self):
        self.update_status_requests += 1

    def request_update_check_all(self):
        self.update_check_all_requests += 1

    def request_update_check(self, target):
        self.update_check_requests.append(target)

    def stop(self):
        self.stop_count += 1


def _row(idx, *, live=False, error="-", name="测试主播", tags=None):
    return {
        "idx": idx,
        "enabled": True,
        "live": live,
        "checking": False,
        "state": "online" if live else "offline",
        "tags": tags or ["游戏"],
        "name": name,
        "platform": "bilibili",
        "title": "测试标题",
        "quality": "原画",
        "last_check": "03:04:05",
        "health": "healthy",
        "plugin": "streamlink",
        "error": error,
        "url": "https://live.bilibili.com/1",
    }


def test_quick_table_model_exposes_tui_columns_and_follower_index():
    model = StreamTableModel()
    model.set_rows([_row(7, live=True)])

    assert model.rowCount() == 1
    assert model.columnCount() == len(HEADERS)
    assert model.headerData(0, Qt.Orientation.Horizontal) == "状态"
    assert model.data(model.index(0, 0)) == "●"
    assert model.data(model.index(0, 1)) == "游戏"
    assert model.data(model.index(0, 0), model.FollowerIndexRole) == 7
    assert model.roleNames()[model.ForegroundRole] == b"foreground"
    assert model.roleNames()[model.ConfiguredQualityRole] == b"configuredQuality"
    assert model.data(model.index(0, 5), model.ConfiguredQualityRole) == "best"


def test_quick_controller_persists_layout_choices(tmp_path):
    settings_path = tmp_path / "ui.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    controller = QuickController(_Monitor(), settings=settings)

    controller.setLogVisible(False)
    controller.setLogWidth(488)
    controller.setColumnPreset("compact")
    controller.setColumnWidth(2, 211)
    controller.persistColumnWidths()
    controller.setTheme("warm")
    settings.sync()

    restored = QuickController(
        _Monitor(),
        settings=QSettings(str(settings_path), QSettings.Format.IniFormat),
    )
    assert restored.logVisible is False
    assert restored.logWidth == 488
    assert restored.columnPreset == "compact"
    assert restored.table_model.columnWidth(2) == 211
    assert restored.themeName == "warm"
    assert restored.themeLabel == "暖黄"
    assert restored.themePalette["accent"] == "#f1b84b"


def test_quick_controller_emits_notification_only_for_confirmed_live_start():
    controller = QuickController(_Monitor())
    received = []
    controller.notificationRequested.connect(lambda title, message: received.append((title, message)))

    controller.apply_snapshot({"rows": [], "tags": ["全部"], "notifications_enabled": True})

    controller._on_live_event(False, "主播", "标题", False)
    controller._on_live_event(True, "主播", "标题", True)
    assert received == []

    controller._on_live_event(True, "主播", "开播啦", False)
    assert received == [("主播 开播了", "开播啦")]

    controller.apply_snapshot({"rows": [], "tags": ["全部"], "notifications_enabled": False})
    controller._on_live_event(True, "主播", "开播啦", False)
    assert len(received) == 1

    controller.apply_snapshot({"rows": [], "tags": ["全部"], "notifications_enabled": True})
    controller._on_live_event(True, "主播", "  ", False)
    assert received[-1] == ("主播 开播了", "正在直播")


def test_quick_controller_tray_guard_blocks_hide_when_unavailable():
    controller = QuickController(_Monitor())
    hidden = []
    controller.hideRequested.connect(lambda: hidden.append(1))

    controller.action("tray")
    assert hidden == [1]

    controller.setTrayAvailable(False)
    assert controller.trayAvailable is False
    controller.action("tray")
    assert hidden == [1]


def test_quick_controller_restarts_monitor_after_fatal_with_limit(monkeypatch):
    monkeypatch.setattr(
        quick_controller_module.QTimer, "singleShot", staticmethod(lambda _ms, callback: callback())
    )
    dialogs = []
    monkeypatch.setattr(
        quick_controller_module.QMessageBox, "critical", staticmethod(lambda *args, **kwargs: dialogs.append(args))
    )
    monitor = _Monitor()
    controller = QuickController(monitor)
    notified = []
    controller.notificationRequested.connect(lambda title, _message: notified.append(title))

    for _ in range(quick_controller_module.MAX_MONITOR_RESTARTS):
        monitor.bridge.fatal.emit("boom")
        monitor.bridge.stopped.emit()
    assert monitor.start_count == quick_controller_module.MAX_MONITOR_RESTARTS
    assert notified

    monitor.bridge.fatal.emit("boom")
    monitor.bridge.stopped.emit()
    assert monitor.start_count == quick_controller_module.MAX_MONITOR_RESTARTS
    assert dialogs

    monitor.bridge.polling.emit(True, 1)
    monitor.bridge.fatal.emit("boom")
    monitor.bridge.stopped.emit()
    assert monitor.start_count == quick_controller_module.MAX_MONITOR_RESTARTS + 1

    controller.shutdown()
    monitor.bridge.fatal.emit("boom")
    monitor.bridge.stopped.emit()
    assert monitor.start_count == quick_controller_module.MAX_MONITOR_RESTARTS + 1


def test_quick_controller_stop_player_waits_then_kills():
    controller = QuickController(_Monitor())

    class _StubProcess:
        def __init__(self):
            self.terminated = 0
            self.killed = 0
            self._poll = None

        def poll(self):
            return self._poll

        def terminate(self):
            self.terminated += 1
            self._poll = 0

        def wait(self, timeout=None):
            if self.terminated == 0 and self.killed == 0:
                raise subprocess.TimeoutExpired(cmd="mpv", timeout=timeout)
            return 0

        def kill(self):
            self.killed += 1
            self._poll = 0

    graceful = _StubProcess()
    controller._player_process = graceful
    controller._stop_player()
    assert graceful.terminated == 1
    assert graceful.killed == 0
    assert controller._player_process is None

    stubborn = _StubProcess()

    def _never_exits(timeout=None):
        raise subprocess.TimeoutExpired(cmd="mpv", timeout=timeout)

    stubborn.wait = _never_exits
    controller._player_process = stubborn
    controller._stop_player()
    assert stubborn.terminated == 1
    assert stubborn.killed == 1
    assert controller._player_process is None


def test_quick_controller_sort_column_toggles_and_persists(tmp_path):
    settings_path = tmp_path / "sort.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    controller = QuickController(_Monitor(), settings=settings)
    controller.apply_snapshot({"rows": [_row(1, name="乙"), _row(2, name="甲")], "tags": ["全部"]})

    assert controller.sortColumn == "default"
    controller.setSortColumn("name")
    assert controller.sortColumn == "name"
    assert controller.sortDescending is False
    controller.setSortColumn("name")
    assert controller.sortDescending is True
    controller.setSortColumn("name")
    assert controller.sortDescending is False

    restored = QuickController(_Monitor(), settings=QSettings(str(settings_path), QSettings.Format.IniFormat))
    assert restored.sortColumn == "name"
    assert restored.sortDescending is False


def test_quick_controller_back_to_form_restores_typed_input():
    controller = QuickController(_Monitor())
    controller._show_dialog("settings", {"stage": "form", "poll_interval": "60"})
    controller._on_dialog_data("settings", {"stage": "confirm", "previewText": "预览"})
    assert controller.dialogStage == "confirm"

    controller.backToForm()

    assert controller.dialogStage == "form"
    assert controller.dialogData.get("poll_interval") == "60"

    # 关闭对话框后表单缓存应被清空，再次返回只能关闭。
    controller._on_dialog_data("settings", {"stage": "confirm", "previewText": "预览2"})
    controller.closeDialog()
    controller.backToForm()
    assert controller.dialogKind == ""


def test_quick_controller_window_geometry_roundtrip(tmp_path):
    settings_path = tmp_path / "geometry.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    controller = QuickController(_Monitor(), settings=settings)

    assert controller.windowGeometry().get("valid") is False

    controller.saveWindowGeometry(120, 80, 1400, 860, True)

    restored = QuickController(_Monitor(), settings=QSettings(str(settings_path), QSettings.Format.IniFormat))
    geometry = restored.windowGeometry()
    assert geometry["valid"] is True
    assert geometry["x"] == 120
    assert geometry["y"] == 80
    assert geometry["width"] == 1400
    assert geometry["height"] == 860
    assert geometry["maximized"] is True


def test_quick_controller_action_dispatches_toggle_enabled():
    monitor = _Monitor()
    controller = QuickController(monitor)
    controller.apply_snapshot({"rows": [_row(3)], "tags": ["全部"]})
    controller.selectFollower(3)

    controller.action("toggle_enabled")

    assert monitor.enabled_requests == [3]


def test_quick_main_theme_switch_updates_palette_and_header_button(tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "theme.ini"), QSettings.Format.IniFormat)
    controller = QuickController(_Monitor(), settings=settings)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("controller", controller)
    engine.rootContext().setContextProperty("streamModel", controller.table_model)
    qml_path = Path(__file__).parents[1] / "qt_quick" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    root = engine.rootObjects()[0]
    theme_button = root.findChild(QObject, "themeButton")

    assert root.property("bg0").name() == "#080c12"
    assert theme_button.property("text") == "主题 · 深海蓝  ▾"

    controller.setTheme("day")
    app.processEvents()

    assert root.property("bg0").name() == "#eef2f5"
    assert root.property("textMain").name() == "#17232e"
    assert theme_button.property("text") == "主题 · 日间  ▾"

    root.setProperty("allowClose", True)
    root.close()
    app.processEvents()


def test_quick_controller_dispatches_proxy_health_test():
    monitor = _Monitor()
    controller = QuickController(monitor)
    controller._show_dialog("proxy", {"stage": "form"})

    controller.testProxy({"youtube": "7890", "twitch": "direct"})

    assert controller.dialogBusy is True
    assert monitor.proxy_tests == [{"youtube": "7890", "twitch": "direct"}]


def test_quick_worker_proxy_health_distinguishes_direct_reachable_and_dead(monkeypatch):
    class Writer:
        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def reachable(_host, _port):
        return object(), Writer()

    monkeypatch.setattr(asyncio, "open_connection", reachable)
    reachable_result = asyncio.run(
        QuickMonitorThread._test_proxy_endpoint("youtube", "http://127.0.0.1:7890")
    )
    direct_result = asyncio.run(QuickMonitorThread._test_proxy_endpoint("twitch", "direct"))

    async def refused(_host, _port):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(asyncio, "open_connection", refused)
    dead_result = asyncio.run(
        QuickMonitorThread._test_proxy_endpoint("youtube", "http://127.0.0.1:7890")
    )

    assert reachable_result["status"] == "ok"
    assert direct_result["status"] == "direct"
    assert dead_result["status"] == "error"
    assert "127.0.0.1:7890" in dead_result["detail"]


def test_quick_controller_filters_selects_and_dispatches_ready_actions():
    QApplication.instance() or QApplication([])
    monitor = _Monitor()
    controller = QuickController(monitor)
    controller.apply_snapshot(
        {
            "rows": [_row(1, live=True), _row(2, name="离线主播")],
            "tags": ["全部", "游戏"],
            "poll_interval": 30,
            "platform_health": {},
            "notifications_enabled": True,
        }
    )

    assert controller.table_model.rowCount() == 2
    assert controller.selectedFollower == 1
    assert "在线 1" in controller.summaryText

    controller.setStateFilter("离线")
    assert controller.table_model.rowCount() == 1
    assert controller.selectedFollower == 2

    controller.action("refresh")
    controller.action("notification")
    controller.action("copy_stream")
    assert monitor.refresh_count == 1
    assert monitor.notification_count == 1
    assert monitor.stream_requests == [(2, "copy")]

    controller.action("edit")
    assert controller.dialogKind == "edit"
    assert controller.dialogBusy is True
    assert monitor.dialog_requests == [("edit", 2)]


def test_quick_controller_retries_next_cdn_when_mpv_exits_immediately(monkeypatch):
    QApplication.instance() or QApplication([])
    monitor = _Monitor()
    controller = QuickController(monitor)
    calls = []

    class FakeProcess:
        def __init__(self, returncode):
            self.returncode = returncode
            self.pid = 123

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

    processes = [FakeProcess(2), FakeProcess(None)]

    def fake_play_url(url, **kwargs):
        calls.append(url)
        return processes[len(calls) - 1]

    monkeypatch.setattr(quick_controller_module, "play_url", fake_play_url)
    controller._stream_ready("play", {
        "url": "https://cdn-a.example/live.m3u8",
        "urls": [
            "https://cdn-a.example/live.m3u8",
            "https://cdn-b.example/live.m3u8",
        ],
    })
    generation = controller._player_generation
    controller._verify_player_candidate(generation)
    controller._verify_player_candidate(generation)

    assert calls == [
        "https://cdn-a.example/live.m3u8",
        "https://cdn-b.example/live.m3u8",
    ]
    assert "自动切换下一条" in controller.logText
    assert "已启动 mpv 播放" in controller.logText
    controller._stop_player()


def test_quick_qml_keeps_all_previous_function_keys_and_shortcuts():
    qml = (Path(__file__).parents[1] / "qt_quick" / "qml" / "Main.qml").read_text(encoding="utf-8")
    expected = {
        "通知  N": "notification",
        "详情  I": "details",
        "编辑  E": "edit",
        "复制流  C": "copy_stream",
        "监控设置  [S]": "settings",
        "打开网页  [F]": "web",
        "导入直播间  [J]": "import",
        "更新中心  [U]": "update",
        "下载视频  [D]": "download",
        "平台代理  [P]": "proxy",
        "切换筛选  [O]": "filter",
        "隐藏到托盘  [H]": "tray",
        "退出程序  [T]": "quit",
    }

    for label, command in expected.items():
        assert label in qml
        assert f'controller.action("{command}")' in qml or f'command: "{command}"' in qml
    assert "Layout.preferredHeight: 54" in qml
    assert 'text: "更多  ···"' in qml
    assert 'label: "播放  Enter"' in qml
    assert 'text: "新增直播间"' in qml
    assert 'objectName: "themeButton"' in qml
    assert 'controller.setTheme("black")' in qml
    assert 'controller.setTheme("warm")' in qml
    assert 'controller.setTheme("day")' in qml
    assert 'objectName: "updateComponentCards"' in (Path(__file__).parents[1] / "qt_quick" / "qml" / "DialogOverlay.qml").read_text(encoding="utf-8")
    assert "modelData.actionLabel" in (Path(__file__).parents[1] / "qt_quick" / "qml" / "DialogOverlay.qml").read_text(encoding="utf-8")
    assert 'text: "复制直播流地址  [C]"' in qml
    assert 'text: "复制直播间摘要  [Ctrl+Shift+C]"' in qml
    assert 'text: "修改直播间信息  [E]"' in qml
    assert 'text: "下载当前视频  [D]"' in qml
    assert 'text: "删除直播间"' in qml
    assert 'controller.action("delete")' in qml
    assert 'objectName: "customTitleBar"' in qml
    assert "Qt.FramelessWindowHint" in qml
    assert "root.startSystemMove()" in qml
    assert "root.startSystemResize(resizeEdges)" in qml
    assert 'objectName: "logScroll"' in qml
    assert 'ScrollBar.vertical: ScrollBar' in qml
    assert 'policy: ScrollBar.AsNeeded' in qml
    assert 'objectName: "logPanel"' in qml
    assert 'objectName: cell.column === 5 ? "qualitySelector-" + cell.followerIndex' in qml
    assert "controller.setFollowerQuality(cell.followerIndex, currentValue)" in qml
    assert '{ label: "[Ctrl⇧C] 复制选中"' not in qml


def test_quick_qml_instantiates_real_table_delegates():
    app = QApplication.instance() or QApplication([])
    monitor = _Monitor()
    controller = QuickController(monitor)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("controller", controller)
    engine.rootContext().setContextProperty("streamModel", controller.table_model)
    qml_path = Path(__file__).parents[1] / "qt_quick" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    assert engine.rootObjects()

    controller.apply_snapshot(
        {
            "rows": [_row(1, live=True)],
            "tags": ["全部", "游戏"],
            "poll_interval": 30,
            "platform_health": {},
            "notifications_enabled": True,
        }
    )
    for _ in range(5):
        app.processEvents()

    root = engine.rootObjects()[0]
    title_bar = root.findChild(QObject, "customTitleBar")
    assert title_bar is not None
    assert title_bar.property("height") >= 36
    table = root.findChild(QObject, "streamTable")
    assert table is not None
    assert table.property("rows") == 1
    assert table.property("contentHeight") >= 32

    menu = root.findChild(QObject, "rowContextMenu")
    assert menu is not None
    QTest.mouseClick(root, Qt.MouseButton.RightButton, pos=QPoint(25, 258), delay=10)
    for _ in range(5):
        app.processEvents()
    assert menu.property("visible") is True
    geometry = (
        menu.property("x"), menu.property("y"),
        menu.property("width"), menu.property("height"),
        menu.property("contentHeight"),
    )
    assert geometry[0] >= 0, geometry
    assert geometry[1] >= 0, geometry
    assert geometry[0] + geometry[2] <= root.width(), geometry
    assert geometry[1] + geometry[3] <= root.height(), geometry
    assert geometry[2] >= 200, geometry
    assert geometry[3] > 100, geometry
    assert geometry[4] > 100, geometry
    menu.close()

    more_button = root.findChild(QObject, "moreButton")
    more_menu = root.findChild(QObject, "moreActionsMenu")
    assert more_button is not None
    assert more_menu is not None
    more_button.click()
    for _ in range(5):
        app.processEvents()
    assert more_menu.property("visible") is True
    more_menu.close()

    root.setProperty("allowClose", True)
    root.close()
    app.processEvents()


def test_quick_controller_opens_delete_confirmation_for_selection():
    monitor = _Monitor()
    controller = QuickController(monitor)
    controller.apply_snapshot({"rows": [_row(3)], "tags": ["全部"], "poll_interval": 30})

    controller.action("delete")

    assert monitor.delete_requests == [3]
    assert controller.dialogKind == "delete"
    assert controller.dialogStage == "loading"


def test_quick_controller_exposes_plugin_specific_quality_options_and_saves_choice():
    monitor = _Monitor()
    controller = QuickController(monitor)
    row = _row(4)
    row.update(configured_quality="best", configured_plugin="streamget", configured_platform="twitch")
    controller.apply_snapshot({"rows": [row], "tags": ["全部"], "poll_interval": 30})

    options = controller.qualityOptions(4)
    controller.setFollowerQuality(4, "UHD")

    assert options[0] == {"label": "最优画质", "value": "best"}
    assert {item["value"] for item in options} == {"best", "UHD", "HD", "LD"}
    assert len(options) == 4
    assert monitor.quality_requests == [(4, "UHD")]


def test_quick_worker_confirms_delete_and_reindexes_runtime(tmp_path):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv(
        [
            Follower(name="甲", plugin="streamget", platform="example", url="https://example.com/a"),
            Follower(name="乙", plugin="streamget", platform="example", url="https://example.com/b"),
        ]
    )
    worker = QuickMonitorThread(str(path))
    worker._service = MonitorService(str(path))
    dialogs = []
    finished = []
    worker.bridge.dialogData.connect(lambda kind, data: dialogs.append((kind, dict(data))))
    worker.bridge.operationFinished.connect(
        lambda kind, ok, message, data: finished.append((kind, ok, message, dict(data)))
    )

    asyncio.run(worker._preview_delete(0))
    assert dialogs[-1][0] == "delete"
    assert dialogs[-1][1]["stage"] == "confirm"
    assert "名称：甲" in dialogs[-1][1]["previewText"]

    asyncio.run(worker._confirm_delete())

    assert [follower.name for follower in manager.read_followers()] == ["乙"]
    assert [follower.name for follower in worker._service.cfg.followers] == ["乙"]
    assert worker._service.followers[0].follower.name == "乙"
    assert finished[-1][:2] == ("delete", True)


def test_quick_worker_persists_inline_quality_selection(tmp_path):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv(
        [Follower(name="主播", plugin="streamlink", platform="twitch", url="https://twitch.tv/test")]
    )
    worker = QuickMonitorThread(str(path))
    worker._service = MonitorService(str(path))
    finished = []
    worker.bridge.operationFinished.connect(
        lambda kind, ok, message, data: finished.append((kind, ok, message, dict(data)))
    )

    asyncio.run(worker._set_quality(0, "720p60"))

    assert manager.read_followers()[0].quality == "720p60"
    assert worker._service.followers[0].follower.quality == "720p60"
    assert finished[-1][0:2] == ("quality", True)


def test_quick_update_progress_stays_in_lower_half_of_update_window():
    app = QApplication.instance() or QApplication([])
    monitor = _Monitor()
    controller = QuickController(monitor)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("controller", controller)
    engine.rootContext().setContextProperty("streamModel", controller.table_model)
    qml_path = Path(__file__).parents[1] / "qt_quick" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    root = engine.rootObjects()[0]

    controller.action("update")
    controller._on_dialog_data(
        "update",
        {
            "stage": "form",
            "target": "streamlink",
            "items": [
                {
                    "value": "streamlink", "label": "streamlink", "version": "8.4.0",
                    "lastUpdated": "无程序内更新记录", "description": "通用直播解析器",
                    "restartRequired": True, "installed": True, "actionLabel": "更新",
                    "actionEnabled": True, "source": "当前 Python 环境", "kind": "package",
                },
                {
                    "value": "ffmpeg", "label": "FFmpeg", "version": "未安装",
                    "lastUpdated": "无程序内更新记录", "description": "视频音频合并组件",
                    "restartRequired": False, "installed": False, "actionLabel": "安装",
                    "actionEnabled": True, "source": "可自动安装", "kind": "tool",
                },
            ],
        },
    )
    for _ in range(5):
        app.processEvents()
    QTest.qWait(100)
    update_pane = root.findChild(QObject, "updatePane")
    action_button = root.findChild(QObject, "updateActionButton")
    assert update_pane is not None
    assert action_button is not None
    update_pane.setProperty("selectedIndex", 1)
    for _ in range(3):
        app.processEvents()
    assert action_button.property("text") == "安装 FFmpeg"
    assert action_button.property("enabled") is True

    controller._on_dialog_data(
        "update",
        {"stage": "progress", "progress": 42, "progressText": "正在更新 streamlink…"},
    )
    for _ in range(5):
        app.processEvents()
    QTest.qWait(100)

    panel = root.findChild(QObject, "dialogPanel")
    backdrop = root.findChild(QObject, "dialogBackdrop")
    progress_areas = root.findChildren(QObject, "updateProgressArea")
    update_panes = root.findChildren(QObject, "updatePane")
    assert panel is not None
    assert backdrop is not None
    assert progress_areas
    assert update_panes
    assert panel.property("height") >= 650
    assert panel.property("y") < root.height() / 2
    assert any(area.property("visible") for area in progress_areas), (
        controller.dialogData,
        [(pane.property("visible"), pane.property("running")) for pane in update_panes],
        [(area.property("visible"), area.property("y")) for area in progress_areas],
    )
    progress_area = next(area for area in progress_areas if area.property("visible"))
    component_cards = root.findChild(QObject, "updateComponentCards")
    selected_detail = root.findChild(QObject, "selectedUpdateDetail")
    assert component_cards is not None and component_cards.property("visible") is True
    assert selected_detail is not None and selected_detail.property("visible") is True
    assert progress_area.property("y") > selected_detail.property("y")
    assert progress_area.property("y") >= panel.property("height") / 2
    assert backdrop.property("visible") is True
    assert controller.dialogData["items"][0]["value"] == "streamlink"

    controller._on_operation_finished("update", True, "更新完成", {"done": True})
    assert controller.dialogKind == "update"
    assert controller.dialogData["stage"] == "done"
    controller.continueUpdateCenter()
    assert controller.dialogKind == "update"
    assert controller.dialogData["stage"] == "form"
    controller.closeDialog()
    root.setProperty("allowClose", True)
    root.close()
    app.processEvents()


def test_update_center_checks_only_selected_component_on_first_click():
    monitor = _Monitor()
    controller = QuickController(monitor)
    controller.action("update")
    controller._on_dialog_data(
        "update",
        {
            "stage": "form",
            "target": "ffmpeg",
            "items": [
                {
                    "value": "ffmpeg",
                    "label": "FFmpeg",
                    "version": "未安装",
                    "installed": False,
                    "actionLabel": "未安装",
                    "actionEnabled": True,
                    "actionKind": "check",
                    "updateStatus": "unchecked",
                }
            ],
        },
    )

    controller.checkUpdate("ffmpeg")

    assert monitor.update_check_requests == ["ffmpeg"]
    assert controller.dialogData["items"][0]["actionLabel"] == "检查中"
    assert controller.dialogData["items"][0]["actionEnabled"] is False

    controller._on_dialog_data(
        "update",
        {
            "stage": "checked",
            "target": "ffmpeg",
            "item": {
                "remoteVersion": "8.1.2",
                "updateStatus": "install",
                "actionLabel": "安装",
                "actionEnabled": True,
                "actionKind": "execute",
            },
        },
    )
    assert controller.dialogData["stage"] == "form"
    assert controller.dialogData["items"][0]["actionLabel"] == "安装"
    assert controller.dialogData["items"][0]["remoteVersion"] == "8.1.2"


def test_completed_update_stays_in_center_and_refreshes_local_card():
    monitor = _Monitor()
    controller = QuickController(monitor)
    controller.action("update")
    controller._on_dialog_data(
        "update",
        {
            "stage": "form",
            "target": "ffmpeg",
            "items": [
                {
                    "value": "ffmpeg",
                    "kind": "tool",
                    "version": "未安装",
                    "installed": False,
                    "actionLabel": "安装",
                    "actionEnabled": True,
                    "actionKind": "execute",
                }
            ],
        },
    )

    controller._on_operation_finished(
        "update",
        True,
        "FFmpeg 安装完成",
        {
            "done": True,
            "downloaded": True,
            "target": "ffmpeg",
            "version": "ffmpeg version 8.1.2",
        },
    )

    item = controller.dialogData["items"][0]
    assert controller.dialogKind == "update"
    assert controller.dialogData["stage"] == "done"
    assert item["version"] == "ffmpeg version 8.1.2"
    assert item["actionLabel"] == "检查更新"
    assert item["lastUpdated"] == "刚刚"

    controller.continueUpdateCenter()

    assert controller.dialogKind == "update"
    assert controller.dialogData["stage"] == "form"


def test_failed_update_stays_in_center_and_can_continue_managing():
    monitor = _Monitor()
    controller = QuickController(monitor)
    controller.action("update")
    controller._on_dialog_data("update", {"stage": "form", "items": []})
    controller._on_dialog_data("update", {"stage": "progress", "progress": 40})

    controller._on_operation_finished("update", False, "网络中断", {})

    assert controller.dialogKind == "update"
    assert controller.dialogData["stage"] == "failed"
    assert controller.dialogData["progressText"] == "网络中断"

    controller.continueUpdateCenter()

    assert controller.dialogKind == "update"
    assert controller.dialogData["stage"] == "form"


def test_quick_table_keyboard_shortcuts_dispatch_selected_row_actions():
    app = QApplication.instance() or QApplication([])
    monitor = _Monitor()
    controller = QuickController(monitor)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("controller", controller)
    engine.rootContext().setContextProperty("streamModel", controller.table_model)
    qml_path = Path(__file__).parents[1] / "qt_quick" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    controller.apply_snapshot(
        {
            "rows": [_row(1, live=True), _row(2, live=True, name="第二位主播")],
            "tags": ["全部", "游戏"],
            "poll_interval": 30,
            "platform_health": {},
            "notifications_enabled": True,
        }
    )
    table = root.findChild(QObject, "streamTable")
    assert table is not None
    table.forceActiveFocus()
    root.requestActivate()
    for _ in range(5):
        app.processEvents()

    QTest.keyClick(root, Qt.Key.Key_Down)
    QTest.keyClick(root, Qt.Key.Key_Up)
    QTest.keyClick(root, Qt.Key.Key_Return)
    QTest.keyClick(root, Qt.Key.Key_Enter)
    QTest.keyClick(root, Qt.Key.Key_C)
    QTest.keyClick(root, Qt.Key.Key_R)
    QTest.keyClick(root, Qt.Key.Key_N)
    for _ in range(5):
        app.processEvents()

    assert controller.selectedFollower == 1
    assert monitor.stream_requests == [(1, "play"), (1, "play"), (1, "copy")]
    assert monitor.refresh_count == 1
    assert monitor.notification_count == 1

    QTest.keyClick(root, Qt.Key.Key_D)
    for _ in range(3):
        app.processEvents()
    assert controller.dialogKind == "download"
    assert controller.dialogData["url"] == "https://live.bilibili.com/1"
    controller.closeDialog()
    root.setProperty("allowClose", True)
    root.close()
    app.processEvents()


def test_quick_download_shortcut_prefills_selected_room_url():
    QApplication.instance() or QApplication([])
    monitor = _Monitor()
    controller = QuickController(monitor)
    controller.apply_snapshot(
        {
            "rows": [_row(7, live=True)],
            "tags": ["全部", "游戏"],
            "poll_interval": 30,
            "platform_health": {},
            "notifications_enabled": True,
        }
    )

    controller.action("download")

    assert controller.dialogKind == "download"
    assert controller.dialogData["url"] == "https://live.bilibili.com/1"


def test_quick_remaining_advertised_shortcuts_are_wired(monkeypatch):
    QApplication.instance() or QApplication([])
    monitor = _Monitor()
    controller = QuickController(monitor)
    controller.apply_snapshot({
        "rows": [_row(1, live=True)],
        "tags": ["全部", "游戏"],
        "poll_interval": 30,
        "platform_health": {},
        "notifications_enabled": True,
    })
    opened = []
    hidden = []
    quit_requests = []
    monkeypatch.setattr(quick_controller_module.webbrowser, "open", lambda url: opened.append(url))
    controller.hideRequested.connect(lambda: hidden.append(True))
    controller.quitRequested.connect(lambda: quit_requests.append(True))
    controller.action("web")
    controller.action("details")
    controller.hideDetails()

    controller.action("edit")
    controller._on_dialog_data("edit", {"stage": "form"})
    controller.closeDialog()
    controller.action("settings")
    controller._on_dialog_data("settings", {"stage": "form"})
    controller.closeDialog()
    controller.action("proxy")
    controller._on_dialog_data("proxy", {"stage": "form"})
    controller.closeDialog()

    controller.action("import")
    assert controller.dialogKind == "import"
    controller.closeDialog()
    controller.action("update")
    assert controller.dialogKind == "update"
    assert monitor.update_status_requests == 1
    controller._on_dialog_data("update", {"stage": "form", "items": []})
    controller.closeDialog()
    controller.action("filter")
    controller.action("tray")
    controller.action("quit")
    controller.action("copy_selected")

    assert opened == ["https://live.bilibili.com/1"]
    assert monitor.dialog_requests == [
        ("details", 1),
        ("edit", 1),
        ("settings", None),
        ("proxy", None),
    ]
    assert controller.stateFilter == "在线"
    assert hidden == [True]
    assert quit_requests == [True]
    assert "测试主播" in QApplication.clipboard().text()


def test_quick_fs1_web_action_opens_room_page_when_check_is_error(monkeypatch):
    QApplication.instance() or QApplication([])
    monitor = _Monitor()
    controller = QuickController(monitor)
    row = _row(1, live=False, error="403 Forbidden")
    row.update(
        url="360907633",
        platform="飞速",
        configured_platform="fs1",
        configured_plugin="fs1",
        sport_id="1",
    )
    controller.apply_snapshot(
        {
            "rows": [row],
            "tags": ["全部", "游戏"],
            "poll_interval": 30,
            "platform_health": {"fs1": "degraded"},
            "notifications_enabled": True,
        }
    )
    opened = []
    monkeypatch.setattr(quick_controller_module.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr("zhibo.plugins.fs1_plugin.get_fs_site_url", lambda: "https://www.fszb148.com")

    controller.action("web")

    assert opened == ["https://www.fszb148.com/broadcast/details?room_id=360907633&sport_id=1"]


def test_quick_worker_edit_preview_and_confirm_use_atomic_config_api(tmp_path):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv(
        [
            Follower(
                name="旧名称",
                plugin="streamlink",
                platform="bilibili",
                url="https://live.bilibili.com/1",
                quality="best",
                tags=["游戏"],
            )
        ]
    )
    worker = QuickMonitorThread(str(path))
    worker._service = MonitorService(str(path))
    dialogs = []
    results = []
    worker.bridge.dialogData.connect(lambda kind, data: dialogs.append((kind, dict(data))))
    worker.bridge.operationFinished.connect(
        lambda kind, success, message, data: results.append((kind, success, message, dict(data)))
    )

    asyncio.run(worker._preview_edit(0, {"name": "新名称"}))
    assert dialogs[-1][0] == "edit"
    assert dialogs[-1][1]["stage"] == "confirm"
    assert "旧名称" in dialogs[-1][1]["previewText"]

    asyncio.run(worker._confirm_edit())
    assert results[-1][0:2] == ("edit", True)
    assert manager.read_followers()[0].name == "新名称"
    assert worker._service.followers[0].follower.name == "新名称"

    settings = manager.read_monitoring_settings()
    values = {key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in settings.items()}
    values["poll_interval"] = "75"
    asyncio.run(worker._preview_settings(values))
    assert dialogs[-1][0] == "settings"
    assert dialogs[-1][1]["stage"] == "confirm"
    asyncio.run(worker._confirm_settings())
    assert results[-1][0:2] == ("settings", True)
    assert manager.read_monitoring_settings()["poll_interval"] == 75
    assert worker._service.poll_interval == 75

    asyncio.run(worker._save_proxy({"youtube": "7897", "twitch": "direct"}))
    assert results[-1][0:2] == ("proxy", True)
    reloaded = manager.load_config()
    assert reloaded.platform_proxies == {"youtube": "7897", "twitch": "direct"}


def test_quick_worker_translates_quality_when_primary_plugin_changes(tmp_path):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv(
        [
            Follower(
                name="主播", plugin="streamlink", platform="twitch",
                url="https://twitch.tv/test", quality="720p60",
            )
        ]
    )
    worker = QuickMonitorThread(str(path))
    worker._service = MonitorService(str(path))

    asyncio.run(worker._preview_edit(0, {"plugin": "streamget", "quality": "720p60"}))
    assert worker._pending["edit"]["preview"].follower.quality == "HD"

    asyncio.run(worker._confirm_edit())
    saved = manager.read_followers()[0]
    assert saved.plugin == "streamget"
    assert saved.quality == "HD"


def test_quick_worker_keeps_runtime_state_when_notification_save_fails(tmp_path, monkeypatch):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv(
        [Follower(name="测试", plugin="streamlink", platform="twitch", url="https://www.twitch.tv/test")]
    )
    worker = QuickMonitorThread(str(path))
    worker._service = MonitorService(str(path))
    original = worker._service.cfg.notifications_enabled

    def fail_save(_cfg):
        raise OSError("disk unavailable")

    monkeypatch.setattr(worker._service.config_manager, "save_config", fail_save)

    try:
        asyncio.run(worker._toggle_notifications())
    except OSError:
        pass
    else:
        raise AssertionError("save failure should propagate to the task error reporter")

    assert worker._service.cfg.notifications_enabled is original


def test_quick_worker_keeps_runtime_proxies_when_proxy_save_fails(tmp_path, monkeypatch):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv(
        [Follower(name="测试", plugin="streamlink", platform="twitch", url="https://www.twitch.tv/test")]
    )
    worker = QuickMonitorThread(str(path))
    worker._service = MonitorService(str(path))
    original = dict(worker._service.cfg.platform_proxies)
    results = []
    worker.bridge.operationFinished.connect(
        lambda kind, success, message, data: results.append((kind, success, message, dict(data)))
    )

    def fail_save(_cfg):
        raise OSError("disk unavailable")

    monkeypatch.setattr(worker._service.config_manager, "save_config", fail_save)
    asyncio.run(worker._save_proxy({"youtube": "7897"}))

    assert results[-1][0:2] == ("proxy", False)
    assert worker._service.cfg.platform_proxies == original


def test_quick_worker_fs1_update_clears_platform_circuit(tmp_path, monkeypatch):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv(
        [Follower(name="FS", plugin="fs1", platform="fs1", url="room-1")]
    )
    worker = QuickMonitorThread(str(path))
    worker._service = MonitorService(str(path))
    health = worker._service._record_platform_connectivity_failure(
        "fs1", "401 Unauthorized: token expired", source=0
    )
    assert health.state == "degraded"

    monkeypatch.setattr(
        "zhibo.plugins.fs1_plugin.update_from_curl",
        lambda content: {"token": "updated", "api_url": "https://example.test/v1/room"},
    )
    monkeypatch.setattr("zhibo.plugins.fs1_plugin.Fs1Plugin.reload_config", lambda self: None)

    asyncio.run(worker._update_fs1("curl https://example.test/v1/room"))

    assert health.state == "healthy"
    assert health.next_allowed_at is None


def test_quick_package_update_is_not_blocked_by_baseline_requirements(monkeypatch):
    captured = {}

    class FakeStdout:
        async def readline(self):
            return b""

    class FakeProcess:
        stdout = FakeStdout()
        returncode = 0

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    worker = QuickMonitorThread()

    asyncio.run(worker._update_package("streamlink"))

    assert captured["args"][-1] == "streamlink"
    assert "--upgrade" in captured["args"]
    assert "--upgrade-strategy" in captured["args"]
    assert "--constraint" not in captured["args"]


def test_quick_worker_can_auto_install_missing_media_tool(monkeypatch):
    from zhibo.tool_runtime import ToolUpdatePlan

    worker = QuickMonitorThread()
    results = []
    recorded = []
    worker.bridge.operationFinished.connect(
        lambda kind, success, message, data: results.append((kind, success, message, dict(data)))
    )

    plan = ToolUpdatePlan(
        "ffmpeg",
        "未安装",
        "8.0",
        "install",
        {"name": "ffmpeg.7z", "browser_download_url": "https://example.test/ffmpeg.7z", "size": 1},
    )

    async def fake_install(tool, checked_plan):
        assert tool == "ffmpeg"
        assert checked_plan is plan
        return "ffmpeg version 8.0"

    async def fake_record(target, version):
        recorded.append((target, version))

    monkeypatch.setattr("zhibo.tool_runtime.check_tool_update", lambda _name: plan)
    monkeypatch.setattr(worker, "_install_tool", fake_install)
    monkeypatch.setattr(worker, "_record_update", fake_record)

    asyncio.run(worker._run_update("ffmpeg", ""))

    assert results[-1][0:2] == ("update", True)
    assert "安装完成" in results[-1][2]
    assert recorded == [("ffmpeg", "ffmpeg version 8.0")]


def test_quick_worker_can_install_uosc_interface(monkeypatch):
    from zhibo.mpv_ui import UoscUpdatePlan

    worker = QuickMonitorThread()
    results = []
    recorded = []
    worker.bridge.operationFinished.connect(
        lambda kind, success, message, data: results.append((kind, success, message, dict(data)))
    )
    plan = UoscUpdatePlan(
        "未安装",
        "5.12.0",
        "install",
        (
            {"name": "uosc.conf", "size": 1},
            {"name": "uosc.zip", "size": 2},
        ),
    )

    async def fake_install(checked_plan):
        assert checked_plan is plan
        return "5.12.0"

    async def fake_record(target, version):
        recorded.append((target, version))

    monkeypatch.setattr("zhibo.mpv_ui.check_uosc_update", lambda: plan)
    monkeypatch.setattr(worker, "_install_uosc", fake_install)
    monkeypatch.setattr(worker, "_record_update", fake_record)

    asyncio.run(worker._run_update("uosc", ""))

    assert results[-1][0:2] == ("update", True)
    assert "uosc 安装完成" in results[-1][2]
    assert results[-1][3]["target"] == "uosc"
    assert recorded == [("uosc", "5.12.0")]


def test_quick_worker_does_not_download_current_media_tool(monkeypatch):
    from zhibo.tool_runtime import ToolUpdatePlan

    worker = QuickMonitorThread()
    results = []
    worker.bridge.operationFinished.connect(
        lambda kind, success, message, data: results.append((kind, success, message, dict(data)))
    )
    monkeypatch.setattr(
        "zhibo.tool_runtime.check_tool_update",
        lambda _name: ToolUpdatePlan("ffmpeg", "ffmpeg version 8.1.2", "8.1.2", "current", {"size": 1}),
    )
    monkeypatch.setattr(
        worker,
        "_install_tool",
        lambda *_args: pytest.fail("current version must not be downloaded"),
    )

    asyncio.run(worker._run_update("ffmpeg", ""))

    assert results[-1][0:2] == ("update", True)
    assert "已是最新版本" in results[-1][2]
    assert results[-1][3]["downloaded"] is False


def test_quick_worker_does_not_run_pip_for_current_package(monkeypatch):
    from zhibo.update_state import PackageUpdatePlan

    worker = QuickMonitorThread()
    results = []
    worker.bridge.operationFinished.connect(
        lambda kind, success, message, data: results.append((kind, success, message, dict(data)))
    )
    monkeypatch.setattr(
        "zhibo.update_state.check_package_update",
        lambda *_args: PackageUpdatePlan("streamlink", "streamlink", "8.5.0", "8.5.0", "current"),
    )
    monkeypatch.setattr(
        worker,
        "_update_package",
        lambda *_args: pytest.fail("current package must not invoke pip"),
    )

    asyncio.run(worker._run_update("streamlink", ""))

    assert results[-1][0:2] == ("update", True)
    assert "没有执行 pip 下载" in results[-1][2]
    assert results[-1][3]["downloaded"] is False


def test_update_center_open_auto_checks_all_targets(monkeypatch):
    worker = QuickMonitorThread()
    dialogs = []
    worker.bridge.dialogData.connect(lambda kind, data: dialogs.append((kind, dict(data))))
    monkeypatch.setattr(
        "zhibo.update_state.update_items",
        lambda: [{"value": "ffmpeg", "actionLabel": "未安装", "actionKind": "check"}],
    )
    checked = []

    def fake_check(target):
        checked.append(target)
        return {
            "remoteVersion": "8.1.2",
            "updateStatus": "install",
            "actionLabel": "安装",
            "actionEnabled": True,
            "actionKind": "execute",
        }

    monkeypatch.setattr("zhibo.update_state.check_update_target", fake_check)

    asyncio.run(worker._load_update_status())

    assert dialogs[0][0] == "update"
    assert dialogs[0][1]["items"][0]["actionLabel"] == "未安装"
    # 打开即自动检查全部 6 个支持远端检查的组件，无需逐个点击。
    assert sorted(checked) == ["ffmpeg", "mpv", "streamget", "streamlink", "uosc", "yt-dlp"]
    patches = [data for kind, data in dialogs if kind == "update" and data.get("stage") == "checked"]
    assert {patch["target"] for patch in patches} == set(checked)


def test_update_worker_checks_only_requested_target(monkeypatch):
    worker = QuickMonitorThread()
    dialogs = []
    worker.bridge.dialogData.connect(lambda kind, data: dialogs.append((kind, dict(data))))
    checked = []

    def fake_check(target):
        checked.append(target)
        return {
            "remoteVersion": "8.1.2",
            "updateStatus": "install",
            "actionLabel": "安装",
            "actionEnabled": True,
            "actionKind": "execute",
        }

    monkeypatch.setattr("zhibo.update_state.check_update_target", fake_check)

    asyncio.run(worker._check_update_target("ffmpeg"))

    assert checked == ["ffmpeg"]
    assert dialogs[-1][1]["stage"] == "checked"
    assert dialogs[-1][1]["target"] == "ffmpeg"


def test_quick_worker_import_preview_and_confirm_update_runtime(tmp_path, monkeypatch):
    path = tmp_path / "followers.csv"
    manager = ConfigManager(path)
    manager.write_followers_csv(
        [
            Follower(
                name="原关注",
                plugin="streamlink",
                platform="twitch",
                url="https://www.twitch.tv/original",
                tags=["游戏"],
            )
        ]
    )

    async def fake_builder(url, tag="未分类"):
        return Follower(
            name="新关注",
            plugin="streamlink",
            platform="twitch",
            url=url,
            tags=[tag or "未分类"],
        )

    monkeypatch.setattr("zhibo.import_preview.build_follower_from_url", fake_builder)
    worker = QuickMonitorThread(str(path))
    worker._service = MonitorService(str(path))
    dialogs = []
    results = []
    worker.bridge.dialogData.connect(lambda kind, data: dialogs.append((kind, dict(data))))
    worker.bridge.operationFinished.connect(
        lambda kind, success, message, data: results.append((kind, success, message, dict(data)))
    )

    asyncio.run(worker._preview_import("https://www.twitch.tv/new-room", "新品"))
    assert dialogs[-1][0] == "import"
    assert dialogs[-1][1]["canConfirm"] is True
    asyncio.run(worker._confirm_import())

    assert results[-1][0:2] == ("import", True)
    assert [item.name for item in manager.read_followers()] == ["原关注", "新关注"]
    assert worker._service.followers[1].follower.name == "新关注"


def test_quick_controller_check_all_update_dispatches_only_in_update_dialog():
    monitor = _Monitor()
    controller = QuickController(monitor)

    controller.checkAllUpdate()
    assert monitor.update_check_all_requests == 0  # 对话框未打开时不动作

    controller._show_dialog("update", {"stage": "form", "items": []})
    controller.checkAllUpdate()
    assert monitor.update_check_all_requests == 1


def test_quick_controller_busy_is_per_operation():
    controller = QuickController(_Monitor())
    controller._show_dialog("settings", {"stage": "form"})
    controller.submitSettings({"poll_interval": "30"})
    assert controller.dialogBusy is True

    # 另一 kind 的操作完成不得解除设置对话框的忙状态（旧全局 busy 的竞态）。
    controller._on_operation_finished("quality", True, "画质已更新", {})
    assert controller.dialogBusy is True

    controller._on_operation_finished("settings", True, "已保存", {"close": True})
    assert controller.dialogBusy is False


def test_quick_controller_polling_active_tracks_poll_state():
    controller = QuickController(_Monitor())
    assert controller.pollingActive is False
    controller.set_polling(True, 3)
    assert controller.pollingActive is True
    assert "检测中" in controller.statusText
    controller.set_polling(False, 0)
    assert controller.pollingActive is False
