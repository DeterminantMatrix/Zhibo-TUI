"""streamlink 通用直播插件 — 支持 138+ 平台"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from proxy_config import proxy_for_platform
from .bilibili_quality import fetch_best_bilibili_stream, wants_best_bilibili_quality
from .base import LiveStreamPlugin, LiveInfo

_executor = ThreadPoolExecutor(max_workers=8)

CHECK_TIMEOUT = 12
HTTP_TIMEOUT = 6
STREAM_TIMEOUT = 10


class StreamlinkPlugin(LiveStreamPlugin):
    name = "streamlink"

    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        quality = kwargs.get("quality", "best")
        platform = str(kwargs.get("platform", "")).strip().casefold()
        proxy_url = proxy_for_platform(platform)

        def _fetch():
            from streamlink import Streamlink
            last_error = ""
            attempts = 1 if platform in {"twitch", "youtube"} else 2
            for attempt in range(attempts):
                try:
                    session = Streamlink()
                    if proxy_url:
                        session.set_option("http-proxy", proxy_url)
                    session.set_option("http-timeout", HTTP_TIMEOUT)
                    session.set_option("stream-segment-timeout", HTTP_TIMEOUT)
                    session.set_option("stream-timeout", STREAM_TIMEOUT)
                    session.set_option("webbrowser-timeout", HTTP_TIMEOUT)
                    name, plugin_cls, resolved_url = session.resolve_url(url)
                    plugin = plugin_cls(session, resolved_url)
                    streams = plugin.streams()
                    if not streams:
                        return LiveInfo(is_live=False)
                    stream = streams.get(quality, streams.get("best"))
                    stream_url = stream.to_url() if stream else ""
                    headers = dict(session.http.headers)

                    metadata = plugin.get_metadata() or {}
                    anchor_name = metadata.get("author") or ""
                    title = metadata.get("title") or ""

                    return LiveInfo(
                        is_live=True,
                        anchor_name=anchor_name,
                        title=title,
                        stream_url=stream_url,
                        quality_name=quality,
                        extra={"headers": headers, "proxy": bool(proxy_url)},
                    )
                except Exception as e:
                    last_error = str(e)
                    if attempt + 1 < attempts:
                        time.sleep(1)
            return LiveInfo(is_live=False, extra={"error": last_error, "proxy": bool(proxy_url)})

        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(_executor, _fetch),
                timeout=CHECK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return LiveInfo(is_live=False, extra={"error": "检测超时", "proxy": bool(proxy_url)})

        # 如果 streamlink 没有提取到标题/主播名，用 streamget 回退
        if result.is_live and (not result.title or not result.anchor_name):
            try:
                sg_info = await self._streamget_meta(url, kwargs)
                if sg_info:
                    if not result.title and sg_info.title:
                        result.title = sg_info.title
                    if not result.anchor_name and sg_info.anchor_name:
                        result.anchor_name = sg_info.anchor_name
            except Exception:
                pass

        if result.is_live and wants_best_bilibili_quality(platform, quality):
            try:
                best_bili = await fetch_best_bilibili_stream(url)
                if best_bili and best_bili.get("url"):
                    result.stream_url = best_bili["url"]
                    result.m3u8_url = best_bili["url"] if ".m3u8" in best_bili["url"] else ""
                    result.flv_url = best_bili["url"] if ".flv" in best_bili["url"] else ""
                    result.quality_name = best_bili.get("label") or quality
                    result.extra["bilibili_qn"] = best_bili.get("qn")
                    result.extra["bilibili_codec"] = best_bili.get("codec")
                    result.extra["bilibili_format"] = best_bili.get("format")
                    result.extra["bilibili_protocol"] = best_bili.get("protocol")
                    if best_bili.get("quality_warning"):
                        result.extra["error"] = best_bili["quality_warning"]
                        result.extra["nonfatal_error"] = True
            except Exception as e:
                result.extra["bilibili_quality_error"] = str(e)

        return result

    async def _streamget_meta(self, url: str, kwargs: dict) -> LiveInfo | None:
        """用 streamget 获取元数据作为回退"""
        from .streamget_plugin import resolve_platform, STREAMGET_PLATFORMS

        platform = kwargs.get("platform", "")
        platform_key = resolve_platform(platform)
        if not platform_key:
            return None

        cls = STREAMGET_PLATFORMS.get(platform_key)
        if not cls:
            return None

        try:
            stream = cls(proxy_addr=proxy_for_platform(platform_key))
            data = await asyncio.wait_for(
                stream.fetch_web_stream_data(url, process_data=True),
                timeout=10,
            )
            stream_obj = None
            try:
                quality = kwargs.get("quality", "best")
                stream_obj = await asyncio.wait_for(
                    stream.fetch_stream_url(data, quality),
                    timeout=10,
                )
            except Exception:
                try:
                    stream_obj = await asyncio.wait_for(
                        stream.fetch_stream_url(data),
                        timeout=10,
                    )
                except Exception:
                    stream_obj = None

            return LiveInfo(
                is_live=True,
                anchor_name=(
                    getattr(stream_obj, "anchor_name", "")
                    or data.get("anchor_name", "")
                    or data.get("nickname", "")
                ),
                title=(
                    getattr(stream_obj, "title", "")
                    or data.get("title", "")
                    or data.get("room_title", "")
                ),
            )
        except Exception:
            return None

    async def get_stream_url(self, url: str, quality: str, **kwargs) -> str:
        info = await self.check_live(url, quality=quality, **kwargs)
        if not info.is_live:
            raise RuntimeError("主播未开播")
        if not info.stream_url:
            raise RuntimeError("无法获取流地址")
        return info.stream_url


from . import register_plugin

register_plugin(StreamlinkPlugin())
