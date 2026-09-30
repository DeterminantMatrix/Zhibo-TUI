import asyncio

from zhibo.plugins import bilibili_quality
from zhibo.plugins.bilibili_quality import (
    _bilibili_cookie_header,
    _is_cookie_auth_rejection,
    _quality_warning,
    _select_from_playinfo,
    _select_best_from_playinfo,
    fetch_bilibili_live_status,
    fetch_bilibili_room_metadata,
)


def test_select_best_bilibili_stream_prefers_highest_current_qn():
    payload = {
        "data": {
            "live_status": 1,
            "playurl_info": {
                "playurl": {
                    "stream": [
                        {
                            "protocol_name": "http_stream",
                            "format": [
                                {
                                    "format_name": "flv",
                                    "codec": [
                                        {
                                            "codec_name": "avc",
                                            "current_qn": 250,
                                            "base_url": "/low.flv",
                                            "url_info": [{"host": "https://example.com", "extra": "?qn=250"}],
                                        }
                                    ],
                                }
                            ],
                        },
                        {
                            "protocol_name": "http_hls",
                            "format": [
                                {
                                    "format_name": "fmp4",
                                    "codec": [
                                        {
                                            "codec_name": "av1",
                                            "current_qn": 400,
                                            "base_url": "/high.m3u8",
                                            "url_info": [{"host": "https://example.com", "extra": "?qn=400"}],
                                        }
                                    ],
                                }
                            ],
                        },
                    ]
                }
            },
        }
    }

    selected = _select_best_from_playinfo(payload)

    assert selected["qn"] == 400
    assert selected["codec"] == "av1"
    assert selected["url"] == "https://example.com/high.m3u8?qn=400"


def test_select_bilibili_stream_honors_explicit_quality_and_keeps_cdn_candidates():
    payload = {
        "data": {
            "live_status": 1,
            "playurl_info": {"playurl": {"stream": [{
                "protocol_name": "http_hls",
                "format": [{"format_name": "fmp4", "codec": [
                    {"codec_name": "avc", "current_qn": 400, "base_url": "/blue.m3u8",
                     "url_info": [
                         {"host": "https://cdn-a.example", "extra": "?qn=400"},
                         {"host": "https://cdn-b.example", "extra": "?qn=400"},
                     ]},
                    {"codec_name": "avc", "current_qn": 10000, "base_url": "/source.m3u8",
                     "url_info": [{"host": "https://cdn-a.example", "extra": "?qn=10000"}]},
                ]}],
            }]}},
        }
    }

    selected = _select_from_playinfo(payload, 400)

    assert selected["qn"] == 400
    assert selected["url"].endswith("?qn=400")
    assert selected["candidates"][:2] == [
        "https://cdn-a.example/blue.m3u8?qn=400",
        "https://cdn-b.example/blue.m3u8?qn=400",
    ]


def test_bilibili_cookie_header_reads_netscape_cookie_file(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t1893456000\tSESSDATA\tabc\n"
        ".youtube.com\tTRUE\t/\tFALSE\t1893456000\tSID\tignored\n",
        encoding="utf-8",
    )

    assert _bilibili_cookie_header(cookie_file) == "SESSDATA=abc"


def test_bilibili_cookie_header_reads_http_only_netscape_cookie_record(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t1893456000\tSESSDATA\tabc\n",
        encoding="utf-8",
    )

    assert _bilibili_cookie_header(cookie_file) == "SESSDATA=abc"


def test_bilibili_cookie_header_rejects_lookalike_domains(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".evilbilibili.com\tTRUE\t/\tTRUE\t1893456000\tSESSDATA\tmust-not-use\n",
        encoding="utf-8",
    )

    assert _bilibili_cookie_header(cookie_file) == ""


def test_unavailable_accepted_quality_does_not_report_a_cookie_error():
    selected = {"qn": 400}

    warning = _quality_warning(selected, cookie_auth_rejected=False)

    assert warning == ""


def test_quality_warning_is_only_emitted_after_explicit_auth_rejection():
    warning = _quality_warning({"qn": 400}, cookie_auth_rejected=True)

    assert "Cookie 已被接口明确拒绝" in warning
    assert "蓝光(400)" in warning
    assert "最高画质" not in warning


def test_cookie_auth_rejection_requires_explicit_not_logged_in_code():
    assert _is_cookie_auth_rejection({"code": -101, "message": "账号未登录"}) is True
    assert _is_cookie_auth_rejection({"code": 0}) is False
    assert _is_cookie_auth_rejection({"code": -400, "message": "请求错误"}) is False


def test_bilibili_live_status_uses_lightweight_no_playurl_probe(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "data": {"live_status": 0}}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *, params):
            captured["url"] = url
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr(bilibili_quality.httpx, "AsyncClient", FakeClient)

    is_live = asyncio.run(fetch_bilibili_live_status("https://live.bilibili.com/52032"))

    assert is_live is False
    assert captured["params"]["room_id"] == "52032"
    assert captured["params"]["no_playurl"] == 1


def test_bilibili_live_status_reports_online_without_resolving_stream(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "data": {"live_status": 1}}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *, params):
            return FakeResponse()

    monkeypatch.setattr(bilibili_quality.httpx, "AsyncClient", FakeClient)

    assert asyncio.run(fetch_bilibili_live_status("https://live.bilibili.com/52032")) is True


def test_bilibili_room_metadata_reads_title_without_media_resolution(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "data": {"title": "测试直播标题"}}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, *, params):
            captured.update(url=url, params=params)
            return FakeResponse()

    monkeypatch.setattr(bilibili_quality.httpx, "AsyncClient", FakeClient)

    metadata = asyncio.run(fetch_bilibili_room_metadata("https://live.bilibili.com/6"))

    assert metadata == {"title": "测试直播标题"}
    assert captured["params"] == {"room_id": "6"}
