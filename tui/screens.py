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
from textual.widgets import Button, DataTable, Input, Select, Static, TextArea

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
    .panel {
        width: 100;
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
        height: 3;
    }
    .form-label {
        width: 16;
        padding: 1 1 0 0;
    }
    .form-row Input, .form-row Select {
        width: 1fr;
    }
    .form-row TextArea {
        height: 8;
    }
    .button-row {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }
    .button-row Button {
        margin-left: 2;
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
                enabled = Select(
                    [("启用监控", "true"), ("停用监控", "false")],
                    value=str(form.get("enabled", "true")),
                    allow_blank=False,
                )
                self._fields["enabled"] = enabled
                yield enabled
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
                current = str(form.get("plugin", ""))
                options = [(p, p) for p in plugins]
                if current and current not in plugins:
                    options.insert(0, (current, current))
                select = Select(options, value=current or None, allow_blank=False)
                self._fields["plugin"] = select
                yield select
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
                quality = Select(
                    q_options, value=cur_q or None, allow_blank=False
                )
                self._fields["quality"] = quality
                yield quality
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
        if event.button.id != "save" or self._busy:
            return
        values = {
            name: (widget.text if isinstance(widget, TextArea) else widget.value)
            for name, widget in self._fields.items()
        }
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
                select = Select(
                    [("开启", "true"), ("关闭", "false")],
                    value=current,
                    allow_blank=False,
                )
                self._fields["notifications_enabled"] = select
                yield select
            yield Static("", classes="form-error")
            with Horizontal(classes="button-row"):
                yield Button("保存修改", id="save", variant="primary")
                yield Button("关闭", id="close")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close":
            if len(self.app.screen_stack) > 1:
                self.dismiss()
            return
        if event.button.id != "save" or self._busy:
            return
        values = {name: widget.value for name, widget in self._fields.items()}
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
