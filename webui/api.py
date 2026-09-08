"""JS 桥 — 前端通过 window.pywebview.api.* 调用的 Python 接口。

P0/P1：窗口控制 + 快照/刷新/画质/插件。
P2：详情编辑/设置/代理/导入 四个事务对话框。
P3：播放(外部 mpv)/停止/复制流/打开网页/停用恢复/删除/通知开关/播放器控制。
"""
from __future__ import annotations


class ZhiboApi:
    def __init__(self) -> None:
        self._window = None
        self._service = None
        self._quitting = False

    def attach(self, window) -> None:
        self._window = window

    def attach_service(self, service) -> None:
        self._service = service

    @property
    def quitting(self) -> bool:
        return self._quitting

    # ---- 窗口控制 -------------------------------------------------------

    def ping(self) -> str:
        return "pong"

    def minimizeWindow(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def maximizeWindow(self) -> None:
        if self._window is not None:
            self._window.maximize()

    def restoreWindow(self) -> None:
        if self._window is not None:
            self._window.restore()

    def hideToTray(self) -> None:
        if self._window is not None:
            self._window.hide()

    def quitApp(self) -> None:
        self._destroy_window()

    def quit_from_tray(self) -> None:
        """托盘退出 / 冒烟自检：跳过"隐藏到托盘"语义，直接结束。"""
        self._destroy_window()

    def _destroy_window(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        window = self._window
        if window is None:
            return
        try:
            window.destroy()
        except Exception:
            pass

    # ---- P1：监控数据 ---------------------------------------------------

    def getSnapshot(self) -> dict:
        if self._service is None:
            return {}
        return self._service.snapshot()

    def refresh(self) -> None:
        if self._service is not None:
            self._service.refresh()

    def setQuality(self, follower_index: int, quality: str) -> None:
        if self._service is not None:
            self._service.set_quality(int(follower_index), str(quality))

    def setPlugin(self, follower_index: int, plugin: str) -> None:
        if self._service is not None:
            self._service.set_plugin(int(follower_index), str(plugin))

    # ---- P2：事务对话框 ---------------------------------------------------

    def loadDetails(self, follower_index: int) -> None:
        if self._service is not None:
            self._service.load_details(int(follower_index))

    def previewEdit(self, follower_index: int, values: dict) -> None:
        if self._service is not None:
            self._service.preview_edit(int(follower_index), dict(values or {}))

    def confirmDialog(self, kind: str) -> None:
        if self._service is not None:
            self._service.confirm_dialog(str(kind))

    def loadSettings(self) -> None:
        if self._service is not None:
            self._service.load_settings()

    def previewSettings(self, values: dict) -> None:
        if self._service is not None:
            self._service.preview_settings(dict(values or {}))

    def loadProxy(self) -> None:
        if self._service is not None:
            self._service.load_proxy()

    def saveProxy(self, values: dict) -> None:
        if self._service is not None:
            self._service.save_proxy(dict(values or {}))

    def testProxy(self, values: dict) -> None:
        if self._service is not None:
            self._service.test_proxy(dict(values or {}))

    def previewImport(self, url: str, tag: str) -> None:
        if self._service is not None:
            self._service.preview_import(str(url or ""), str(tag or ""))

    # ---- P3：播放与行操作 --------------------------------------------------

    def play(self, follower_index: int) -> None:
        if self._service is not None:
            self._service.play(int(follower_index))

    def stopPlayer(self) -> None:
        if self._service is not None:
            self._service.stop_player()

    def copyStream(self, follower_index: int) -> None:
        if self._service is not None:
            self._service.copy_stream(int(follower_index))

    def openWeb(self, follower_index: int) -> None:
        if self._service is not None:
            self._service.open_web(int(follower_index))

    def toggleEnabled(self, follower_index: int) -> None:
        if self._service is not None:
            self._service.toggle_enabled(int(follower_index))

    def playerControl(self, action: str) -> None:
        if self._service is not None:
            self._service.player_control(str(action))

    def toggleNotifications(self) -> None:
        if self._service is not None:
            self._service.toggle_notifications()
