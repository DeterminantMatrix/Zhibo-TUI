"""yt-dlp 插件 — YouTube 直播检测、流地址获取、格式列举与视频下载。"""
from __future__ import annotations

import asyncio
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from proxy_config import proxy_for_platform
from .base import LiveStreamPlugin, LiveInfo

_executor = ThreadPoolExecutor(max_workers=4)

CHECK_TIMEOUT = 15
YTDLP_SOCKET_TIMEOUT = 6

DEFAULT_DOWNLOAD_DIR = Path.home() / "Videos" / "Youtube"

# ANSI 转义序列正则
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _is_youtube_url(url: str) -> bool:
    return "youtube.com" in url.casefold() or "youtu.be" in url.casefold()


def _clean_error(error: str) -> str:
    """剥离 ANSI 转义码并翻译常见 yt-dlp 错误。"""
    text = _ANSI_RE.sub('', str(error)).strip()
    if 'Sign in to confirm' in text or 'sign in' in text.lower():
        return '需要登录：该视频需要 YouTube 登录验证。\n请在浏览器登录 YouTube 后，用 Get cookies.txt 扩展导出 cookies.txt 放到项目根目录'
    if 'Requested format is not available' in text:
        return '格式不可用：该视频可能不支持所选画质，请尝试其他格式或视频'
    if 'Video unavailable' in text:
        return text.replace('Video unavailable', '视频不可用')
    if 'Private video' in text:
        return '私密视频，无法访问'
    if 'This video is not available' in text:
        return '该视频不可用（可能被删除或设为私密）'
    if 'cookie' in text.lower():
        return 'Cookie 无效或已过期，请重新导出 cookies.txt'
    if 'ERROR:' in text:
        return text.split('ERROR:', 1)[-1].strip()
    return text


@dataclass
class FormatInfo:
    """yt-dlp 可下载格式信息"""
    format_id: str
    resolution: str      # e.g. "1920x1080"
    codec: str           # e.g. "avc1" / "vp9"
    fps: float | None
    filesize: int | None # bytes
    note: str            # e.g. "1080p" / "720p60"
    ext: str             # e.g. "mp4" / "webm"
    has_audio: bool = False  # 是否自带音频（无需 ffmpeg）

    @property
    def label(self) -> str:
        """格式选择列表中展示的文字"""
        parts = [self.note or self.resolution or "?"]
        if self.codec:
            parts.append(self.codec)
        if self.fps and self.fps > 30:
            parts.append(f"{int(self.fps)}fps")
        if self.filesize:
            size_mb = self.filesize / (1024 * 1024)
            parts.append(f"{size_mb:.1f}MB")
        if not self.has_audio:
            parts.append("[无音频]")
        return "  ".join(parts)


def _base_ytdlp_opts(
    extra: dict | None = None,
    *,
    use_cookies: bool = False,
    proxy_url: str | None = None,
) -> dict:
    """构建 yt-dlp 基础选项。cookies 默认关闭（避免 JS 签名验证问题），仅在需要登录时开启。"""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": YTDLP_SOCKET_TIMEOUT,
        "extractor_retries": 1,
        "retries": 1,
        "fragment_retries": 1,
        "noplaylist": True,
    }
    if use_cookies:
        cookie_file = Path(__file__).parent.parent / "cookies.txt"
        if cookie_file.exists():
            opts["cookiefile"] = str(cookie_file)
    if proxy_url:
        opts["proxy"] = proxy_url
    if extra:
        opts.update(extra)
    return opts


def _try_with_cookies_fallback(func, url: str, proxy_url: str | None = None):
    """先不用 cookies 调用，遇到 Sign in 错误时自动用 cookies 重试。"""
    from yt_dlp import YoutubeDL

    def _do_extract(use_cookies: bool):
        opts = _base_ytdlp_opts({
            "extract_flat": False,
            "skip_download": True,
            "ignore_no_formats_error": True,
        }, use_cookies=use_cookies, proxy_url=proxy_url)
        with YoutubeDL(opts) as ydl:
            return func(ydl.extract_info(url, download=False))

    try:
        return _do_extract(use_cookies=False)
    except Exception as e:
        err_raw = str(e).lower()
        # 直接匹配英文原文，避免翻译文本变化导致误判
        if 'sign in' not in err_raw:
            raise
    return _do_extract(use_cookies=True)


class YtDlpPlugin(LiveStreamPlugin):
    """使用 yt-dlp 检测 YouTube 直播并获取流地址。"""

    name = "yt_dlp"

    # ── 核心插件接口 ──────────────────────────────────────────

    async def check_live(self, url: str, **kwargs) -> LiveInfo:
        """检测 YouTube 直播间/视频是否正在直播。"""
        quality = kwargs.get("quality", "best")
        platform = str(kwargs.get("platform", "youtube")).strip().casefold() or "youtube"
        proxy_url = proxy_for_platform(platform)

        def _fetch() -> LiveInfo:
            def _build(info: dict) -> LiveInfo:
                is_live = (
                    info.get("is_live") is True
                    or info.get("live_status") == "is_live"
                )
                title = info.get("title") or ""
                anchor_name = info.get("channel") or info.get("uploader") or ""
                stream_url = m3u8_url = flv_url = quality_name = ""

                formats = info.get("formats") or []
                if formats and is_live:
                    # 只考虑包含视频的格式
                    video_formats = [f for f in formats if (f.get("vcodec") or "none") != "none"]
                    best_format = None
                    for fmt in video_formats:
                        fmt_note = (fmt.get("format_note") or "").lower()
                        if quality != "best" and quality.lower() in fmt_note:
                            best_format = fmt
                            break
                    if best_format is None and video_formats:
                        best_format = video_formats[-1]  # yt-dlp 按质量升序排列，最后一个最高
                    if best_format:
                        stream_url = best_format.get("url") or ""
                        quality_name = best_format.get("format_note") or quality
                        if best_format.get("protocol") == "m3u8_native":
                            m3u8_url = stream_url
                        elif ".m3u8" in stream_url:
                            m3u8_url = stream_url

                return LiveInfo(
                    is_live=is_live, anchor_name=anchor_name, title=title,
                    stream_url=stream_url, m3u8_url=m3u8_url, flv_url=flv_url,
                    quality_name=quality_name,
                    extra={"duration": info.get("duration"),
                           "view_count": info.get("view_count"),
                           "live_status": info.get("live_status"),
                           "proxy": bool(proxy_url)},
                )

            try:
                return _try_with_cookies_fallback(_build, url, proxy_url=proxy_url)
            except Exception as e:
                return LiveInfo(is_live=False, extra={"error": _clean_error(str(e)), "proxy": bool(proxy_url)})

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_executor, _fetch),
                timeout=CHECK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return LiveInfo(is_live=False, extra={"error": "检测超时", "proxy": bool(proxy_url)})

    async def get_stream_url(self, url: str, quality: str = "best", **kwargs) -> str:
        """获取 YouTube 直播流地址。"""
        info = await self.check_live(url, quality=quality, **kwargs)
        if not info.is_live:
            raise RuntimeError("主播未开播")
        if not info.stream_url and not info.m3u8_url:
            raise RuntimeError("无法获取流地址")
        return info.stream_url or info.m3u8_url

    # ── 下载相关方法 ──────────────────────────────────────────

    async def list_formats(self, url: str) -> list[FormatInfo]:
        proxy_url = proxy_for_platform("youtube") if _is_youtube_url(url) else None
        """提取视频可用格式列表。"""

        def _fetch() -> list[FormatInfo]:
            def _build(info: dict) -> list[FormatInfo]:
                formats = info.get("formats") or []
                result: list[FormatInfo] = []
                seen: set[str] = set()
                for fmt in formats:
                    fid = fmt.get("format_id") or ""
                    if not fid or fid in seen:
                        continue
                    seen.add(fid)
                    vcodec = fmt.get("vcodec") or "none"
                    if vcodec == "none":
                        continue
                    resolution = fmt.get("resolution") or ""
                    note = fmt.get("format_note") or ""
                    if not resolution and note:
                        resolution = note
                    fps_val = fmt.get("fps")
                    fps = float(fps_val) if fps_val else None
                    filesize = fmt.get("filesize") or fmt.get("filesize_approx")
                    acodec = fmt.get("acodec") or "none"
                    result.append(FormatInfo(
                        format_id=fid, resolution=resolution, codec=vcodec,
                        fps=fps, filesize=int(filesize) if filesize else None,
                        note=note, ext=fmt.get("ext") or "mp4",
                        has_audio=(acodec != "none"),
                    ))
                return result

            return _try_with_cookies_fallback(_build, url, proxy_url=proxy_url)

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_executor, _fetch),
                timeout=CHECK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("获取格式列表超时")
        except Exception as e:
            raise RuntimeError(_clean_error(str(e))) from e

    async def download(
        self,
        url: str,
        format_id: str,
        output_dir: str | None = None,
        progress_cb: Callable[[str], None] | None = None,
        has_audio: bool = False,
    ) -> str:
        """下载视频，返回最终文件路径。"""

        output_dir = output_dir or str(DEFAULT_DOWNLOAD_DIR)
        os.makedirs(output_dir, exist_ok=True)
        proxy_url = proxy_for_platform("youtube") if _is_youtube_url(url) else None

        def _progress_hook(d: dict) -> None:
            if progress_cb is None:
                return
            status = d.get("status", "")
            if status == "downloading":
                pct = d.get("_percent_str", "").strip()
                speed = d.get("_speed_str", "").strip()
                eta = d.get("_eta_str", "").strip()
                filename = d.get("info_dict", {}).get("_filename") or d.get("filename") or ""
                name = os.path.basename(filename) if filename else "?"
                msg = f"下载中 {name}  {pct}  速度:{speed}  剩余:{eta}"
                progress_cb(msg)
            elif status == "finished":
                progress_cb("下载完成，正在合并处理...")
            elif status == "error":
                progress_cb("下载出错")

        def _run() -> str:
            from yt_dlp import YoutubeDL, DownloadError

            outtmpl = os.path.join(output_dir, "%(uploader)s-%(title)s.%(ext)s")
            # 基础选项（不用 cookies，避免签名验证问题）
            def _make_opts(extra=None):
                return _base_ytdlp_opts({
                    "outtmpl": outtmpl,
                    "progress_hooks": [_progress_hook],
                    "socket_timeout": 30,
                    "merge_output_format": "mp4",
                    **(extra or {}),
                }, proxy_url=proxy_url)

            def _do_download(fmt_str: str, use_cookies: bool = False) -> str:
                opts = _make_opts({"format": fmt_str})
                if use_cookies:
                    cookie_file = Path(__file__).parent.parent / "cookies.txt"
                    if cookie_file.exists():
                        opts["cookiefile"] = str(cookie_file)
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    return ydl.prepare_filename(info)

            # 选定格式自带音频 → 直接下载
            if has_audio:
                try:
                    return _do_download(format_id)
                except DownloadError as e:
                    if 'sign in' in str(e).lower():
                        return _do_download(format_id, use_cookies=True)
                    raise RuntimeError(_clean_error(str(e))) from e

            # 纯视频格式 → 尝试合并音频
            try:
                return _do_download(f"{format_id}+bestaudio/best")
            except DownloadError as e:
                msg = _clean_error(str(e))
                if 'sign in' in str(e).lower():
                    return _do_download(f"{format_id}+bestaudio/best", use_cookies=True)
                if "ffmpeg" not in msg.lower():
                    raise RuntimeError(msg) from e
                if progress_cb:
                    progress_cb("未安装 ffmpeg，改用自带音频的格式下载...")

            # 回退：有音频的最高画质
            try:
                return _do_download("best[acodec!=none]/best")
            except DownloadError as e:
                if 'sign in' in str(e).lower():
                    return _do_download("best[acodec!=none]/best", use_cookies=True)
                raise RuntimeError(_clean_error(str(e))) from e

        loop = asyncio.get_running_loop()
        try:
            filepath = await asyncio.wait_for(
                loop.run_in_executor(_executor, _run),
                timeout=600,  # 10 分钟超时
            )
        except asyncio.TimeoutError:
            raise RuntimeError("下载超时（超过 10 分钟）")

        return filepath


from . import register_plugin

register_plugin(YtDlpPlugin())
