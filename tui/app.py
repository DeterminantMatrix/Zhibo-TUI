"""ZHIBO TUI — Textual 终端界面，接 zhibo.monitor 真实后端。

UI 与监控服务同处一个 asyncio 循环；数据经 tui/backend.MonitorBridge
的回调进入界面。测试时可注入同接口的 FakeBridge。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from rich.cells import cell_len
from rich.markup import escape
from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static, Tab, Tabs

from tui.backend import MonitorBridge
from tui.screens import (
    ConfirmScreen,
    DownloadScreen,
    EditScreen,
    ImportScreen,
    ProxyScreen,
    SettingsScreen,
    UpdateCenterScreen,
)

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
    """详情弹层（只读；编辑事务在后续阶段加入）。

    用两列 DataTable 呈现，彻底避免中文 label 的对齐错位。
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "关闭"),
        Binding("d", "dismiss_screen", "关闭", show=False),
        Binding("e", "edit", "编辑"),
    ]

    CSS = """
    DetailScreen {
        align: center middle;
        background: $background 60%;
    }
    #detailBox {
        width: 92;
        max-height: 82%;
        border: round $accent;
        background: $surface;
        padding: 1;
        overflow: auto;
    }
    #detailTable {
        height: auto;
        border: none;
        background: $surface;
    }
    #detailHint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    VALUE_CLIP = 64

    def __init__(self, detail: dict, idx: int) -> None:
        super().__init__()
        self._detail = detail
        self._idx = idx

    def compose(self) -> ComposeResult:
        with Vertical(id="detailBox"):
            yield DataTable(id="detailTable", show_cursor=False, zebra_stripes=True)
            yield Static("按 Esc 返回 · 按 e 进入编辑", id="detailHint")

    def on_mount(self) -> None:
        table = self.query_one("#detailTable", DataTable)
        table.border_title = self._detail.get("title", "详情")
        table.add_column("字段", key="field", width=14)
        table.add_column("内容", key="value", width=64)
        for row in self._detail.get("rows", []):
            tone = _TONE_STYLE.get(row.get("tone", "normal"), "")
            value = self._clip(row.get("value", ""), self.VALUE_CLIP)
            table.add_row(
                Text(row.get("label", ""), style="bold"),
                Text(value, style=tone) if tone else Text(value),
            )

    def action_edit(self) -> None:
        self.dismiss()
        self.app.open_edit(self._idx)

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        """按显示宽度截断（CJK 一格一字宽），超出补省略号。"""
        text = str(text or "")
        if cell_len(text) <= limit:
            return text
        out = ""
        width = 0
        for ch in text:
            width += cell_len(ch)
            if width > limit - 2:
                break
            out += ch
        return out + "…"

    def action_dismiss_screen(self) -> None:
        self.dismiss()


class ZhiboTui(App):
    """直播监控终端界面。"""

    TITLE = "ZHIBO 直播监控"

    # 用户要求隐藏底栏的 palette 提示（Ctrl+P 一并停用）。
    ENABLE_COMMAND_PALETTE = False

    def get_key_display(self, binding: Binding) -> str:
        # 底栏只显示动作名，不显示快捷键字母。
        return ""

    CSS = """
    * {
        scrollbar-size-horizontal: 1;
        scrollbar-size-vertical: 1;
    }
    #filterBar {
        height: auto;
    }
    #tagTabs {
        width: 1fr;
        border: none;
        padding: 0;
    }
    #liveCount {
        width: auto;
        margin: 0 1 0 0;
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
        overflow-x: hidden;
    }
    #log {
        width: 34;
        border: round $accent 30%;
        padding: 0 1;
        overflow-x: hidden;
    }
    """

    # 列序（用户定稿）：状态 | 标签 | 平台 | 主播 | 标题 | 画质 | 插件 | 检测
    # cell_padding=0（间距已含在列宽内）：列之间无缝，分隔行的灰线才能连通。
    # 列宽总和（84）刻意收窄到常规窗口内，横向滚动条不出现。
    TABLE_COLUMNS = (
        ("status", "状态", 5),
        ("tags", "标签", 7),
        ("platform", "平台", 9),
        ("name", "主播", 13),
        ("title", "标题", 21),
        ("quality", "画质", 6),
        ("plugin", "插件", 12),
        ("last_check", "检测", 11),
    )

    BINDINGS = [
        Binding("enter", "play", "播放"),
        Binding("d", "detail", "详情"),
        Binding("e", "edit", "编辑"),
        Binding("space", "toggle_enabled", "停用/恢复"),
        Binding("s", "settings", "设置"),
        Binding("p", "proxy", "代理"),
        Binding("i", "import_room", "导入"),
        Binding("u", "updates", "更新"),
        Binding("w", "download", "下载"),
        Binding("r", "refresh", "刷新"),
        Binding("l", "toggle_log", "日志"),
        Binding("q", "quit", "退出"),
        Binding("/", "focus_search", "搜索", show=False),
        Binding("x", "stop_player", "停止", show=False),
        Binding("c", "copy_stream", "复制流", show=False),
        Binding("o", "open_web", "网页", show=False),
        Binding("delete", "delete_row", "删除", show=False),
        Binding("n", "toggle_notifications", "通知", show=False),
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
        self._playing: set[int] = set()
        self._rows: list[dict] = []
        self._tags: list[str] = ["全部"]
        self._col_keys: dict[str, object] = {}
        self._row_keys: dict[int, object] = {}

    # ---- 布局与生命周期 ---------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="filterBar"):
            yield Tabs(id="tagTabs")
            yield Static("", id="liveCount")
            yield Input(placeholder="/ 搜索主播、平台、标题…", id="search")
        with Horizontal(id="mainArea"):
            yield DataTable(id="streamTable", cursor_type="row", zebra_stripes=True)
            yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one(
            "#streamTable", DataTable
        )
        table.cell_padding = 0  # 间距已含在列宽内；分隔灰线靠无缝单元格连通
        for key, label, width in self.TABLE_COLUMNS:
            self._col_keys[key] = table.add_column(label, key=key, width=width)
        log = self.query_one("#log", RichLog)
        log.border_title = "运行日志"

        if self._bridge is None:
            self._bridge = MonitorBridge()
        bridge = self._bridge
        bridge.on_snapshot = self.apply_snapshot
        bridge.on_log = self.log_line
        bridge.on_players = self.set_players
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

    def set_players(self, idxs: list[int]) -> None:
        self._playing = set(idxs)
        self._refresh_status_cells()

    def apply_snapshot(self, snapshot: dict) -> None:
        self._rows = snapshot.get("rows", [])
        self._tags = snapshot.get("tags") or ["全部"]
        self._rebuild_tabs(self._tags)
        self._rebuild_table()
        live = sum(1 for r in self._rows if r.get("live"))
        # 标签栏旁只标正在开播数（用户要求：不显示总数）。
        self.query_one("#liveCount", Static).update(
            Text(f"● {live}", style="bold green")
        )
        interval = snapshot.get("poll_interval") or "-"
        poll_part = "● 检测中…" if snapshot.get("polling") else f"○ 间隔 {interval}s"
        self.sub_title = f"第 {snapshot.get('poll_round', 0)} 轮 · {poll_part}"

    # ---- 表格 ------------------------------------------------------------

    def _visible_rows(self) -> list[dict]:
        from zhibo.viewmodel import matches_snapshot

        return [
            row
            for row in self._rows
            if matches_snapshot(row, tag=self._tag, search=self._query)
        ]

    def _rebuild_tabs(self, tags: list[str]) -> None:
        # clear/add 是异步完成的，同步连用会撞出"同 ID widget 已存在"；
        # 放进 exclusive worker，await 清理完成后再逐个加入。
        self.run_worker(
            self._rebuild_tabs_worker(tags),
            group="tag-tabs",
            exclusive=True,
            exit_on_error=False,
        )

    async def _rebuild_tabs_worker(self, tags: list[str]) -> None:
        try:
            tabs = self.query_one("#tagTabs", Tabs)
        except Exception:
            return  # 界面已关闭
        live = sum(1 for r in self._rows if r.get("live"))
        # 每个标签一个该标签下正在开播的数量；"全部"即总开播数。
        counts = {}
        for tag in tags:
            if tag == "全部":
                counts[tag] = live
            else:
                counts[tag] = sum(
                    1
                    for r in self._rows
                    if r.get("live") and tag in (r.get("tags") or [])
                )
        desired = [(f"tag-{i}", f"{tag} {counts[tag]}") for i, tag in enumerate(tags)]
        if [(t.id, str(t.label)) for t in tabs.query(Tab)] == desired:
            return
        await tabs.clear()
        for i, tag in enumerate(tags):
            label = Text(tag)
            label.append(f" {counts[tag]}", style="green")
            await tabs.add_tab(Tab(label, id=f"tag-{i}"))
        if self._tag not in tags:
            self._tag = "全部"
        tabs.active = f"tag-{tags.index(self._tag)}"

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        """按字符数截断标题类内容（中文一格一字，10~15 字观感）。"""
        text = str(text or "")
        return text if len(text) <= limit else text[:limit] + "…"

    def _rebuild_table(self) -> None:
        table = self.query_one("#streamTable", DataTable)
        cursor = table.cursor_row
        table.clear()
        self._row_keys.clear()
        prev_live: bool | None = None
        sep = 0
        for row in self._visible_rows():
            is_live = bool(row.get("live"))
            if prev_live is True and not is_live:
                # 开播与未开播分组之间的灰色分隔线（终端行高固定，无法做半行）。
                sep += 1
                table.add_row(
                    *[
                        Text("─" * width, style="#808080")
                        for _key, _label, width in self.TABLE_COLUMNS
                    ],
                    key=f"sep-{sep}",
                )
            cells: list = (
                _status_cell(row, row["idx"] in self._playing),
                "、".join(row.get("tags") or []) or "-",
                row.get("platform", "-"),
                row.get("name", "-"),
                self._clip(row.get("title") or "-", 12),
                row.get("configured_quality") or row.get("quality") or "-",
                row.get("configured_plugin") or row.get("plugin") or "-",
                row.get("last_check", "-"),
            )
            if not is_live:
                # 未开播整行置暗；状态列本身已是暗色圆点，保持不动。
                cells = [
                    cell if isinstance(cell, Text) else Text(str(cell), style="dim")
                    for cell in cells
                ]
            row_key = table.add_row(*cells, key=row["idx"])
            self._row_keys[row["idx"]] = row_key
            prev_live = is_live
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    def _refresh_status_cells(self) -> None:
        for idx in self._row_keys:
            row = next((r for r in self._rows if r["idx"] == idx), None)
            if row is not None:
                self.query_one("#streamTable", DataTable).update_cell(
                    self._row_keys[idx],
                    self._col_keys["status"],
                    _status_cell(row, idx in self._playing),
                )

    def _selected_idx(self) -> int | None:
        table = self.query_one("#streamTable", DataTable)
        if not table.row_count:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        if row_key is None or not isinstance(row_key.value, int):
            return None  # 分隔行等非数据行
        return row_key.value

    # ---- 交互动作 ---------------------------------------------------------

    def _modal_open(self) -> bool:
        """弹层打开时屏蔽主界面动作（q/空格等全局键不该穿透）。"""
        return len(self.screen_stack) > 1

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_play(self) -> None:
        if self._modal_open():
            return
        idx = self._selected_idx()
        if idx is None or self._bridge is None:
            return
        # 已在播放的行再按一次 = 停止该直播间（多播放器语义）。
        if idx in self._playing:
            self._bridge.stop_one(idx)
        else:
            self._bridge.play(idx)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """表格内按 Enter 触发行选中 = 播放/停止切换。"""
        if self._modal_open():
            return
        row_key = event.row_key
        if (
            row_key is None
            or not isinstance(row_key.value, int)
            or self._bridge is None
        ):
            return
        idx = row_key.value
        if idx in self._playing:
            self._bridge.stop_one(idx)
        else:
            self._bridge.play(idx)

    def action_stop_player(self) -> None:
        if self._bridge is not None:
            self._bridge.stop_all_players()

    def action_detail(self) -> None:
        if self._modal_open():
            return
        idx = self._selected_idx()
        if idx is None or self._bridge is None:
            return
        detail = self._bridge.get_detail(idx)
        if detail is not None:
            self.push_screen(DetailScreen(detail, idx=idx))

    def action_edit(self) -> None:
        if self._modal_open():
            return
        idx = self._selected_idx()
        if idx is not None:
            self.open_edit(idx)

    def open_edit(self, idx: int) -> None:
        if self._bridge is None:
            return
        payload = self._bridge.get_edit_payload(idx)
        if payload is None:
            self.log_line("选中的直播间已不存在")
            return
        self.push_screen(EditScreen(self._bridge, payload))

    def action_settings(self) -> None:
        if self._modal_open() or self._bridge is None:
            return

        async def open_settings() -> None:
            values = await self._bridge.get_settings()
            self.push_screen(SettingsScreen(self._bridge, values))

        asyncio.create_task(open_settings())

    def action_proxy(self) -> None:
        if self._modal_open() or self._bridge is None:
            return

        async def open_proxy() -> None:
            values = await self._bridge.get_proxy()
            self.push_screen(ProxyScreen(self._bridge, values))

        asyncio.create_task(open_proxy())

    def action_import_room(self) -> None:
        if self._modal_open():
            return
        self.push_screen(ImportScreen(self._bridge))

    def action_delete_row(self) -> None:
        if self._modal_open() or self._bridge is None:
            return
        idx = self._selected_idx()
        if idx is None:
            return

        async def flow() -> None:
            ok, text = await self._bridge.preview_delete(idx)
            if not ok:
                self.log_line(text)
                return
            self.push_screen(
                ConfirmScreen("确认删除直播间？", text, "确认删除", self._bridge.confirm_delete)
            )

        asyncio.create_task(flow())

    def action_toggle_notifications(self) -> None:
        if self._modal_open() or self._bridge is None:
            return
        self._bridge.toggle_notifications()

    def action_updates(self) -> None:
        if self._modal_open() or self._bridge is None:
            return
        self.push_screen(UpdateCenterScreen(self._bridge))

    def action_download(self) -> None:
        if self._modal_open() or self._bridge is None:
            return
        self.push_screen(DownloadScreen(self._bridge))

    def action_copy_stream(self) -> None:
        if self._modal_open():
            return
        idx = self._selected_idx()
        if idx is not None and self._bridge is not None:
            self._bridge.copy_stream(idx)

    def action_open_web(self) -> None:
        if self._modal_open():
            return
        idx = self._selected_idx()
        if idx is not None and self._bridge is not None:
            self._bridge.open_web(idx)

    def action_toggle_enabled(self) -> None:
        if self._modal_open():
            return
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
        if self._modal_open():
            return
        log = self.query_one("#log", RichLog)
        log.display = not log.display

    def action_refresh(self) -> None:
        if self._modal_open():
            return
        if self._bridge is not None:
            self._bridge.refresh()

    def action_cycle_theme(self) -> None:
        if self._modal_open():
            return
        self._theme_index = (self._theme_index + 1) % len(self.THEMES)
        self.theme = self.THEMES[self._theme_index]

    def action_quit(self) -> None:
        if self._modal_open():
            return
        self.exit()

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
