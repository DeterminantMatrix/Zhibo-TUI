import desktop


def test_play_url_passes_http_headers_to_mpv(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 1234

    def fake_popen(cmd):
        calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(desktop.subprocess, "Popen", fake_popen)

    process = desktop.play_url(
        "https://example.com/live.flv",
        title="test",
        headers={
            "User-Agent": "Fake UA",
            "Referer": "https://www.huya.com/",
            "Origin": "https://www.huya.com",
            "Accept": "*/*",
        },
    )

    assert process.pid == 1234
    assert calls[0] == [
        "mpv",
        "https://example.com/live.flv",
        "--no-cache",
        "--stream-lavf-o=reconnect=1",
        "--stream-lavf-o=reconnect_streamed=1",
        "--title=test",
        "--user-agent=Fake UA",
        "--referrer=https://www.huya.com/",
        "--http-header-fields=Origin: https://www.huya.com",
        "--http-header-fields=Accept: */*",
    ]


def test_play_with_potplayer_uses_configured_executable(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 5678

    def fake_popen(cmd):
        calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(desktop.subprocess, "Popen", fake_popen)

    process = desktop.play_with_potplayer("https://example.com/twitch.m3u8")

    assert process.pid == 5678
    assert calls[0] == [
        r"C:\Program Files\PotPlayer\PotPlayerMini64.exe",
        "https://example.com/twitch.m3u8",
    ]
