"""TUI 事务弹层 — 编辑 / 确认 / 设置 / 代理 / 导入。

全部为 ModalScreen，事务逻辑在 MonitorBridge（同 asyncio 循环），
界面只收集输入、展示差异/预览并把确认结果反馈给用户。
"""
from __future__ import annotations

from rich.text import Text

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Input,
    OptionList,
    ProgressBar,
    Select,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from tui.backend import MonitorBridge

_TONE_STYLE = {
    "ok": "green",
    "warning": "yellow",
    "error": "red",
    "muted": "dim",
    "normal": "",
}


class ModalBase(ModalScreen):
    """公共底座：Esc 关闭、统一的表单样式与错误提示。"""

    CSS = """
    ModalBase, DetailModal {
        align: center middle;
        background: $background 60%;
    }
    /* 紧凑弹层：单行无边框输入、窄按钮，信息密度对齐更新中心表格 */
    .panel {
        width: 76;
        max-height: 86%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
        overflow: auto;
    }
    .panel-title {
        text-style: bold;
        margin-bottom: 1;
    }
    .form-row {
        height: 1;
    }
    .form-label {
        width: 14;
        padding: 0 1 0 0;
    }
    .form-row Input, .form-row Select,
    .form-row Input:focus, .form-row Select:focus {
        width: 1fr;
        height: 1;
        border: none;
        background: $surface-darken-1;
        padding: 0 1;
    }
    .form-row TextArea, .form-row TextArea:focus {
        height: 6;
        border: none;
        background: $surface-darken-1;
    }
    .form-row CycleButton, .form-row CycleButton:focus {
        min-width: 10;
        height: 1;
        border: none;
        background: $surface-darken-1;
        padding: 0 2;
    }
    .button-row {
        height: 1;
        margin-top: 1;
        align-horizontal: right;
    }
    .button-row Button, .button-row Button:focus {
        min-width: 0;
        height: 1;
        border: none;
        padding: 0 2;
    }
    .form-error {
        color: $error;
        margin-top: 1;
        display: none;
    }
    .form-error.show {
        display: block;
    }
    .result-line {
        margin-top: 1;
    }
    """

    BINDINGS = [Binding("escape", "dismiss_screen", "关闭")]

    def action_dismiss_screen(self) -> None:
        self.dismiss()

    def _alive(self) -> bool:
        """await 之后界面可能已被关闭；先确认弹层还在再更新 UI。"""
        try:
            self.query_one(".panel")
            return True
        except Exception:
            return False

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            if len(self.app.screen_stack) > 1:
                self.dismiss()

    def _set_error(self, message: str) -> None:
        try:
            error = self.query_one(".form-error", Static)
        except Exception:
            return
        if message:
            error.update(Text(message, style="red"))
            error.add_class("show")
        else:
            error.update("")
            error.remove_class("show")

    def _pick_plugin(self, value: str) -> None:
        self._plugin_value = value
        self._plugin_btn.label = value or "-"

    def _pick_quality(self, value: str) -> None:
        self._quality_value = value
        self._quality_btn.label = value or "-"


class ConfirmScreen(ModalBase):
    """通用差异确认页：确认 / 返回 / 关闭。"""

    def __init__(
        self,
        title: str,
        text: str,
        confirm_label: str,
        on_confirm,
        on_success=None,
    ) -> None:
        super().__init__()
        self._title = title
        self._text = text
        self._confirm_label = confirm_label
        self._on_confirm = on_confirm
        self._on_success = on_success
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static(self._title, classes="panel-title")
            yield Static(self._text, id="confirmBody")
            yield Static("", classes="form-error")
            with Horizontal(classes="button-row"):
                yield Button(
                    self._confirm_label, id="confirm", variant="primary", disabled=self._busy
                )
                yield Button("返回", id="back")
                yield Button("关闭", id="close")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            if self._busy:
                return
            self._busy = True
            event.button.disabled = True
            ok, message = await self._on_confirm()
            self._busy = False
            if not self._alive():
                return
            if ok:
                self.app.log_line(message)
                self.app.notify(message, title="操作完成")
                self.dismiss()
                if self._on_success is not None:
                    self._on_success()
            else:
                self._set_error(message)
                event.button.disabled = False
        elif event.button.id == "back":
            self.dismiss()
        elif event.button.id == "close":
            self.dismiss()




class PickScreen(ModalBase):
    """紧凑选项挑选弹层 — 替代 Select 的下拉展示。"""

    CSS = """
    PickScreen {
        align: center middle;
        background: $background 60%;
    }
    #pickTitle {
        margin-bottom: 1;
        text-style: bold;
    }
    #pickList {
        width: 44;
        max-height: 70%;
        border: round $accent;
        background: $surface;
    }
    """

    def __init__(self, title: str, options: list[tuple[str, str]], current: str, on_pick) -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._current = current
        self._on_pick = on_pick

    def compose(self) -> ComposeResult:
        yield Static(self._title, id="pickTitle")
        yield OptionList(id="pickList")

    def on_mount(self) -> None:
        option_list = self.query_one("#pickList", OptionList)
        for i, (label, value) in enumerate(self._options):
            option_list.add_option(Option(label, id=f"opt-{i}"))
        for i, (label, value) in enumerate(self._options):
            if value == self._current:
                option_list.highlighted = i
                break

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        index = event.option_list.highlighted
        if index is None:
            return
        label, value = self._options[index]
        self.dismiss()
        if self._on_pick is not None:
            self._on_pick(value)


class CycleButton(Button):
    """二态循环按钮：点击在选项间切换，用于 启用/通知 这类布尔字段。"""

    def __init__(self, options: list[tuple[str, str]], current: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._options = options
        self.value = current
        self._sync_label()

    def _sync_label(self) -> None:
        for label, value in self._options:
            if value == self.value:
                self.label = label
                return

    def cycle(self) -> str:
        index = next(
            (i for i, (_l, v) in enumerate(self._options) if v == self.value), 0
        )
        self.value = self._options[(index + 1) % len(self._options)][1]
        self._sync_label()
        return self.value


def make_cycle_button(options: list[tuple[str, str]], current: str, button_id: str) -> CycleButton:
    return CycleButton(options, current, id=button_id)



class EditScreen(ModalBase):
    """详情与修改：表单 → 差异确认 → 原子保存。"""

    def __init__(self, bridge: MonitorBridge, payload: dict) -> None:
        super().__init__()
        self._bridge = bridge
        self._payload = payload
        self._fields: dict[str, object] = {}
        self._busy = False

    def compose(self) -> ComposeResult:
        form = self._payload["form"]
        plugins = self._payload["pluginOptions"]
        qualities = self._payload["qualityOptions"]
        with Vertical(classes="panel"):
            yield Static(
                f"详情与修改 · {self._payload.get('name', '')}", classes="panel-title"
            )
            with Horizontal(classes="form-row"):
                yield Static("启用", classes="form-label")
                self._enabled = make_cycle_button(
                    [("启用监控", "true"), ("停用监控", "false")],
                    str(form.get("enabled", "true")),
                    "enabledCycle",
                )
                yield self._enabled
            with Horizontal(classes="form-row"):
                yield Static("名称", classes="form-label")
                name = Input(value=str(form.get("name", "")))
                self._fields["name"] = name
                yield name
            with Horizontal(classes="form-row"):
                yield Static("标签", classes="form-label")
                tags = Input(value=str(form.get("tags", "")), placeholder="多个标签用 | 分隔")
                self._fields["tags"] = tags
                yield tags
            with Horizontal(classes="form-row"):
                yield Static("主插件", classes="form-label")
                self._plugin_value = str(form.get("plugin", ""))
                self._plugin_btn = Button(self._plugin_value or "-", id="pluginPick")
                yield self._plugin_btn
            with Horizontal(classes="form-row"):
                yield Static("备用插件", classes="form-label")
                fallback = Input(
                    value=str(form.get("fallback_plugins", "")), placeholder="多个插件用 | 分隔"
                )
                self._fields["fallback_plugins"] = fallback
                yield fallback
            with Horizontal(classes="form-row"):
                yield Static("平台", classes="form-label")
                platform = Input(value=str(form.get("platform", "")))
                self._fields["platform"] = platform
                yield platform
            with Horizontal(classes="form-row"):
                yield Static("直播间地址", classes="form-label")
                url = Input(value=str(form.get("url", "")))
                self._fields["url"] = url
                yield url
            with Horizontal(classes="form-row"):
                yield Static("画质", classes="form-label")
                cur_q = str(form.get("quality", "best"))
                q_options = [(q["label"], q["value"]) for q in qualities]
                if cur_q and all(q["value"] != cur_q for q in qualities):
                    q_options.insert(0, (cur_q, cur_q))
                self._quality_value = cur_q
                self._quality_btn = Button(cur_q or "-", id="qualityPick")
                yield self._quality_btn
            with Horizontal(classes="form-row"):
                yield Static("sport_id", classes="form-label")
                sport = Input(value=str(form.get("sport_id", "")))
                self._fields["sport_id"] = sport
                yield sport
            with Horizontal(classes="form-row"):
                yield Static("扩展字段", classes="form-label")
                extra = TextArea(text=str(form.get("extra", "{}")))
                self._fields["extra"] = extra
                yield extra
            yield Static("", classes="form-error")
            with Horizontal(classes="button-row"):
                yield Button("保存修改", id="save", variant="primary")
                yield Button("关闭", id="close")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            if len(self.app.screen_stack) > 1:
                self.dismiss()
            return
        if event.button.id == "enabledCycle":
            self._enabled.cycle()
            return
        if event.button.id == "pluginPick":
            options = [(p, p) for p in self._payload["pluginOptions"]]
            if self._plugin_value and all(v != self._plugin_value for _l, v in options):
                options.insert(0, (self._plugin_value, self._plugin_value))
            self.app.push_screen(
                PickScreen("选择主插件", options, self._plugin_value, self._pick_plugin)
            )
            return
        if event.button.id == "qualityPick":
            options = [(q["label"], q["value"]) for q in self._payload["qualityOptions"]]
            if self._quality_value and all(v != self._quality_value for _l, v in options):
                options.insert(0, (self._quality_value, self._quality_value))
            self.app.push_screen(
                PickScreen("选择画质", options, self._quality_value, self._pick_quality)
            )
            return
        if event.button.id != "save" or self._busy:
            return
        values = {
            name: (widget.text if isinstance(widget, TextArea) else widget.value)
            for name, widget in self._fields.items()
        }
        values["enabled"] = self._enabled.value
        values["plugin"] = self._plugin_value
        values["quality"] = self._quality_value
        self._busy = True
        self._set_error("")
        ok, result = await self._bridge.preview_edit(self._payload["idx"], values)
        self._busy = False
        if not self._alive():
            return
        if not ok:
            self._set_error(result)
            return
        self.app.push_screen(
            ConfirmScreen(
                "确认保存修改？",
                result,
                "确认保存",
                self._bridge.confirm_edit,
                on_success=self.dismiss,
            )
        )


class SettingsScreen(ModalBase):
    """监控设置：表单 → 差异确认 → 保存并即时生效。"""

    NUMBER_FIELDS = (
        ("poll_interval", "轮询间隔（秒）"),
        ("max_concurrent_checks", "最大并发检测"),
        ("failure_backoff_after", "失败后退避阈值"),
        ("failure_backoff_polls", "退避轮数"),
    )

    def __init__(self, bridge: MonitorBridge, values: dict) -> None:
        super().__init__()
        self._bridge = bridge
        self._values = values
        self._fields: dict[str, object] = {}
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static("监控设置", classes="panel-title")
            for field, label in self.NUMBER_FIELDS:
                with Horizontal(classes="form-row"):
                    yield Static(label, classes="form-label")
                    input_widget = Input(value=str(self._values.get(field, "")))
                    self._fields[field] = input_widget
                    yield input_widget
            with Horizontal(classes="form-row"):
                yield Static("桌面通知", classes="form-label")
                current = str(self._values.get("notifications_enabled", "true"))
                self._notif = make_cycle_button(
                    [("开启", "true"), ("关闭", "false")], current, "notifCycle"
                )
                yield self._notif
            yield Static("", classes="form-error")
            with Horizontal(classes="button-row"):
                yield Button("保存修改", id="save", variant="primary")
                yield Button("关闭", id="close")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            if len(self.app.screen_stack) > 1:
                self.dismiss()
            return
        if event.button.id == "notifCycle":
            self._notif.cycle()
            return
        if event.button.id != "save" or self._busy:
            return
        values = {name: widget.value for name, widget in self._fields.items()}
        values["notifications_enabled"] = self._notif.value
        self._busy = True
        self._set_error("")
        ok, result = await self._bridge.preview_settings(values)
        self._busy = False
        if not self._alive():
            return
        if not ok:
            self._set_error(result)
            return
        self.app.push_screen(
            ConfirmScreen(
                "确认保存监控设置？",
                result,
                "确认保存",
                self._bridge.confirm_settings,
                on_success=self.dismiss,
            )
        )


class ProxyScreen(ModalBase):
    """平台代理：按平台配置 + 连通性测试 + 保存。"""

    def __init__(self, bridge: MonitorBridge, values: dict) -> None:
        super().__init__()
        self._bridge = bridge
        self._values = values
        self._fields: dict[str, Input] = {}
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static("平台代理（留空 = 默认规则）", classes="panel-title")
            for platform, value in self._values.items():
                with Horizontal(classes="form-row"):
                    yield Static(platform, classes="form-label")
                    input_widget = Input(value=str(value), placeholder="主机:端口 或完整代理 URL")
                    self._fields[platform] = input_widget
                    yield input_widget
            yield Static("", classes="form-error")
            yield Static("", id="proxyResult", classes="result-line")
            with Horizontal(classes="button-row"):
                yield Button("测试连接", id="test")
                yield Button("保存", id="save", variant="primary")
                yield Button("关闭", id="close")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            if len(self.app.screen_stack) > 1:
                self.dismiss()
            return
        if self._busy:
            return
        values = {name: widget.value for name, widget in self._fields.items()}
        if event.button.id == "test":
            self._busy = True
            self._set_error("")
            results = await self._bridge.test_proxy(values)
            self._busy = False
            if not self._alive():
                return
            lines = Text()
            for item in results:
                if item["status"] == "ok":
                    tone = "green"
                elif item["status"] == "error":
                    tone = "red"
                else:
                    tone = "dim"
                lines.append(f"{item['platform']:<10}", style="bold")
                lines.append(f"{item['label']} — {item['detail']}\n", style=tone)
            failed = sum(1 for item in results if item["status"] == "error")
            direct = sum(1 for item in results if item["status"] == "direct")
            summary = (
                f"{failed} 个平台代理不可连接"
                if failed
                else f"代理检查通过；{direct} 个平台使用直连"
            )
            lines.append(summary + "\n", style="red" if failed else "green")
            self.query_one("#proxyResult", Static).update(lines)
        elif event.button.id == "save":
            self._busy = True
            self._set_error("")
            ok, message = await self._bridge.save_proxy(values)
            self._busy = False
            if not self._alive():
                return
            if ok:
                self.app.log_line(message)
                self.app.notify(message, title="代理已保存")
                self.dismiss()
            else:
                self._set_error(message)


class ImportScreen(ModalBase):
    """导入直播间：地址 → 预览（冲突要求二次确认）→ 写入。"""

    def __init__(self, bridge: MonitorBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self._busy = False
        self._can_confirm = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static("导入直播间", classes="panel-title")
            with Horizontal(classes="form-row"):
                yield Static("直播间地址", classes="form-label")
                self._url = Input(placeholder="粘贴平台直播间链接")
                yield self._url
            with Horizontal(classes="form-row"):
                yield Static("标签", classes="form-label")
                self._tag = Input(value="未分类")
                yield self._tag
            yield Static("", classes="form-error")
            yield Static("", id="importPreview")
            with Horizontal(classes="button-row"):
                yield Button("获取预览", id="preview", variant="primary")
                yield Button("确认导入", id="confirm", disabled=True)
                yield Button("关闭", id="close")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            if len(self.app.screen_stack) > 1:
                self.dismiss()
            return
        if self._busy:
            return
        if event.button.id == "preview":
            self._busy = True
            self._set_error("")
            url = self._url.value.strip()
            if not url:
                self._busy = False
                self._set_error("请先粘贴直播间地址")
                return
            ok, result = await self._bridge.get_import_preview(
                url, self._tag.value.strip() or "未分类"
            )
            self._busy = False
            if not self._alive():
                return
            if not ok:
                self._set_error(str(result))
                return
            self._can_confirm = bool(result.get("canConfirm"))
            self.query_one("#importPreview", Static).update(
                Text(result.get("previewText", ""))
            )
            confirm_button = self.query_one("#confirm", Button)
            confirm_button.label = str(result.get("confirmLabel", "确认导入"))
            confirm_button.disabled = not self._can_confirm
        elif event.button.id == "confirm":
            self._busy = True
            ok, message = await self._bridge.confirm_import()
            self._busy = False
            if not self._alive():
                return
            if ok:
                self.app.log_line(message)
                self.app.notify(message, title="导入完成")
                self.dismiss()
            else:
                self._set_error(message)


class UpdateCenterScreen(ModalBase):
    """更新中心：组件表格（名称/当前版本/最新版本/是否需要更新）+ 详情与操作。"""

    CSS = """
    .panel {
        width: 100;
    }
    #ucTable {
        height: 1fr;
        min-height: 12;
        border: round $accent 30%;
    }
    #ucDetailText {
        margin-top: 1;
    }
    #ucContent {
        height: 6;
        margin-top: 1;
    }
    #ucProgress {
        display: none;
        margin-top: 1;
    }
    #ucProgress.visible {
        display: block;
    }
    """

    # 表格列（用户定稿）：组件名称 | 当前版本 | 最新版本 | 是否需要更新
    NEED_LABEL = {
        "checking": ("检查中…", "yellow"),
        "current": ("已是最新", "green"),
        "install": ("需要安装", "cyan"),
        "update": ("需要更新", "yellow"),
        "unknown": ("检查失败", "red"),
        "failed": ("更新失败", "red"),
    }

    def __init__(self, bridge: MonitorBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self._selected = "mpv"
        self._busy = False
        self._items: list[dict] = []
        self._col_keys: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static("更新中心（打开后自动检查全部组件远端版本）", classes="panel-title")
            yield DataTable(id="ucTable", cursor_type="row", zebra_stripes=True)
            yield Static("", id="ucDetailText")
            yield TextArea(id="ucContent")
            yield Static("", classes="form-error")
            with Vertical(id="ucProgress"):
                yield ProgressBar(total=100.0, show_eta=False, id="ucBar")
                yield Static("", id="ucProgressText")
            with Horizontal(classes="button-row"):
                yield Button("组件操作", id="ucAction", variant="primary")
                yield Button("关闭", id="close")

    def on_mount(self) -> None:
        table = self.query_one("#ucTable", DataTable)
        for key, label, width in (
            ("name", "组件名称", 18),
            ("version", "版本号", 14),
            ("remote", "最新版本", 14),
            ("need", "是否需要更新", 14),
        ):
            self._col_keys[key] = table.add_column(label, key=key, width=width)
        self._content = self.query_one("#ucContent", TextArea)
        self._content.display = False
        self.query_one("#ucProgress").display = False

        bridge = self._bridge
        bridge.on_update_items = self._on_items
        bridge.on_progress = self._on_progress
        bridge.on_update_done = self._on_done
        if bridge.update_items:
            self._on_items(bridge.update_items)
        bridge.load_update_center()

    def on_unmount(self) -> None:
        bridge = self._bridge
        if bridge.on_update_items is self._on_items:
            bridge.on_update_items = None
        if bridge.on_progress is self._on_progress:
            bridge.on_progress = None
        if bridge.on_update_done is self._on_done:
            bridge.on_update_done = None

    @staticmethod
    def _need_cell(item: dict) -> Text:
        kind = item.get("kind", "")
        status = str(item.get("updateStatus") or "unchecked")
        if status in UpdateCenterScreen.NEED_LABEL:
            text, tone = UpdateCenterScreen.NEED_LABEL[status]
        elif kind == "runtime":
            text, tone = "运行环境", "dim"
        elif kind == "configuration":
            text, tone = "本地配置", "dim"
        elif kind == "credential":
            text, tone = "本地凭据", "dim"
        else:
            text, tone = "未检查", "dim"
        return Text(text, style=tone)

    def _on_items(self, items: list[dict]) -> None:
        if not self._alive():
            return
        self._items = items
        table = self.query_one("#ucTable", DataTable)
        cursor = table.cursor_row
        table.clear()
        for item in items:
            latest = str(item.get("remoteVersion") or "-")
            table.add_row(
                Text(item.get("label", item.get("value", ""))),
                Text(str(item.get("version", "-"))),
                Text(latest),
                self._need_cell(item),
                key=item.get("value"),
            )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        item = next((i for i in self._items if i.get("value") == self._selected), None)
        if item is None:
            return
        action = self.query_one("#ucAction", Button)
        action.label = str(item.get("actionLabel", "操作"))
        action.disabled = not bool(item.get("actionEnabled"))
        body = Text()
        body.append(item.get("description", "") + "\n", style="dim")
        body.append(f"来源：{item.get('source', '-')}")
        if item.get("restartRequired"):
            body.append("（更新后需重启程序）", style="yellow")
        body.append("\n")
        hint = item.get("updateHint", "")
        if hint:
            tone = ""
            if item.get("updateStatus") == "unknown":
                tone = "red"
            elif item.get("updateStatus") in {"current", "checking"}:
                tone = "green"
            body.append(f"状态：{hint}", style=tone)
        self.query_one("#ucDetailText", Static).update(body)
        self._content.display = item.get("value") in {"fs1", "bilibili_cookie"}

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is not None and event.row_key.value:
            self._selected = str(event.row_key.value)
            self._refresh_detail()

    def _on_progress(self, kind: str, value: float, text: str) -> None:
        if kind != "update" or not self._alive():
            return
        self.query_one("#ucProgress").display = True
        bar = self.query_one("#ucBar", ProgressBar)
        bar.update(progress=max(0.0, min(100.0, value)))
        self.query_one("#ucProgressText", Static).update(
            Text(text, style="dim" if 0 <= value < 100 else "")
        )

    def _on_done(self, ok: bool, message: str) -> None:
        if not self._alive():
            return
        self.query_one("#ucProgress").display = True
        bar = self.query_one("#ucBar", ProgressBar)
        bar.update(progress=100.0)
        self.query_one("#ucProgressText", Static).update(
            Text(message, style="green" if ok else "red")
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            if len(self.app.screen_stack) > 1:
                self.dismiss()
            return
        if event.button.id == "ucAction" and not self._busy:
            item = next(
                (i for i in self._items if i.get("value") == self._selected), None
            )
            if item is None or not item.get("actionEnabled"):
                return
            kind = item.get("actionKind", "check")
            if kind in {"check", "recheck"}:
                self._bridge.check_update(item["value"])
            else:
                content = self._content.text if self._content.display else ""
                self._bridge.run_update(item["value"], content)


class DownloadScreen(ModalBase):
    """视频下载：地址 → 格式列表 → 下载进度。"""

    CSS = """
    #dlFormats {
        height: auto;
        max-height: 14;
        border: round $accent 30%;
        background: $surface;
        display: none;
        margin-top: 1;
    }
    #dlFormats.visible {
        display: block;
    }
    #dlProgress {
        display: none;
        margin-top: 1;
    }
    #dlProgress.visible {
        display: block;
    }
    #dlUrl {
        width: 1fr;
    }
    """

    def __init__(self, bridge: MonitorBridge) -> None:
        super().__init__()
        self._bridge = bridge
        self._busy = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="panel"):
            yield Static("视频下载（yt-dlp）", classes="panel-title")
            with Horizontal(classes="form-row"):
                yield Static("视频地址", classes="form-label")
                yield Input(placeholder="YouTube 等视频页面链接", id="dlUrl")
            with Horizontal(classes="button-row"):
                yield Button("获取格式列表", id="dlLoad", variant="primary")
                yield Button("开始下载", id="dlStart", disabled=True)
                yield Button("关闭", id="close")
            yield OptionList(id="dlFormats")
            yield Static("", classes="form-error")
            with Vertical(id="dlProgress"):
                yield ProgressBar(total=100.0, show_eta=False, id="dlBar")
                yield Static("", id="dlProgressText")

    def on_mount(self) -> None:
        self._bridge.on_formats = self._on_formats
        self._bridge.on_progress = self._on_progress
        self.query_one("#dlProgress").display = False

    def on_unmount(self) -> None:
        bridge = self._bridge
        if bridge.on_formats is self._on_formats:
            bridge.on_formats = None
        if bridge.on_progress is self._on_progress:
            bridge.on_progress = None

    def _on_formats(self, formats: list[dict]) -> None:
        if not self._alive():
            return
        self._formats = formats
        option_list = self.query_one("#dlFormats", OptionList)
        option_list.clear_options()
        for fmt in formats:
            prefix = "♪ " if fmt.get("hasAudio") else "视频 "
            option_list.add_option(
                Option(f"{prefix}{fmt['label']}  [{fmt['formatId']}]", id=str(fmt["index"]))
            )
        option_list.display = True
        if option_list.option_count and option_list.highlighted is None:
            option_list.highlighted = 0
        self.query_one("#dlStart", Button).disabled = False
        self._busy = False

    def _on_progress(self, kind: str, value: float, text: str) -> None:
        if kind != "download" or not self._alive():
            return
        if value >= 100.0:
            self._busy = False
        self.query_one("#dlProgress").display = True
        bar = self.query_one("#dlBar", ProgressBar)
        bar.update(progress=max(0.0, min(100.0, value)))
        self.query_one("#dlProgressText", Static).update(
            Text(text, style="green" if value >= 100 else "")
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if self._busy:
            return
        if event.button.id == "close":
            if len(self.app.screen_stack) > 1:
                self.dismiss()
            return
        if event.button.id == "dlLoad":
            url = self.query_one("#dlUrl", Input).value.strip()
            if not url:
                self._set_error("请先粘贴视频地址")
                return
            self._busy = True
            self._set_error("")
            self._bridge.list_download_formats(url)
            return
        if event.button.id == "dlStart":
            option_list = self.query_one("#dlFormats", OptionList)
            index = option_list.highlighted
            if index is None:
                self._set_error("请先选择一个下载格式")
                return
            option = option_list.get_option_at_index(index)
            self._busy = True
            event.button.disabled = True
            self._bridge.start_download(int(option.id))
