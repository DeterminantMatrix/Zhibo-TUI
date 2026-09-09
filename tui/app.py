"""ZHIBO TUI — Textual 终端界面，接 zhibo.monitor 真实后端。

UI 与监控服务同处一个 asyncio 循环；数据经 tui/backend.MonitorBridge
的回调进入界面。测试时可注入同接口的 FakeBridge。
"""
from __future__ import annotations

from datetime import datetime

from rich.markup import escape
from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static, Tab, Tabs

from tui.backend import MonitorBridge

_TONE_STYLE = {
    "ok": "green",
    "warning": "yellow",
    "error": "red",
    "muted": "dim",
    "normal": "",
}


def _status_cell(row: dict, playing: bool) -> Text:
    if not row.get("enabled", True):
        cell = Text("⊘", style="dim strike")
    elif row.get("live"):
        cell = Text("●", style="bold green")
    elif row.get("checking"):
        cell = Text("…", style="yellow")
    else:
        cell = Text("○", style="dim")
    if playing:
        cell.append(" ▶", style="bold cyan")
    return cell


class DetailScreen(ModalScreen):
    """详情弹层（只读；编辑事务在后续阶段加入）。"""

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
        width: 72;
        height: auto;
        max-height: 82%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    """

    def __init__(self, detail: dict) -> None:
        super().__init__()
        self._detail = detail

    def compose(self) -> ComposeResult:
        body = Text()
        body.append(self._detail.get("title", "详情"), style="bold")
        body.append("\n\n")
        for row in self._detail.get("rows", []):
            body.append(f"{row['label']:<10}".ljust(12))
            tone = _TONE_STYLE.get(row.get("tone", "normal"), "")
            value = row.get("value", "")
            body.append(value + "\n", style=tone or "")
        body.append("\n按 Esc 返回；编辑功能将在后续阶段加入", style="dim")
        yield Static(body, id="detailBox")

    def action_dismiss_screen(self) -> None:
        self.dismiss()


class ZhiboTui(App):
    """直播监控终端界面。"""

    TITLE = "ZHIBO 直播监控"

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
        Binding("x", "stop_player", "停止", show=False),
        Binding("d", "detail", "详情"),
        Binding("c", "copy_stream", "复制流", show=False),
        Binding("o", "open_web", "网页", show=False),
        Binding("space", "toggle_enabled", "停用/恢复"),
        Binding("l", "toggle_log", "日志"),
        Binding("r", "refresh", "刷新"),
        Binding("t", "cycle_theme", "主题", show=False),
        Binding("ctrl+up", "volume_up", "音量+", show=False),
        Binding("ctrl+down", "volume_down", "音量-", show=False),
        Binding("ctrl+m", "mute", "静音", show=False),
    ]

    THEMES = ["gruvbox", "tokyo-night", "nord", "dracula", "textual-dark"]

    def __init__(
        self,
        bridge: MonitorBridge | None = None,
        smoke: bool = False,
        startup_notes: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._bridge = bridge
        self._smoke = smoke
        self._startup_notes = list(startup_notes or [])
        self._tag = "全部"
        self._query = ""
        self._theme_index = 0
        self._playing_idx = None
        self._rows: list[dict] = []
        self._tags: list[str] = ["全部"]
        self._col_keys: dict[str, object] = {}
        self._row_keys: dict[int, object] = {}

    # ---- 布局与生命周期 ---------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="filterBar"):
            yield Tabs(id="tagTabs")
            yield Input(placeholder="/ 搜索主播、平台、标题…", id="search")
        with Horizontal(id="mainArea"):
            yield DataTable(id="streamTable", cursor_type="row", zebra_stripes=True)
            yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    async def on_mount(self) -> None:
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
        log = self.query_one("#log", RichLog)
        log.border_title = "运行日志"

        if self._bridge is None:
            self._bridge = MonitorBridge()
        bridge = self._bridge
        bridge.on_snapshot = self.apply_snapshot
        bridge.on_log = self.log_line
        bridge.on_player = self.set_player
        for note in self._startup_notes:
            self.log_line(note)
        await bridge.start()
        if self._smoke:
            self.set_timer(6.0, self.action_quit)

    async def on_unmount(self) -> None:
        if self._bridge is not None:
            await self._bridge.stop()

    # ---- 桥接回调（监控循环内同步调用） -----------------------------------

    def log_line(self, text: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self.query_one("#log", RichLog).write(f"[dim]{now}[/dim] {escape(str(text))}")

    def set_player(self, idx: int | None) -> None:
        self._playing_idx = idx
        self._refresh_status_cells()

    def apply_snapshot(self, snapshot: dict) -> None:
        self._rows = snapshot.get("rows", [])
        self._tags = snapshot.get("tags") or ["全部"]
        self._rebuild_tabs(self._tags)
        self._rebuild_table()
        online = sum(1 for r in self._rows if r.get("live"))
        total = len(self._rows)
        interval = snapshot.get("poll_interval") or "-"
        poll_part = "● 检测中…" if snapshot.get("polling") else f"○ 间隔 {interval}s"
        self.sub_title = f"在线 {online} / 总计 {total} · 第 {snapshot.get('poll_round', 0)} 轮 · {poll_part}"

    # ---- 表格 ------------------------------------------------------------

    def _visible_rows(self) -> list[dict]:
        from zhibo.viewmodel import matches_snapshot

        return [
            row
            for row in self._rows
            if matches_snapshot(row, tag=self._tag, search=self._query)
        ]

    def _rebuild_tabs(self, tags: list[str]) -> None:
        tabs = self.query_one("#tagTabs", Tabs)
        new_ids = [f"tag-{i}" for i in range(len(tags))]
        if [t.id for t in tabs.query(Tab)] == new_ids:
            return
        tabs.clear()
        for idx, tag in enumerate(tags):
            tabs.add_tab(Tab(tag, id=f"tag-{idx}"))
        if self._tag not in tags:
            self._tag = "全部"
        tabs.active = f"tag-{tags.index(self._tag)}"

    def _rebuild_table(self) -> None:
        table = self.query_one("#streamTable", DataTable)
        cursor = table.cursor_row
        table.clear()
        self._row_keys.clear()
        for row in self._visible_rows():
            row_key = table.add_row(
                _status_cell(row, row["idx"] == self._playing_idx),
                row.get("name", "-"),
                row.get("title") or "-",
                row.get("platform", "-"),
                "、".join(row.get("tags") or []) or "-",
                row.get("configured_quality") or row.get("quality") or "-",
                row.get("configured_plugin") or row.get("plugin") or "-",
                row.get("last_check", "-"),
                key=row["idx"],
            )
            self._row_keys[row["idx"]] = row_key
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    def _refresh_status_cells(self) -> None:
        for idx in self._row_keys:
            row = next((r for r in self._rows if r["idx"] == idx), None)
            if row is not None:
                self.query_one("#streamTable", DataTable).update_cell(
                    self._row_keys[idx],
                    self._col_keys["status"],
                    _status_cell(row, idx == self._playing_idx),
                )

    def _selected_idx(self) -> int | None:
        table = self.query_one("#streamTable", DataTable)
        if not table.row_count:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        return row_key.value if row_key is not None else None

    # ---- 交互动作 ---------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """表格内按 Enter 触发行选中 = 播放。"""
        row_key = event.row_key
        if row_key is not None and row_key.value is not None and self._bridge is not None:
            self._bridge.play(row_key.value)

    def action_play(self) -> None:
        idx = self._selected_idx()
        if idx is not None and self._bridge is not None:
            self._bridge.play(idx)

    def action_stop_player(self) -> None:
        if self._bridge is not None:
            self._bridge.stop_player()

    def action_detail(self) -> None:
        idx = self._selected_idx()
        if idx is None or self._bridge is None:
            return
        detail = self._bridge.get_detail(idx)
        if detail is not None:
            self.push_screen(DetailScreen(detail))

    def action_copy_stream(self) -> None:
        idx = self._selected_idx()
        if idx is not None and self._bridge is not None:
            self._bridge.copy_stream(idx)

    def action_open_web(self) -> None:
        idx = self._selected_idx()
        if idx is not None and self._bridge is not None:
            self._bridge.open_web(idx)

    def action_toggle_enabled(self) -> None:
        idx = self._selected_idx()
        if idx is not None and self._bridge is not None:
            self._bridge.toggle_enabled(idx)

    def action_volume_up(self) -> None:
        if self._bridge is not None:
            self._bridge.player_control("volume_up")

    def action_volume_down(self) -> None:
        if self._bridge is not None:
            self._bridge.player_control("volume_down")

    def action_mute(self) -> None:
        if self._bridge is not None:
            self._bridge.player_control("toggle_mute")

    def action_toggle_log(self) -> None:
        log = self.query_one("#log", RichLog)
        log.display = not log.display

    def action_refresh(self) -> None:
        if self._bridge is not None:
            self._bridge.refresh()

    def action_cycle_theme(self) -> None:
        self._theme_index = (self._theme_index + 1) % len(self.THEMES)
        self.theme = self.THEMES[self._theme_index]

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._query = event.value
            self._rebuild_table()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        tab_id = str(event.tab.id or "")
        if not tab_id.startswith("tag-"):
            return
        try:
            tag = self._tags[int(tab_id[4:])]
        except (IndexError, ValueError):
            return
        if tag != self._tag:
            self._tag = tag
            self._rebuild_table()
