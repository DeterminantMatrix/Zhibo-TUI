"""轮询调度器 — 管理所有关注主播的状态"""
import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from app_logging import get_logger
from plugins.base import LiveInfo, LiveStreamPlugin
from plugins import get_plugin
from config import ConfigManager
from models import Follower, AppConfig
from proxy_config import set_platform_proxies


@dataclass
class FollowerStatus:
    """单个关注主播的运行时状态"""
    follower: Follower
    live_info: LiveInfo = field(default_factory=lambda: LiveInfo(is_live=False))
    last_check: datetime | None = None
    error: str = ""
    is_checking: bool = False
    failure_count: int = 0
    skip_polls: int = 0
    metadata_health: str = "-"
    is_initial_result: bool = True


class MonitorService:
    """直播监控服务"""

    def __init__(self, config_path: str | None = None):
        self.config_manager = ConfigManager(config_path)
        self.cfg: AppConfig = self.config_manager.load_config()
        set_platform_proxies(self.cfg.platform_proxies)
        self.poll_interval = self.cfg.poll_interval
        self.max_concurrent_checks = self.cfg.max_concurrent_checks
        self.failure_backoff_after = self.cfg.failure_backoff_after
        self.failure_backoff_polls = self.cfg.failure_backoff_polls
        self.followers: dict[int, FollowerStatus] = {}
        self._callbacks: list = []
        self._error_callbacks: list = []
        self._poll_start_callbacks: list = []
        self._poll_end_callbacks: list = []
        self._logger = get_logger("zhibo.monitor")
        self._poll_lock = asyncio.Lock()

        for i, f in enumerate(self.cfg.followers):
            self.followers[i] = FollowerStatus(follower=f)

        self._running = False

    @property
    def all_tags(self) -> list[str]:
        """所有可用标签"""
        from config import extract_tags
        return extract_tags(self.cfg)

    def get_by_tag(self, tag: str | None) -> list[tuple[int, FollowerStatus]]:
        """按标签筛选 follower 列表"""
        result = []
        for idx, status in self.followers.items():
            if not status.follower.enabled:
                continue
            follower_tags = status.follower.tags
            if tag is None or tag == "全部" or not follower_tags or tag in follower_tags:
                result.append((idx, status))
        return result

    def on_status_change(self, callback):
        """注册状态变化回调"""
        self._callbacks.append(callback)

    def on_error(self, callback):
        """注册内部错误回调"""
        self._error_callbacks.append(callback)

    def on_poll_start(self, callback):
        self._poll_start_callbacks.append(callback)

    def on_poll_end(self, callback):
        self._poll_end_callbacks.append(callback)

    async def _notify_error(self, message: str):
        for cb in self._error_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(message)
                else:
                    cb(message)
            except Exception:
                pass

    async def _notify_change(self, idx: int, status: FollowerStatus):
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(idx, status)
                else:
                    cb(idx, status)
            except Exception as e:
                await self._notify_error(f"回调异常: {e}")

    async def _notify_poll_start(self, count: int):
        for cb in self._poll_start_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(count)
                else:
                    cb(count)
            except Exception as e:
                await self._notify_error(f"回调异常: {e}")

    async def _notify_poll_end(self):
        for cb in self._poll_end_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception as e:
                await self._notify_error(f"回调异常: {e}")

    async def _check_with_plugin(self, plugin_name: str, f: Follower) -> LiveInfo:
        plugin = get_plugin(plugin_name)
        if plugin is None:
            raise RuntimeError(f"未知插件: {plugin_name}")

        platform = (f.platform or "").strip().casefold()
        quality = (f.quality or "").strip() or "best"

        return await plugin.check_live(
            url=f.url,
            platform=f.platform,
            quality=quality,
            extra=f.extra,
            sport_id=f.extra.get("sport_id", "1"),
        )

    def _platform_fallback_plugins(self, f: Follower) -> list[str]:
        platform = (f.platform or "").strip().casefold()
        if platform == "twitch":
            return ["streamget", "streamlink"]
        if platform == "youtube":
            return ["yt_dlp", "streamget", "streamlink"]
        return []

    def _is_platform_connectivity_error(self, platform: str, error: str) -> bool:
        platform = (platform or "").strip().casefold()
        text = (error or "").casefold()
        if platform == "twitch":
            return (
                "检测超时" in error
                or "gql.twitch.tv" in text
                or "usher.ttvnw.net" in text
                or "connecttimeout" in text
                or "connection to gql.twitch.tv timed out" in text
            )
        if platform == "youtube":
            return (
                "检测超时" in error
                or "youtube.com" in text and ("connecttimeout" in text or "timed out" in text)
            )
        return False

    def _mark_platform_skipped(self, idx: int, reason: str) -> FollowerStatus:
        status = self.followers[idx]
        message = f"平台连接超时，本轮跳过：{reason}"
        status.error = message
        status.live_info = LiveInfo(is_live=False, extra={"error": message})
        status.last_check = datetime.now()
        status.metadata_health = self._metadata_health(status.follower, status.live_info)
        self._logger.info(
            "主播检测跳过 idx=%s name=%s platform=%s error=%s",
            idx,
            status.follower.name,
            status.follower.platform,
            message,
        )
        return status

    def _metadata_health(self, f: Follower, info: LiveInfo) -> str:
        checks = {
            "主播": bool(info.anchor_name),
            "标题": bool(info.title),
        }
        if info.is_live:
            checks["流"] = bool(info.flv_url or info.m3u8_url or info.stream_url)
        if info.is_live and (f.platform == "huya" or info.extra.get("headers")):
            checks["头"] = bool(info.extra.get("headers"))

        passed = sum(1 for ok in checks.values() if ok)
        return f"{passed}/{len(checks)}"

    def _should_fallback(self, info: LiveInfo) -> bool:
        if info.extra.get("nonfatal_error") and info.is_live and (info.flv_url or info.m3u8_url or info.stream_url):
            return False
        if info.extra.get("error"):
            return True
        if info.is_live and not (info.flv_url or info.m3u8_url or info.stream_url):
            return True
        return False

    async def _check_with_fallbacks(self, f: Follower) -> LiveInfo:
        plugin_names = [f.plugin, *f.fallback_plugins, *self._platform_fallback_plugins(f)]
        errors: list[str] = []

        for index, plugin_name in enumerate(dict.fromkeys(plugin_names)):
            plugin_start = time.perf_counter()
            try:
                info = await self._check_with_plugin(plugin_name, f)
            except Exception as e:
                elapsed = time.perf_counter() - plugin_start
                self._logger.warning(
                    "插件检测异常 name=%s platform=%s plugin=%s elapsed=%.2fs error=%s",
                    f.name,
                    f.platform,
                    plugin_name,
                    elapsed,
                    e,
                )
                errors.append(f"{plugin_name}: {e}")
                continue
            elapsed = time.perf_counter() - plugin_start

            if index > 0:
                info.extra["plugin_used"] = plugin_name
            self._logger.info(
                "插件检测完成 name=%s platform=%s plugin=%s elapsed=%.2fs live=%s playable=%s error=%s",
                f.name,
                f.platform,
                plugin_name,
                elapsed,
                info.is_live,
                bool(info.flv_url or info.m3u8_url or info.stream_url),
                info.extra.get("error", ""),
            )
            if not self._should_fallback(info):
                return info

            error = info.extra.get("error")
            errors.append(f"{plugin_name}: {error or '结果不可播放'}")
            if error and self._is_platform_connectivity_error(f.platform, error):
                break

        return LiveInfo(is_live=False, extra={"error": "; ".join(errors) or "所有插件检测失败"})

    async def check_one(self, idx: int) -> FollowerStatus:
        """检测单个 follower"""
        status = self.followers[idx]
        f = status.follower
        started = time.perf_counter()

        if not f.enabled:
            status.error = "已禁用"
            status.live_info = LiveInfo(is_live=False, extra={"error": status.error})
            return status

        if status.skip_polls > 0:
            status.skip_polls -= 1
            status.last_check = datetime.now()
            status.error = f"连续失败退避中，剩余 {status.skip_polls} 轮"
            status.live_info = LiveInfo(is_live=False, extra={"error": status.error})
            return status

        status.is_checking = True
        try:
            live_info = await self._check_with_fallbacks(f)
            was_live = status.live_info.is_live
            status.is_initial_result = status.last_check is None
            status.live_info = live_info
            status.error = live_info.extra.get("error", "")
            status.last_check = datetime.now()
            status.metadata_health = self._metadata_health(f, live_info)

            if status.error and not live_info.extra.get("nonfatal_error"):
                status.failure_count += 1
            else:
                status.failure_count = 0
                status.skip_polls = 0

            if status.failure_count >= self.failure_backoff_after:
                status.skip_polls = self.failure_backoff_polls

            if was_live != live_info.is_live:
                await self._notify_change(idx, status)
        except Exception as e:
            status.error = str(e)
            status.live_info = LiveInfo(is_live=False, extra={"error": str(e)})
            status.last_check = datetime.now()
            status.failure_count += 1
            status.metadata_health = self._metadata_health(f, status.live_info)
            if status.failure_count >= self.failure_backoff_after:
                status.skip_polls = self.failure_backoff_polls
        finally:
            status.is_checking = False
            elapsed = time.perf_counter() - started
            used_plugin = status.live_info.extra.get("plugin_used", f.plugin)
            self._logger.info(
                "主播检测结束 idx=%s name=%s platform=%s plugin=%s used=%s elapsed=%.2fs live=%s error=%s",
                idx,
                f.name,
                f.platform,
                f.plugin,
                used_plugin,
                elapsed,
                status.live_info.is_live,
                status.error,
            )

        return status

    async def poll_all(self, tag: str | None = None) -> list[tuple[int, FollowerStatus]]:
        """并发检测所有 follower（可选按标签筛选）"""
        async with self._poll_lock:
            started = time.perf_counter()
            items = self.get_by_tag(tag)
            semaphore = asyncio.Semaphore(self.max_concurrent_checks)
            platform_semaphores = {
                "twitch": asyncio.Semaphore(1),
                "youtube": asyncio.Semaphore(1),
            }
            platform_outages: dict[str, str] = {}

            async def _check_with_limit(idx: int) -> FollowerStatus:
                async with semaphore:
                    follower = self.followers[idx].follower
                    platform = (follower.platform or "").strip().casefold()
                    platform_semaphore = platform_semaphores.get(platform)
                    if platform_semaphore is None:
                        return await self.check_one(idx)

                    async with platform_semaphore:
                        if platform in platform_outages:
                            return self._mark_platform_skipped(idx, platform_outages[platform])
                        status = await self.check_one(idx)
                        if status.error and self._is_platform_connectivity_error(platform, status.error):
                            platform_outages[platform] = status.error
                        return status

            tasks = [_check_with_limit(idx) for idx, _ in items]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (idx, status), result in zip(items, results):
                if not isinstance(result, Exception):
                    continue
                message = f"检测任务异常: {result}"
                status.error = message
                status.live_info = LiveInfo(is_live=False, extra={"error": message})
                status.last_check = datetime.now()
                status.metadata_health = self._metadata_health(status.follower, status.live_info)
                self._logger.error(
                    "主播检测任务未捕获异常 idx=%s name=%s error=%r",
                    idx,
                    status.follower.name,
                    result,
                )

            elapsed = time.perf_counter() - started
            errors = sum(1 for _, status in items if status.error)
            online = sum(1 for _, status in items if status.live_info.is_live)
            self._logger.info(
                "轮询结束 tag=%s total=%s online=%s errors=%s elapsed=%.2fs",
                tag or "全部",
                len(items),
                online,
                errors,
                elapsed,
            )
            return items

    @property
    def is_polling(self) -> bool:
        return self._poll_lock.locked()

    async def get_stream_info(self, idx: int) -> LiveInfo:
        """获取指定 follower 的完整播放信息"""
        status = self.followers[idx]
        f = status.follower
        info = await self._check_with_fallbacks(f)
        if not info.is_live:
            raise RuntimeError("主播未开播")
        if not (info.flv_url or info.m3u8_url or info.stream_url):
            raise RuntimeError("无法获取流地址")

        return info

    async def get_stream(self, idx: int) -> str:
        """获取指定 follower 的播放流地址"""
        info = await self.get_stream_info(idx)
        return info.flv_url or info.m3u8_url or info.stream_url

    async def run(self):
        """持续轮询循环"""
        self._running = True
        poll_count = 0
        while self._running:
            poll_count += 1
            await self._notify_poll_start(poll_count)
            await self.poll_all(None)
            await self._notify_poll_end()
            await asyncio.sleep(self.poll_interval)

    def stop(self):
        self._running = False
