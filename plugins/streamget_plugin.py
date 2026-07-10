"""streamget 通用直播插件 — 支持 47+ 平台"""
import asyncio
import httpx
import streamget as _streamget
from proxy_config import proxy_for_platform
from .bilibili_quality import fetch_best_bilibili_stream, wants_best_bilibili_quality
from .base import LiveStreamPlugin, LiveInfo

from streamget import (
    DouyinLiveStream, DouyuLiveStream, HuyaLiveStream,
    BilibiliLiveStream, YoutubeLiveStream, TwitchLiveStream,
    ChzzkLiveStream, TikTokLiveStream, TwitCastingLiveStream,
)


def _optional_stream_class(name: str):
    return getattr(_streamget, name, None)

STREAMGET_PLATFORMS = {
    "douyin": DouyinLiveStream,
    "douyu": DouyuLiveStream,
    "huya": HuyaLiveStream,
    "bilibili": BilibiliLiveStream,
    "youtube": YoutubeLiveStream,
    "twitch": TwitchLiveStream,
    "chzzk": ChzzkLiveStream,
    "tiktok": TikTokLiveStream,
    "twitcasting": TwitCastingLiveStream,
}

_rednote_stream = _optional_stream_class("RedNoteLiveStream")
if _rednote_stream is not None:
    STREAMGET_PLATFORMS["rednote"] = _rednote_stream

PLATFORM_NAMES = {
    "douyin": "抖音",
    "douyu": "斗鱼",
    "huya": "虎牙",
    "bilibili": "B站",
    "youtube": "YouTube",
    "twitch": "Twitch",
    "chzzk": "Chzzk",
    "tiktok": "TikTok",
    "twitcasting": "TwitCasting",
    "rednote": "RedNote",
}

def normalize_platform_name(name: str | None) -> str:
    """Return the canonical lookup form for platform names."""
    if name is None:
        return ""
    return str(name).strip().casefold()


# 中文别名 → 英文 key
PLATFORM_ALIASES: dict[str, str] = {}
for _key, _name in PLATFORM_NAMES.items():
    PLATFORM_ALIASES[normalize_platform_name(_name)] = _key
# 额外的常用别名
PLATFORM_ALIASES[normalize_platform_name("哔哩哔哩")] = "bilibili"
PLATFORM_ALIASES[normalize_platform_name("b站")] = "bilibili"
PLATFORM_ALIASES[normalize_platform_name("xiaohongshu")] = "rednote"
PLATFORM_ALIASES[normalize_platform_name("xhs")] = "rednote"
PLATFORM_ALIASES[normalize_platform_name("redbook")] = "rednote"
PLATFORM_ALIASES[normalize_platform_name("\u5c0f\u7ea2\u4e66")] = "rednote"


def resolve_platform(name: str) -> str | None:
    """将平台名（中文或英文）解析为英文 key"""
    normalized_name = normalize_platform_name(name)
    if normalized_name in STREAMGET_PLATFORMS:
        return normalized_name
    if normalized_name in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[normalized_name]
    return None


def get_display_name(key: str) -> str:
    """获取平台的显示名称"""
    return PLATFORM_NAMES.get(key, key)


def _rednote_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.xiaohongshu.com/",
        "Origin": "https://www.xiaohongshu.com",
        "Accept": "*/*",
    }


async def _stream_url_is_reachable(url: str, headers: dict[str, str] | None = None) -> bool:
    if not url:
        return False
    request_headers = dict(headers or {})
    if ".m3u8" not in url:
        request_headers["Range"] = "bytes=0-1023"
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers=request_headers) as client:
            resp = await client.get(url)
    except Exception:
        return False
    return resp.status_code in {200, 206}


async def _select_rednote_stream_url(stream_obj) -> tuple[str, str, str, dict]:
    headers = _rednote_headers()
    m3u8_url = getattr(stream_obj, "m3u8_url", "") or ""
    flv_url = getattr(stream_obj, "flv_url", "") or ""
    if await _stream_url_is_reachable(m3u8_url, headers):
        return m3u8_url, m3u8_url, flv_url, headers
    if await _stream_url_is_reachable(flv_url, headers):
        return flv_url, m3u8_url, flv_url, headers
    return "", m3u8_url, flv_url, headers


class StreamgetPlugin(LiveStreamPlugin):
    name = "streamget"

    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        raw_platform = kwargs.get("platform", "")
        quality = kwargs.get("quality", "best")

        platform_key = resolve_platform(raw_platform)
        if platform_key is None:
            return LiveInfo(is_live=False, extra={"error": f"未知平台: {raw_platform}"})

        cls = STREAMGET_PLATFORMS[platform_key]
        proxy_url = proxy_for_platform(platform_key)

        try:
            stream = cls(proxy_addr=proxy_url)
            fetch_data = getattr(stream, "fetch_web_stream_data", None) or getattr(stream, "fetch_app_stream_data")
            timeout = 8 if platform_key in {"twitch", "youtube"} else 15
            data = await asyncio.wait_for(fetch_data(url, process_data=True), timeout=timeout)
        except asyncio.TimeoutError:
            return LiveInfo(is_live=False, extra={"error": "检测超时", "platform": get_display_name(platform_key), "proxy": bool(proxy_url)})
        except Exception as e:
            return LiveInfo(is_live=False, extra={"error": str(e), "platform": get_display_name(platform_key), "proxy": bool(proxy_url)})

        try:
            stream_obj = await stream.fetch_stream_url(data, quality)
        except Exception:
            stream_obj = None

        if stream_obj is None:
            stream_obj = await stream.fetch_stream_url(data)

        extra = {"platform": get_display_name(platform_key), "proxy": bool(proxy_url)}
        record_url = getattr(stream_obj, "record_url", "") or ""
        stream_url = stream_obj.flv_url or stream_obj.m3u8_url or record_url
        m3u8_url = stream_obj.m3u8_url or ""
        flv_url = stream_obj.flv_url or ""
        quality_name = stream_obj.quality or quality

        if stream_obj.is_live and wants_best_bilibili_quality(platform_key, quality):
            try:
                best_bili = await fetch_best_bilibili_stream(url)
                if best_bili and best_bili.get("url"):
                    stream_url = best_bili["url"]
                    m3u8_url = best_bili["url"] if ".m3u8" in best_bili["url"] else ""
                    flv_url = best_bili["url"] if ".flv" in best_bili["url"] else ""
                    quality_name = best_bili.get("label") or quality_name
                    extra["bilibili_qn"] = best_bili.get("qn")
                    extra["bilibili_codec"] = best_bili.get("codec")
                    extra["bilibili_format"] = best_bili.get("format")
                    extra["bilibili_protocol"] = best_bili.get("protocol")
                    if best_bili.get("quality_warning"):
                        extra["error"] = best_bili["quality_warning"]
                        extra["nonfatal_error"] = True
            except Exception as e:
                extra["bilibili_quality_error"] = str(e)

        if platform_key == "rednote" and stream_obj.is_live:
            stream_url, m3u8_url, flv_url, headers = await _select_rednote_stream_url(stream_obj)
            extra["headers"] = headers
            if not stream_url:
                extra["error"] = "小红书直播源地址不可达，streamget 返回的 FLV/M3U8 当前为 403/404"
                extra["candidate_m3u8_url"] = m3u8_url
                extra["candidate_flv_url"] = flv_url
                return LiveInfo(
                    is_live=False,
                    anchor_name=stream_obj.anchor_name or data.get("anchor_name", "") or data.get("nickname", ""),
                    title=stream_obj.title or data.get("title", "") or data.get("room_title", ""),
                    quality_name=stream_obj.quality or quality,
                    extra=extra,
                )

        return LiveInfo(
            is_live=stream_obj.is_live if stream_obj.is_live else False,
            anchor_name=stream_obj.anchor_name or data.get("anchor_name", "") or data.get("nickname", ""),
            title=stream_obj.title or data.get("title", "") or data.get("room_title", ""),
            stream_url=stream_url,
            m3u8_url=m3u8_url,
            flv_url=flv_url,
            quality_name=quality_name,
            extra=extra,
        )

    async def get_stream_url(self, url: str, quality: str, **kwargs) -> str:
        info = await self.check_live(url, quality=quality, **kwargs)
        if not info.is_live:
            raise RuntimeError("主播未开播")
        url = info.flv_url or info.m3u8_url or info.stream_url
        if not url:
            raise RuntimeError("无法获取流地址")
        return url


from . import register_plugin

register_plugin(StreamgetPlugin())
