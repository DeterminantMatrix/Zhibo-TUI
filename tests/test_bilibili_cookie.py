from __future__ import annotations

from pathlib import Path

import pytest

from zhibo import bilibili_cookie
from zhibo.bilibili_cookie import (
    BilibiliCookieStoreError,
    BilibiliCookieValidationError,
    MAX_COOKIE_INPUT_BYTES,
    update_bilibili_cookies,
)


def _record(
    domain: str,
    name: str,
    value: str,
    *,
    expiry: int = 4_102_444_800,
    http_only: bool = False,
) -> str:
    prefix = "#HttpOnly_" if http_only else ""
    return f"{prefix}{domain}\tTRUE\t/\tTRUE\t{expiry}\t{name}\t{value}"


def _netscape(*records: str) -> str:
    return "# Netscape HTTP Cookie File\n# generated for test\n" + "\n".join(records) + "\n"


def _valid_bili_export(*extra_records: str) -> str:
    return _netscape(
        _record(".bilibili.com", "SESSDATA", "test-session-value"),
        _record(".bilibili.com", "bili_jct", "test-csrf-value"),
        *extra_records,
    )


def test_update_replaces_bilibili_rows_and_preserves_existing_non_bilibili_rows(tmp_path: Path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        _netscape(
            _record(".youtube.com", "SID", "youtube-session"),
            _record(".bilibili.com", "SESSDATA", "old-bili-session"),
        ),
        encoding="utf-8",
    )

    result = update_bilibili_cookies(
        _valid_bili_export(_record(".example.com", "OTHER", "must-not-import")),
        cookie_file=cookie_file,
    )
    stored = cookie_file.read_text(encoding="utf-8")

    assert result.bilibili_records_updated == 2
    assert result.non_bilibili_records_preserved == 1
    assert "youtube-session" in stored
    assert "old-bili-session" not in stored
    assert "test-session-value" in stored
    assert "must-not-import" not in stored


def test_update_accepts_http_only_bilibili_sessdata_and_preserves_prefix(tmp_path: Path):
    cookie_file = tmp_path / "cookies.txt"

    update_bilibili_cookies(
        _netscape(_record(".bilibili.com", "SESSDATA", "test-session-value", http_only=True)),
        cookie_file=cookie_file,
    )

    assert "#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE" in cookie_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "content",
    [
        "not a Netscape export",
        _netscape(_record(".bilibili.com", "bili_jct", "test-csrf-value")),
        _netscape(_record(".www.bilibili.com", "SESSDATA", "test-session-value")),
        _netscape(_record(".bilibili.com", "SESSDATA", "", expiry=4_102_444_800)),
        _netscape(_record(".bilibili.com", "SESSDATA", "test-session-value", expiry=1)),
    ],
)
def test_update_rejects_missing_or_unusable_root_sessdata_without_writing(tmp_path: Path, content: str):
    cookie_file = tmp_path / "cookies.txt"

    with pytest.raises(BilibiliCookieValidationError) as error:
        update_bilibili_cookies(content, cookie_file=cookie_file)

    assert not cookie_file.exists()
    assert "test-session-value" not in str(error.value)


def test_update_rejects_oversized_input_without_echoing_it(tmp_path: Path):
    cookie_file = tmp_path / "cookies.txt"
    secret_marker = "unique-secret-marker"
    content = "# Netscape HTTP Cookie File\n" + secret_marker + ("x" * MAX_COOKIE_INPUT_BYTES)

    with pytest.raises(BilibiliCookieValidationError) as error:
        update_bilibili_cookies(content, cookie_file=cookie_file)

    assert secret_marker not in str(error.value)
    assert not cookie_file.exists()


def test_existing_invalid_jar_is_not_overwritten(tmp_path: Path):
    cookie_file = tmp_path / "cookies.txt"
    original = "not a Netscape cookie jar"
    cookie_file.write_text(original, encoding="utf-8")

    with pytest.raises(BilibiliCookieStoreError):
        update_bilibili_cookies(_valid_bili_export(), cookie_file=cookie_file)

    assert cookie_file.read_text(encoding="utf-8") == original


def test_atomic_failure_leaves_existing_jar_untouched(tmp_path: Path, monkeypatch):
    cookie_file = tmp_path / "cookies.txt"
    original = _netscape(_record(".youtube.com", "SID", "youtube-session"))
    cookie_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        bilibili_cookie.os,
        "replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("test failure")),
    )

    with pytest.raises(BilibiliCookieStoreError) as error:
        update_bilibili_cookies(_valid_bili_export(), cookie_file=cookie_file)

    assert "test-session-value" not in str(error.value)
    assert cookie_file.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".cookies.txt.*.tmp"))
