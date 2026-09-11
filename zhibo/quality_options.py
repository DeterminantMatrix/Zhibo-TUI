from __future__ import annotations

import re


BEST = {"label": "最优画质", "value": "best"}

STREAMLINK_OPTIONS = (
    BEST,
    {"label": "高清（1080p）", "value": "1080p"},
    {"label": "流畅（720p）", "value": "720p"},
    {"label": "最低画质", "value": "worst"},
)

STREAMGET_OPTIONS = (
    BEST,
    {"label": "超清（UHD）", "value": "UHD"},
    {"label": "高清（HD）", "value": "HD"},
    {"label": "流畅（LD）", "value": "LD"},
)

BILIBILI_OPTIONS = (
    BEST,
    {"label": "蓝光", "value": "蓝光"},
    {"label": "高清", "value": "高清"},
    {"label": "流畅", "value": "流畅"},
)

FS1_OPTIONS = (
    BEST,
    {"label": "蓝光真码", "value": "lgzm"},
    {"label": "高清真码", "value": "gqzm"},
    {"label": "标清真码", "value": "bqzm"},
)

YTDLP_OPTIONS = (
    BEST,
    {"label": "1080p", "value": "1080p"},
    {"label": "720p", "value": "720p"},
    {"label": "360p", "value": "360p"},
)


def _plugin_key(plugin: str | None) -> str:
    value = str(plugin or "").strip().casefold().replace("-", "_")
    if value.endswith("_plugin"):
        value = value[:-7]
    return value


def quality_options(
    plugin: str | None,
    platform: str | None,
    current: str | None = None,
) -> list[dict[str, str]]:
    """Return safe UI choices understood by the configured primary plugin."""
    plugin_key = _plugin_key(plugin)
    platform_key = str(platform or "").strip().casefold()
    if platform_key == "bilibili" and plugin_key in {"streamlink", "streamget"}:
        source = BILIBILI_OPTIONS
    elif plugin_key == "streamget":
        source = STREAMGET_OPTIONS
    elif plugin_key == "streamlink":
        source = STREAMLINK_OPTIONS
    elif plugin_key == "fs1":
        source = FS1_OPTIONS
    elif plugin_key == "yt_dlp":
        source = YTDLP_OPTIONS
    else:
        source = (BEST,)

    result = [dict(item) for item in source]
    configured = str(current or "best").strip() or "best"
    if configured.casefold() not in {item["value"].casefold() for item in result}:
        # Keep a legacy or previously selected plugin-native value visible,
        # while retaining best, one middle tier, and the lowest tier.
        compact = [
            result[0],
            {"label": f"当前选择（{configured}）", "value": configured},
        ]
        if len(result) > 1:
            compact.append(result[1])
        if len(result) > 2 and result[-1]["value"].casefold() not in {
            item["value"].casefold() for item in compact
        }:
            compact.append(result[-1])
        result = compact
    return result


def normalize_quality_choice(plugin: str | None, platform: str | None, value: str | None) -> str:
    requested = str(value or "best").strip() or "best"
    options = quality_options(plugin, platform, requested)
    for item in options:
        if item["value"].casefold() == requested.casefold():
            return item["value"]
    return "best"


def quality_for_plugin(
    value: str | None,
    *,
    source_plugin: str | None,
    target_plugin: str | None,
    platform: str | None,
) -> str:
    """Translate a saved selector when monitor fallback changes plugin families."""
    requested = str(value or "best").strip() or "best"
    source = _plugin_key(source_plugin)
    target = _plugin_key(target_plugin)
    platform_key = str(platform or "").strip().casefold()
    if source == target or platform_key == "bilibili":
        return requested

    folded = requested.casefold()
    if target == "streamget":
        if folded in {"best", "source", "od", "原画", "最高", "最高画质"}:
            return "OD"
        if folded in {"4k", "2160p", "2160p60", "1440p", "1440p60", "bd", "蓝光"}:
            return "BD"
        if folded in {"1080p", "1080p60", "uhd", "超清"}:
            return "UHD"
        if folded in {"720p", "720p60", "hd", "高清"}:
            return "HD"
        if folded in {"480p", "sd", "标清"}:
            return "SD"
        if folded in {"audio_only", "audio", "ad"}:
            return "AD"
        if folded in {"worst", "360p", "ld", "流畅"}:
            return "LD"
        return "OD"

    if target == "streamlink":
        mapping = {
            "od": "best", "bd": "best", "uhd": "1080p", "hd": "720p",
            "sd": "480p", "ld": "360p", "ad": "audio_only",
            "原画": "best", "蓝光": "best", "超清": "1080p",
            "高清": "720p", "标清": "480p", "流畅": "360p",
        }
        return mapping.get(folded, requested if re.fullmatch(r"(?:best|worst|audio_only|\d{3,4}p(?:\d{2})?)", folded) else "best")

    return requested
