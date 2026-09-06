"""streamlink 通用直播插件 — 支持 138+ 平台"""
import asyncio
import time
from zhibo.proxy_config import proxy_for_platform
from .bilibili_quality import (
    BILIBILI_HEADERS,
    fetch_best_bilibili_stream,
    fetch_bilibili_live_status,
    fetch_bilibili_room_metadata,
    bilibili_quality_qn,
)
from .base import LiveStreamPlugin, LiveInfo
from .bounded_executor import BoundedExecutor, ExecutorBusyError, WorkerProcessError, run_bounded

_executor = BoundedExecutor(max_workers=8, thread_name_prefix="zhibo-streamlink")

CHECK_TIMEOUT = 12
HTTP_TIMEOUT = 6
STREAM_TIMEOUT = 10


def _streamlink_check_worker(url: str, quality: str, platform: str, proxy_url: str | None) -> LiveInfo:
    """Blocking streamlink work executed in an isolated child process."""
    from streamlink import Streamlink

    last_error = ""
    # Bilibili is preflighted through its lightweight room-status API.  A
    # second full Streamlink attempt can take 2 * HTTP_TIMEOUT + 1s and cannot
    # fit inside CHECK_TIMEOUT, so it used to manufacture deterministic outer
    # timeouts for slow/offline rooms.
    attempts = 1 if platform in {"twitch", "youtube", "bilibili"} else 2
    for attempt in range(attempts):
        try:
            session = Streamlink()
            if proxy_url:
                session.set_option("http-proxy", proxy_url)
            session.set_option("http-timeout", HTTP_TIMEOUT)
            session.set_option("stream-segment-timeout", HTTP_TIMEOUT)
            session.set_option("stream-timeout", STREAM_TIMEOUT)
            session.set_option("webbrowser-timeout", HTTP_TIMEOUT)
            _, plugin_cls, resolved_url = session.resolve_url(url)
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


class StreamlinkPlugin(LiveStreamPlugin):
    name = "streamlink"

    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        quality = kwargs.get("quality", "best")
        platform = str(kwargs.get("platform", "")).strip().casefold()
        proxy_url = proxy_for_platform(platform)

        if platform == "bilibili":
            bili_result = await self._check_bilibili(url, quality)
            if bili_result is not None:
                return bili_result

        try:
            result = await run_bounded(
                _executor,
                _streamlink_check_worker,
                url,
                quality,
                platform,
                proxy_url,
                timeout=CHECK_TIMEOUT,
            )
        except ExecutorBusyError:
            return LiveInfo(is_live=False, extra={"error": "检测繁忙，请稍后重试", "proxy": bool(proxy_url), "busy": True})
        except asyncio.TimeoutError:
            return LiveInfo(is_live=False, extra={"error": "检测超时", "proxy": bool(proxy_url)})
        except WorkerProcessError as e:
            return LiveInfo(is_live=False, extra={"error": str(e), "proxy": bool(proxy_url)})

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

        if result.is_live and bilibili_quality_qn(platform, quality) is not None:
            try:
                best_bili = await fetch_best_bilibili_stream(url, quality=quality)
                if best_bili and best_bili.get("url"):
                    result.stream_url = best_bili["url"]
                    result.m3u8_url = best_bili["url"] if ".m3u8" in best_bili["url"] else ""
                    result.flv_url = best_bili["url"] if ".flv" in best_bili["url"] else ""
                    result.quality_name = best_bili.get("label") or quality
                    result.extra["bilibili_qn"] = best_bili.get("qn")
                    result.extra["bilibili_codec"] = best_bili.get("codec")
                    result.extra["bilibili_format"] = best_bili.get("format")
                    result.extra["bilibili_protocol"] = best_bili.get("protocol")
                    result.extra["stream_candidates"] = list(best_bili.get("candidates") or [])
                    if best_bili.get("quality_warning"):
                        result.extra["error"] = best_bili["quality_warning"]
                        result.extra["nonfatal_error"] = True
            except Exception as e:
                result.extra["bilibili_quality_error"] = str(e)

        return result

    async def _check_bilibili(self, url: str, quality: str) -> LiveInfo | None:
        """Check room state first and bypass Streamlink for best-quality live rooms.

        ``None`` means the room is live but a non-best quality was explicitly
        requested, so the generic Streamlink resolver should continue.
        """
        try:
            is_live = await fetch_bilibili_live_status(url)
        except Exception as exc:
            text = f"{type(exc).__name__} {exc}".casefold()
            error = "B站状态探测超时" if "timeout" in text or "timed out" in text else "B站状态探测失败"
            return LiveInfo(
                is_live=False,
                extra={"error": error, "bilibili_probe_error": type(exc).__name__},
            )

        if not is_live:
            return LiveInfo(is_live=False, extra={"bilibili_status": "offline"})
        if bilibili_quality_qn("bilibili", quality) is None:
            return None

        selected_result, metadata_result = await asyncio.gather(
            fetch_best_bilibili_stream(url, quality=quality),
            fetch_bilibili_room_metadata(url),
            return_exceptions=True,
        )
        if isinstance(selected_result, Exception):
            exc = selected_result
            text = f"{type(exc).__name__} {exc}".casefold()
            error = "B站直播流接口超时" if "timeout" in text or "timed out" in text else "B站直播流接口请求失败"
            return LiveInfo(
                is_live=False,
                extra={"error": error, "bilibili_quality_error": type(exc).__name__},
            )
        selected = selected_result
        if not selected or not selected.get("url"):
            return LiveInfo(
                is_live=False,
                extra={"error": "B站已开播，但接口未返回可用直播流"},
            )

        stream_url = str(selected["url"])
        metadata = metadata_result if isinstance(metadata_result, dict) else {}
        warning = str(selected.get("quality_warning") or "")
        extra = {
            "headers": dict(BILIBILI_HEADERS),
            "bilibili_status": "live",
            "bilibili_qn": selected.get("qn"),
            "bilibili_codec": selected.get("codec"),
            "bilibili_format": selected.get("format"),
            "bilibili_protocol": selected.get("protocol"),
            "used_cookie": bool(selected.get("used_cookie")),
            "stream_candidates": list(selected.get("candidates") or []),
        }
        if warning:
            extra["error"] = warning
            extra["nonfatal_error"] = True
        return LiveInfo(
            is_live=True,
            title=str(metadata.get("title") or ""),
            stream_url=stream_url,
            m3u8_url=stream_url if ".m3u8" in stream_url else "",
            flv_url=stream_url if ".flv" in stream_url else "",
            quality_name=str(selected.get("label") or quality),
            extra=extra,
        )

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
