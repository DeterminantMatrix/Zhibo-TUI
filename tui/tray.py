"""TUI 系统托盘 + 终端窗口隐藏/显示。

终端应用的"托盘"方案：pystray 图标常驻，配合隐藏/显示当前控制台
窗口实现"隐藏到托盘、后台继续监控"。点击终端 X 时尽力拦截为隐藏
（SetConsoleCtrlHandler，Windows 对强制关闭有超时，属尽力而为）。
"""
from __future__ import annotations

import ctypes
import os
import threading
import ctypes.wintypes
from pathlib import Path

SW_HIDE = 0
SW_SHOW = 5
CTRL_CLOSE_EVENT = 2

_HANDLERS: list[object] = []  # 保活 ctypes 回调，防止被 GC


def console_hwnd() -> int | None:
    if os.name != "nt":
        return None
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:
        return None
    return hwnd or None


_host_hwnd: int | None = None


def capture_host_window() -> bool:
    """记录启动时的前台窗口。

    在 Windows Terminal 里运行时，GetConsoleWindow 返回的是 ConPTY
    的隐藏宿主（对它 ShowWindow 毫无效果）；真正可见的是 WT 窗口。
    启动瞬间 WT 必然在前台，记下它的句柄，隐藏/显示都作用于它。
    """
    global _host_hwnd
    hwnd = None
    if os.name == "nt":
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow() or None
        except Exception:
            hwnd = None
    if hwnd is None:
        hwnd = console_hwnd()
    _host_hwnd = hwnd
    return hwnd is not None


def _target_hwnd() -> int | None:
    return _host_hwnd or console_hwnd()


def hide_console() -> bool:
    hwnd = _target_hwnd()
    if not hwnd:
        return False
    ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
    return True


def show_console() -> bool:
    hwnd = _target_hwnd()
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.SetForegroundWindow(hwnd)
    return True


def intercept_close_button() -> bool:
    """拦截终端 X 关闭：改为隐藏窗口，进程继续在托盘中运行。"""
    if os.name != "nt":
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handler_type = ctypes.WINFUNCTYPE(ctypes.wintypes.DWORD, ctypes.wintypes.DWORD)

        def handler(event_type):
            if event_type == CTRL_CLOSE_EVENT:
                hide_console()
                return 1
            return 0

        handler_ref = handler_type(handler)
        if kernel32.SetConsoleCtrlHandler(handler_ref, 1):
            _HANDLERS.append(handler_ref)
            return True
    except Exception:
        return False
    return False


class TrayController:
    """pystray 托盘图标（显示/隐藏/退出）。"""

    def __init__(self, icon_path: Path, tooltip: str, on_show, on_hide, on_quit) -> None:
        self._icon_path = Path(icon_path)
        self._tooltip = tooltip
        self._on_show = on_show
        self._on_hide = on_hide
        self._on_quit = on_quit
        self._icon = None
        self.available = False

    def start(self) -> None:
        try:
            import pystray
            from PIL import Image

            image = Image.open(self._icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("显示窗口", self._show, default=True),
                pystray.MenuItem("隐藏窗口", self._hide),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", self._quit),
            )
            self._icon = pystray.Icon("zhibo-tui", image, self._tooltip, menu)
            threading.Thread(target=self._run, name="zhibo-tui-tray", daemon=True).start()
        except Exception:
            self.available = False

    def _run(self) -> None:
        try:
            self.available = True
            self._icon.run()
        except Exception:
            self.available = False

    def _show(self, *_args) -> None:
        self._on_show()

    def _hide(self, *_args) -> None:
        self._on_hide()

    def _quit(self, *_args) -> None:
        self._on_quit()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
