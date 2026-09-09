"""TUI 冒烟测试 — FakeBridge 驱动界面逻辑，不触网络。"""
import pytest

from textual.widgets import DataTable, Input, RichLog, Tab, Tabs

from tui.app import ZhiboTui


def _fake_snapshot() -> dict:
    rows = [
        {
            "idx": 0, "enabled": True, "live": True, "checking": False,
            "tags": ["游戏", "LOL"], "name": "夜色", "platform": "douyu",
            "title": "闲聊", "quality": "best", "configured_quality": "best",
            "configured_plugin": "streamlink", "plugin": "streamlink",
            "last_check": "10:00:00",
        },
        {
            "idx": 1, "enabled": True, "live": False, "checking": False,
            "tags": ["体育"], "name": "北风", "platform": "fs1",
            "title": "-", "quality": "best", "configured_quality": "best",
            "configured_plugin": "fs1", "plugin": "fs1",
            "last_check": "10:00:01",
        },
    ]
    return {
        "rows": rows,
        "tags": ["全部", "游戏", "LOL", "体育"],
        "polling": False,
        "poll_interval": 240,
        "poll_round": 3,
    }


class FakeBridge:
    """与 MonitorBridge 同接口的测试替身。"""

    def __init__(self, snapshot=None):
        self.snapshot = snapshot or _fake_snapshot()
        self.startup_notes: list[str] = []
        self.started = False
        self.stopped = False
        self.played: list[int] = []
        self.stopped_one: list[int] = []
        self.stopped_all = 0
        self.refreshed = 0
        self.toggled: list[int] = []
        self.copied: list[int] = []
        self.opened: list[int] = []
        self.checked: list[str] = []
        self.ran_updates: list[tuple] = []
        self.dl_started = None
        self.dl_formats_url = None
        self.on_snapshot = None
        self.on_log = None
        self.on_player = None

    async def start(self) -> None:
        self.started = True
        self.on_snapshot(self.snapshot)
        self.on_log("假日志一行")

    async def stop(self) -> None:
        self.stopped = True

    def refresh(self) -> None:
        self.refreshed += 1

    def play(self, idx: int) -> None:
        self.played.append(idx)

    def stop_one(self, idx: int) -> None:
        self.stopped_one.append(idx)

    def get_field_options(self, idx: int) -> dict:
        return {
            "plugin": ["streamlink", "streamget"],
            "quality": [{"label": "best", "value": "best"}, {"label": "高清", "value": "高清"}],
        }

    def set_quality(self, idx: int, quality: str) -> None:
        self.quality_set = (idx, quality)

    def set_plugin(self, idx: int, plugin: str) -> None:
        self.plugin_set = (idx, plugin)

    def stop_all_players(self) -> None:
        self.stopped_all += 1

    def copy_stream(self, idx: int) -> None:
        self.copied.append(idx)

    def open_web(self, idx: int) -> None:
        self.opened.append(idx)

    def toggle_enabled(self, idx: int) -> None:
        self.toggled.append(idx)

    def player_control(self, action: str) -> None:
        pass

    def get_detail(self, idx: int) -> dict:
        return {
            "title": f"详情 {idx}",
            "rows": [{"label": "状态", "value": "直播中", "tone": "ok"}],
        }

    # ---- P2 事务（假实现，记录调用） ----

    def get_edit_payload(self, idx: int) -> dict:
        return {
            "idx": idx,
            "name": "夜色",
            "form": {
                "enabled": "true", "name": "夜色", "tags": "游戏|LOL",
                "plugin": "streamlink", "fallback_plugins": "", "platform": "douyu",
                "url": "https://douyu.com/1", "quality": "best", "sport_id": "",
                "extra": "{}",
            },
            "pluginOptions": ["streamlink"],
            "qualityOptions": [{"label": "best", "value": "best"}],
        }

    async def preview_edit(self, idx, values):
        self.edit_previewed = (idx, values)
        return True, "配置修改预览（尚未保存）\n名称：夜色 → 夜色改"

    async def confirm_edit(self):
        self.edit_confirmed = True
        return True, "已保存 夜色 的配置修改"

    async def get_settings(self):
        return {
            "poll_interval": "240", "max_concurrent_checks": "8",
            "failure_backoff_after": "3", "failure_backoff_polls": "2",
            "notifications_enabled": "true",
        }

    async def preview_settings(self, values):
        self.settings_previewed = values
        return True, "监控设置预览（尚未保存）\n轮询间隔（秒）：240 → 60"

    async def confirm_settings(self):
        self.settings_confirmed = True
        return True, "监控设置已保存并立即生效"

    async def get_proxy(self):
        return {"twitch": "", "youtube": ""}

    async def test_proxy(self, values):
        self.proxy_tested = values
        return [{"platform": "twitch", "status": "direct", "label": "直连", "detail": "该平台不会经过代理"}]

    async def save_proxy(self, values):
        self.proxy_saved = values
        return True, "平台代理设置已保存"

    async def get_import_preview(self, url, tag):
        self.import_previewed = (url, tag)
        return True, {
            "previewText": "导入预览（尚未写入配置）",
            "canConfirm": True,
            "requiresOverride": False,
            "confirmLabel": "确认导入",
        }

    async def confirm_import(self):
        self.imported = True
        return True, "已导入 新主播"

    async def preview_delete(self, idx):
        return True, "即将永久删除这个直播间"

    async def confirm_delete(self):
        self.deleted = True
        return True, "已删除直播间：夜色"

    def toggle_notifications(self):
        self.notifications_toggled = True
        return True, "桌面通知已关闭"

    # ---- 更新中心 / 下载 ----

    update_items = [
        {
            "value": "mpv", "label": "MPV 播放器", "kind": "tool",
            "version": "v0.41", "installed": True, "actionLabel": "检查更新",
            "actionEnabled": True, "actionKind": "check",
            "updateStatus": "unchecked", "updateHint": "", "remoteVersion": "",
            "downloadSize": "", "description": "直播流播放组件", "source": "便携版",
            "lastUpdated": "刚刚",
        },
        {
            "value": "fs1", "label": "FS1 配置", "kind": "configuration",
            "version": "内置配置适配器", "installed": True, "actionLabel": "更新配置",
            "actionEnabled": True, "actionKind": "execute", "updateStatus": "local",
            "updateHint": "", "remoteVersion": "", "downloadSize": "",
            "description": "飞速直播接口", "source": "本地配置", "lastUpdated": "",
        },
    ]

    def load_update_center(self):
        self.update_loaded = True
        self.on_update_items(self.update_items)

    def check_update(self, target):
        self.checked.append(target)

    def run_update(self, target, content=""):
        self.ran_updates.append((target, content))
        self.on_progress("update", 100.0, "更新完成")
        self.on_update_done(True, "更新完成")

    def list_download_formats(self, url):
        self.dl_formats_url = url
        self.on_formats([
            {"index": 0, "label": "1080p mp4", "formatId": "f1", "hasAudio": True},
        ])

    def start_download(self, index):
        self.dl_started = index
        self.on_progress("download", 100.0, "下载完成：C:/x.mp4")


@pytest.mark.asyncio
async def test_tui_renders_snapshot_and_bindings():
    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()
        assert bridge.started
        table = app.query_one("#streamTable", DataTable)
        # 2 个数据行 + 开播/未开播之间的分隔行。
        assert table.row_count == 3
        # 日志默认收起；按 l 展开后能看到历史日志。
        await pilot.press("l")
        await pilot.pause()
        log = app.query_one("#log", RichLog)
        assert log.display is True
        assert any("假日志一行" in "".join(str(s) for s in log.lines)
                   for log in [app.query_one("#log", RichLog)])

        # enter 播放当前行（默认光标在第 0 行）。
        table.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert bridge.played == [0]

        # d 打开详情弹层，esc 关闭。
        await pilot.press("d")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        # / 聚焦搜索，输入过滤。
        await pilot.press("/")
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "search"
        await pilot.press("escape")
        app.query_one("#search", Input).value = "北风"
        await pilot.pause()
        # 只有未开播行 → 无分隔行。
        assert table.row_count == 1
        app.query_one("#search", Input).value = ""
        await pilot.pause()
        assert table.row_count == 3

        # 播放状态回调刷新状态列（不抛异常即可）。
        app.set_players([1])
        await pilot.pause()
        app.set_players([])
        await pilot.pause()


@pytest.mark.asyncio
async def test_tui_tag_filter_and_lifecycle():
    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()
        table = app.query_one("#streamTable", DataTable)

        # 模拟点击"游戏"标签（id 序号与快照 tags 对应）。
        tabs = app.query_one("#tagTabs", Tabs)
        app.on_tabs_tab_activated(Tabs.TabActivated(tabs=tabs, tab=Tab("游戏", id="tag-1")))
        await pilot.pause()
        assert table.row_count == 1
        app.on_tabs_tab_activated(Tabs.TabActivated(tabs=tabs, tab=Tab("全部", id="tag-0")))
        await pilot.pause()
        assert table.row_count == 3

        # 其余行操作转发给桥。
        await pilot.pause()
        app.action_refresh()
        app.action_stop_player()
        app.action_copy_stream()
        app.action_open_web()
        app.action_toggle_enabled()
        await pilot.pause()
        assert bridge.refreshed == 1
        assert bridge.stopped_all == 1
        assert bridge.copied == [0]
        assert bridge.opened == [0]
        assert bridge.toggled == [0]
    # 退出时桥被停止。
    assert bridge.stopped


@pytest.mark.asyncio
async def test_tui_edit_transaction_flow():
    from textual.widgets import Button

    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()
        table = app.query_one("#streamTable", DataTable)
        table.focus()
        await pilot.press("e")
        await pilot.pause()
        # 弹出了编辑表单。
        assert len(app.screen_stack) == 2

        # 修改名称并保存 → 进入差异确认页。
        app.screen._fields["name"].value = "夜色改"
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        assert bridge.edit_previewed[0] == 0
        assert len(app.screen_stack) == 3

        # 确认保存 → 两层弹层都关闭。
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()
        assert bridge.edit_confirmed
        assert len(app.screen_stack) == 1

        # 弹层打开期间 q 不应退出应用。
        await pilot.press("e")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        assert bridge.stopped is False


@pytest.mark.asyncio
async def test_tui_close_button_dismisses_every_dialog():
    import asyncio

    from textual.widgets import Button

    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()

        async def wait_stack(depth: int, timeout: float = 3.0) -> bool:
            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                if len(app.screen_stack) == depth:
                    return True
                await pilot.pause(0.05)
            return len(app.screen_stack) == depth

        for key in ("s", "p", "i", "e"):
            await pilot.press(key)
            assert await wait_stack(2), f"{key} 弹层未打开"
            app.screen.query_one("#close", Button).press()
            assert await wait_stack(1), f"{key} 弹层的关闭按钮不生效"
            # 焦点还给表格，避免悬空焦点吃掉下一个按键。
            app.query_one("#streamTable", DataTable).focus()
            await pilot.pause()


@pytest.mark.asyncio
async def test_tui_settings_proxy_import_delete_flows():
    from textual.widgets import Button, Input

    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()

        # 设置事务。
        await pilot.press("s")
        await pilot.pause()
        await pilot.pause()
        assert len(app.screen_stack) == 2
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        assert len(app.screen_stack) == 3
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()
        assert bridge.settings_confirmed
        assert len(app.screen_stack) == 1

        # 代理：测试 + 保存。
        await pilot.press("p")
        await pilot.pause()
        await pilot.pause()
        app.screen.query_one("#test", Button).press()
        await pilot.pause()
        app.screen.query_one("#save", Button).press()
        await pilot.pause()
        assert bridge.proxy_saved is not None
        assert len(app.screen_stack) == 1

        # 导入：预览 → 确认。
        await pilot.press("i")
        await pilot.pause()
        app.screen.query_one(Input).value = "https://www.twitch.tv/example"
        app.screen.query_one("#preview", Button).press()
        await pilot.pause()
        confirm_btn = app.screen.query_one("#confirm", Button)
        assert confirm_btn.disabled is False
        confirm_btn.press()
        await pilot.pause()
        assert bridge.imported
        assert len(app.screen_stack) == 1

        # 删除事务。
        table = app.query_one("#streamTable", DataTable)
        table.focus()
        await pilot.press("delete")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()
        assert bridge.deleted

        # 通知开关。
        await pilot.press("n")
        await pilot.pause()
        assert bridge.notifications_toggled


@pytest.mark.asyncio
async def test_tui_update_center_flow():
    from textual.widgets import Button, DataTable, ProgressBar

    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()
        await pilot.press("u")
        table = None
        for _ in range(30):
            await pilot.pause(0.05)
            if len(app.screen_stack) == 2:
                table = app.screen.query_one("#ucTable", DataTable)
                if table.row_count == 2:
                    break
        assert len(app.screen_stack) == 2
        assert table is not None and table.row_count == 2

        # 选中"检查更新"类组件并触发。
        app.screen.query_one("#ucAction", Button).press()
        await pilot.pause()
        assert bridge.checked == ["mpv"]

        # 执行类组件（fs1）走 run_update 并收到进度/完成回调。
        app.screen._selected = "fs1"
        app.screen._refresh_detail()
        app.screen.query_one("#ucAction", Button).press()
        await pilot.pause()
        assert bridge.ran_updates == [("fs1", "")]
        bar = app.screen.query_one("#ucBar", ProgressBar)
        for _ in range(20):
            await pilot.pause(0.05)
            if bar.progress == 100.0:
                break
        assert bar.progress == 100.0

        # 关闭。
        app.screen.query_one("#close", Button).press()
        for _ in range(20):
            await pilot.pause(0.05)
            if len(app.screen_stack) == 1:
                break
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_tui_download_flow():
    from textual.widgets import Button, Input, OptionList, ProgressBar

    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()
        await pilot.press("w")
        for _ in range(30):
            await pilot.pause(0.05)
            if len(app.screen_stack) == 2:
                break
        assert len(app.screen_stack) == 2

        app.screen.query_one("#dlUrl", Input).value = "https://youtu.be/x"
        app.screen.query_one("#dlLoad", Button).press()
        for _ in range(30):
            await pilot.pause(0.05)
            if app.screen.query_one("#dlFormats", OptionList).option_count == 1:
                break
        assert app.screen.query_one("#dlFormats", OptionList).option_count == 1
        assert app.screen.query_one("#dlStart", Button).disabled is False

        app.screen.query_one("#dlStart", Button).press()
        bar = app.screen.query_one("#dlBar", ProgressBar)
        for _ in range(30):
            await pilot.pause(0.05)
            if bar.progress == 100.0:
                break
        assert bar.progress == 100.0
        assert bridge.dl_started == 0
        assert bridge.dl_formats_url == "https://youtu.be/x"


@pytest.mark.asyncio
async def test_tui_inline_quality_plugin_pick():
    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()
        # 打开画质选择（等效点击画质列）。
        app._open_field_picker(0, "quality")
        for _ in range(20):
            await pilot.pause(0.05)
            if len(app.screen_stack) == 2:
                break
        assert len(app.screen_stack) == 2
        # 选"高清"。
        screen = app.screen
        idxs = [v for _l, v in screen._options]
        assert "高清" in idxs
        screen._on_pick("高清")
        await pilot.pause()
        assert bridge.quality_set == (0, "高清")

        # 插件选择。
        app._open_field_picker(0, "plugin")
        for _ in range(20):
            await pilot.pause(0.05)
            if len(app.screen_stack) == 2:
                break
        app.screen._on_pick("streamget")
        await pilot.pause()
        assert bridge.plugin_set == (0, "streamget")


@pytest.mark.asyncio
async def test_tui_action_bar_layout():
    from textual.widgets import Button

    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()
        buttons = app.query("#actionBar Button")
        labels = [b.label for b in buttons]
        assert labels[0] == "播放"
        assert labels[-1] == "导入"
        play_btn = app.query_one("#abPlay", Button)
        import_btn = app.query_one("#abImport", Button)
        assert "corner" in play_btn.classes and "corner" in import_btn.classes
        # 点击导入打开导入弹层。
        app.query_one("#abImport", Button).press()
        for _ in range(20):
            await pilot.pause(0.05)
            if len(app.screen_stack) == 2:
                break
        assert len(app.screen_stack) == 2
        app.screen.query_one("#close", Button).press()
        for _ in range(20):
            await pilot.pause(0.05)
            if len(app.screen_stack) == 1:
                break
        assert len(app.screen_stack) == 1


@pytest.mark.asyncio
async def test_tui_quality_column_enter_opens_picker():
    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()
        table = app.query_one("#streamTable", DataTable)
        table.focus()
        table.move_cursor(row=0, column=5)  # 画质列
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if len(app.screen_stack) == 2:
                break
        assert len(app.screen_stack) == 2  # 选择框打开而不是播放
        screen = app.screen
        assert "高清" in [v for _l, v in screen._options]
        screen._on_pick("高清")
        await pilot.pause()
        assert bridge.quality_set == (0, "高清")


def test_tray_controller_constructs_and_imports():
    """托盘模块可导入、控制器可构造（防 NameError 之类的低级崩溃回归）。"""
    from pathlib import Path

    from tui.tray import TrayController, console_hwnd, hide_console

    controller = TrayController(
        icon_path=Path("nonexistent.ico"),
        tooltip="test",
        on_show=lambda: None,
        on_hide=lambda: None,
        on_quit=lambda: None,
    )
    assert controller.available is False  # 未 start
    controller.start()  # 图标文件不存在 → 降级为不可用，不抛异常
    controller.stop()
    assert isinstance(console_hwnd() is None, bool)
    assert isinstance(hide_console(), bool)
