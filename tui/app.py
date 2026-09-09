"""ZHIBO TUI 骨架 — Textual P0。

假数据驱动的界面骨架：表格 / 标签筛选 / 搜索 / 详情弹层 / 日志面板 /
状态模拟器。全部键位与将来接 zhibo.monitor 后保持一致。
"""
from __future__ import annotations

import random
from datetime import datetime

from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static, Tab, Tabs

from tui.fake_data import TAGS, build_followers, fake_title

_STATUS_GLYPH = {
    "live": Text("●", style="bold green"),
    "offline": Text("○", style="dim"),
    "disabled": Text("⊘", style="dim strike"),
    "checking": Text("…", style="yellow"),
}


def _status_cell(follower: dict) -> Text:
    if not follower["enabled"]:
        return _STATUS_GLYPH["disabled"]
    if follower["live"]:
        return _STATUS_GLYPH["live"]
    return _STATUS_GLYPH["offline"]


class DetailScreen(ModalScreen):
    """详情与修改弹层（P0 只读展示；接后端后变为编辑表单）。"""

    BINDINGS = [
        Binding("escape", "dismiss_screen", "返回"),
        Binding("d", "dismiss_screen", "返回", show=False),
    ]

    CSS = """
    DetailScreen {
        align: center middle;
        background: $background 60%;
    }
    #detailBox {
        width: 64;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #detailBox .detail-title {
        text-style: bold;
        margin-bottom: 1;
    }
    """

    def __init__(self, follower: dict) -> None:
        super().__init__()
        self._follower = follower

    def compose(self) -> ComposeResult:
        f = self._follower
        tags = "、".join(f["tags"])
        state = "● 直播中" if f["live"] else "○ 未开播"
        body = "\n".join(
            [
                f"状态：{state}",
                f"平台：{f['platform']}    插件：{f['plugin']}    画质：{f['quality']}",
                f"标签：{tags}",
                f"标题：{f['title'] or '未提供'}",
                f"上次检测：{f['last_check']}",
                "",
                "（P0 骨架：编辑与保存将在接入后端后开放；按 Esc 返回）",
            ]
        )
        yield Static(body, id="detailBox")

    def action_dismiss_screen(self) -> None:
        self.dismiss()


class ZhiboTui(App):
    """直播监控终端界面骨架。"""

    TITLE = "ZHIBO 直播监控"
    SUB_TITLE = "TUI 骨架 · 假数据"

    CSS = """
    * {
        scrollbar-size: 1 1;
    }
    #filterBar {
        height: auto;
    }
    #tagTabs {
        width: 1fr;
        border: none;
        padding: 0;
    }
    #search {
        width: 36;
        height: 1;
        border: none;
        padding: 0 1;
        margin: 0 1 0 0;
        background: $surface;
    }
    #mainArea {
        height: 1fr;
    }
    #streamTable {
        width: 1fr;
        height: 1fr;
        border: round $accent;
    }
    #log {
        width: 34;
        border: round $accent 30%;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("/", "focus_search", "搜索"),
        Binding("enter", "play", "播放"),
        Binding("d", "detail", "详情"),
        Binding("l", "toggle_log", "日志"),
        Binding("r", "refresh", "刷新"),
        Binding("t", "cycle_theme", "主题", show=False),
    ]

    THEMES = ["gruvbox", "tokyo-night", "nord", "dracula", "textual-dark"]

    def __init__(self) -> None:
        super().__init__()
        self._followers = build_followers()
        self._tag = "全部"
        self._query = ""
        self._theme_index = 0
        self._poll_round = 0
        self._col_keys: dict[str, object] = {}
        self._row_keys: dict[int, object] = {}

    # ---- 布局 ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="filterBar"):
            yield Tabs(
                *[Tab(tag, id=f"tag-{idx}") for idx, tag in enumerate(TAGS)],
                id="tagTabs",
            )
            yield Input(placeholder="/ 搜索主播、平台、标题…", id="search")
        with Horizontal(id="mainArea"):
            yield DataTable(id="streamTable", cursor_type="row", zebra_stripes=True)
            yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#streamTable", DataTable)
        # 列序（用户定稿）：状态 | 主播 | 标题 | 平台 | 标签 | 画质 | 插件 | 检测
        for key, label in (
            ("status", "状态"),
            ("name", "主播"),
            ("title", "标题"),
            ("platform", "平台"),
            ("tags", "标签"),
            ("quality", "画质"),
            ("plugin", "插件"),
            ("last_check", "检测"),
        ):
            self._col_keys[key] = table.add_column(label, key=key)
        self._rebuild_table()
        log = self.query_one("#log", RichLog)
        log.border_title = "运行日志"
        log.write("[b]ZHIBO TUI 骨架已启动[/b]（假数据，未接监控核心）")
        log.write("键位：enter 播放 · d 详情 · / 搜索 · r 刷新 · l 日志 · t 主题 · q 退出")
        self.log_line("模拟监控核心就绪，共 44 个关注项")
        self.set_interval(2.5, self._simulate_tick)

    # ---- 表格 ------------------------------------------------------------

    def _visible_followers(self) -> list[dict]:
        query = self._query.strip().lower()
        rows = []
        for f in self._followers:
            if self._tag != "全部" and self._tag not in f["tags"]:
                continue
            if query:
                hay = " ".join(
                    [f["name"], f["platform"], f["title"], f["plugin"], " ".join(f["tags"])]
                ).lower()
                if query not in hay:
                    continue
            rows.append(f)
        return rows

    def _rebuild_table(self) -> None:
        table = self.query_one("#streamTable", DataTable)
        cursor = table.cursor_row
        table.clear()
        self._row_keys.clear()
        for f in self._visible_followers():
            row_key = table.add_row(
                _status_cell(f),
                f["name"],
                f["title"] or "-",
                f["platform"],
                "、".join(f["tags"]),
                f["quality"],
                f["plugin"],
                f["last_check"],
                key=f["idx"],
            )
            self._row_keys[f["idx"]] = row_key
        if table.row_count:
            table.move_cursor(
                row=min(cursor, table.row_count - 1),
            )

    def _selected_follower(self) -> dict | None:
        table = self.query_one("#streamTable", DataTable)
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        if row_key is None:
            return None
        idx = row_key.value
        return next((f for f in self._followers if f["idx"] == idx), None)

    def _update_row(self, follower: dict) -> None:
        table = self.query_one("#streamTable", DataTable)
        row_key = self._row_keys.get(follower["idx"])
        if row_key is None:
            return
        updates = {
            "status": _status_cell(follower),
            "title": follower["title"] or "-",
            "last_check": follower["last_check"],
        }
        for col_key, value in updates.items():
            table.update_cell(row_key, self._col_keys[col_key], value)

    # ---- 交互动作 ---------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_play(self) -> None:
        follower = self._selected_follower()
        if follower is None:
            return
        if not follower["live"]:
            self.log_line(f"× {follower['name']} 当前未开播，无法播放")
            return
        self.log_line(f"▶ [b]{follower['name']}[/b] 已交给 mpv 播放（P0 假动作）")
        self.notify(f"已调用 mpv 播放 {follower['name']}（P0 假动作）", title="播放")

    def action_detail(self) -> None:
        follower = self._selected_follower()
        if follower is None:
            return
        self.push_screen(DetailScreen(follower))

    def action_toggle_log(self) -> None:
        log = self.query_one("#log", RichLog)
        log.display = not log.display

    def action_refresh(self) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        for f in self._followers:
            if f["enabled"]:
                f["last_check"] = now
        for f in self._visible_followers():
            self._update_row(f)
        self.log_line(f"手动刷新完成 {now}（P0 假动作）")

    def action_cycle_theme(self) -> None:
        self._theme_index = (self._theme_index + 1) % len(self.THEMES)
        self.theme = self.THEMES[self._theme_index]

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._query = event.value
            self._rebuild_table()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        tab_id = str(event.tab.id or "")
        if tab_id.startswith("tag-"):
            try:
                self._tag = TAGS[int(tab_id[4:])]
            except (KeyError, ValueError):
                return
            self._rebuild_table()

    # ---- 状态模拟器（接后端后整体删除） -----------------------------------

    def log_line(self, text: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self.query_one("#log", RichLog).write(f"[dim]{now}[/dim] {text}")

    def _simulate_tick(self) -> None:
        if random.random() < 0.5:
            self._poll_round += 1
            self.log_line(f"开始第 {self._poll_round} 轮检测…")
        now = datetime.now().strftime("%H:%M:%S")
        for f in self._followers:
            f["last_check"] = now
        flips: list[str] = []
        for f in random.sample(self._followers, k=random.randint(1, 3)):
            if not f["enabled"]:
                continue
            f["live"] = not f["live"]
            f["title"] = fake_title(f["platform"]) if f["live"] else ""
            flips.append(("↑ " if f["live"] else "↓ ") + f["name"] + (" 开播" if f["live"] else " 下播"))
            self._update_row(f)
        for line in flips:
            self.log_line(line)
        online = sum(1 for f in self._followers if f["live"])
        self.sub_title = f"在线 {online} / 总计 {len(self._followers)} · 第 {self._poll_round} 轮"
        if flips:
            self.log_line(f"轮询结束 {now}")
