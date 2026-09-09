"""TUI 骨架冒烟测试 — Textual headless 驱动，无需真实终端。"""
import pytest

from tui.app import ZhiboTui
from tui.fake_data import TAGS


@pytest.mark.asyncio
async def test_tui_skeleton_mounts_table_and_bindings():
    app = ZhiboTui()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        from textual.widgets import DataTable, Input, RichLog

        table = app.query_one("#streamTable", DataTable)
        assert table.row_count == 44
        # 模拟器已跑过 tick：日志面板有内容。
        log = app.query_one("#log", RichLog)
        assert len(log.lines) > 0

        # 搜索过滤。
        app._query = "老K"
        app._rebuild_table()
        assert table.row_count == 1
        app._query = ""
        app._rebuild_table()
        assert table.row_count == 44

        # 标签筛选（第一个非"全部"标签的行数与假数据一致）。
        tag = TAGS[1]
        app._tag = tag
        app._rebuild_table()
        expected = sum(1 for f in app._followers if tag in f["tags"])
        assert table.row_count == expected
        app._tag = "全部"
        app._rebuild_table()

        # 键位：/ 聚焦搜索，d 打开详情弹层，esc 关闭。
        await pilot.press("/")
        await pilot.pause()
        assert app.focused is not None and app.focused.id == "search"
        # 焦点还给表格再测弹层（Input 会吞字符键）。
        app.query_one("#streamTable", DataTable).focus()
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert len(app.screen_stack) == 2  # 主屏 + 详情
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1

        # 播放（假动作）不抛异常且写日志。
        before = len(log.lines)
        await pilot.press("enter")
        await pilot.pause()
        assert len(log.lines) >= before


@pytest.mark.asyncio
async def test_tui_simulator_flips_status():
    app = ZhiboTui()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        from textual.widgets import DataTable

        table = app.query_one("#streamTable", DataTable)
        # 直接驱动 20 个 tick，等价于 50 秒模拟，状态应发生翻转。
        for _ in range(20):
            app._simulate_tick()
        await pilot.pause()
        assert any(f["last_check"] != "--:--:--" for f in app._followers)
        assert table.row_count == 44  # 表格始终完整可渲染
