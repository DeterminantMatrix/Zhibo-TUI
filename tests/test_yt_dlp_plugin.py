"""测试 yt-dlp 插件"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from zhibo.plugins.base import LiveInfo
from zhibo.plugins.yt_dlp_plugin import (
    DEFAULT_DOWNLOAD_DIR,
    FormatInfo,
    YtDlpPlugin,
    _base_ytdlp_opts,
    _cookie_file_path,
    _ytdlp_download_worker,
)


async def _run_inline(_executor, worker, /, *args, timeout, **kwargs):
    """Keep parent-process mocks valid while unit-testing worker logic."""
    return worker(*args, **kwargs)


class TestFormatInfo:
    """FormatInfo dataclass 单元测试"""

    def test_label_basic(self):
        fmt = FormatInfo(
            format_id="137",
            resolution="1920x1080",
            codec="avc1.640028",
            fps=30.0,
            filesize=50_000_000,
            note="1080p",
            ext="mp4",
        )
        label = fmt.label
        assert "1080p" in label
        assert "avc1" in label
        assert "47.7MB" in label or "47" in label

    def test_label_no_filesize(self):
        fmt = FormatInfo(
            format_id="137",
            resolution="1920x1080",
            codec="vp9",
            fps=60.0,
            filesize=None,
            note="1080p60",
            ext="webm",
        )
        label = fmt.label
        assert "1080p60" in label
        assert "vp9" in label
        assert "60fps" in label
        assert "MB" not in label

    def test_label_minimal(self):
        fmt = FormatInfo(
            format_id="1",
            resolution="",
            codec="",
            fps=None,
            filesize=None,
            note="",
            ext="mp4",
        )
        label = fmt.label
        assert "?" in label


class TestYtDlpPlugin:
    """YtDlpPlugin 单元测试"""

    def setup_method(self):
        self.plugin = YtDlpPlugin()

    def test_plugin_name(self):
        assert self.plugin.name == "yt_dlp"

    def test_plugin_registered(self):
        from zhibo.plugins import _plugins, register_plugin, get_plugin
        # 独立验证注册/查找逻辑，不依赖全局状态
        saved = dict(_plugins)
        _plugins.clear()
        try:
            register_plugin(self.plugin)
            assert get_plugin("yt_dlp") is self.plugin
            assert get_plugin("nonexistent") is None
        finally:
            _plugins.clear()
            _plugins.update(saved)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_live_not_live(self):
        """检测非直播 YouTube 视频应返回 is_live=False"""
        result = await self.plugin.check_live(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        assert isinstance(result, LiveInfo)
        assert result.is_live is False
        # 无 cookies 时可能返回错误但不会崩溃
        if result.extra.get("error"):
            assert "需要登录" in result.extra["error"] or "格式不可用" in result.extra["error"]
        else:
            assert result.title != ""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_live_error_url(self):
        """无效 URL 应返回 is_live=False 并附带 error"""
        result = await self.plugin.check_live(
            "https://www.youtube.com/watch?v=this_video_does_not_exist_999999"
        )
        assert result.is_live is False
        assert result.extra.get("error") != ""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_stream_url_not_live_raises(self):
        """未开播时 get_stream_url 应抛出 RuntimeError"""
        with pytest.raises(RuntimeError, match="未开播"):
            await self.plugin.get_stream_url(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_formats(self):
        """获取格式列表应返回 list（无 cookies 时可能为空）"""
        formats = await self.plugin.list_formats(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        assert isinstance(formats, list)
        for fmt in formats:
            assert isinstance(fmt, FormatInfo)
            assert fmt.format_id != ""
            assert fmt.ext in ("mp4", "webm", "mkv")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_formats_error_url(self):
        """无效 URL 应返回空列表或抛出异常"""
        try:
            formats = await self.plugin.list_formats(
                "https://www.youtube.com/watch?v=invalid_url_999999999"
            )
            assert formats == []
        except RuntimeError:
            pass  # 抛出异常也是可接受的

    @pytest.mark.asyncio
    async def test_check_live_with_mock(self, monkeypatch):
        """使用 mock 验证 check_live 正常路径"""
        mock_info = {
            "is_live": True,
            "live_status": "is_live",
            "title": "Mock Live Stream",
            "channel": "Mock Channel",
            "formats": [
                {
                    "format_id": "1",
                    "url": "http://example.com/stream.m3u8",
                    "format_note": "1080p",
                    "protocol": "m3u8_native",
                }
            ],
        }

        monkeypatch.setattr("zhibo.plugins.yt_dlp_plugin.run_bounded", _run_inline)
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.return_value = mock_info
            result = await self.plugin.check_live("http://youtube.com/test")

        assert result.is_live is True
        assert result.title == "Mock Live Stream"
        assert result.anchor_name == "Mock Channel"

    @pytest.mark.asyncio
    async def test_check_live_ytdlp_exception(self, monkeypatch):
        """yt-dlp 抛出异常时应返回 error 信息"""
        monkeypatch.setattr("zhibo.plugins.yt_dlp_plugin.run_bounded", _run_inline)
        with patch("yt_dlp.YoutubeDL") as mock_ydl:
            mock_ydl.return_value.__enter__.return_value.extract_info.side_effect = ValueError("Boom")
            result = await self.plugin.check_live("http://youtube.com/test")

        assert result.is_live is False
        assert "Boom" in result.extra.get("error", "")

    def test_default_download_dir(self):
        """默认下载目录应为 Videos/Youtube"""
        assert DEFAULT_DOWNLOAD_DIR.name == "Youtube"
        assert DEFAULT_DOWNLOAD_DIR.parent.name == "Videos"

    def test_base_opts_include_proxy_when_provided(self):
        opts = _base_ytdlp_opts(proxy_url="http://127.0.0.1:7890")

        assert opts["proxy"] == "http://127.0.0.1:7890"

    def test_cookie_file_can_be_configured_outside_project(self, tmp_path, monkeypatch):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        monkeypatch.setenv("ZHIBO_COOKIE_FILE", str(cookie_file))

        assert _cookie_file_path() == cookie_file
        assert _base_ytdlp_opts(use_cookies=True)["cookiefile"] == str(cookie_file)

    @pytest.mark.asyncio
    async def test_download_uses_top_level_worker_and_progress_bridge(self, tmp_path, monkeypatch):
        captured = {}
        messages = []

        async def fake_run(_executor, worker, /, *args, timeout, progress_callback=None, progress_kwarg=None, **kwargs):
            captured.update(
                worker=worker,
                args=args,
                timeout=timeout,
                progress_kwarg=progress_kwarg,
            )
            if progress_callback:
                progress_callback("下载中 test  50%")
            return str(tmp_path / "finished.mp4")

        monkeypatch.setattr("zhibo.plugins.yt_dlp_plugin.run_bounded", fake_run)
        result = await self.plugin.download(
            "https://www.youtube.com/watch?v=test",
            "137",
            output_dir=str(tmp_path),
            progress_cb=messages.append,
        )

        assert result.endswith("finished.mp4")
        assert captured["worker"] is _ytdlp_download_worker
        assert captured["progress_kwarg"] == "progress_sink"
        assert captured["timeout"] == 600
        assert messages == ["下载中 test  50%"]
