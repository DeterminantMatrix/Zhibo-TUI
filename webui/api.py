"""JS 桥 — 前端通过 window.pywebview.api.* 调用的 Python 接口。

P0 只包含窗口控制；监控/配置/播放等业务方法在后续阶段加入。
"""
from __future__ import annotations


class ZhiboApi:
    def __init__(self) -> None:
        self._window = None
        self._quitting = False

    def attach(self, window) -> None:
        self._window = window

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
