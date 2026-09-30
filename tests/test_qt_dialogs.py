"""DialogController 状态机单元测试。"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qt_quick.dialogs import DialogController


def test_show_busy_and_close_flow():
    d = DialogController()
    d.show("edit", {"stage": "loading"}, busy=True)
    assert d.kind == "edit"
    assert d.busy is True

    d.apply_incoming("edit", {"stage": "form", "name": "主播"})
    assert d.busy is False
    assert d.stage == "form"

    d.close()
    assert d.kind == ""
    assert d.data == {}


def test_busy_is_per_kind_and_closed_dialog_ignores_close_when_busy():
    d = DialogController()
    d.show("settings", {"stage": "form"})
    d.begin_op("settings")
    d.begin_op("settings")  # 重复登记不叠加
    assert d.busy is True

    d.end_op("quality")  # 别的 kind 完成不影响
    assert d.busy is True

    d.close()  # 有在途操作时关闭被拒绝
    assert d.kind == "settings"

    d.end_op("settings")
    assert d.busy is False
    d.close()
    assert d.kind == ""


def test_apply_incoming_patches_update_checked_and_returns_to_form():
    d = DialogController()
    d.show("update", {"stage": "form", "items": [{"value": "mpv", "version": "旧"}]})
    d.apply_incoming("update", {
        "stage": "checked",
        "target": "mpv",
        "item": {"updateStatus": "update", "remoteVersion": "新"},
    })

    assert d.stage == "form"
    assert d.data["items"][0]["remoteVersion"] == "新"


def test_apply_incoming_captures_form_for_back_to_form():
    d = DialogController()
    d.show("settings", {"stage": "form", "poll_interval": "60"})
    d.apply_incoming("settings", {"stage": "confirm", "previewText": "预览"})
    assert d.stage == "confirm"

    d.back_to_form()
    assert d.stage == "form"
    assert d.data["poll_interval"] == "60"


def test_apply_result_failed_update_switches_stage():
    d = DialogController()
    d.show("update", {"stage": "progress", "progress": 40})
    close, new_data = d.apply_result("update", False, "网络中断", {})

    assert close is False and new_data is None
    assert d.stage == "failed"
    assert d.error == "网络中断"


def test_apply_result_close_payload_and_update_done_state():
    d = DialogController()
    d.show("edit", {"stage": "form"})
    close, _ = d.apply_result("edit", True, "已保存", {"close": True})
    assert close is True

    d.show("update", {"stage": "progress", "items": [{"value": "mpv", "kind": "tool"}], "target": "mpv"})
    close, new_data = d.apply_result(
        "update", True, "MPV 安装完成", {"done": True, "downloaded": True, "target": "mpv", "version": "9.0"}
    )
    assert close is False
    assert new_data["stage"] == "done"
    assert new_data["items"][0]["version"] == "9.0"
    assert new_data["items"][0]["installed"] is True
