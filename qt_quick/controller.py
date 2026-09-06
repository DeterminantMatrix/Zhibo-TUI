from __future__ import annotations

import json
import subprocess
import webbrowser
from datetime import datetime

from PySide6.QtCore import QObject, Property, QSettings, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from zhibo.app_logging import redact_sensitive_text
from zhibo.desktop import play_url
from zhibo.quality_options import quality_options as available_quality_options
from qt_quick.viewmodel import (
    SORTABLE_COLUMNS,
    matches_snapshot,
    sort_snapshots,
    web_url_for_snapshot,
)

from .model import COLUMNS, COLUMN_PRESETS, StreamTableModel
from .worker import QuickMonitorThread


THEME_ORDER = ("blue", "black", "warm", "day")
# 监控线程异常退出后的自动重启上限；一次成功轮询会清零计数。
MAX_MONITOR_RESTARTS = 3
MONITOR_RESTART_DELAY_MS = 3000
THEME_LABELS = {
    "blue": "深海蓝",
    "black": "纯黑",
    "warm": "暖黄",
    "day": "日间",
}
THEME_PALETTES = {
    "blue": {
        "bg0": "#080c12", "bg1": "#0d141d", "bg2": "#121c28", "bg3": "#172534",
        "line": "#203142", "lineBright": "#36546d", "textMain": "#e3edf7", "textMuted": "#8497aa",
        "accent": "#55d8ea", "green": "#4ce38a", "yellow": "#ffd166", "red": "#ff667a",
        "primaryText": "#061015", "disabledText": "#657486", "pressedText": "#ffffff",
        "primaryPressed": "#35adbd", "primaryHover": "#6de4f2", "buttonPressed": "#20394c", "buttonHover": "#192a39",
        "highlight": "#183f59", "highlightedText": "#ffffff", "menuHighlight": "#183247", "placeholder": "#64778a",
        "selectedBg": "#14384c", "tagSelected": "#173747", "tagBorder": "#2d7182",
        "headerBg": "#13202c", "headerText": "#9eb4c9", "rowSelected": "#16384b", "rowAlternate": "#0c131b",
        "rowBorder": "#172532", "footerBg": "#0b1118", "footerText": "#65788c", "backdrop": "#b5080c12",
        "statusIdleBg": "#10242b", "statusIdleBorder": "#1f4d58", "statusBusyBg": "#2a2415", "statusBusyBorder": "#6d5723",
        "dangerBg": "#40242d", "dangerBorder": "#884052", "dangerText": "#ff8796", "successBg": "#123a3e", "successBorder": "#286875",
    },
    "black": {
        "bg0": "#000000", "bg1": "#050505", "bg2": "#0c0c0c", "bg3": "#151515",
        "line": "#252525", "lineBright": "#4a4a4a", "textMain": "#f2f2f2", "textMuted": "#969696",
        "accent": "#f0f0f0", "green": "#63d98a", "yellow": "#e8c35c", "red": "#ff6f7f",
        "primaryText": "#080808", "disabledText": "#666666", "pressedText": "#ffffff",
        "primaryPressed": "#bdbdbd", "primaryHover": "#ffffff", "buttonPressed": "#292929", "buttonHover": "#1d1d1d",
        "highlight": "#343434", "highlightedText": "#ffffff", "menuHighlight": "#242424", "placeholder": "#777777",
        "selectedBg": "#202020", "tagSelected": "#222222", "tagBorder": "#555555",
        "headerBg": "#101010", "headerText": "#c5c5c5", "rowSelected": "#242424", "rowAlternate": "#080808",
        "rowBorder": "#1c1c1c", "footerBg": "#030303", "footerText": "#777777", "backdrop": "#c0000000",
        "statusIdleBg": "#151a16", "statusIdleBorder": "#3b4c40", "statusBusyBg": "#211d12", "statusBusyBorder": "#685923",
        "dangerBg": "#29161a", "dangerBorder": "#71313c", "dangerText": "#ff8694", "successBg": "#14231a", "successBorder": "#356047",
    },
    "warm": {
        "bg0": "#100d07", "bg1": "#18130a", "bg2": "#241b0d", "bg3": "#302410",
        "line": "#4a3819", "lineBright": "#765a25", "textMain": "#f7ead0", "textMuted": "#b9a47d",
        "accent": "#f1b84b", "green": "#70d69a", "yellow": "#ffd166", "red": "#ff7770",
        "primaryText": "#1a1102", "disabledText": "#75684f", "pressedText": "#fff7e6",
        "primaryPressed": "#c48a27", "primaryHover": "#ffd071", "buttonPressed": "#503815", "buttonHover": "#382813",
        "highlight": "#5a4018", "highlightedText": "#fff8e8", "menuHighlight": "#3b2a13", "placeholder": "#887758",
        "selectedBg": "#4a3314", "tagSelected": "#3d2c14", "tagBorder": "#8a6426",
        "headerBg": "#2a1f0f", "headerText": "#dbc49a", "rowSelected": "#443015", "rowAlternate": "#151008",
        "rowBorder": "#332610", "footerBg": "#0c0a05", "footerText": "#8f7b59", "backdrop": "#bd0b0905",
        "statusIdleBg": "#18231a", "statusIdleBorder": "#3d6846", "statusBusyBg": "#35270f", "statusBusyBorder": "#886527",
        "dangerBg": "#3c1d18", "dangerBorder": "#87443c", "dangerText": "#ff9588", "successBg": "#183024", "successBorder": "#3f7357",
    },
    "day": {
        "bg0": "#eef2f5", "bg1": "#ffffff", "bg2": "#e4eaf0", "bg3": "#d7e0e8",
        "line": "#c2ccd6", "lineBright": "#8da0b2", "textMain": "#17232e", "textMuted": "#637180",
        "accent": "#087f96", "green": "#14864a", "yellow": "#9a6b00", "red": "#c93449",
        "primaryText": "#ffffff", "disabledText": "#9aa5af", "pressedText": "#ffffff",
        "primaryPressed": "#056477", "primaryHover": "#0a96b1", "buttonPressed": "#cbd5de", "buttonHover": "#d8e1e9",
        "highlight": "#b9dce4", "highlightedText": "#10212a", "menuHighlight": "#d6e9ee", "placeholder": "#8a98a5",
        "selectedBg": "#d2eaf0", "tagSelected": "#dceef2", "tagBorder": "#67aebb",
        "headerBg": "#dbe4eb", "headerText": "#435566", "rowSelected": "#d4eaf0", "rowAlternate": "#f6f8fa",
        "rowBorder": "#d2dbe3", "footerBg": "#dde5eb", "footerText": "#6e7c88", "backdrop": "#660b1620",
        "statusIdleBg": "#dcefe5", "statusIdleBorder": "#77ad8d", "statusBusyBg": "#fff0c8", "statusBusyBorder": "#d5ad4d",
        "dangerBg": "#f8dfe3", "dangerBorder": "#d98b98", "dangerText": "#a92338", "successBg": "#dcefe7", "successBorder": "#75ad8b",
    },
}


class QuickController(QObject):
    tabsChanged = Signal()
    currentTagChanged = Signal()
    stateFilterChanged = Signal()
    selectedFollowerChanged = Signal()
    selectedDetailsChanged = Signal()
    detailsVisibleChanged = Signal()
    logTextChanged = Signal()
    statusTextChanged = Signal()
    summaryTextChanged = Signal()
    notificationStateChanged = Signal()
    dialogKindChanged = Signal()
    dialogDataChanged = Signal()
    dialogBusyChanged = Signal()
    dialogErrorChanged = Signal()
    layoutStateChanged = Signal()
    themeChanged = Signal()
    trayAvailabilityChanged = Signal()
    notificationRequested = Signal(str, str)
    sortChanged = Signal()
    hideRequested = Signal()
    showRequested = Signal()
    quitRequested = Signal()

    def __init__(self, monitor: QuickMonitorThread, parent=None, settings: QSettings | None = None):
        super().__init__(parent)
        self.monitor = monitor
        self.table_model = StreamTableModel(self)
        self._snapshot = {"rows": [], "tags": ["全部"], "poll_interval": 60}
        self._tabs = ["全部"]
        self._current_tag = "全部"
        self._state_filter = "全部"
        self._search = ""
        self._selected_follower = -1
        self._selected_details: dict = {}
        self._details_visible = False
        self._log_lines: list[str] = []
        self._status_text = "正在初始化监控核心…"
        self._summary_text = "在线 0 / 总计 0"
        self._polling = False
        self._poll_count = 0
        self._notifications_enabled = True
        self._player_process: subprocess.Popen | None = None
        self._player_candidates: list[str] = []
        self._player_candidate_index = 0
        self._player_payload: dict = {}
        self._player_generation = 0
        self._dialog_kind = ""
        self._dialog_data: dict = {}
        self._dialog_busy = False
        self._dialog_error = ""
        self._monitor_restarts = 0
        self._fatal_pending = ""
        self._shutting_down = False
        self._ui_settings = settings or QSettings("Zhibo", "Zhibo Quick")
        self._tray_available = True
        configured_theme = str(self._ui_settings.value("appearance/theme", "blue"))
        self._theme_name = configured_theme if configured_theme in THEME_PALETTES else "blue"
        self._log_visible = self._ui_settings.value("layout/logVisible", True, type=bool)
        self._log_width = max(250, min(600, self._ui_settings.value("layout/logWidth", 350, type=int)))
        preset = str(self._ui_settings.value("layout/columnPreset", "full"))
        self._column_preset = preset if preset in COLUMN_PRESETS else "full"
        self._column_widths = self._load_column_widths(self._column_preset)
        self.table_model.set_column_widths(self._column_widths)
        sort_column = str(self._ui_settings.value("layout/sortColumn", "default"))
        self._sort_column = sort_column if sort_column in SORTABLE_COLUMNS else "default"
        self._sort_descending = self._ui_settings.value("layout/sortDescending", False, type=bool)
        self._dialog_form_data: dict = {}

        bridge = monitor.bridge
        bridge.snapshot.connect(self.apply_snapshot)
        bridge.log.connect(self.append_log)
        bridge.polling.connect(self.set_polling)
        bridge.streamReady.connect(self._stream_ready)
        bridge.detailData.connect(self._on_detail_data)
        bridge.dialogData.connect(self._on_dialog_data)
        bridge.operationFinished.connect(self._on_operation_finished)
        bridge.progress.connect(self._on_progress)
        bridge.liveEvent.connect(self._on_live_event)
        bridge.fatal.connect(self._on_monitor_fatal)
        bridge.stopped.connect(self._on_monitor_stopped)

    @Property("QStringList", notify=tabsChanged)
    def tabs(self):
        return self._tabs

    @Property(str, notify=currentTagChanged)
    def currentTag(self):
        return self._current_tag

    @Property(str, notify=stateFilterChanged)
    def stateFilter(self):
        return self._state_filter

    @Property(int, notify=selectedFollowerChanged)
    def selectedFollower(self):
        return self._selected_follower

    @Property("QVariantMap", notify=selectedDetailsChanged)
    def selectedDetails(self):
        return self._selected_details

    @Property(bool, notify=detailsVisibleChanged)
    def detailsVisible(self):
        return self._details_visible

    @Property(str, notify=logTextChanged)
    def logText(self):
        return "\n".join(self._log_lines)

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status_text

    @Property(str, notify=summaryTextChanged)
    def summaryText(self):
        return self._summary_text

    @Property(bool, notify=notificationStateChanged)
    def notificationsEnabled(self):
        return self._notifications_enabled

    @Property(str, notify=dialogKindChanged)
    def dialogKind(self):
        return self._dialog_kind

    @Property("QVariantMap", notify=dialogDataChanged)
    def dialogData(self):
        return self._dialog_data

    @Property(str, notify=dialogDataChanged)
    def dialogStage(self):
        return str(self._dialog_data.get("stage") or "")

    @Property(bool, notify=dialogBusyChanged)
    def dialogBusy(self):
        return self._dialog_busy

    @Property(str, notify=dialogErrorChanged)
    def dialogError(self):
        return self._dialog_error

    @Property(bool, notify=layoutStateChanged)
    def logVisible(self):
        return self._log_visible

    @Property(int, notify=layoutStateChanged)
    def logWidth(self):
        return self._log_width

    @Property(str, notify=layoutStateChanged)
    def columnPreset(self):
        return self._column_preset

    @Property(str, notify=themeChanged)
    def themeName(self):
        return self._theme_name

    @Property(str, notify=themeChanged)
    def themeLabel(self):
        return THEME_LABELS[self._theme_name]

    @Property(bool, notify=trayAvailabilityChanged)
    def trayAvailable(self):
        return self._tray_available

    @Property(str, notify=sortChanged)
    def sortColumn(self):
        return self._sort_column

    @Property(bool, notify=sortChanged)
    def sortDescending(self):
        return self._sort_descending

    @Slot(str)
    def setSortColumn(self, column: str) -> None:
        """点击列头排序：同列点击切换升降序，换列则重置为升序。"""
        column = str(column or "default")
        if column not in SORTABLE_COLUMNS:
            column = "default"
        if column == self._sort_column:
            self._sort_descending = not self._sort_descending
        else:
            self._sort_column = column
            self._sort_descending = False
        self._ui_settings.setValue("layout/sortColumn", self._sort_column)
        self._ui_settings.setValue("layout/sortDescending", self._sort_descending)
        self.sortChanged.emit()
        self._refresh_rows()

    @Property(bool, notify=selectedFollowerChanged)
    def selectedRowEnabled(self):
        row = self._selected_row()
        return bool(row and row.get("enabled", True))

    @Slot(bool)
    def setTrayAvailable(self, available: bool) -> None:
        available = bool(available)
        if available == self._tray_available:
            return
        self._tray_available = available
        self.trayAvailabilityChanged.emit()

    @Slot(bool, str, str, bool)
    def _on_live_event(self, is_live: bool, name: str, title: str, is_initial: bool) -> None:
        """把开播事件转成托盘通知请求；下播和启动时的初始结果不通知。"""
        if not is_live or is_initial or not self._notifications_enabled:
            return
        message = title.strip() or "正在直播"
        self.notificationRequested.emit(f"{name} 开播了", message)

    @Property("QVariantMap", notify=themeChanged)
    def themePalette(self):
        return dict(THEME_PALETTES[self._theme_name])

    @Slot(str)
    def setTheme(self, name: str) -> None:
        key = str(name).strip().casefold()
        if key not in THEME_PALETTES or key == self._theme_name:
            return
        self._theme_name = key
        self._ui_settings.setValue("appearance/theme", key)
        self.themeChanged.emit()

    @Slot(bool)
    def setLogVisible(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._log_visible:
            return
        self._log_visible = visible
        self._ui_settings.setValue("layout/logVisible", visible)
        self.layoutStateChanged.emit()

    @Slot(int)
    def setLogWidth(self, width: int) -> None:
        width = max(250, min(600, int(width)))
        if abs(width - self._log_width) < 2:
            return
        self._log_width = width
        self._ui_settings.setValue("layout/logWidth", width)
        self.layoutStateChanged.emit()

    @Slot(str)
    def setColumnPreset(self, preset: str) -> None:
        if preset not in COLUMN_PRESETS or preset == self._column_preset:
            return
        self._column_preset = preset
        self._column_widths = self._load_column_widths(preset)
        self.table_model.set_column_widths(self._column_widths)
        self._ui_settings.setValue("layout/columnPreset", preset)
        self.layoutStateChanged.emit()

    @Slot(int, int)
    def setColumnWidth(self, column: int, width: int) -> None:
        """只更新内存与视图；持久化由 persistColumnWidths 完成（拖动防抖）。"""
        if not 0 <= column < len(COLUMNS):
            return
        width = 0 if width <= 0 else max(44, min(900, int(width)))
        if width == self._column_widths[column]:
            return
        self._column_widths[column] = width
        self.table_model.set_column_widths(self._column_widths)

    @Slot()
    def persistColumnWidths(self) -> None:
        self._save_column_widths()

    @Slot()
    def resetColumnWidths(self) -> None:
        self._column_widths = list(COLUMN_PRESETS[self._column_preset])
        self.table_model.set_column_widths(self._column_widths)
        self._save_column_widths()
        self.layoutStateChanged.emit()

    @Slot("QVariantMap")
    def testProxy(self, values) -> None:
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        self.monitor.test_proxy(dict(values))

    def _load_column_widths(self, preset: str) -> list[int]:
        raw = self._ui_settings.value(f"layout/columnWidths/{preset}", "")
        try:
            widths = [int(value) for value in json.loads(str(raw))]
        except (TypeError, ValueError, json.JSONDecodeError):
            widths = []
        if len(widths) != len(COLUMNS):
            return list(COLUMN_PRESETS[preset])
        return [max(0, min(900, width)) for width in widths]

    def _save_column_widths(self) -> None:
        self._ui_settings.setValue(
            f"layout/columnWidths/{self._column_preset}",
            json.dumps(self._column_widths),
        )

    @Slot(object)
    def apply_snapshot(self, payload) -> None:
        self._snapshot = dict(payload)
        tabs = list(payload.get("tags") or ["全部"])
        if tabs != self._tabs:
            self._tabs = tabs
            if self._current_tag not in tabs:
                self._current_tag = "全部"
                self.currentTagChanged.emit()
            self.tabsChanged.emit()
        notifications = bool(payload.get("notifications_enabled", True))
        if notifications != self._notifications_enabled:
            self._notifications_enabled = notifications
            self.notificationStateChanged.emit()
        self._refresh_rows()

    @Slot(str)
    def setCurrentTag(self, tag: str) -> None:
        if tag == self._current_tag:
            return
        self._current_tag = tag
        self.currentTagChanged.emit()
        self._refresh_rows()

    @Slot(str)
    def setStateFilter(self, value: str) -> None:
        if value == self._state_filter:
            return
        self._state_filter = value
        self.stateFilterChanged.emit()
        self._refresh_rows()

    @Slot(str)
    def setSearch(self, value: str) -> None:
        value = value.strip()
        if value == self._search:
            return
        self._search = value
        self._refresh_rows()

    @Slot(int)
    def selectFollower(self, follower_index: int) -> None:
        if follower_index == self._selected_follower:
            return
        self._selected_follower = follower_index
        self.selectedFollowerChanged.emit()
        self._update_selected_details()

    @Slot(int)
    def moveSelection(self, offset: int) -> None:
        target = self.table_model.adjacent_follower_index(self._selected_follower, offset)
        if target >= 0:
            self.selectFollower(target)

    @Slot(int, result="QVariantList")
    def qualityOptions(self, follower_index: int):
        row = next(
            (item for item in self._snapshot.get("rows", []) if int(item.get("idx", -1)) == follower_index),
            None,
        )
        if row is None:
            return [{"label": "最优画质", "value": "best"}]
        return available_quality_options(
            row.get("configured_plugin") or row.get("plugin"),
            row.get("configured_platform") or row.get("platform"),
            row.get("configured_quality") or "best",
        )

    @Slot(int, str)
    def setFollowerQuality(self, follower_index: int, quality: str) -> None:
        if follower_index < 0:
            return
        self.monitor.set_quality(follower_index, quality)

    @Slot(str)
    def action(self, name: str) -> None:
        actions = {
            "notification": self.monitor.toggle_notifications,
            "refresh": self.monitor.request_refresh,
            "details": self.showDetails,
            "edit": self._open_edit,
            "delete": self._open_delete,
            "settings": self._open_settings,
            "web": self._open_web,
            "copy_stream": lambda: self._request_stream("copy"),
            "update": self._open_update,
            "download": self._download_selected,
            "download_selected": self._download_selected,
            "proxy": self._open_proxy,
            "copy_selected": self._copy_selected,
            "filter": self._cycle_filter,
            "toggle_enabled": self._toggle_selected_enabled,
            "tray": self._hide_to_tray,
            "quit": self.quitRequested.emit,
            "play": lambda: self._request_stream("play"),
            "stop": self._stop_player,
            "import": lambda: self._show_dialog(
                "import",
                {"stage": "form", "tag": "" if self._current_tag == "全部" else self._current_tag, "url": ""},
            ),
        }
        callback = actions.get(name)
        if callback is None:
            self.append_log(f"未知操作：{name}")
            return
        callback()

    @Slot()
    def showDetails(self) -> None:
        if self._selected_follower < 0:
            self.append_log("请先选择一个关注项")
            return
        self._update_selected_details()
        self._selected_details = {**self._selected_details, "detailTitle": "正在读取详情…", "detailRows": []}
        self.selectedDetailsChanged.emit()
        self._details_visible = True
        self.detailsVisibleChanged.emit()
        self.monitor.request_details(self._selected_follower)

    @Slot()
    def hideDetails(self) -> None:
        if self._details_visible:
            self._details_visible = False
            self.detailsVisibleChanged.emit()

    @Slot()
    def closeDialog(self) -> None:
        if self._dialog_busy:
            return
        self._set_dialog_kind("")
        self._dialog_data = {}
        self._dialog_form_data = {}
        self.dialogDataChanged.emit()
        self._set_dialog_error("")

    @Slot()
    def backToForm(self) -> None:
        """确认页的"返回"回到表单并保留已输入内容；无表单时关闭对话框。"""
        if self._dialog_busy:
            return
        if not self._dialog_form_data:
            self.closeDialog()
            return
        self._dialog_data = dict(self._dialog_form_data)
        self.dialogDataChanged.emit()
        self._set_dialog_error("")

    @Slot("QVariantMap")
    def submitEdit(self, values) -> None:
        if self._dialog_kind != "edit":
            return
        index = int(self._dialog_data.get("index", self._selected_follower))
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        self.monitor.preview_edit(index, dict(values))

    @Slot("QVariantMap")
    def submitSettings(self, values) -> None:
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        self.monitor.preview_settings(dict(values))

    @Slot("QVariantMap")
    def submitProxy(self, values) -> None:
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        self.monitor.save_proxy(dict(values))

    @Slot(str, str)
    def submitImport(self, url: str, tag: str) -> None:
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        self.monitor.preview_import(url.strip(), tag.strip())

    @Slot()
    def confirmDialog(self) -> None:
        if not self._dialog_kind:
            return
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        self.monitor.confirm_pending(self._dialog_kind)

    @Slot(str)
    def requestDownloadFormats(self, url: str) -> None:
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        self.monitor.request_download_formats(url.strip())

    @Slot(int)
    def startDownload(self, format_index: int) -> None:
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        self.monitor.start_download(format_index)

    @Slot(str, str)
    def submitUpdate(self, target: str, content: str) -> None:
        self._dialog_data = {**self._dialog_data, "target": target}
        self.dialogDataChanged.emit()
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        if target in {"mpv", "ffmpeg"}:
            # 更新会原子替换工具目录；正在运行的 mpv 会锁住 exe 导致失败。
            self._stop_player()
        self.monitor.request_update(target, content)

    @Slot(str)
    def checkUpdate(self, target: str) -> None:
        if self._dialog_kind != "update":
            return
        items = [dict(item) for item in self._dialog_data.get("items", [])]
        matched = False
        for item in items:
            if item.get("value") == target:
                item.update(
                    updateStatus="checking",
                    updateHint="正在检查远端版本…",
                    actionLabel="检查中",
                    actionEnabled=False,
                    actionKind="none",
                )
                matched = True
                break
        if not matched:
            return
        self._dialog_data = {**self._dialog_data, "items": items, "target": target}
        self.dialogDataChanged.emit()
        self._set_dialog_busy(True)
        self._set_dialog_error("")
        self.monitor.request_update_check(target)

    @Slot()
    def continueUpdateCenter(self) -> None:
        if self._dialog_kind != "update" or self._dialog_busy:
            return
        self._dialog_data = {
            **self._dialog_data,
            "stage": "form",
            "progress": 0,
            "progressText": "",
        }
        self.dialogDataChanged.emit()

    @Slot()
    def windowHidden(self) -> None:
        self.append_log("窗口已隐藏到系统托盘")

    @Slot(str)
    def append_log(self, message: str) -> None:
        safe = redact_sensitive_text(str(message))
        self._log_lines.append(f"[{datetime.now():%H:%M:%S}] {safe}")
        del self._log_lines[:-800]
        self.logTextChanged.emit()

    @Slot(int, int, int, int, bool)
    def saveWindowGeometry(self, x: int, y: int, width: int, height: int, maximized: bool) -> None:
        self._ui_settings.setValue("window/x", int(x))
        self._ui_settings.setValue("window/y", int(y))
        self._ui_settings.setValue("window/width", int(width))
        self._ui_settings.setValue("window/height", int(height))
        self._ui_settings.setValue("window/maximized", bool(maximized))

    @Slot(result="QVariantMap")
    def windowGeometry(self) -> dict:
        """上次会话的窗口几何；尺寸不合理时返回 invalid 让 QML 用默认值。"""
        width = self._ui_settings.value("window/width", 0, type=int)
        height = self._ui_settings.value("window/height", 0, type=int)
        if width < 400 or height < 300:
            return {"valid": False}
        return {
            "valid": True,
            "x": self._ui_settings.value("window/x", 80, type=int),
            "y": self._ui_settings.value("window/y", 60, type=int),
            "width": width,
            "height": height,
            "maximized": self._ui_settings.value("window/maximized", False, type=bool),
        }

    @Slot(bool, int)
    def set_polling(self, active: bool, count: int) -> None:
        self._polling = active
        if count:
            self._poll_count = count
        if active:
            # 一轮成功启动说明重启后的核心已恢复健康。
            self._monitor_restarts = 0
        self._update_status()

    @Slot(str)
    def _on_monitor_fatal(self, message: str) -> None:
        self._fatal_pending = message or "未知错误"
        self.append_log(f"致命错误：{self._fatal_pending}")

    @Slot()
    def _on_monitor_stopped(self) -> None:
        if self._shutting_down or not self._fatal_pending:
            self.append_log("监控线程已停止")
            return
        message = self._fatal_pending
        self._fatal_pending = ""
        if self._monitor_restarts >= MAX_MONITOR_RESTARTS:
            self.append_log("监控核心连续异常退出，已停止自动重启")
            self.notificationRequested.emit("监控核心已停止", "多次自动重启失败；请查看日志后重启程序")
            QMessageBox.critical(
                None,
                "监控核心已停止",
                f"监控核心连续异常退出：\n{message}\n\n已停止自动重启。请查看日志后重启程序。",
            )
            return
        self._monitor_restarts += 1
        attempt = self._monitor_restarts
        self.append_log(f"监控核心将在 {MONITOR_RESTART_DELAY_MS // 1000} 秒后自动重启（第 {attempt}/{MAX_MONITOR_RESTARTS} 次）")
        self.notificationRequested.emit("监控核心异常", f"{message}；即将自动重启（第 {attempt} 次）")
        QTimer.singleShot(MONITOR_RESTART_DELAY_MS, self._restart_monitor)

    def _restart_monitor(self) -> None:
        if self._shutting_down:
            return
        self.append_log("正在重启监控核心…")
        self.monitor.start()

    def request_show(self, command: str = "show") -> None:
        if command == "show":
            self.showRequested.emit()

    def shutdown(self) -> None:
        self._shutting_down = True
        self._stop_player()
        self.monitor.stop()

    def _open_edit(self) -> None:
        if self._selected_follower < 0:
            self.append_log("请先选择一个关注项")
            return
        self._show_dialog("edit", {"stage": "loading"}, busy=True)
        self.monitor.request_edit(self._selected_follower)

    def _open_delete(self) -> None:
        if self._selected_follower < 0:
            self.append_log("请先选择一个关注项")
            return
        self._show_dialog("delete", {"stage": "loading"}, busy=True)
        self.monitor.preview_delete(self._selected_follower)

    def _open_settings(self) -> None:
        self._show_dialog("settings", {"stage": "loading"}, busy=True)
        self.monitor.request_settings()

    def _open_proxy(self) -> None:
        self._show_dialog("proxy", {"stage": "loading"}, busy=True)
        self.monitor.request_proxy()

    def _open_update(self) -> None:
        self._show_dialog("update", {"stage": "loading"}, busy=True)
        self.monitor.request_update_status()

    def _show_dialog(self, kind: str, data: dict, *, busy: bool = False) -> None:
        self._set_dialog_kind(kind)
        self._dialog_data = dict(data)
        self._dialog_form_data = {}
        self.dialogDataChanged.emit()
        self._set_dialog_busy(busy)
        self._set_dialog_error("")

    def _set_dialog_kind(self, value: str) -> None:
        if value != self._dialog_kind:
            self._dialog_kind = value
            self.dialogKindChanged.emit()

    def _set_dialog_busy(self, value: bool) -> None:
        if value != self._dialog_busy:
            self._dialog_busy = value
            self.dialogBusyChanged.emit()

    def _set_dialog_error(self, value: str) -> None:
        value = redact_sensitive_text(value)
        if value != self._dialog_error:
            self._dialog_error = value
            self.dialogErrorChanged.emit()

    @Slot(str, object)
    def _on_dialog_data(self, kind: str, payload) -> None:
        self._set_dialog_kind(kind)
        incoming = dict(payload)
        if kind == "update" and incoming.get("stage") == "checked":
            target = str(incoming.get("target") or "")
            patch = dict(incoming.get("item") or {})
            items = [dict(item) for item in self._dialog_data.get("items", [])]
            for item in items:
                if item.get("value") == target:
                    item.update(patch)
                    break
            self._dialog_data = {
                **self._dialog_data,
                "stage": "form",
                "target": target,
                "items": items,
            }
        elif kind == "update" and incoming.get("stage") in {"progress", "done"}:
            self._dialog_data = {**self._dialog_data, **incoming}
        else:
            if (
                kind == self._dialog_kind
                and str(self._dialog_data.get("stage")) == "form"
                and str(incoming.get("stage")) == "confirm"
            ):
                # 记住表单内容，确认页"返回"时原样恢复。
                self._dialog_form_data = dict(self._dialog_data)
            self._dialog_data = incoming
        self.dialogDataChanged.emit()
        self._set_dialog_busy(False)
        self._set_dialog_error("")

    @Slot(str, bool, str, object)
    def _on_operation_finished(self, kind: str, success: bool, message: str, payload) -> None:
        data = dict(payload)
        self._set_dialog_busy(False)
        if message:
            self.append_log(message)
        if success:
            self._set_dialog_error("")
            if data.get("close"):
                self.closeDialog()
            elif self._dialog_kind == kind and data.get("done"):
                done_state = {
                    **self._dialog_data,
                    "stage": "done",
                    "progress": 100,
                    "progressText": message,
                }
                if kind == "update":
                    items = [dict(item) for item in self._dialog_data.get("items", [])]
                    target = str(data.get("target") or self._dialog_data.get("target") or "")
                    if data.get("downloaded") is True:
                        for item in items:
                            if item.get("value") != target:
                                continue
                            if data.get("version"):
                                item["version"] = str(data["version"])
                                item["installed"] = True
                            item["lastUpdated"] = "刚刚"
                            if item.get("kind") in {"tool", "package"}:
                                item.update(
                                    actionLabel="检查更新",
                                    actionEnabled=True,
                                    actionKind="check",
                                    updateStatus="unchecked",
                                    updateHint="更新完成；可按需再次检查",
                                    remoteVersion="",
                                    downloadSize="",
                                )
                            break
                    done_state.update(target=target, items=items)
                self._dialog_data = done_state
                self.dialogDataChanged.emit()
        else:
            self._set_dialog_error(message or "操作失败")
            if kind == "update" and self._dialog_kind == "update":
                self._dialog_data = {
                    **self._dialog_data,
                    "stage": "failed",
                    "progressText": message or "更新失败",
                }
                self.dialogDataChanged.emit()

    @Slot(str, float, str)
    def _on_progress(self, kind: str, value: float, message: str) -> None:
        if self._dialog_kind != kind:
            return
        self._dialog_data = {
            **self._dialog_data,
            "progress": value,
            "progressText": redact_sensitive_text(message),
        }
        self.dialogDataChanged.emit()

    def _refresh_rows(self) -> None:
        rows = [
            row for row in self._snapshot.get("rows", [])
            if matches_snapshot(row, self._current_tag, self._state_filter, self._search)
        ]
        rows = sort_snapshots(rows, self._sort_column, self._sort_descending)
        self.table_model.set_rows(rows)
        visible_indices = {int(row["idx"]) for row in rows}
        if self._selected_follower not in visible_indices:
            self._selected_follower = self.table_model.first_follower_index()
            self.selectedFollowerChanged.emit()
            self._update_selected_details()
        online = sum(1 for row in self._snapshot.get("rows", []) if row.get("live"))
        total = len(self._snapshot.get("rows", []))
        self._summary_text = f"在线 {online} / 总计 {total} / 当前 {len(rows)}"
        self.summaryTextChanged.emit()
        self._update_status()

    def _update_status(self) -> None:
        if self._polling:
            text = f"● 第 {self._poll_count} 轮检测中"
        else:
            interval = self._snapshot.get("poll_interval", 60)
            health = self._snapshot.get("platform_health", {})
            unhealthy = sum(1 for value in health.values() if value != "healthy")
            text = f"○ 等待下一轮 / 间隔 {interval}s"
            if unhealthy:
                text += f" / 异常平台 {unhealthy}"
        if text != self._status_text:
            self._status_text = text
            self.statusTextChanged.emit()

    def _update_selected_details(self) -> None:
        row = next(
            (dict(item) for item in self._snapshot.get("rows", []) if item.get("idx") == self._selected_follower),
            {},
        )
        self._selected_details = row
        self.selectedDetailsChanged.emit()

    @Slot(object)
    def _on_detail_data(self, payload) -> None:
        data = dict(payload)
        if int(data.get("idx", -1)) != self._selected_follower:
            return
        self._selected_details = {**self._selected_details, **data}
        self.selectedDetailsChanged.emit()

    def _selected_row(self) -> dict | None:
        return next(
            (row for row in self._snapshot.get("rows", []) if row.get("idx") == self._selected_follower),
            None,
        )

    def _open_web(self) -> None:
        row = self._selected_row()
        if not row:
            self.append_log("请先选择一个关注项")
            return
        url = web_url_for_snapshot(row)
        if not url.startswith(("http://", "https://")):
            self.append_log("该关注项没有可直接打开的网页地址")
            return
        webbrowser.open(url)

    def _request_stream(self, purpose: str) -> None:
        if self._selected_follower < 0:
            self.append_log("请先选择一个关注项")
            return
        self.append_log("正在获取直播流…")
        self.monitor.request_stream(self._selected_follower, purpose)

    def _download_selected(self) -> None:
        row = self._selected_row()
        if not row:
            self.append_log("请先选择一个关注项")
            return
        self._show_dialog("download", {"stage": "form", "url": str(row.get("url", ""))})

    @Slot(str, object)
    def _stream_ready(self, purpose: str, payload) -> None:
        data = dict(payload)
        url = str(data.get("url", ""))
        if purpose == "copy":
            QApplication.clipboard().setText(url)
            self.append_log("直播流地址已复制")
            return
        if purpose == "play":
            self._stop_player()
            self._player_candidates = list(dict.fromkeys(str(item) for item in (data.get("urls") or [url]) if item))
            self._player_candidate_index = 0
            self._player_payload = data
            self._start_next_player_candidate(self._player_generation)

    def _start_next_player_candidate(self, generation: int) -> None:
        if generation != self._player_generation:
            return
        if self._player_candidate_index >= len(self._player_candidates):
            self._player_process = None
            self.append_log(f"mpv 的全部 {len(self._player_candidates)} 条 CDN 候选均启动失败")
            return
        url = self._player_candidates[self._player_candidate_index]
        self._player_candidate_index += 1
        try:
            self._player_process = play_url(
                url,
                title=str(self._player_payload.get("title", "Zhibo")),
                headers=dict(self._player_payload.get("headers", {})),
                proxy_url=str(self._player_payload.get("proxy", "")),
                use_cache=True,
            )
        except Exception as exc:
            self.append_log(f"CDN 候选启动失败：{exc}")
            self._start_next_player_candidate(generation)
            return
        QTimer.singleShot(1000, lambda: self._verify_player_candidate(generation))

    def _verify_player_candidate(self, generation: int) -> None:
        if generation != self._player_generation or self._player_process is None:
            return
        if self._player_process.poll() is None:
            self.append_log("已启动 mpv 播放")
            return
        code = self._player_process.returncode
        self._player_process = None
        if self._player_candidate_index < len(self._player_candidates):
            self.append_log(f"CDN 候选启动失败（退出码={code}），自动切换下一条")
        self._start_next_player_candidate(generation)

    def _stop_player(self) -> None:
        self._player_generation += 1
        self._player_candidates = []
        self._player_candidate_index = 0
        process = self._player_process
        self._player_process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            return
        # 等待退出并逐级升级到 kill，避免留下句柄或孤儿 mpv 进程。
        try:
            process.wait(timeout=2.0)
            self.append_log("已停止当前 mpv")
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.kill()
            process.wait(timeout=1.0)
            self.append_log("已强制结束当前 mpv")
        except (OSError, subprocess.TimeoutExpired):
            self.append_log("mpv 进程未能立即退出，可能仍占用播放文件")

    def _copy_selected(self) -> None:
        row = self._selected_row()
        if not row:
            self.append_log("请先选择一个关注项")
            return
        text = "\t".join(
            str(value) for value in (
                row.get("name", ""), row.get("platform", ""), row.get("title", ""),
                row.get("quality", ""), row.get("last_check", ""), row.get("error", ""),
            )
        )
        QApplication.clipboard().setText(text)
        self.append_log("已复制选中行摘要")

    def _toggle_selected_enabled(self) -> None:
        if self._selected_follower < 0:
            self.append_log("请先选择一个关注项")
            return
        self.monitor.toggle_enabled(self._selected_follower)

    def _hide_to_tray(self) -> None:
        if not self._tray_available:
            self.append_log("系统托盘不可用，窗口保持显示")
            return
        self.hideRequested.emit()

    def _cycle_filter(self) -> None:
        values = ["全部", "在线", "离线", "异常"]
        current = values.index(self._state_filter) if self._state_filter in values else 0
        self.setStateFilter(values[(current + 1) % len(values)])
