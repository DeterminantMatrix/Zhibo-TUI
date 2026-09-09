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


@pytest.mark.asyncio
async def test_tui_renders_snapshot_and_bindings():
    bridge = FakeBridge()
    app = ZhiboTui(bridge=bridge)
    async with app.run_test(size=(130, 34)) as pilot:
        await pilot.pause()
        assert bridge.started
        table = app.query_one("#streamTable", DataTable)
        assert table.row_count == 2
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
        assert table.row_count == 1
        app.query_one("#search", Input).value = ""
        await pilot.pause()
        assert table.row_count == 2

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
        assert table.row_count == 2

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
