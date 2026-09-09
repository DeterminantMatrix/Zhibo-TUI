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
        self.stopped_player = 0
        self.refreshed = 0
        self.toggled: list[int] = []
        self.copied: list[int] = []
        self.opened: list[int] = []
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

    def stop_player(self) -> None:
        self.stopped_player += 1

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
        # 快照进了日志面板。
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
        app.set_player(1)
        await pilot.pause()
        app.set_player(None)
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
        assert bridge.stopped_player == 1
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
