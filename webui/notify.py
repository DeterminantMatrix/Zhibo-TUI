"""P3 托盘通知 — winotify 原生 toast + 点击唤起并播放。

Windows toast 的点击激活只能走协议 URL：启动时把 ``zhibo://`` 注册到
HKCU（无需管理员），toast 的 launch 指向 ``zhibo://play/<idx>``；
系统因此拉起 ``notify_play.pyw``，后者经单实例命令通道把 ``play:<idx>``
转发给运行中的主实例（没有实例则正常启动应用）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


PROTOCOL_SCHEME = "zhibo"
LAUNCHER_NAME = "notify_play.pyw"


def notify_uri_for(idx: int) -> str:
    return f"{PROTOCOL_SCHEME}://play/{int(idx)}"


def parse_notify_uri(uri: str) -> int:
    """zhibo://play/3 → 3；无法解析返回 -1。"""
    text = str(uri or "").strip()
    prefix = f"{PROTOCOL_SCHEME}://play/"
    if not text.casefold().startswith(prefix):
        return -1
    tail = text[len(prefix):].strip().rstrip("/")
    try:
        idx = int(tail)
    except ValueError:
        return -1
    return idx if idx >= 0 else -1


def launcher_path() -> Path:
    return Path(__file__).resolve().parent.parent / LAUNCHER_NAME


def protocol_open_command() -> str:
    """注册表 open 命令：pythonw + 启动器 + 协议 URL 参数。"""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpreter}" "{launcher_path()}" "%1"'


def ensure_protocol_registered() -> bool:
    """把 zhibo:// 协议写入 HKCU；失败静默（通知退化为不可点击）。"""
    if os.name != "nt":
        return False
    try:
        import winreg

        base = rf"Software\Classes\{PROTOCOL_SCHEME}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, base, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, f"URL:{PROTOCOL_SCHEME} Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, rf"{base}\shell\open\command", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, protocol_open_command())
        return True
    except OSError:
        return False


class Notifier:
    """开播通知；dry_run 供测试与无 toast 环境记录调用。"""

    APP_ID = "直播监控工具"

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = bool(dry_run)
        self.sent: list[dict] = []

    def notify_live(self, idx: int, name: str, title: str) -> None:
        message = str(title or "").strip() or "正在直播"
        launch = notify_uri_for(idx)
        if self.dry_run:
            self.sent.append({"idx": idx, "name": name, "message": message, "launch": launch})
            return
        try:
            from winotify import Notification, audio

            toast = Notification(
                app_id=self.APP_ID,
                title=f"{name} 开播了",
                msg=message,
                duration="short",
                launch=launch,
            )
            toast.set_audio(audio.Silent, loop=False)
            toast.show()
        except Exception:
            # 通知是锦上添花：失败只影响提醒，不影响监控。
            pass
