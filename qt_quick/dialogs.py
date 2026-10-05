"""对话框状态机 — 从 QuickController 抽离的对话框域逻辑。

管理对话框的 kind/stage/data/busy/error，以及 bridge 数据的三种
合并策略（update checked 打补丁、progress/done 合并、其余整包替换）。
QuickController 只保留转发给 QML 的属性与槽。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from zhibo.app_logging import redact_sensitive_text


class DialogController(QObject):
    kindChanged = Signal()
    dataChanged = Signal()
    busyChanged = Signal()
    errorChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind = ""
        self.data: dict = {}
        # 确认页"返回"时要恢复的表单快照（表单 → 确认 时捕获）。
        self.form_data: dict = {}
        # 在途操作按 kind 登记；只有同 kind 的完成事件解除它。
        self.ops: set[str] = set()
        self.error = ""

    # ---- 只读视图 -------------------------------------------------------

    @property
    def stage(self) -> str:
        return str(self.data.get("stage") or "")

    @property
    def busy(self) -> bool:
        return bool(self.ops)

    # ---- 状态变更 -------------------------------------------------------

    def emit_data(self) -> None:
        self.dataChanged.emit()

    def set_kind(self, value: str) -> None:
        if value != self.kind:
            self.kind = value
            self.kindChanged.emit()

    def set_error(self, value: str) -> None:
        value = redact_sensitive_text(value)
        if value != self.error:
            self.error = value
            self.errorChanged.emit()

    def begin_op(self, kind: str) -> None:
        """登记一个在途操作；只有同 kind 的完成事件才会解除它。"""
        if kind and kind not in self.ops:
            self.ops.add(kind)
            self.busyChanged.emit()

    def end_op(self, kind: str) -> None:
        if kind in self.ops:
            self.ops.discard(kind)
            self.busyChanged.emit()

    # ---- 状态机迁移 -----------------------------------------------------

    def show(self, kind: str, data: dict, *, busy: bool = False) -> None:
        self.set_kind(kind)
        self.data = dict(data)
        self.form_data = {}
        self.dataChanged.emit()
        if busy:
            self.begin_op(kind)
        self.set_error("")

    def close(self) -> None:
        if self.ops:
            return
        self.set_kind("")
        self.data = {}
        self.form_data = {}
        self.dataChanged.emit()
        self.set_error("")

    def back_to_form(self) -> None:
        """确认页的"返回"回到表单并保留已输入内容；无表单时关闭对话框。"""
        if self.ops:
            return
        if not self.form_data:
            self.close()
            return
        self.data = dict(self.form_data)
        self.dataChanged.emit()
        self.set_error("")

    def apply_incoming(self, kind: str, payload: dict) -> None:
        """合并 bridge 的 dialogData 推送。

        三种策略：update/checked 对 items 打补丁并回到 form；
        update/progress|done 合并入现有数据；其余整包替换。
        表单 → 确认 时捕获表单快照供"返回"恢复。
        """
        self.set_kind(kind)
        incoming = dict(payload)
        if kind == "update" and incoming.get("stage") == "checked":
            target = str(incoming.get("target") or "")
            patch = dict(incoming.get("item") or {})
            items = [dict(item) for item in self.data.get("items", [])]
            for item in items:
                if item.get("value") == target:
                    item.update(patch)
                    break
            self.data = {
                **self.data,
                "stage": "form",
                "target": target,
                "items": items,
            }
        elif kind == "update" and incoming.get("stage") in {"progress", "done"}:
            self.data = {**self.data, **incoming}
        else:
            if (
                kind == self.kind
                and str(self.data.get("stage")) == "form"
                and str(incoming.get("stage")) == "confirm"
            ):
                # 记住表单内容，确认页"返回"时原样恢复。
                self.form_data = dict(self.data)
            self.data = incoming
        self.dataChanged.emit()
        self.end_op(kind)
        self.set_error("")

    def apply_result(
        self, kind: str, success: bool, message: str, payload: dict
    ) -> tuple[bool, dict | None]:
        """合并 operationFinished；返回 (是否关闭对话框, 新数据或 None)。

        None 表示数据无变化。失败时把 update 对话框切到 failed 舞台。
        """
        self.end_op(kind)
        if not success:
            self.set_error(message or "操作失败")
            if kind == "update" and kind == self.kind:
                self.data = {
                    **self.data,
                    "stage": "failed",
                    "progressText": message or "更新失败",
                }
                self.dataChanged.emit()
            return False, None

        self.set_error("")
        data = dict(payload)
        if data.get("close"):
            return True, None
        if self.kind == kind and data.get("done"):
            done_state = {
                **self.data,
                "stage": "done",
                "progress": 100,
                "progressText": message,
            }
            if kind == "update":
                items = [dict(item) for item in self.data.get("items", [])]
                target = str(data.get("target") or self.data.get("target") or "")
                if data.get("downloaded") is True:
                    for item in items:
                        if item.get("value") != target:
                            continue
                        if data.get("version"):
                            item["version"] = str(data["version"])
                            item["installed"] = True
                        item["lastUpdated"] = "刚刚"
                        if item.get("kind") in {"tool", "package"}:
                            item.update(
                                actionLabel="检查更新",
                                actionEnabled=True,
                                actionKind="check",
                                updateStatus="unchecked",
                                updateHint="更新完成；可按需再次检查",
                                remoteVersion="",
                                downloadSize="",
                            )
                        break
                done_state.update(target=target, items=items)
            return False, done_state
        return False, None
