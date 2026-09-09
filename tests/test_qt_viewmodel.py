from datetime import datetime

from zhibo.models import Follower
from zhibo.monitor import FollowerStatus
from zhibo.plugins.base import LiveInfo
from qt_quick.viewmodel import matches_snapshot, sort_snapshots, status_snapshot, web_url_for_snapshot


def _status(*, live=False, state="offline", error=""):
    return FollowerStatus(
        follower=Follower(
            name="测试主播",
            plugin="streamlink",
            url="https://example.com/live",
            platform="bilibili",
            quality="best",
            tags=["游戏"],
        ),
        live_info=LiveInfo(is_live=live, title="测试标题", quality_name="蓝光"),
        last_check=datetime(2026, 1, 2, 3, 4, 5),
        check_state=state,
        error=error,
    )


def test_status_snapshot_is_detached_and_marks_confirmed_live():
    status = _status(live=True, state="online")

    row = status_snapshot(7, status)
    status.follower.tags.append("后来修改")

    assert row["idx"] == 7
    assert row["live"] is True
    assert row["last_check"] == "03:04:05"
    assert row["tags"] == ["游戏"]


def test_status_snapshot_redacts_sensitive_errors():
    row = status_snapshot(0, _status(error="cookie: session-secret"))

    assert "session-secret" not in row["error"]


def test_status_snapshot_redacts_legacy_signed_follower_url():
    status = _status()
    status.follower.url = "https://example.com/live?token=legacy-secret&room=1"

    row = status_snapshot(0, status)

    assert "legacy-secret" not in row["url"]
    assert "room=1" in row["url"]


def test_fs1_snapshot_builds_web_url_without_live_result(monkeypatch):
    monkeypatch.setattr("zhibo.plugins.fs1_plugin.get_fs_site_url", lambda: "https://www.fszb148.com")

    url = web_url_for_snapshot(
        {
            "url": "360907633",
            "configured_plugin": "fs1",
            "configured_platform": "fs1",
            "sport_id": "1",
            "live": False,
            "error": "403 Forbidden",
        }
    )

    assert url == "https://www.fszb148.com/broadcast/details?room_id=360907633&sport_id=1"


def test_native_filters_and_sorting_match_monitor_expectations():
    online = status_snapshot(1, _status(live=True, state="online"))
    offline = status_snapshot(2, _status())

    assert matches_snapshot(online, tag="游戏", state_filter="在线", search="测试")
    assert not matches_snapshot(offline, state_filter="在线")
    assert [row["idx"] for row in sort_snapshots([offline, online])] == [1, 2]


def test_column_sort_keeps_live_rows_first_and_handles_descending():
    offline_b = {"idx": 1, "live": False, "name": "乙主播", "platform": "douyu", "tags": ["游戏"]}
    offline_a = {"idx": 2, "live": False, "name": "甲主播", "platform": "bilibili", "tags": ["游戏"]}
    live_c = {"idx": 3, "live": True, "name": "丙主播", "platform": "twitch", "tags": ["游戏"]}

    # 中文按拼音排序：丙(bing) < 甲(jia) < 乙(yi)。
    rows = sort_snapshots([offline_b, live_c, offline_a], "name")
    assert [row["idx"] for row in rows] == [3, 2, 1]  # 在线优先，名称升序

    rows = sort_snapshots([offline_b, live_c, offline_a], "name", descending=True)
    assert [row["idx"] for row in rows] == [3, 1, 2]  # 名称降序，在线仍在前

    empty = {"idx": 4, "live": False, "name": "", "platform": "douyu", "tags": []}
    rows = sort_snapshots([empty, offline_a], "name")
    assert [row["idx"] for row in rows] == [2, 4]  # 空值沉底
