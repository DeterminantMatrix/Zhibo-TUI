import ctypes
from unittest.mock import MagicMock

from zhibo import desktop
from zhibo.app_logging import redact_sensitive_text


def test_hwnd_value_normalizes_ctypes_handles():
    assert desktop._hwnd_value(42) == 42
    assert desktop._hwnd_value(ctypes.c_void_p(42)) == 42
    assert desktop._hwnd_value(None) == 0


def test_marked_terminal_window_must_have_one_unambiguous_match(monkeypatch):
    monkeypatch.setattr(desktop, "_visible_top_level_windows", lambda: [10, 20, 30])
    titles = {10: "PowerShell", 20: "直播监控工具 [marker]", 30: "记事本"}
    monkeypatch.setattr(desktop, "_window_title", titles.get)

    assert desktop._find_marked_terminal_window("[marker]") == 20

    titles[30] = "另一个直播监控工具 [marker]"
    assert desktop._find_marked_terminal_window("[marker]") == 0


def test_tray_icon_uses_a_stable_guid_identity():
    icon = desktop.TrayIcon("直播监控工具")
    icon._hwnd = 123

    first = icon._notify_data()
    second = icon._notify_data()

    assert ctypes.sizeof(first.guidItem) == 16
    assert bytes(first.guidItem) == bytes(second.guidItem)
    assert first.guidItem.Data1 == desktop.TRAY_ICON_GUID.Data1


def test_log_redaction_hides_header_and_query_credentials():
    message = "authorization: Bearer top-secret token=abc\nhttps://example.test/live?signature=xyz&safe=1"

    redacted = redact_sensitive_text(message)

    assert "top-secret" not in redacted
    assert "token=abc" not in redacted
    assert "signature=xyz" not in redacted
    assert "safe=1" in redacted


def test_notify_keeps_remote_text_out_of_powershell_source(monkeypatch):
    process = MagicMock()
    monkeypatch.setattr(desktop.platform, "system", lambda: "Windows")
    monkeypatch.setattr(desktop.subprocess, "Popen", process)
    title = "主播\n'@; Start-Process calc; #"
    message = "标题\n'@; Start-Process calc; #"

    assert desktop.notify(title, message) is True

    args, kwargs = process.call_args
    command = args[0]
    assert "-EncodedCommand" in command
    assert title not in command
    assert message not in command
    assert kwargs["env"]["ZHIBO_NOTIFY_TITLE"] == title[:63]
    assert kwargs["env"]["ZHIBO_NOTIFY_MESSAGE"] == message[:255]
