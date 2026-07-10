from plugins.bilibili_quality import _bilibili_cookie_header, _quality_warning, _select_best_from_playinfo


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


def test_bilibili_cookie_header_reads_netscape_cookie_file(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".bilibili.com\tTRUE\t/\tFALSE\t1893456000\tSESSDATA\tabc\n"
        ".youtube.com\tTRUE\t/\tFALSE\t1893456000\tSID\tignored\n",
        encoding="utf-8",
    )

    assert _bilibili_cookie_header(cookie_file) == "SESSDATA=abc"


def test_quality_warning_reports_missing_cookie_for_lower_bilibili_quality():
    selected = {"qn": 400}
    payload = {
        "data": {
            "playurl_info": {
                "playurl": {
                    "stream": [
                        {
                            "format": [
                                {
                                    "codec": [
                                        {
                                            "accept_qn": [10000, 400],
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            }
        }
    }

    warning = _quality_warning(selected, payload, used_cookie=False, has_cookie=False)

    assert "B站缺少 cookie" in warning
    assert "原画(10000)" in warning
    assert "蓝光(400)" in warning
