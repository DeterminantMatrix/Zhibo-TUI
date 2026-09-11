"""Build follower config entries from live room URLs."""
from __future__ import annotations

from urllib.parse import parse_qs, unquote_plus, urlparse

from zhibo.app_logging import is_sensitive_field
from zhibo.plugins import get_plugin
from zhibo.plugins.fs1_plugin import is_fs_site_url
from zhibo.models import Follower


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


def _contains_sensitive_url_component(parsed) -> bool:
    """Reject credential-bearing room links before any plugin/network lookup.

    Import links are normally public room URLs.  A signed playback URL or a
    copied authenticated browser URL must not be handed to a plugin merely to
    build a preview: plugins may log or request it before later persistence
    validation gets a chance to reject it.  Decode query-key spelling so an
    encoded ``to%6ben`` cannot bypass the check.
    """
    for component in (parsed.query, parsed.fragment):
        for pair in component.replace(";", "&").split("&"):
            raw_key = pair.partition("=")[0]
            key = unquote_plus(raw_key).strip()
            if key and is_sensitive_field(key):
                return True
    return False


def _parse_http_url(url: str):
    try:
        parsed = urlparse(url)
        parsed.port
    except ValueError as e:
        raise ValueError("A live room URL must contain a valid hostname and port") from e
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Only HTTP(S) live room URLs are supported")
    if _contains_sensitive_url_component(parsed):
        raise ValueError("直播间网址不能包含令牌、签名或认证参数")
    return parsed


def _host_matches(host: str, domain: str) -> bool:
    host = host.casefold().rstrip(".")
    domain = domain.casefold()
    return host == domain or host.endswith("." + domain)


def detect_platform(url: str) -> tuple[str, str, list[str]]:
    normalized_url = _normalize_url(url)
    parsed = _parse_http_url(normalized_url)
    host = parsed.hostname.casefold()
    if is_fs_site_url(normalized_url):
        return "fs1", "fs1", []
    for domain, platform, plugin, fallbacks in PLATFORM_RULES:
        if _host_matches(host, domain):
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
    initial = urlparse(url)
    if initial.scheme and initial.scheme.casefold() not in {"http", "https"}:
        raise ValueError("Only HTTP(S) live room URLs are supported")
    if initial.scheme and not initial.netloc:
        raise ValueError("A live room URL must include a hostname")
    if url.startswith("//"):
        url = "https:" + url
    elif not initial.scheme:
        url = "https://" + url
    _parse_http_url(url)
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
