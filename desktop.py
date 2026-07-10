"""Desktop integration helpers: media playback and notifications."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import platform
import shutil
import subprocess
import threading

SW_HIDE = 0
SW_SHOW = 5
SW_RESTORE = 9
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

_terminal_window_hwnd = None
_window_exstyles: dict[int, int] = {}


def is_mpv_available() -> bool:
    return shutil.which("mpv") is not None


def play_url(
    stream_url: str,
    title: str = "",
    referrer: str = "",
    headers: dict[str, str] | None = None,
    proxy_url: str = "",
    use_cache: bool = False,
) -> subprocess.Popen:
    """Play a stream with mpv."""
    cmd = ["mpv", stream_url]
    if use_cache:
        cmd.extend([
            "--cache=yes",
            "--demuxer-max-bytes=268435456",
            "--demuxer-readahead-secs=15",
        ])
    else:
        cmd.append("--no-cache")
    cmd.extend([
        "--stream-lavf-o=reconnect=1",
        "--stream-lavf-o=reconnect_streamed=1",
    ])
    if title:
        cmd.append(f"--title={title}")

    if proxy_url:
        cmd.append(f"--http-proxy={proxy_url}")

    headers = headers or {}
    user_agent = headers.get("User-Agent")
    if user_agent:
        cmd.append(f"--user-agent={user_agent}")

    referrer = referrer or headers.get("Referer", "")
    if referrer:
        cmd.append(f"--referrer={referrer}")

    for key in ("Origin", "Accept"):
        value = headers.get(key)
        if value:
            cmd.append(f"--http-header-fields={key}: {value}")

    try:
        return subprocess.Popen(cmd)
    except FileNotFoundError:
        raise RuntimeError("未找到 mpv，请先安装 mpv 播放器：https://mpv.io/installation/") from None


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _configure_win32_api() -> None:
    if not _is_windows():
        return
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HANDLE,
        wintypes.HINSTANCE,
        ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreatePopupMenu.restype = wintypes.HANDLE
    user32.LoadIconW.argtypes = None
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, LPARAM]
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, LPARAM]
    user32.DefWindowProcW.restype = LPARAM
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindowAsync.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        ctypes.c_void_p,
    ]
    user32.TrackPopupMenu.restype = wintypes.BOOL


def remember_terminal_window() -> bool:
    """Remember the visible top-level terminal host window for later hide/show."""
    global _terminal_window_hwnd
    if not _is_windows():
        return False
    _configure_win32_api()
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if hwnd and ctypes.windll.user32.IsWindowVisible(hwnd):
        root = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)
        _terminal_window_hwnd = root or hwnd
        return True
    return False


def _window_candidates() -> list[int]:
    user32 = ctypes.windll.user32
    handles = []
    console_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if console_hwnd:
        handles.append(console_hwnd)
        root = user32.GetAncestor(console_hwnd, GA_ROOT)
        if root:
            handles.append(root)
    if _terminal_window_hwnd:
        handles.append(_terminal_window_hwnd)
        root = user32.GetAncestor(_terminal_window_hwnd, GA_ROOT)
        if root:
            handles.append(root)

    unique = []
    seen = set()
    for hwnd in handles:
        if hwnd and hwnd not in seen and user32.IsWindow(hwnd):
            unique.append(hwnd)
            seen.add(hwnd)
    return unique


def _apply_taskbar_hidden_style(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    if hwnd not in _window_exstyles:
        _window_exstyles[hwnd] = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    style = (_window_exstyles[hwnd] & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    user32.SetWindowPos(
        hwnd,
        None,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


def _restore_taskbar_style(hwnd: int) -> None:
    if hwnd not in _window_exstyles:
        return
    user32 = ctypes.windll.user32
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, _window_exstyles.pop(hwnd))
    user32.SetWindowPos(
        hwnd,
        None,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


def hide_console_window() -> bool:
    """Hide the current console window on Windows."""
    if not _is_windows():
        return False
    _configure_win32_api()
    if not _terminal_window_hwnd:
        remember_terminal_window()
    hidden = False
    for hwnd in _window_candidates():
        _apply_taskbar_hidden_style(hwnd)
        ctypes.windll.user32.ShowWindowAsync(hwnd, SW_HIDE)
        hidden = True
    return hidden


def show_console_window() -> bool:
    """Show and focus the current console window on Windows."""
    if not _is_windows():
        return False
    _configure_win32_api()
    shown = False
    focus_hwnd = None
    for hwnd in _window_candidates():
        _restore_taskbar_style(hwnd)
        ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW)
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        shown = True
        focus_hwnd = hwnd
    if focus_hwnd:
        ctypes.windll.user32.SetForegroundWindow(focus_hwnd)
    return shown


class MinimizeToTrayMonitor:
    """Poll terminal window state and hide it to tray when minimized."""

    def __init__(self, interval_seconds: float = 0.2):
        self.interval_seconds = interval_seconds
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def start(self) -> None:
        if not _is_windows():
            return
        if self._thread and self._thread.is_alive():
            return
        _configure_win32_api()
        self._stopped.clear()
        self._thread = threading.Thread(target=self._run, name="zhibo-minimize-to-tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        while not self._stopped.wait(self.interval_seconds):
            try:
                for hwnd in _window_candidates():
                    if user32.IsIconic(hwnd):
                        hide_console_window()
                        break
            except Exception:
                continue


def show_already_running_message() -> None:
    """Tell the user that an existing instance must be closed first."""
    message = "直播监控工具已在运行。\n\n请先关闭第一个窗口，或在系统托盘中退出后再打开。"
    title = "不能打开第二个"
    if _is_windows():
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
    else:
        print(f"{title}: {message}")


class TrayIcon:
    """Small Windows tray icon implemented with the Win32 shell API."""

    WM_USER = 0x0400
    WM_TRAYICON = WM_USER + 20
    WM_DESTROY = 0x0002
    WM_CLOSE = 0x0010
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205

    NIM_ADD = 0x00000000
    NIM_MODIFY = 0x00000001
    NIM_DELETE = 0x00000002
    NIF_MESSAGE = 0x00000001
    NIF_ICON = 0x00000002
    NIF_TIP = 0x00000004
    NIF_INFO = 0x00000010
    NIIF_INFO = 0x00000001

    TPM_RIGHTBUTTON = 0x0002
    TPM_RETURNCMD = 0x0100
    MF_STRING = 0x0000
    IDI_APPLICATION = 32512
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x00000010

    MENU_SHOW = 1001
    MENU_HIDE = 1002
    MENU_EXIT = 1003

    def __init__(
        self,
        tooltip: str,
        on_show=None,
        on_hide=None,
        on_quit=None,
        icon_path: str | Path | None = None,
    ):
        self.tooltip = tooltip[:127]
        self.on_show = on_show
        self.on_hide = on_hide
        self.on_quit = on_quit
        self.icon_path = Path(icon_path) if icon_path else None
        self._thread: threading.Thread | None = None
        self._hwnd = None
        self._icon_handle = None
        self._owns_icon_handle = False
        self._ready = threading.Event()
        self._class_name = "ZhiboTrayWindow"
        self._message_map = {}
        self._wndproc_ref = None

    def start(self) -> bool:
        if not _is_windows():
            return False
        _configure_win32_api()
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._run, name="zhibo-tray", daemon=True)
        self._thread.start()
        return self._ready.wait(timeout=3)

    def stop(self) -> None:
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, self.WM_CLOSE, 0, 0)

    def show_balloon(self, title: str, message: str) -> bool:
        if not self._hwnd:
            return False
        data = self._notify_data()
        data.uFlags = self.NIF_INFO
        data.szInfoTitle = title[:63]
        data.szInfo = message[:255]
        data.dwInfoFlags = self.NIIF_INFO
        return bool(ctypes.windll.shell32.Shell_NotifyIconW(self.NIM_MODIFY, ctypes.byref(data)))

    def _run(self) -> None:
        user32 = ctypes.windll.user32

        wndclass = WNDCLASS()
        self._wndproc_ref = WNDPROC(self._wndproc)
        wndclass.lpfnWndProc = self._wndproc_ref
        wndclass.lpszClassName = self._class_name
        wndclass.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)
        user32.RegisterClassW(ctypes.byref(wndclass))

        self._hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            wndclass.hInstance,
            None,
        )
        self._add_icon()
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_TRAYICON:
            if lparam == self.WM_LBUTTONDBLCLK and self.on_show:
                self.on_show()
                return 0
            if lparam == self.WM_RBUTTONUP:
                self._show_menu(hwnd)
                return 0
        if msg == self.WM_CLOSE:
            ctypes.windll.user32.DestroyWindow(hwnd)
            return 0
        if msg == self.WM_DESTROY:
            self._delete_icon()
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self, hwnd) -> None:
        user32 = ctypes.windll.user32
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, self.MF_STRING, self.MENU_SHOW, "显示窗口")
        user32.AppendMenuW(menu, self.MF_STRING, self.MENU_HIDE, "隐藏至托盘")
        user32.AppendMenuW(menu, self.MF_STRING, self.MENU_EXIT, "退出")

        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(hwnd)
        command = user32.TrackPopupMenu(
            menu,
            self.TPM_RIGHTBUTTON | self.TPM_RETURNCMD,
            point.x,
            point.y,
            0,
            hwnd,
            None,
        )
        user32.DestroyMenu(menu)

        if command == self.MENU_SHOW and self.on_show:
            self.on_show()
        elif command == self.MENU_HIDE and self.on_hide:
            self.on_hide()
        elif command == self.MENU_EXIT and self.on_quit:
            self.on_quit()

    def _add_icon(self) -> bool:
        data = self._notify_data()
        data.uFlags = self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP
        data.uCallbackMessage = self.WM_TRAYICON
        data.hIcon = self._load_icon()
        data.szTip = self.tooltip
        return bool(ctypes.windll.shell32.Shell_NotifyIconW(self.NIM_ADD, ctypes.byref(data)))

    def _delete_icon(self) -> None:
        if self._hwnd:
            data = self._notify_data()
            ctypes.windll.shell32.Shell_NotifyIconW(self.NIM_DELETE, ctypes.byref(data))
            self._hwnd = None
        if self._icon_handle and self._owns_icon_handle:
            ctypes.windll.user32.DestroyIcon(self._icon_handle)
        self._icon_handle = None
        self._owns_icon_handle = False

    def _load_icon(self):
        if self.icon_path and self.icon_path.exists():
            icon = ctypes.windll.user32.LoadImageW(
                None,
                str(self.icon_path),
                self.IMAGE_ICON,
                0,
                0,
                self.LR_LOADFROMFILE,
            )
            if icon:
                self._icon_handle = icon
                self._owns_icon_handle = True
                return icon
        icon = ctypes.windll.user32.LoadIconW(None, self.IDI_APPLICATION)
        self._icon_handle = icon
        self._owns_icon_handle = False
        return icon

    def _notify_data(self):
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        return data


def notify(title: str, message: str, enabled: bool = True) -> bool:
    """Send a best-effort Windows desktop notification."""
    if not enabled or platform.system() != "Windows":
        return False

    # The notification text can originate from a remote stream title.  Keep it
    # out of PowerShell source code so a quote/newline cannot become a command.
    script = """
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipTitle = [Environment]::GetEnvironmentVariable('ZHIBO_NOTIFY_TITLE', 'Process')
$n.BalloonTipText = [Environment]::GetEnvironmentVariable('ZHIBO_NOTIFY_MESSAGE', 'Process')
$n.Visible = $true
$n.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$n.Dispose()
"""
    try:
        env = os.environ.copy()
        env["ZHIBO_NOTIFY_TITLE"] = title[:63]
        env["ZHIBO_NOTIFY_MESSAGE"] = message[:255]
        encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        return True
    except Exception:
        return False


HBRUSH = wintypes.HANDLE
HCURSOR = wintypes.HANDLE
LPARAM = getattr(wintypes, "LPARAM", ctypes.c_ssize_t)

WNDPROC = ctypes.WINFUNCTYPE(LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]
