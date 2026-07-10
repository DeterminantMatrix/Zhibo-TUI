from unittest.mock import MagicMock

import desktop


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
