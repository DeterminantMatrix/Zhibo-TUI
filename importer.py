"""Build follower config entries from live room URLs."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from plugins import get_plugin
from plugins.fs1_plugin import is_fs_site_url
from models import Follower


PLATFORM_RULES = [
    ("youtube.com", "youtube", "yt_dlp", ["streamget", "streamlink"]),
    ("youtu.be", "youtube", "yt_dlp", ["streamget", "streamlink"]),
    ("huya.com", "huya", "streamlink", ["streamget"]),
    ("douyu.com", "douyu", "streamget", ["streamlink"]),
    ("live.bilibili.com", "bilibili", "streamlink", ["streamget"]),
    ("live.douyin.com", "douyin", "streamlink", ["streamget"]),
    ("xiaohongshu.com", "rednote", "streamget", []),
    ("xhslink.com", "rednote", "streamget", []),
    ("twitch.tv", "twitch", "streamlink", ["streamget"]),
]


def detect_platform(url: str) -> tuple[str, str, list[str]]:
    host = urlparse(url).netloc.casefold()
    if is_fs_site_url(url):
        return "fs1", "fs1", []
    for domain, platform, plugin, fallbacks in PLATFORM_RULES:
        if domain in host:
            return platform, plugin, fallbacks
    raise ValueError("暂不支持该直播间网址")


def fallback_name(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    room_id = query.get("room_id", [""])[0]
    if room_id:
        return room_id
    path = parsed.path.strip("/")
    if path:
        return path.split("/")[-1]
    return parsed.netloc or "新主播"


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("请输入直播间网址")
    if "://" not in url:
        url = "https://" + url
    return url


def _fs_extra(url: str) -> tuple[str, dict]:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    room_id = query.get("room_id", [""])[0].strip()
    sport_id = query.get("sport_id", ["1"])[0].strip() or "1"
    if not room_id:
        raise ValueError("飞速直播网址缺少 room_id")
    return room_id, {"sport_id": sport_id}


async def build_follower_from_url(url: str, tag: str = "未分类") -> Follower:
    url = _normalize_url(url)
    tag = tag.strip() or "未分类"

    platform, plugin_name, fallbacks = detect_platform(url)
    room_url = url
    extra: dict = {}
    if platform == "fs1":
        room_url, extra = _fs_extra(url)

    name = fallback_name(url)
    plugin = get_plugin(plugin_name)
    if plugin is not None:
        try:
            info = await plugin.check_live(room_url, platform=platform, quality="best", extra=extra)
            if platform == "fs1":
                name = info.title or info.anchor_name or name
            else:
                name = info.anchor_name or name
        except Exception:
            pass

    return Follower(
        name=name,
        plugin=plugin_name,
        fallback_plugins=fallbacks,
        platform=platform,
        url=room_url,
        quality="best",
        tags=[tag],
        extra=extra,
        enabled=True,
    )
