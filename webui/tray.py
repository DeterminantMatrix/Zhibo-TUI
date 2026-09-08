"""系统托盘 — pystray 实现（显示/隐藏/退出，双击显示）。"""
from __future__ import annotations

import threading
from pathlib import Path

import pystray
from PIL import Image


class TrayController:
    def __init__(self, icon_path: Path, tooltip: str, on_show, on_hide, on_quit) -> None:
        self._icon_path = Path(icon_path)
        self._tooltip = tooltip
        self._on_show = on_show
        self._on_hide = on_hide
        self._on_quit = on_quit
        self._icon: pystray.Icon | None = None
        self.available = False

    def start(self) -> None:
        if self._icon is not None:
            return
        try:
            image = Image.open(self._icon_path)
        except Exception:
            image = Image.new("RGB", (16, 16), "navy")
        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self._show, default=True),
            pystray.MenuItem("隐藏到托盘", lambda *_: self._on_hide()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )
        self._icon = pystray.Icon("zhibo-web", image, self._tooltip, menu)
        threading.Thread(target=self._run, name="zhibo-web-tray", daemon=True).start()

    def _run(self) -> None:
        try:
            self.available = True
            self._icon.run()
        except Exception:
            self.available = False

    def _show(self, *_args) -> None:
        self._on_show()

    def _quit(self, *_args) -> None:
        self._on_quit()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
