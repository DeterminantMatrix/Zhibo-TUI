from __future__ import annotations

from urllib.parse import urlencode

from zhibo.app_logging import redact_sensitive_text, redact_url
from zhibo.monitor import FollowerStatus


PLATFORM_DISPLAY_NAMES = {"fs1": "飞速"}

# 可排序的表格列（与 model.COLUMNS 一一对应；default 为在线优先的监控视图）。
SORTABLE_COLUMNS = (
    "default", "tags", "name", "platform", "title", "quality",
    "last_check", "health", "plugin", "error",
)


def web_url_for_snapshot(row: dict) -> str:
    """Return a navigable page URL even when a live check has failed.

    FS1 followers store an opaque room id rather than a web URL. Opening the
    page is a static navigation action, so it must not depend on ``live`` or a
    resolved stream URL. The configured FS site origin is loaded lazily to
    keep this view-model helper side-effect free for other platforms.
    """
    raw_url = str(row.get("url") or "").strip()
    if raw_url.startswith(("http://", "https://")):
        return raw_url

    plugin = str(row.get("configured_plugin") or row.get("plugin") or "").casefold()
    platform = str(row.get("configured_platform") or row.get("platform") or "").casefold()
    if plugin == "fs1" or platform in {"fs1", "飞速"}:
        if not raw_url:
            return ""
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        sport_id = str(row.get("sport_id") or extra.get("sport_id") or "1").strip() or "1"
        from zhibo.plugins.fs1_plugin import get_fs_site_url

        query = urlencode({"room_id": raw_url, "sport_id": sport_id})
        return f"{get_fs_site_url()}/broadcast/details?{query}"

    return raw_url


def status_snapshot(idx: int, status: FollowerStatus) -> dict:
    """Copy mutable monitor state into a thread-safe, redacted UI payload."""
    follower = status.follower
    info = status.live_info
    platform = str(info.extra.get("platform") or follower.platform or "-")
    error = "已禁用" if not follower.enabled else status.error or info.extra.get("error", "")
    return {
        "idx": idx,
        "enabled": bool(follower.enabled),
        "live": bool(info.is_live and status.check_state == "online"),
        "checking": bool(status.is_checking),
        "state": status.check_state,
        "tags": list(follower.tags),
        "name": follower.name,
        "platform": PLATFORM_DISPLAY_NAMES.get(platform, platform),
        "title": info.title or "-",
        "quality": info.quality_name or follower.quality or "-",
        "configured_quality": follower.quality or "best",
        "configured_plugin": follower.plugin or "",
        "configured_platform": follower.platform or "",
        "last_check": status.last_check.strftime("%H:%M:%S") if status.last_check else "-",
        "health": status.metadata_health or "-",
        "plugin": str(info.extra.get("plugin_used") or follower.plugin or "-"),
        "error": redact_sensitive_text(str(error)) if error else "-",
        "sport_id": str((follower.extra or {}).get("sport_id") or "1"),
        # Legacy CSV files may still contain a signed URL.  This payload is
        # shared with Qt/QML and must not move such a value into the UI.
        "url": redact_url(follower.url),
    }


def matches_snapshot(row: dict, tag: str = "全部", state_filter: str = "全部", search: str = "") -> bool:
    if tag != "全部" and tag not in row.get("tags", []):
        return False
    if state_filter == "在线" and not row.get("live"):
        return False
    if state_filter == "离线" and (row.get("live") or row.get("error") not in {"", "-"}):
        return False
    if state_filter == "异常" and row.get("error") in {"", "-"}:
        return False
    needle = search.strip().casefold()
    if not needle:
        return True
    haystack = " ".join(
        str(value)
        for value in (
            row.get("name", ""),
            row.get("platform", ""),
            row.get("title", ""),
            row.get("plugin", ""),
            row.get("error", ""),
            " ".join(row.get("tags", [])),
        )
    ).casefold()
    return needle in haystack


def sort_snapshots(
    rows: list[dict],
    sort_key: str = "default",
    descending: bool = False,
) -> list[dict]:
    """按指定列排序；默认保持"在线优先→标签→平台→主播"的监控视图。

    任何列排序都以在线状态作为第一优先级（在线行始终在离线行之前），
    这是直播监控工具的通行做法：点列头是为了在组内定位条目，
    而不是让在线主播沉底。空值排到最后，避免空单元格抢占顶部。
    """
    if sort_key == "default":
        return sorted(
            rows,
            key=lambda row: (
                not row.get("live", False),
                ", ".join(row.get("tags", [])),
                row.get("platform", ""),
                row.get("name", ""),
            ),
            reverse=descending,
        )

    def column_value(row: dict) -> str:
        if sort_key == "tags":
            return ", ".join(row.get("tags", [])).casefold()
        text = str(row.get(sort_key, "") or "").strip().casefold()
        return text if text else "\uffff"

    # 两段稳定排序：先按列值（支持降序），再把在线行稳定提前，
    # 保证任何排序方向下在线主播都不会沉底。
    ordered = sorted(rows, key=column_value, reverse=descending)
    ordered.sort(key=lambda row: not row.get("live", False))
    return ordered
