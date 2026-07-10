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


def test_play_url_can_use_proxy_and_buffer(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 42

    monkeypatch.setattr(desktop.subprocess, "Popen", lambda cmd: calls.append(cmd) or FakeProcess())

    desktop.play_url(
        "https://example.com/live.m3u8",
        proxy_url="http://127.0.0.1:7890",
        use_cache=True,
    )

    assert "--http-proxy=http://127.0.0.1:7890" in calls[0]
    assert "--cache=yes" in calls[0]
    assert "--no-cache" not in calls[0]
