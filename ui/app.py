"""Textual TUI 界面主应用"""
import asyncio
import subprocess
import sys
import unicodedata
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Header, Input, ProgressBar, RadioButton, RadioSet, RichLog, Static, TabbedContent, TabPane, TextArea
from textual.binding import Binding

from app_logging import LOG_FILE, get_logger
from plugins.fs1_plugin import get_fs_site_url
from monitor import MonitorService, FollowerStatus
from models import Follower

# ── 快捷键按钮定义 ──────────────────────────────────────────
SHORTCUT_BUTTONS = [
    ("通知", "toggle_notifications"),
    ("刷新", "refresh"),
    ("网页", "open_web"),
    ("复制流", "copy_stream_url"),
    ("更新", "open_fs1_update"),
    ("下载", "download_video"),
    ("复制选中", "copy_selection"),
    ("筛选", "cycle_state_filter"),
    ("托盘", "hide_to_tray"),
    ("退出", "quit"),
]

# 列宽限制（中文字符算2宽）
COL_WIDTHS = {
    "状态": 4,
    "标签": 7,
    "主播": 14,
    "平台": 9,
    "标题": 24,
    "画质": 8,
    "检测": 8,
    "健康": 6,
    "插件": 10,
    "错误": 20,
}

PLATFORM_DISPLAY_NAMES = {
    "fs1": "飞速",
}

def _disp_width(s: str) -> int:
    """计算字符串显示宽度（中文=2，英文=1）"""
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w

def _truncate(s: str, max_width: int) -> str:
    """按显示宽度截断字符串"""
    if _disp_width(s) <= max_width:
        return s
    if max_width <= 0:
        return ""
    if max_width == 1:
        return "…"

    result, cur = "", 0
    target_width = max_width - 1
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur + cw > target_width:
            break
        result += ch
        cur += cw
    return result + "…"

def _display_platform(platform: str) -> str:
    return PLATFORM_DISPLAY_NAMES.get(platform, platform)

def _web_url_for_follower(follower: Follower) -> str:
    url = follower.url
    if url.startswith(("http://", "https://")):
        return url

    if follower.plugin == "fs1" or follower.platform == "fs1":
        extra = follower.extra if isinstance(follower.extra, dict) else {}
        query = {"room_id": url, "sport_id": extra.get("sport_id", "1")}
        return get_fs_site_url() + "/broadcast/details?" + urlencode(query)

    return url

def _update_fs1_script_path() -> Path:
    return Path(__file__).parent.parent / "update_fs1.cmd"


class LiveTable(DataTable):
    """直播状态表格 — 覆盖 Enter 键防止被 DataTable 吞噬"""

    def action_select_cursor(self) -> None:
        self.app.action_play_selected()  # type: ignore[attr-defined]


class LogPanel(RichLog):
    """右侧日志面板 — 可鼠标拖选文字"""
    can_focus = True


class StatusBar(Static):
    """动态状态栏"""


class SearchInput(Input):
    """搜索输入框"""


class ImportUrlScreen(ModalScreen[dict[str, str] | None]):
    """导入直播间信息的弹窗"""

    CSS = """
    ImportUrlScreen {
        align: center middle;
    }

    #import_box {
        width: 72;
        height: 12;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #import_tag,
    #import_url {
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="import_box"):
            yield Static("导入直播间")
            yield Input(placeholder="标签，例如 ASMR / LOL / POE", id="import_tag")
            yield Input(placeholder="粘贴虎牙/斗鱼/B站/抖音/Twitch 直播间网址", id="import_url")
            yield Static("Enter 下一步/导入，Esc 取消")

    def on_mount(self) -> None:
        self.query_one("#import_tag", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "import_tag":
            self.query_one("#import_url", Input).focus()
            return
        if event.input.id == "import_url":
            tag = self.query_one("#import_tag", Input).value.strip()
            url = event.value.strip()
            self.dismiss({"tag": tag, "url": url})

    def key_escape(self) -> None:
        self.dismiss(None)


class DownloadScreen(ModalScreen[dict | None]):
    """YouTube 视频下载弹窗 — 输入 URL → 选择格式 → 下载"""

    CSS = """
    DownloadScreen {
        align: center middle;
    }

    #download_box {
        width: 76;
        height: auto;
        max-height: 30;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #download_url {
        margin-top: 1;
    }

    #format_loading {
        margin-top: 1;
        height: 1;
    }

    #format_list {
        margin-top: 1;
        height: auto;
        max-height: 18;
        overflow-y: auto;
    }

    #format_list RadioButton {
        padding: 0 1;
    }

    #download_confirm {
        margin-top: 1;
        width: 20;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="download_box"):
            yield Static("下载 YouTube 视频", id="download_title")
            yield Input(placeholder="粘贴 YouTube 视频/直播链接...", id="download_url")
            yield Static("", id="format_loading")
            yield RadioSet(id="format_list")
            yield Button("开始下载", id="download_confirm", variant="primary")
            yield Static("↑↓ 选择格式  Enter/点击按钮下载  Esc 取消", id="download_help")

    def on_mount(self) -> None:
        self.query_one("#download_url", Input).focus()
        self.query_one("#format_list", RadioSet).display = False
        self.query_one("#download_confirm", Button).display = False
        self.query_one("#download_help", Static).display = False
        self._formats: list = []

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "download_url":
            return
        url = event.value.strip()
        if not url:
            return
        # 禁用输入，开始获取格式
        event.input.disabled = True
        self.query_one("#download_title", Static).update("正在获取可用格式...")
        asyncio.create_task(self._load_formats(url))

    async def _load_formats(self, url: str) -> None:
        from plugins import get_plugin
        from plugins.yt_dlp_plugin import FormatInfo

        plugin = get_plugin("yt_dlp")
        if plugin is None:
            self.query_one("#format_loading", Static).update("[red]yt-dlp 插件未加载[/red]")
            return

        try:
            formats = await plugin.list_formats(url)
        except Exception as e:
            self.query_one("#format_loading", Static).update(f"[red]获取格式失败: {e}[/red]")
            return

        if not formats:
            self.query_one("#format_loading", Static).update("[red]未找到可用视频格式[/red]")
            return

        self._formats = formats
        radio_set = self.query_one("#format_list", RadioSet)
        radio_set.remove_children()

        # 默认选中第一个（通常是最低画质），用户可上下选择
        for i, fmt in enumerate(formats):
            radio_set.mount(RadioButton(fmt.label, id=f"fmt_{i}", value=(i == 0)))

        self.query_one("#download_title", Static).update(f"选择下载格式（共 {len(formats)} 个）")
        self.query_one("#format_loading", Static).display = False
        radio_set.display = True
        self.query_one("#download_confirm", Button).display = True
        self.query_one("#download_help", Static).display = True
        radio_set.focus()

    def key_escape(self) -> None:
        self.dismiss(None)

    def key_enter(self) -> None:
        """Enter 键：如果 RadioSet 已显示则触发下载"""
        radio_set = self.query_one("#format_list", RadioSet)
        if radio_set.display:
            self._confirm_download()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "download_confirm":
            self._confirm_download()

    def _confirm_download(self) -> None:
        radio_set = self.query_one("#format_list", RadioSet)
        selected = radio_set.pressed_button
        if selected is None:
            return
        try:
            idx = int(selected.id.split("_")[-1])
            fmt = self._formats[idx]
        except (IndexError, ValueError):
            return
        self.dismiss({"url": self.query_one("#download_url", Input).value.strip(), "format": fmt})


class UpdateScreen(ModalScreen[dict | None]):
    """程序内更新弹窗。"""

    CSS = """
    UpdateScreen {
        align: center middle;
    }

    #update_box {
        width: 76;
        height: auto;
        max-height: 28;
        border: solid $accent;
        background: $surface;
        padding: 1 2;
    }

    #update_option_list {
        margin-top: 1;
        height: auto;
    }

    #update_option_list RadioButton {
        padding: 0 1;
    }

    #fs1_curl {
        margin-top: 1;
        height: 9;
        border: solid $accent;
    }

    #update_buttons {
        margin-top: 1;
        height: 3;
    }

    #update_confirm,
    #update_cancel {
        width: 14;
        margin-right: 1;
    }

    #update_hint {
        margin-top: 1;
        height: 1;
    }
    """

    OPTIONS = {
        "update_streamlink": "streamlink",
        "update_streamget": "streamget",
        "update_yt_dlp": "yt-dlp",
        "update_fs1": "fs1",
    }

    def compose(self) -> ComposeResult:
        with Vertical(id="update_box"):
            yield Static("更新（Python 依赖更新后需要重启应用）", id="update_title")
            with RadioSet(id="update_option_list"):
                yield RadioButton("1. streamlink", id="update_streamlink", value=True)
                yield RadioButton("2. streamget", id="update_streamget")
                yield RadioButton("3. yt-dlp", id="update_yt_dlp")
                yield RadioButton("4. fs1", id="update_fs1")
            yield TextArea(
                "",
                id="fs1_curl",
                language=None,
                soft_wrap=True,
                placeholder="选择 fs1 后，在这里粘贴最新 FS /v1/room 请求 curl。",
            )
            yield Static("", id="update_hint")
            with Horizontal(id="update_buttons"):
                yield Button("开始更新", id="update_confirm", variant="primary")
                yield Button("取消", id="update_cancel")

    def on_mount(self) -> None:
        self.query_one("#fs1_curl", TextArea).display = False
        self.query_one("#update_hint", Static).update("选择要更新的项目，执行输出会显示在右侧日志。")
        self.query_one("#update_option_list", RadioSet).focus()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        is_fs1 = event.pressed.id == "update_fs1"
        self.query_one("#fs1_curl", TextArea).display = is_fs1
        hint = "粘贴 curl 后点击开始更新，配置会写入 sports/rooms.yaml。" if is_fs1 else "点击开始更新后，将升级选中的 Python 包；完成后请重启应用。"
        self.query_one("#update_hint", Static).update(hint)
        if is_fs1:
            self.query_one("#fs1_curl", TextArea).focus()

    def key_escape(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "update_cancel":
            self.dismiss(None)
            return
        if event.button.id == "update_confirm":
            self._confirm_update()

    def _confirm_update(self) -> None:
        selected = self.query_one("#update_option_list", RadioSet).pressed_button
        if selected is None or selected.id not in self.OPTIONS:
            return

        target = self.OPTIONS[selected.id]
        curl = self.query_one("#fs1_curl", TextArea).text.strip()
        if target == "fs1" and not curl:
            self.query_one("#update_hint", Static).update("[red]请先粘贴 FS /v1/room 请求 curl。[/red]")
            self.query_one("#fs1_curl", TextArea).focus()
            return

        self.dismiss({"target": target, "curl": curl})


class ZhiboApp(App):
    """直播监控 TUI 主应用"""

    CSS = """
    #main-area {
        height: 1fr;
    }

    #bottom-bar {
        height: 1;
    }

    #import_button {
        width: 8;
        min-width: 8;
        height: 1;
        border: none;
        padding: 0 1;
        color: $accent;
        background: $panel;
    }

    #table-area {
        width: 3fr;
    }

    #log-area {
        width: 1fr;
        border-left: solid $accent;
    }

    LogPanel {
        height: 1fr;
        border: none;
        background: transparent;
        padding: 0 1;
    }

    #download_progress {
        height: 1;
        margin: 0 1;
        display: none;
    }

    #download_progress Bar {
        background: $accent;
    }


    StatusBar {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text;
    }

    #shortcut_bar {
        width: 1fr;
        height: 1;
        background: $panel;
    }

    .shortcut-btn {
        min-width: 4;
        height: 1;
        border: none;
        padding: 0 1;
        background: transparent;
        color: $text-muted;
    }

    .shortcut-btn:hover {
        color: $accent;
        background: $boost;
    }

    SearchInput {
        height: 3;
        border: solid $accent;
        padding: 0 1;
        margin: 0 1 1 1;
    }

    TabbedContent {
        height: auto;
    }

    TabbedContent Tab {
        padding: 0 1;
    }

    TabPane {
        padding: 0;
    }

    LiveTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "next_tab", "切换分组"),
        Binding("r", "refresh", "手动刷新"),
        Binding("t", "quit", "退出"),
        Binding("f", "open_web", "网页打开"),
        Binding("c", "copy_stream_url", "复制流地址"),
        Binding("u", "open_fs1_update", "更新"),
        Binding("n", "toggle_notifications", "通知开关"),
        Binding("x", "stop_player", "停止播放"),
        Binding("j", "import_url", "导入"),
        Binding("d", "download_video", "下载视频"),
        Binding("h", "hide_to_tray", "隐藏托盘"),
        Binding("o", "cycle_state_filter", "筛选状态"),
        Binding("enter", "play_selected", "播放"),
        Binding("up,k", "cursor_up", "上移"),
        Binding("down", "cursor_down", "下移"),
        Binding("ctrl+shift+c", "copy_selection", "复制选中"),
    ]

    def __init__(self, config_path: str | None = None):
        super().__init__()
        self._monitor = MonitorService(config_path)
        self._poll_task: asyncio.Task | None = None
        self._refresh_task: asyncio.Task | None = None
        self._logs: list[str] = []
        self._current_tag: str | None = None
        self._poll_count_counter = 0
        self._tags: list[str] = []
        self._tag_to_id: dict[str, str] = {}
        self._id_to_tag: dict[str, str] = {}
        # 行号 → follower 索引映射（每个标签独立），避免 DataTable key 复用问题
        self._row_map: dict[str, list[int]] = {}
        self._player_process: subprocess.Popen | None = None
        self._search_text = ""
        self._state_filter = "全部"
        self._last_log_text = ""
        self._restart_required = False
        self._next_poll_at: datetime | None = None
        self._tray_icon = None
        self._minimize_monitor = None
        self._file_logger = get_logger("zhibo.ui")

    def _build_tag_index(self) -> None:
        """构建 tag ↔ safe_id 映射"""
        self._tags = ["全部"] + self._monitor.all_tags
        for i, tag in enumerate(self._tags):
            sid = f"tab-{i}"
            self._tag_to_id[tag] = sid
            self._id_to_tag[sid] = tag

    def _tab_id(self, tag: str) -> str:
        return self._tag_to_id.get(tag, "")

    def _table_id(self, tag: str) -> str:
        return "table-" + self._tab_id(tag)

    def _set_current_tag_from_pane_id(self, pane_id: str | None) -> None:
        tag = self._id_to_tag.get(pane_id or "", "全部")
        self._current_tag = None if tag == "全部" else tag

    def _current_tag_name(self) -> str:
        return self._current_tag or "全部"

    def _check_mpv_available(self) -> None:
        from desktop import is_mpv_available, is_potplayer_available

        if is_mpv_available():
            self._add_log("mpv 检测通过")
        else:
            self._add_log("警告：未找到 mpv，播放功能不可用")
        if is_potplayer_available():
            self._add_log("PotPlayer 检测通过")
        else:
            self._add_log("警告：未找到 PotPlayer，Twitch 播放功能不可用")

    def _update_status_bar(self) -> None:
        try:
            items = self._monitor.get_by_tag(self._current_tag)
            total = len(items)
            online = sum(1 for _, s in items if s.live_info.is_live)
            errors = sum(1 for _, s in items if s.error)
            if self._next_poll_at:
                seconds = max(0, int((self._next_poll_at - datetime.now()).total_seconds()))
            else:
                seconds = self._monitor.poll_interval
            search = f" 搜索:{self._search_text}" if self._search_text else ""
            restart = " 需重启" if self._restart_required else ""
            notify = "开" if self._monitor.cfg.notifications_enabled else "关"
            text = (
                f"分组:{self._current_tag_name()}  在线:{online}/{total}  "
                f"错误:{errors} 通知:{notify} 筛选:{self._state_filter}  下次刷新:{seconds}s{search}{restart}"
            )
            self.query_one("#status_bar", StatusBar).update(text)
        except Exception:
            pass

    def compose(self) -> ComposeResult:
        self._build_tag_index()
        yield Header(show_clock=True)
        with Horizontal(id="main-area"):
            with Vertical(id="table-area"):
                with TabbedContent():
                    for tag in self._tags:
                        with TabPane(tag, id=self._tab_id(tag)):
                            yield LiveTable(id=self._table_id(tag))
            with Vertical(id="log-area"):
                yield LogPanel(id="log_panel", max_lines=500)
                yield ProgressBar(id="download_progress", total=100, show_eta=False)
                yield SearchInput(placeholder="输入主播、平台、标签或标题过滤...", id="search_input")
        yield StatusBar(id="status_bar")
        with Horizontal(id="bottom-bar"):
            yield Button("导入", id="import_button", compact=True, flat=True, tooltip="导入直播间")
            with Horizontal(id="shortcut_bar"):
                for label, action in SHORTCUT_BUTTONS:
                    yield Button(label, id=f"btn_{action}", classes="shortcut-btn")

    def on_mount(self) -> None:
        """启动后初始化表格并开始轮询"""
        self._file_logger.info("应用启动，日志文件=%s", LOG_FILE)
        for tag in self._tags:
            table = self.query_one(f"#{self._table_id(tag)}", LiveTable)
            table.cursor_type = "row"
            for col_name, width in COL_WIDTHS.items():
                table.add_column(col_name, width=width)
            self._refresh_table(table, tag)

        self._monitor.on_status_change(self._on_status_change)
        self._monitor.on_error(self._on_monitor_error)
        self._monitor.on_poll_start(self._on_poll_start)
        self._monitor.on_poll_end(self._on_poll_end)
        self._check_mpv_available()
        self._start_tray_icon()
        self.set_interval(1, self._update_status_bar)
        self._poll_task = asyncio.create_task(self._monitor.run())
        self._update_status_bar()

    def _start_tray_icon(self) -> None:
        from desktop import MinimizeToTrayMonitor, TrayIcon, remember_terminal_window

        remember_terminal_window()
        self._tray_icon = TrayIcon(
            "直播监控工具",
            on_show=self._show_window_from_any_thread,
            on_hide=self._hide_window_from_any_thread,
            on_quit=self._quit_from_any_thread,
            icon_path=Path(__file__).parent / "assets" / "tray.ico",
        )
        if self._tray_icon.start():
            self._add_log("托盘图标已启用，双击可显示窗口")
        else:
            self._tray_icon = None
        self._minimize_monitor = MinimizeToTrayMonitor()
        self._minimize_monitor.start()

    def _hide_window_from_any_thread(self) -> None:
        from desktop import hide_console_window

        hide_console_window()

    def _show_window_from_any_thread(self) -> None:
        from desktop import show_console_window

        show_console_window()

    def _quit_from_any_thread(self) -> None:
        self.call_from_thread(self.exit)

    def handle_instance_command(self, command: str) -> None:
        if command == "show":
            self._show_window_from_any_thread()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """同步鼠标/键盘切换后的当前分组，避免播放时使用旧表格行映射。"""
        self._set_current_tag_from_pane_id(event.pane.id if event.pane else None)
        self._refresh_current_table()

    async def _on_poll_start(self, count: int) -> None:
        self._add_log(f"开始第 {count} 轮检测...")

    async def _on_poll_end(self) -> None:
        self._refresh_current_table()
        online = sum(1 for _, s in self._monitor.get_by_tag(self._current_tag) if s.live_info.is_live)
        total = len(self._monitor.get_by_tag(self._current_tag))
        self._add_log(f"检测完成 · {online}/{total} 在线")
        self._next_poll_at = datetime.now() + timedelta(seconds=self._monitor.poll_interval)
        self._update_status_bar()

    def _refresh_all_tables(self) -> None:
        """刷新当前可见表格；切换标签时会按需重建对应内容。"""
        self._refresh_current_table()

    def _poll_count(self) -> int:
        self._poll_count_counter += 1
        return self._poll_count_counter

    def _add_log(self, msg: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        log_panel = self.query_one("#log_panel", LogPanel)
        log_panel.write(f"[{now}] {msg}")
        # 保存最后一条可复制的纯文本（去掉 RichLog 标记）
        clean = msg.replace("[red]", "").replace("[/red]", "").replace("[dim]", "").replace("[/dim]", "")
        self._last_log_text = clean
        self._file_logger.info(clean)

    def _refresh_table(self, table: LiveTable, tag: str) -> None:
        """刷新指定表格 — 开播在上按标签→平台排序，未开播在下"""
        old_row_map = self._row_map.get(tag, [])
        old_cursor = table.cursor_row
        selected_idx = None
        if old_row_map and 0 <= old_cursor < len(old_row_map):
            selected_idx = old_row_map[old_cursor]

        table.clear()
        items = self._monitor.get_by_tag(None if tag == "全部" else tag)
        items = [(idx, status) for idx, status in items if self._matches_search(status)]
        sorted_items = sorted(
            items,
            key=lambda x: (
                not x[1].live_info.is_live,
                ", ".join(x[1].follower.tags),
                x[1].follower.platform,
            )
        )
        live_items = [(i, s) for i, s in sorted_items if s.live_info.is_live]
        offline_items = [(i, s) for i, s in sorted_items if not s.live_info.is_live]

        row_map: list[int] = []

        for idx, status in live_items:
            self._add_follower_row(table, idx, status, dim=False)
            row_map.append(idx)

        if live_items and offline_items:
            for _ in range(2):
                table.add_row(*[""] * len(COL_WIDTHS))
                row_map.append(-1)

        for idx, status in offline_items:
            self._add_follower_row(table, idx, status, dim=True)
            row_map.append(idx)

        self._row_map[tag] = row_map

        if selected_idx is not None and selected_idx >= 0:
            try:
                new_row = row_map.index(selected_idx)
                table.move_cursor(row=new_row)
            except ValueError:
                pass

    def _matches_search(self, status: FollowerStatus) -> bool:
        if self._state_filter == "在线" and not status.live_info.is_live:
            return False
        if self._state_filter == "离线" and (status.live_info.is_live or status.error):
            return False
        if self._state_filter == "异常" and not status.error:
            return False
        if not self._search_text:
            return True
        f = status.follower
        haystack = " ".join(
            [
                f.name,
                f.platform,
                f.plugin,
                ", ".join(f.tags),
                status.live_info.anchor_name,
                status.live_info.title,
                status.error,
            ]
        ).casefold()
        return self._search_text.casefold() in haystack

    def _add_follower_row(self, table: LiveTable, idx: int, status, dim: bool) -> None:
        f = status.follower
        if status.error:
            live_icon = "[red]●[/red]" if status.live_info.is_live else "[red]○[/red]"
        else:
            live_icon = "[green]●[/green]" if status.live_info.is_live else "[dim]○[/dim]"
        tags_text = ", ".join(f.tags)
        title_text = status.live_info.title or "-"
        platform_text = _display_platform(status.live_info.extra.get("platform") or f.platform or "-")
        plugin_text = status.live_info.extra.get("plugin_used") or f.plugin
        quality_text = status.live_info.quality_name or f.quality or "-"
        check_text = status.last_check.strftime("%H:%M:%S") if status.last_check else "-"
        error_text = status.error or status.live_info.extra.get("error", "-")
        health_text = status.metadata_health

        def _cell(text: str, max_w: int) -> str:
            s = _truncate(text, max_w)
            if status.error and text != live_icon:
                return f"[red]{s}[/red]"
            return f"[dim]{s}[/dim]" if dim else s

        table.add_row(
            live_icon,
            _cell(tags_text, COL_WIDTHS["标签"]),
            _cell(f.name, COL_WIDTHS["主播"]),
            _cell(platform_text, COL_WIDTHS["平台"]),
            _cell(title_text, COL_WIDTHS["标题"]),
            _cell(quality_text, COL_WIDTHS["画质"]),
            _cell(check_text, COL_WIDTHS["检测"]),
            _cell(health_text, COL_WIDTHS["健康"]),
            _cell(plugin_text, COL_WIDTHS["插件"]),
            _cell(error_text, COL_WIDTHS["错误"]),
        )

    def _refresh_current_table(self) -> None:
        """刷新当前选中标签的表格"""
        tag = self._current_tag or "全部"
        try:
            table = self.query_one(f"#{self._table_id(tag)}", LiveTable)
            self._refresh_table(table, tag)
        except Exception as e:
            self._add_log(f"刷新当前表格失败: {e}")

    async def _on_status_change(self, idx: int, status: FollowerStatus) -> None:
        """主播状态变化回调"""
        f = status.follower
        if status.live_info.is_live:
            self._add_log(f"↑ {f.name} 开播了！")
            if not status.is_initial_result:
                self._notify_live(f, status)
        else:
            self._add_log(f"↓ {f.name} 下播了")
        self._refresh_all_tables()
        self._update_status_bar()

    async def _on_monitor_error(self, message: str) -> None:
        self._add_log(message)

    def _notify_live(self, follower: Follower, status: FollowerStatus) -> None:
        from desktop import notify

        title = f"{follower.name} 开播了"
        message = status.live_info.title or follower.url
        if not notify(title, message, self._monitor.cfg.notifications_enabled):
            self._add_log("桌面通知未发送")

    def action_next_tab(self) -> None:
        """切换到下一个标签"""
        tabs = self.query_one(TabbedContent)
        pane_ids = [p.id for p in tabs.query(TabPane) if p.id]
        if not pane_ids:
            return
        current = tabs.active or ""
        try:
            idx = pane_ids.index(current)
            next_idx = (idx + 1) % len(pane_ids)
        except ValueError:
            next_idx = 0
        tabs.active = pane_ids[next_idx]
        self._set_current_tag_from_pane_id(pane_ids[next_idx])
        self._refresh_current_table()

    def action_refresh(self) -> None:
        """手动刷新"""
        if self._refresh_task is not None and not self._refresh_task.done():
            self._add_log("手动刷新已在进行中")
            return
        if self._monitor.is_polling:
            self._add_log("后台检测进行中，本次手动刷新已跳过")
            return
        self._refresh_task = asyncio.create_task(self._do_refresh())

    def action_cycle_state_filter(self) -> None:
        """循环切换全部、在线、离线与异常关注项。"""
        filters = ["全部", "在线", "离线", "异常"]
        self._state_filter = filters[(filters.index(self._state_filter) + 1) % len(filters)]
        self._add_log(f"状态筛选：{self._state_filter}")
        self._refresh_current_table()
        self._update_status_bar()

    def action_copy_selection(self) -> None:
        """复制鼠标选中文字（优先），否则复制最后一条日志"""
        log_panel = self.query_one("#log_panel", LogPanel)
        try:
            sel = log_panel.selection
            if sel is not None:
                text = str(sel)
                if text.strip():
                    self.copy_to_clipboard(text)
                    self._add_log("已复制选中文字")
                    return
        except Exception:
            pass
        # 回退：复制最后一条日志
        if self._last_log_text:
            self.copy_to_clipboard(self._last_log_text)
            self._add_log("已复制日志")
        else:
            self._add_log("暂无内容可复制")

    def on_input_changed(self, SearchInput_Changed) -> None:
        # Check if the changed input is the search input
        if SearchInput_Changed.input.id != "search_input":
            return
        self._search_text = SearchInput_Changed.value.strip()
        self._refresh_all_tables()
        self._update_status_bar()

    def on_input_submitted(self, event) -> None:
        if getattr(event.input, 'id', None) == "search_input":
            self._refresh_current_table()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "import_button":
            self.action_import_url()
            return
        # 快捷键按钮 → 转发到对应 action
        if bid.startswith("btn_"):
            action_name = bid[4:]
            action_method = getattr(self, f"action_{action_name}", None)
            if action_method:
                action_method()

    def action_import_url(self) -> None:
        self.push_screen(ImportUrlScreen(), self._handle_import_url)

    def action_download_video(self) -> None:
        """打开 YouTube 视频下载弹窗"""
        self.push_screen(DownloadScreen(), self._handle_download)

    def _handle_download(self, data: dict | None) -> None:
        if not data:
            return
        url = data["url"]
        fmt = data["format"]
        self._add_log(f"开始下载 {fmt.label} ...")
        asyncio.create_task(self._run_download(url, fmt))

    async def _run_download(self, url: str, fmt) -> None:
        from plugins import get_plugin

        plugin = get_plugin("yt_dlp")
        if plugin is None:
            self._add_log("[red]yt-dlp 插件未加载，无法下载[/red]")
            return

        self._show_progress()

        # 限流：日志和进度条各自独立限流
        last_bar_update = 0.0
        last_log_update = 0.0

        def on_progress(msg: str) -> None:
            nonlocal last_bar_update, last_log_update
            now = __import__('time').monotonic()
            # 日志每秒最多写 2 条
            if now - last_log_update >= 0.5:
                last_log_update = now
                self.call_from_thread(self._add_log, msg)
            # 进度条每 0.3 秒更新
            m = __import__('re').search(r'(\d+\.?\d*)%', msg)
            if m and now - last_bar_update >= 0.3:
                last_bar_update = now
                pct = float(m.group(1))
                self.call_from_thread(self._update_progress, pct)

        try:
            self._add_log(f"正在下载 {fmt.label} ...")
            filepath = await plugin.download(
                url,
                format_id=fmt.format_id,
                progress_cb=on_progress,
                has_audio=fmt.has_audio,
            )
            self._add_log(f"下载完成: {filepath}")
            self._update_progress(100)
        except Exception as e:
            self._add_log(f"下载失败: {e}")
        finally:
            self._hide_progress()

    def _show_progress(self) -> None:
        try:
            bar = self.query_one("#download_progress", ProgressBar)
            bar.display = True
            bar.update(total=100, progress=0)
        except Exception:
            pass

    def _update_progress(self, pct: float) -> None:
        try:
            bar = self.query_one("#download_progress", ProgressBar)
            bar.update(progress=pct)
        except Exception:
            pass

    def _hide_progress(self) -> None:
        async def _delay_hide():
            await asyncio.sleep(3)
            try:
                self.query_one("#download_progress", ProgressBar).display = False
            except Exception:
                pass
        asyncio.create_task(_delay_hide())

    def _handle_import_url(self, data: dict[str, str] | None) -> None:
        if not data:
            return
        asyncio.create_task(self._import_url(data["url"], data["tag"]))

    async def _import_url(self, url: str, tag: str) -> None:
        from importer import build_follower_from_url

        try:
            follower = await build_follower_from_url(url, tag)
            self._monitor.config_manager.append_follower(follower)
            next_idx = max(self._monitor.followers.keys(), default=-1) + 1
            self._monitor.cfg.followers.append(follower)
            self._monitor.followers[next_idx] = FollowerStatus(follower=follower)
            self._add_log(f"已导入 {follower.name} [{follower.platform}] 到 {self._monitor.config_manager.config_path.name}")
            self._refresh_all_tables()
            self._update_status_bar()
        except Exception as e:
            self._add_log(f"导入失败: {e}")

    async def _do_refresh(self) -> None:
        self._add_log("手动刷新...")
        await self._monitor.poll_all(None)
        self._refresh_all_tables()
        self._update_status_bar()

    def _selected_status(self, action_name: str) -> tuple[int, FollowerStatus] | None:
        tag = self._current_tag or "全部"
        table = self.query_one(f"#{self._table_id(tag)}", LiveTable)
        row_map = self._row_map.get(tag, [])
        if not row_map:
            self._add_log(f"列表为空，无法{action_name}")
            return None

        row_idx = table.cursor_row
        if row_idx < 0 or row_idx >= len(row_map):
            self._add_log("未选中有效行，请用 ↑↓ 移动光标")
            return None

        idx = row_map[row_idx]
        if idx < 0:
            self._add_log("分隔行不可操作，请选择主播行")
            return None

        status = self._monitor.followers.get(idx)
        if status is None:
            self._add_log(f"未找到序号 {idx} 的主播")
            return None

        return idx, status

    def action_play_selected(self) -> None:
        """播放选中的主播"""
        selected = self._selected_status("播放")
        if selected is None:
            return

        idx, status = selected

        if not status.live_info.is_live:
            self._add_log(f"{status.follower.name} 未开播，无法播放")
            return

        self._add_log(f"播放 {status.follower.name} [{status.follower.platform or '?'}] ...")
        asyncio.create_task(self._play(idx))

    def action_open_web(self) -> None:
        """浏览器打开选中主播的原链接"""
        selected = self._selected_status("打开")
        if selected is None:
            return
        _, status = selected

        url = _web_url_for_follower(status.follower)
        if not url:
            self._add_log(f"{status.follower.name} 没有链接")
            return

        self._add_log(f"浏览器打开 {status.follower.name} [{url}] ...")
        webbrowser.open(url)

    def action_copy_stream_url(self) -> None:
        """复制选中主播的最新直播流地址"""
        selected = self._selected_status("复制流地址")
        if selected is None:
            return

        idx, status = selected
        if not status.live_info.is_live:
            self._add_log(f"{status.follower.name} 未开播，无法复制流地址")
            return

        self._add_log(f"正在获取 {status.follower.name} 的最新流地址...")
        asyncio.create_task(self._copy_stream_url(idx))

    def action_open_fs1_update(self) -> None:
        """打开程序内更新弹窗。"""
        self.push_screen(UpdateScreen(), self._handle_update)

    def _handle_update(self, data: dict | None) -> None:
        if not data:
            return
        asyncio.create_task(self._run_update(data["target"], data.get("curl", "")))

    async def _run_update(self, target: str, curl: str = "") -> None:
        self._show_progress()
        self._update_progress(5)
        try:
            if target == "fs1":
                success = await self._run_fs1_update(curl)
            else:
                success = await self._run_package_update(target)
            if success:
                self._update_progress(100)
        except Exception as e:
            self._add_log(f"[red]更新失败: {e}[/red]")
        finally:
            self._hide_progress()

    async def _run_fs1_update(self, curl: str) -> bool:
        from plugins.fs1_plugin import update_from_curl

        self._add_log("开始更新 FS1 配置...")
        self._update_progress(25)
        try:
            applied = await asyncio.to_thread(update_from_curl, curl)
        except Exception as e:
            self._add_log(f"[red]FS1 配置更新失败: {e}[/red]")
            return False

        self._update_progress(85)
        self._reload_fs1_runtime_config()
        self._add_log("FS1 配置已更新：")
        for key in sorted(applied):
            value = applied[key]
            if key == "token":
                value = value[:24] + "..." if len(value) > 24 else "***"
            self._add_log(f"- {key}: {value}")
        return True

    async def _run_package_update(self, package: str) -> bool:
        self._add_log(f"开始更新 {package} ...")
        self._update_progress(15)
        kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
            "cwd": str(Path(__file__).parent.parent),
        }
        if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            package,
            **kwargs,
        )

        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                self._add_log(text)

        code = await process.wait()
        self._update_progress(90)
        if code == 0:
            self._restart_required = True
            self._add_log(f"{package} 更新完成；请重启应用后再使用新版本")
            self._update_status_bar()
            return True
        else:
            self._add_log(f"[red]{package} 更新失败，退出码={code}[/red]")
            return False

    def _reload_fs1_runtime_config(self) -> None:
        try:
            from plugins import get_plugin

            plugin = get_plugin("fs1")
            if plugin is None or not hasattr(plugin, "reload_config"):
                return
            plugin.reload_config()
        except Exception as e:
            self._add_log(f"[red]FS1 运行时配置刷新失败: {e}[/red]")

    def _stop_player_process(self) -> bool:
        if self._player_process is None:
            return False
        if self._player_process.poll() is not None:
            self._player_process = None
            return False

        self._player_process.terminate()
        self._player_process = None
        return True

    def action_toggle_notifications(self) -> None:
        """开关桌面通知"""
        enabled = not self._monitor.cfg.notifications_enabled
        self._monitor.cfg.notifications_enabled = enabled
        self._monitor.config_manager.save_config(self._monitor.cfg)
        state = "开启" if enabled else "关闭"
        self._add_log(f"桌面通知已{state}")
        self._update_status_bar()

    def action_hide_to_tray(self) -> None:
        """隐藏当前窗口到系统托盘。"""
        asyncio.create_task(self._hide_to_tray_after_click())

    async def _hide_to_tray_after_click(self) -> None:
        from desktop import hide_console_window

        await asyncio.sleep(0.2)
        if not hide_console_window():
            self._add_log("当前环境不支持隐藏至系统托盘")

    def action_stop_player(self) -> None:
        """停止当前 mpv 播放进程"""
        if self._stop_player_process():
            self._add_log("已停止当前 mpv")
        else:
            self._add_log("当前没有运行中的 mpv")

    async def _play(self, idx: int) -> None:
        from desktop import play_url, play_with_potplayer
        status = self._monitor.followers[idx]
        try:
            stream_info = await self._monitor.get_stream_info(idx)
            stream_url = stream_info.flv_url or stream_info.m3u8_url or stream_info.stream_url
            if self._stop_player_process():
                self._add_log("已停止上一条播放")
            platform = status.follower.platform.casefold() if status.follower.platform else ""
            if platform in ("twitch", "youtube"):
                player_name = "PotPlayer"
                self._add_log("获取流地址成功，启动 PotPlayer...")
                self._player_process = play_with_potplayer(stream_url)
            else:
                player_name = "mpv"
                self._add_log("获取流地址成功，启动 mpv...")
                self._player_process = play_url(
                    stream_url,
                    title=f"{status.follower.name} - Zhibo",
                    headers=stream_info.extra.get("headers", {}),
                )
            await asyncio.sleep(1)
            if self._player_process.poll() is not None:
                code = self._player_process.returncode
                self._player_process = None
                self._add_log(f"{player_name} 启动后已退出，退出码={code}")
                return
            self._add_log(f"{player_name} 已启动 pid={self._player_process.pid}")
        except Exception as e:
            self._add_log(f"播放失败: {e}")

    async def _copy_stream_url(self, idx: int) -> None:
        status = self._monitor.followers[idx]
        try:
            stream_info = await self._monitor.get_stream_info(idx)
            stream_url = stream_info.flv_url or stream_info.m3u8_url or stream_info.stream_url
            if not stream_url:
                self._add_log("无法获取流地址")
                return

            self.copy_to_clipboard(stream_url)
            self._add_log(f"已复制 {status.follower.name} 的直播流地址")
        except Exception as e:
            self._add_log(f"复制流地址失败: {e}")

    def on_unmount(self) -> None:
        """应用关闭时停止轮询"""
        self._monitor.stop()
        if self._poll_task:
            self._poll_task.cancel()
        if self._refresh_task:
            self._refresh_task.cancel()
        self._stop_player_process()
        if self._tray_icon is not None:
            self._tray_icon.stop()
        if self._minimize_monitor is not None:
            self._minimize_monitor.stop()
