"""轮询调度器 — 管理所有关注主播的状态"""
import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zhibo.app_logging import get_logger
from zhibo.plugins.base import LiveInfo, LiveStreamPlugin
from zhibo.plugins import get_plugin
from zhibo.config import ConfigManager, safe_monitoring_settings_for_runtime
from zhibo.models import Follower, AppConfig
from zhibo.proxy_config import set_platform_proxies
from zhibo.quality_options import quality_for_plugin


PLATFORM_BACKOFF_BASE_SECONDS = 15.0
PLATFORM_BACKOFF_MAX_SECONDS = 900.0
PLATFORM_BACKOFF_JITTER_RATIO = 0.20
PLATFORM_OUTAGE_FAILURES = 2
FS1_PLATFORM_BACKOFF_MIN_SECONDS = 1800.0
CHECK_TASK_TIMEOUT_SECONDS = 45.0
POLL_DEADLINE_SECONDS = 90.0
DEADLINE_STATE_POLL_SECONDS = 0.02
STATUS_HISTORY_LIMIT = 20
DIAGNOSTIC_PLUGIN_LIMIT = 32
DIAGNOSTIC_STATES = frozenset(
    {"unknown", "online", "offline", "error", "skipped", "backoff", "disabled", "other"}
)


@dataclass(frozen=True)
class StatusHistoryEntry:
    """A compact, in-memory audit event for the follower detail panel."""

    at: datetime
    state: str
    message: str = ""
    is_live: bool | None = None


@dataclass
class PluginDiagnostics:
    """Aggregated, non-sensitive timing and outcome counters for one plugin."""

    calls: int = 0
    successes: int = 0
    failures: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class MonitorDiagnostics:
    """Bounded in-memory counters used by soak tests and local diagnostics."""

    poll_rounds: int = 0
    checked_rooms: int = 0
    fallback_calls: int = 0
    fallback_deduplicated: int = 0
    timeout_count: int = 0
    cancellation_count: int = 0
    deferred_count: int = 0
    cooldown_events: int = 0
    status_notifications: int = 0
    callback_error_count: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    plugin_stats: dict[str, PluginDiagnostics] = field(default_factory=dict)
    last_poll_elapsed_seconds: float = 0.0
    total_poll_elapsed_seconds: float = 0.0
    max_poll_elapsed_seconds: float = 0.0
    deadline_tasks_peak: int = 0
    stream_deadline_tasks_peak: int = 0
    state_tasks_peak: int = 0
    active_attempts_peak: int = 0
    coordination_tasks_current: int = 0
    coordination_tasks_peak: int = 0


@dataclass
class FollowerStatus:
    """单个关注主播的运行时状态"""
    follower: Follower
    live_info: LiveInfo = field(default_factory=lambda: LiveInfo(is_live=False))
    last_check: datetime | None = None
    last_confirmed_check: datetime | None = None
    error: str = ""
    is_checking: bool = False
    failure_count: int = 0
    skip_polls: int = 0
    metadata_health: str = "-"
    is_initial_result: bool = True
    # unknown / online / offline / error / skipped / backoff / disabled
    check_state: str = "unknown"
    history: list[StatusHistoryEntry] = field(default_factory=list)


@dataclass
class PlatformHealth:
    """平台级连通性状态，供 UI 和日志读取。"""
    state: str = "healthy"  # healthy / degraded / outage
    consecutive_failures: int = 0
    next_allowed_at: datetime | None = None
    last_error: str = ""
    last_failure_at: datetime | None = None
    next_allowed_monotonic: float = field(default=0.0, repr=False)
    retry_after_cap_seconds: float = field(default=PLATFORM_BACKOFF_MAX_SECONDS, repr=False)
    # Bilibili often has room/CDN-specific failures.  Keep the distinct room
    # candidates separate from confirmed platform-level failures so one bad
    # room cannot suppress every later Bilibili check in the same poll.
    failure_sources: set[str] = field(default_factory=set, repr=False)

    @property
    def retry_after_seconds(self) -> float:
        """距离下一次平台探测可执行的秒数。"""
        return min(
            self.retry_after_cap_seconds,
            max(0.0, self.next_allowed_monotonic - time.monotonic()),
        )


class MonitorService:
    """直播监控服务"""

    def __init__(self, config_path: str | None = None):
        self.config_manager = ConfigManager(config_path)
        self.cfg: AppConfig = self.config_manager.load_config()
        set_platform_proxies(self.cfg.platform_proxies)
        self.followers: dict[int, FollowerStatus] = {}
        self._callbacks: list = []
        self._error_callbacks: list = []
        self._poll_start_callbacks: list = []
        self._poll_end_callbacks: list = []
        self._logger = get_logger("zhibo.monitor")
        self._poll_lock = asyncio.Lock()
        self.platform_health: dict[str, PlatformHealth] = {}
        self._active_attempts: dict[int, int] = {}
        self._next_attempt_token = 0
        self._deadline_tasks: dict[int, asyncio.Task] = {}
        self._stream_deadline_tasks: dict[int, asyncio.Task] = {}
        self._state_tasks: set[asyncio.Task] = set()
        self._diagnostics = MonitorDiagnostics()
        self._interval_warned: set[int] = set()
        # The scheduler uses a monotonic deadline for correctness and keeps a
        # wall-clock mirror solely for UI/status display.  A settings change
        # must interrupt a pending wait; otherwise the UI can show the new
        # interval while the worker is still asleep on the old one.
        self._scheduler_loop: asyncio.AbstractEventLoop | None = None
        self._scheduler_wake_event: asyncio.Event | None = None
        self._scheduler_generation = 0
        self._next_poll_monotonic: float | None = None
        self._next_poll_at: datetime | None = None
        self._running = False
        self.apply_monitoring_settings()

        for i, f in enumerate(self.cfg.followers):
            status = FollowerStatus(
                follower=f,
                check_state="disabled" if not f.enabled else "unknown",
            )
            if not f.enabled:
                status.history.append(
                    StatusHistoryEntry(
                        at=datetime.now(),
                        state="disabled",
                        message="",
                        is_live=False,
                    )
                )
            self.followers[i] = status
            self.get_platform_health(f.platform)

    def apply_monitoring_settings(self) -> None:
        """Apply already-validated settings and reschedule a live wait.

        Configuration persistence performs the strict value validation.  This
        method is intentionally synchronous because it is called from the TUI
        confirmation callback, but it can still wake an ``asyncio`` scheduler
        running on the same (or another) event loop.
        """
        self._ensure_scheduler_state()
        safe_settings = safe_monitoring_settings_for_runtime(self.cfg)
        for field, value in safe_settings.items():
            setattr(self.cfg, field, value)
        self.poll_interval = safe_settings["poll_interval"]
        self.max_concurrent_checks = safe_settings["max_concurrent_checks"]
        self.failure_backoff_after = safe_settings["failure_backoff_after"]
        self.failure_backoff_polls = safe_settings["failure_backoff_polls"]
        poll_budget = max(5.0, float(self.poll_interval) * 0.8)
        self.check_task_timeout = min(CHECK_TASK_TIMEOUT_SECONDS, poll_budget)
        self.poll_deadline = min(POLL_DEADLINE_SECONDS, poll_budget)

        # Treat one confirmed settings update as a new scheduling generation.
        # Resetting from the moment it is applied matches the status bar's
        # ``now + poll_interval`` display, including edits that only change a
        # non-interval setting.
        self._scheduler_generation += 1
        if self._running:
            self._schedule_next_poll_from_now()
            self._wake_scheduler()

    def _ensure_scheduler_state(self) -> None:
        """Provide a small compatibility layer for lightweight test doubles."""
        if not hasattr(self, "_scheduler_loop"):
            self._scheduler_loop = None
        if not hasattr(self, "_scheduler_wake_event"):
            self._scheduler_wake_event = None
        if not hasattr(self, "_scheduler_generation"):
            self._scheduler_generation = 0
        if not hasattr(self, "_next_poll_monotonic"):
            self._next_poll_monotonic = None
        if not hasattr(self, "_next_poll_at"):
            self._next_poll_at = None

    @property
    def next_poll_at(self) -> datetime | None:
        """The actual scheduler deadline, suitable for a UI countdown."""
        self._ensure_scheduler_state()
        return self._next_poll_at

    def _schedule_next_poll_from_now(self) -> None:
        """Set the next deadline from one shared point in time."""
        self._ensure_scheduler_state()
        delay = max(0.0, float(self.poll_interval))
        self._next_poll_monotonic = time.monotonic() + delay
        self._next_poll_at = datetime.now() + timedelta(seconds=delay)

    def _clear_next_poll_deadline(self) -> None:
        self._ensure_scheduler_state()
        self._next_poll_monotonic = None
        self._next_poll_at = None

    def _wake_scheduler(self) -> None:
        """Wake a pending interval wait without assuming the caller's thread."""
        self._ensure_scheduler_state()
        event = self._scheduler_wake_event
        loop = self._scheduler_loop
        if event is None or loop is None or loop.is_closed():
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is loop:
            event.set()
        elif loop.is_running():
            loop.call_soon_threadsafe(event.set)

    async def _wait_for_next_poll(self) -> None:
        """Wait until the current deadline, restarting immediately on edits."""
        self._ensure_scheduler_state()
        if self._scheduler_wake_event is None:
            self._scheduler_wake_event = asyncio.Event()
        event = self._scheduler_wake_event

        while self._running:
            if self._next_poll_monotonic is None:
                self._schedule_next_poll_from_now()
            deadline = self._next_poll_monotonic
            if deadline is None:
                return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return

            generation = self._scheduler_generation
            # Clearing before checking the generation makes both races safe:
            # a change before the clear is detected by the generation check;
            # a change after it leaves the event set for this wait.
            event.clear()
            if generation != self._scheduler_generation:
                continue
            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except TimeoutError:
                # An external caller can apply settings at the boundary of a
                # timeout.  Prefer the newly recorded deadline over starting
                # a stale immediate poll in that case.
                if generation != self._scheduler_generation:
                    continue
                return
            # A wake can be caused by stop() or settings application.  In both
            # cases the loop condition/deadline is re-evaluated above.

    @staticmethod
    def _platform_key(platform: str | None) -> str:
        return (platform or "").strip().casefold() or "unknown"

    def get_platform_health(self, platform: str | None) -> PlatformHealth:
        """返回规范化平台的健康状态；未见过的平台默认 healthy。"""
        key = self._platform_key(platform)
        return self.platform_health.setdefault(key, PlatformHealth())

    @property
    def all_tags(self) -> list[str]:
        """所有可用标签"""
        from zhibo.config import extract_tags
        return extract_tags(self.cfg)

    def get_by_tag(
        self,
        tag: str | None,
        *,
        include_disabled: bool = False,
    ) -> list[tuple[int, FollowerStatus]]:
        """Return followers by tag, optionally including disabled editable rows."""
        result = []
        for idx, status in self.followers.items():
            if not status.follower.enabled and not include_disabled:
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

    def _ensure_diagnostics(self) -> MonitorDiagnostics:
        """Lazily support the lightweight ``object.__new__`` test doubles."""
        diagnostics = getattr(self, "_diagnostics", None)
        if diagnostics is None:
            diagnostics = MonitorDiagnostics()
            self._diagnostics = diagnostics
        return diagnostics

    @staticmethod
    def _diagnostic_plugin_key(plugin_name: object) -> str:
        """Keep plugin metric keys bounded and free of arbitrary user text."""
        text = str(plugin_name or "").strip().casefold()
        safe = "".join(
            char if char.isascii() and (char.isalnum() or char in "._-") else "_"
            for char in text
        ).strip("._-")
        return safe[:64] or "unknown"

    def _record_plugin_diagnostic(self, plugin_name: object, elapsed: float, *, success: bool) -> None:
        diagnostics = self._ensure_diagnostics()
        key = self._diagnostic_plugin_key(plugin_name)
        if key not in diagnostics.plugin_stats and len(diagnostics.plugin_stats) >= DIAGNOSTIC_PLUGIN_LIMIT:
            key = "other"
        metric = diagnostics.plugin_stats.setdefault(key, PluginDiagnostics())
        metric.calls += 1
        metric.elapsed_seconds += max(0.0, float(elapsed))
        if success:
            metric.successes += 1
        else:
            metric.failures += 1

    def _update_task_peaks(self) -> None:
        diagnostics = self._ensure_diagnostics()
        diagnostics.deadline_tasks_peak = max(
            diagnostics.deadline_tasks_peak,
            len(getattr(self, "_deadline_tasks", {})),
        )
        diagnostics.stream_deadline_tasks_peak = max(
            diagnostics.stream_deadline_tasks_peak,
            len(getattr(self, "_stream_deadline_tasks", {})),
        )
        diagnostics.state_tasks_peak = max(
            diagnostics.state_tasks_peak,
            len(getattr(self, "_state_tasks", set())),
        )
        diagnostics.active_attempts_peak = max(
            diagnostics.active_attempts_peak,
            len(getattr(self, "_active_attempts", {})),
        )

    def diagnostics_snapshot(self) -> dict:
        """Return a fixed-field, aggregate-only diagnostic snapshot.

        The snapshot intentionally excludes follower names, URLs, error text,
        stream candidates, credentials and response bodies.  It is suitable for
        local soak assertions and a redacted diagnostic report.
        """
        diagnostics = self._ensure_diagnostics()
        self._update_task_peaks()
        try:
            from zhibo.plugins.bounded_executor import bounded_worker_snapshot

            worker_snapshot = bounded_worker_snapshot()
        except Exception:
            worker_snapshot = {
                "runner_count": 0,
                "pending_count": 0,
                "active_pids": [],
                "active_pid_count": 0,
            }

        plugins_snapshot = {}
        for key, metric in sorted(diagnostics.plugin_stats.items()):
            average = metric.elapsed_seconds / metric.calls if metric.calls else 0.0
            plugins_snapshot[key] = {
                "calls": metric.calls,
                "successes": metric.successes,
                "failures": metric.failures,
                "elapsed_seconds": round(metric.elapsed_seconds, 6),
                "average_elapsed_seconds": round(average, 6),
            }

        return {
            "poll_rounds": diagnostics.poll_rounds,
            "checked_rooms": diagnostics.checked_rooms,
            "status_counts": dict(sorted(diagnostics.status_counts.items())),
            "plugin_calls": sum(metric.calls for metric in diagnostics.plugin_stats.values()),
            "fallback_calls": diagnostics.fallback_calls,
            "fallback_deduplicated": diagnostics.fallback_deduplicated,
            "timeout_count": diagnostics.timeout_count,
            "cancellation_count": diagnostics.cancellation_count,
            "deferred_count": diagnostics.deferred_count,
            "cooldown_events": diagnostics.cooldown_events,
            "status_notifications": diagnostics.status_notifications,
            "callback_error_count": diagnostics.callback_error_count,
            "tasks": {
                "deadline_current": len(self._deadline_tasks),
                "stream_deadline_current": len(self._stream_deadline_tasks),
                "state_current": len(self._state_tasks),
                "active_attempts_current": len(self._active_attempts),
                "deadline_peak": diagnostics.deadline_tasks_peak,
                "stream_deadline_peak": diagnostics.stream_deadline_tasks_peak,
                "state_peak": diagnostics.state_tasks_peak,
                "active_attempts_peak": diagnostics.active_attempts_peak,
                "coordination_current": diagnostics.coordination_tasks_current,
                "coordination_peak": diagnostics.coordination_tasks_peak,
            },
            "bounded_workers": worker_snapshot,
            "poll_timing": {
                "last_elapsed_seconds": round(diagnostics.last_poll_elapsed_seconds, 6),
                "total_elapsed_seconds": round(diagnostics.total_poll_elapsed_seconds, 6),
                "max_elapsed_seconds": round(diagnostics.max_poll_elapsed_seconds, 6),
            },
            "plugins": plugins_snapshot,
        }

    async def _notify_error(self, message: str):
        for cb in self._error_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(message)
                else:
                    cb(message)
            except Exception:
                self._ensure_diagnostics().callback_error_count += 1

    async def _notify_change(self, idx: int, status: FollowerStatus):
        self._ensure_diagnostics().status_notifications += 1
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(idx, status)
                else:
                    cb(idx, status)
            except Exception as e:
                self._ensure_diagnostics().callback_error_count += 1
                await self._notify_error(f"回调异常: {e}")

    async def _notify_poll_start(self, count: int):
        for cb in self._poll_start_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(count)
                else:
                    cb(count)
            except Exception as e:
                self._ensure_diagnostics().callback_error_count += 1
                await self._notify_error(f"回调异常: {e}")

    async def _notify_poll_end(self):
        for cb in self._poll_end_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception as e:
                self._ensure_diagnostics().callback_error_count += 1
                await self._notify_error(f"回调异常: {e}")

    async def _check_with_plugin(self, plugin_name: str, f: Follower) -> LiveInfo:
        plugin = get_plugin(plugin_name)
        if plugin is None:
            raise RuntimeError(f"未知插件: {plugin_name}")

        platform = (f.platform or "").strip().casefold()
        quality = quality_for_plugin(
            f.quality,
            source_plugin=f.plugin,
            target_plugin=plugin_name,
            platform=f.platform,
        )

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
        common_markers = (
            "超时",
            "检测超时",
            "任务超时",
            "获取流地址超时",
            "connecttimeout",
            "connection timed out",
            "timed out",
            "timeout",
            "dns",
            "name or service not known",
            "temporary failure",
            "network is unreachable",
            "connection refused",
            "connection reset",
            "proxyerror",
            "proxy error",
        )
        if any(marker in text for marker in common_markers):
            return True
        if platform == "twitch":
            return (
                "gql.twitch.tv" in text
                or "usher.ttvnw.net" in text
                or "connection to gql.twitch.tv" in text
            )
        if platform == "youtube":
            return (
                "youtube.com" in text and ("connect" in text or "timed out" in text)
            )
        return False

    def _is_platform_circuit_error(self, platform: str, error: str) -> bool:
        """Return whether one failure is shared enough to open a platform circuit."""
        if self._is_platform_connectivity_error(platform, error):
            return True
        if self._platform_key(platform) != "fs1":
            return False

        text = (error or "").casefold()
        fs1_shared_markers = (
            "certificate_verify_failed",
            "certificate verify failed",
            "unable to get local issuer certificate",
            "unauthorized",
            "forbidden",
            "authorization",
            "token",
            "cookie",
            "登录失效",
            "登录过期",
            "请先登录",
            "重新登录",
            "登录状态",
            "用户信息已过期",
            "认证失败",
            "鉴权失败",
            "授权失败",
            "凭证失效",
            "凭证过期",
        )
        return (
            any(marker in text for marker in fs1_shared_markers)
            or "client error '401" in text
            or "client error '403" in text
            or "status code 401" in text
            or "status code 403" in text
        )

    def _platform_backoff_delay(self, failure_count: int) -> float:
        exponent = min(max(failure_count - 1, 0), 16)
        base_delay = min(
            PLATFORM_BACKOFF_BASE_SECONDS * (2 ** exponent),
            PLATFORM_BACKOFF_MAX_SECONDS,
        )
        jitter = random.uniform(
            -base_delay * PLATFORM_BACKOFF_JITTER_RATIO,
            base_delay * PLATFORM_BACKOFF_JITTER_RATIO,
        )
        return min(PLATFORM_BACKOFF_MAX_SECONDS, max(0.0, base_delay + jitter))

    def _record_platform_connectivity_failure(
        self,
        platform: str | None,
        error: str,
        *,
        source: object | None = None,
    ) -> PlatformHealth:
        """记录平台共享故障，并打开带抖动的退避窗口。"""
        health = self.get_platform_health(platform)
        platform_key = self._platform_key(platform)
        if platform_key == "bilibili" and source is not None:
            health.failure_sources.add(str(source))
            health.last_error = error
            health.last_failure_at = datetime.now()
            if len(health.failure_sources) < 2:
                self._logger.warning(
                    "B站直播间连接异常，暂不触发平台退避 source=%s error=%s",
                    source,
                    error,
                )
                return health
            # Two different rooms failed consecutively.  Count this cluster as
            # one platform failure and enter the ordinary degraded backoff.
            health.failure_sources.clear()
        health.consecutive_failures += 1
        health.state = "outage" if health.consecutive_failures >= PLATFORM_OUTAGE_FAILURES else "degraded"
        delay = self._platform_backoff_delay(health.consecutive_failures)
        if platform_key == "fs1" and self._is_platform_circuit_error(platform_key, error):
            delay = max(delay, FS1_PLATFORM_BACKOFF_MIN_SECONDS)
            health.retry_after_cap_seconds = FS1_PLATFORM_BACKOFF_MIN_SECONDS
        else:
            health.retry_after_cap_seconds = PLATFORM_BACKOFF_MAX_SECONDS
        health.next_allowed_monotonic = time.monotonic() + delay
        health.next_allowed_at = datetime.now() + timedelta(seconds=delay)
        health.last_error = error
        health.last_failure_at = datetime.now()
        self._logger.warning(
            "平台共享故障 platform=%s state=%s failures=%s retry_in=%.1fs error=%s",
            platform_key,
            health.state,
            health.consecutive_failures,
            delay,
            error,
        )
        return health

    def _mark_platform_healthy(self, platform: str | None) -> PlatformHealth:
        """收到明确的在线/离线结果后清除平台熔断状态。"""
        health = self.get_platform_health(platform)
        was_unhealthy = health.state != "healthy" or health.consecutive_failures > 0
        health.state = "healthy"
        health.consecutive_failures = 0
        health.next_allowed_at = None
        health.next_allowed_monotonic = 0.0
        health.retry_after_cap_seconds = PLATFORM_BACKOFF_MAX_SECONDS
        health.last_error = ""
        health.last_failure_at = None
        health.failure_sources.clear()
        if was_unhealthy:
            self._logger.info("平台连接恢复 platform=%s", self._platform_key(platform))
        return health

    def reset_platform_health(self, platform: str | None) -> PlatformHealth:
        """Explicitly clear a circuit after the user refreshes shared credentials."""
        return self._mark_platform_healthy(platform)

    def _clear_platform_connectivity_candidates(self, platform: str | None) -> None:
        """Break a pending Bilibili connectivity-failure sequence."""
        self.get_platform_health(platform).failure_sources.clear()

    def _platform_in_cooldown(self, platform: str | None) -> bool:
        health = self.get_platform_health(platform)
        return health.state != "healthy" and health.next_allowed_monotonic > time.monotonic()

    def _platform_cooldown_reason(self, platform: str | None) -> str:
        health = self.get_platform_health(platform)
        remaining = int(health.retry_after_seconds + 0.999)
        detail = health.last_error or "等待平台恢复探测"
        return f"平台{health.state}，约 {remaining} 秒后重试：{detail}"

    @staticmethod
    def _is_confirmed_result(info: LiveInfo) -> bool:
        """Whether a plugin result confirms online/offline state.

        A result carrying an error describes an inconclusive check, not a
        confirmed offline room.  The one exception is a playable live result
        with a deliberately non-fatal warning (for example a quality warning).
        """
        error = info.extra.get("error")
        return not error or (info.is_live and bool(info.extra.get("nonfatal_error")))

    def _mark_check_error(self, status: FollowerStatus, message: str, state: str = "error") -> None:
        """Record an inconclusive check without replacing the confirmed state."""
        status.error = message
        status.last_check = datetime.now()
        status.check_state = state
        self._record_status_history(status, state=state, message=message)

    def _record_status_history(
        self,
        status: FollowerStatus,
        *,
        state: str,
        message: str = "",
        is_live: bool | None = None,
    ) -> None:
        """Keep a bounded, de-duplicated status timeline for UI inspection."""
        event_live = status.live_info.is_live if is_live is None else is_live
        detail = (message or "").strip()
        if status.history:
            last = status.history[-1]
            if last.state == state and last.message == detail and last.is_live == event_live:
                return
        diagnostics = self._ensure_diagnostics()
        diagnostic_state = state if state in DIAGNOSTIC_STATES else "other"
        diagnostics.status_counts[diagnostic_state] = diagnostics.status_counts.get(diagnostic_state, 0) + 1
        status.history.append(
            StatusHistoryEntry(
                at=datetime.now(),
                state=state,
                message=detail,
                is_live=event_live,
            )
        )
        if len(status.history) > STATUS_HISTORY_LIMIT:
            del status.history[:-STATUS_HISTORY_LIMIT]

    def _record_failure(self, status: FollowerStatus) -> None:
        status.failure_count += 1
        if status.failure_count >= self.failure_backoff_after:
            status.skip_polls = self.failure_backoff_polls

    def _begin_attempt(self, idx: int) -> int:
        self._next_attempt_token += 1
        token = self._next_attempt_token
        self._active_attempts[idx] = token
        self._update_task_peaks()
        return token

    def _is_attempt_current(self, idx: int, token: int | None) -> bool:
        return token is None or self._active_attempts.get(idx) == token

    def _finish_attempt(self, idx: int, token: int | None) -> None:
        if token is not None and self._active_attempts.get(idx) == token:
            self._active_attempts.pop(idx, None)

    def _track_indexed_task(self, tasks: dict[int, asyncio.Task], idx: int, task: asyncio.Task) -> None:
        tasks[idx] = task
        self._update_task_peaks()

        def _cleanup(completed: asyncio.Task) -> None:
            if tasks.get(idx) is completed:
                tasks.pop(idx, None)
            try:
                completed.exception()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        task.add_done_callback(_cleanup)

    def _track_deadline_task(self, idx: int, task: asyncio.Task) -> None:
        """保留未响应取消的轮询任务，防止重复启动同一主播的检测。"""
        self._track_indexed_task(self._deadline_tasks, idx, task)

    def _track_stream_deadline_task(self, idx: int, task: asyncio.Task) -> None:
        """保留未响应取消的手动取流任务，避免播放/复制重复堆积。"""
        self._track_indexed_task(self._stream_deadline_tasks, idx, task)

    def _track_state_task(self, task: asyncio.Task) -> None:
        """追踪慢状态回调；它们不能再阻塞网络检测。"""
        self._state_tasks.add(task)
        self._update_task_peaks()

        def _cleanup(completed: asyncio.Task) -> None:
            self._state_tasks.discard(completed)
            try:
                completed.exception()
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        task.add_done_callback(_cleanup)

    def _schedule_status_change(self, idx: int, status: FollowerStatus) -> None:
        """调度状态通知，不让 UI/桌面通知进入网络检测 deadline。"""
        self._track_state_task(asyncio.create_task(self._notify_change(idx, status)))

    async def shutdown(self, timeout: float = 1.0) -> None:
        """取消内部任务并最多等待 ``timeout`` 秒；永不因失控插件无限阻塞。"""
        self.stop()
        tasks = {
            *self._deadline_tasks.values(),
            *self._stream_deadline_tasks.values(),
            *self._state_tasks,
        }
        pending = {task for task in tasks if not task.done()}
        self._ensure_diagnostics().cancellation_count += len(pending)
        for task in pending:
            task.cancel()
        if pending:
            _, pending = await asyncio.wait(pending, timeout=max(0.0, timeout))
        if pending:
            self._logger.warning("监控关闭时仍有 %s 个任务未响应取消", len(pending))

    def _mark_deadline_timeout(self, idx: int, token: int, message: str) -> FollowerStatus:
        """使过期任务失效，并保留最近一次已确认的直播状态。"""
        status = self.followers[idx]
        if not self._is_attempt_current(idx, token):
            return status
        self._finish_attempt(idx, token)
        self._ensure_diagnostics().timeout_count += 1
        status.is_checking = False
        # Keep the last confirmed LiveInfo (and therefore avoid a false
        # down/up transition), but always expose the failed *check* and feed
        # it into platform health/circuit-breaking even when that last state
        # was online or offline.
        self._mark_check_error(status, message)
        self._record_failure(status)
        return status

    def _mark_deadline_deferred(self, idx: int, token: int, message: str) -> FollowerStatus:
        """Record scheduler pressure without treating it as a failed request."""
        status = self.followers[idx]
        if not self._is_attempt_current(idx, token):
            return status
        self._finish_attempt(idx, token)
        self._ensure_diagnostics().deferred_count += 1
        status.is_checking = False
        self._mark_check_error(status, message, state="backoff")
        return status

    def _mark_platform_skipped(self, idx: int, reason: str) -> FollowerStatus:
        status = self.followers[idx]
        self._ensure_diagnostics().cooldown_events += 1
        message = f"平台检测暂缓，本轮跳过：{reason}"
        self._mark_check_error(status, message, state="skipped")
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
        candidate_names = [f.plugin, *f.fallback_plugins, *self._platform_fallback_plugins(f)]
        plugin_names = list(dict.fromkeys(candidate_names))
        self._ensure_diagnostics().fallback_deduplicated += max(
            0,
            len(candidate_names) - len(plugin_names),
        )
        errors: list[str] = []
        saw_busy = False

        for index, plugin_name in enumerate(dict.fromkeys(plugin_names)):
            if index > 0:
                self._ensure_diagnostics().fallback_calls += 1
            plugin_start = time.perf_counter()
            try:
                info = await self._check_with_plugin(plugin_name, f)
            except Exception as e:
                elapsed = time.perf_counter() - plugin_start
                self._record_plugin_diagnostic(plugin_name, elapsed, success=False)
                compact_error = " ".join(str(e).split())[:500]
                self._logger.warning(
                    "插件检测异常 name=%s platform=%s plugin=%s elapsed=%.2fs error=%s",
                    f.name,
                    f.platform,
                    plugin_name,
                    elapsed,
                    compact_error,
                )
                errors.append(f"{plugin_name}: {compact_error}")
                # A plugin may raise a shared connectivity/authentication
                # error instead of returning it in LiveInfo.extra.  Continuing
                # the same room's fallback chain in that case only repeats the
                # unavailable platform request and can amplify an outage.
                if compact_error and self._is_platform_circuit_error(f.platform, compact_error):
                    break
                continue
            elapsed = time.perf_counter() - plugin_start

            if index > 0:
                info.extra["plugin_used"] = plugin_name
            compact_error = " ".join(str(info.extra.get("error", "")).split())[:500]
            self._logger.info(
                "插件检测完成 name=%s platform=%s plugin=%s elapsed=%.2fs live=%s playable=%s error=%s",
                f.name,
                f.platform,
                plugin_name,
                elapsed,
                info.is_live,
                bool(info.flv_url or info.m3u8_url or info.stream_url),
                compact_error,
            )
            if not self._should_fallback(info):
                self._record_plugin_diagnostic(plugin_name, elapsed, success=True)
                return info

            self._record_plugin_diagnostic(plugin_name, elapsed, success=False)
            error = compact_error
            errors.append(f"{plugin_name}: {error or '结果不可播放'}")
            if info.extra.get("busy"):
                saw_busy = True
            if error and self._is_platform_circuit_error(f.platform, error):
                break

        aggregate = {"error": "; ".join(errors) or "所有插件检测失败", "check_error": True}
        if saw_busy:
            aggregate["busy"] = True
        return LiveInfo(is_live=False, extra=aggregate)

    async def check_one(
        self,
        idx: int,
        *,
        _attempt_token: int | None = None,
        notify: bool = True,
        on_change=None,
    ) -> FollowerStatus:
        """检测单个 follower"""
        status = self.followers[idx]
        f = status.follower
        started = time.perf_counter()

        if not self._is_attempt_current(idx, _attempt_token):
            return status

        if not f.enabled:
            status.error = "已禁用"
            status.live_info = LiveInfo(is_live=False, extra={"error": status.error})
            status.check_state = "disabled"
            self._record_status_history(status, state="disabled", message=status.error, is_live=False)
            self._finish_attempt(idx, _attempt_token)
            return status

        if status.skip_polls > 0:
            status.skip_polls -= 1
            self._mark_check_error(
                status,
                f"连续失败退避中，剩余 {status.skip_polls} 轮",
                state="backoff",
            )
            self._finish_attempt(idx, _attempt_token)
            return status

        status.is_checking = True
        try:
            live_info = await self._check_with_fallbacks(f)
            if not self._is_attempt_current(idx, _attempt_token):
                return status
            if not self._is_confirmed_result(live_info):
                if live_info.extra.get("busy"):
                    # 线程池耗尽是本机资源压力，不是主播或平台故障：
                    # 只记暂缓，不计失败退避，也不喂给平台熔断。
                    self._mark_check_error(
                        status,
                        live_info.extra.get("error") or "检测通道繁忙，本轮暂缓",
                        state="backoff",
                    )
                    return status
                self._mark_check_error(status, live_info.extra.get("error") or "检测失败")
                self._record_failure(status)
                if self._is_platform_circuit_error(f.platform, status.error):
                    self._record_platform_connectivity_failure(f.platform, status.error, source=idx)
                else:
                    self._clear_platform_connectivity_candidates(f.platform)
                return status

            was_live = status.live_info.is_live
            status.is_initial_result = status.last_confirmed_check is None
            status.live_info = live_info
            status.error = live_info.extra.get("error", "")
            status.last_check = datetime.now()
            status.last_confirmed_check = status.last_check
            status.check_state = "online" if live_info.is_live else "offline"
            status.metadata_health = self._metadata_health(f, live_info)
            self._record_status_history(
                status,
                state=status.check_state,
                message=status.error,
                is_live=live_info.is_live,
            )
            self._mark_platform_healthy(f.platform)

            if status.error and not live_info.extra.get("nonfatal_error"):
                self._record_failure(status)
            else:
                status.failure_count = 0
                status.skip_polls = 0

            if was_live != live_info.is_live:
                if notify:
                    await self._notify_change(idx, status)
                elif on_change is not None:
                    on_change(idx, status)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self._is_attempt_current(idx, _attempt_token):
                self._mark_check_error(status, str(e))
                self._record_failure(status)
                if self._is_platform_circuit_error(f.platform, status.error):
                    self._record_platform_connectivity_failure(f.platform, status.error, source=idx)
                else:
                    self._clear_platform_connectivity_candidates(f.platform)
        finally:
            if self._is_attempt_current(idx, _attempt_token):
                status.is_checking = False
                self._finish_attempt(idx, _attempt_token)
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

    async def _run_started_check_with_deadline(self, idx: int, token: int) -> FollowerStatus:
        """只从真正调用插件时开始计算单主播检测 deadline。"""
        status = self.followers[idx]
        task = asyncio.create_task(
            self.check_one(
                idx,
                _attempt_token=token,
                notify=False,
                on_change=self._schedule_status_change,
            )
        )
        try:
            done, _ = await asyncio.wait({task}, timeout=self.check_task_timeout)
        except asyncio.CancelledError:
            self._ensure_diagnostics().cancellation_count += 1
            task.cancel()
            if not task.done():
                self._track_deadline_task(idx, task)
            raise

        if task in done:
            if task.cancelled():
                return self._mark_deadline_timeout(idx, token, "检测任务已取消")
            try:
                return task.result()
            except asyncio.CancelledError:
                return self._mark_deadline_timeout(idx, token, "检测任务已取消")
            except Exception as exc:
                if self._is_attempt_current(idx, token):
                    self._mark_check_error(status, f"检测任务异常: {exc}")
                    self._record_failure(status)
                    if self._is_platform_circuit_error(status.follower.platform, status.error):
                        self._record_platform_connectivity_failure(
                            status.follower.platform,
                            status.error,
                            source=idx,
                        )
                    self._finish_attempt(idx, token)
                return status

        task_started = status.is_checking and self._is_attempt_current(idx, token)
        self._ensure_diagnostics().cancellation_count += 1
        task.cancel()
        if not task.done():
            self._track_deadline_task(idx, task)
        if not task_started:
            return self._mark_deadline_deferred(idx, token, "检测任务尚未启动，本轮暂缓")

        status = self._mark_deadline_timeout(idx, token, "检测任务超时，检测已取消")
        self._record_platform_connectivity_failure(
            status.follower.platform,
            "检测任务超时，检测已取消",
            source=idx,
        )
        return status

    async def _cancel_runner_task(self, idx: int, token: int, task: asyncio.Task) -> None:
        """给嵌套 deadline 一个很短的收尾窗口，避免覆盖其已追踪的子任务。"""
        self._ensure_diagnostics().cancellation_count += 1
        task.cancel()
        await asyncio.wait({task}, timeout=DEADLINE_STATE_POLL_SECONDS)
        if (
            not task.done()
            and self._is_attempt_current(idx, token)
            and idx not in self._deadline_tasks
        ):
            self._track_deadline_task(idx, task)

    async def _run_check_with_deadline(self, idx: int, deadline: float, runner) -> FollowerStatus:
        """总 deadline 仅限制整轮；排队中的项目会被暂缓而不是记为失败。"""
        status = self.followers[idx]
        overdue_task = self._deadline_tasks.get(idx)
        if overdue_task is not None and not overdue_task.done():
            self._mark_check_error(status, "前次检测仍未结束，本轮跳过", state="backoff")
            return status
        if overdue_task is not None:
            self._deadline_tasks.pop(idx, None)

        token = self._begin_attempt(idx)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return self._mark_deadline_deferred(idx, token, "轮询总时限已到，尚未开始检测")

        task = asyncio.create_task(runner(idx, token))
        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            await self._cancel_runner_task(idx, token, task)
            if self._is_attempt_current(idx, token):
                status.is_checking = False
                self._finish_attempt(idx, token)
            raise

        if task in done:
            if task.cancelled():
                return self._mark_deadline_deferred(idx, token, "检测任务已取消，本轮暂缓")
            try:
                return task.result()
            except asyncio.CancelledError:
                return self._mark_deadline_deferred(idx, token, "检测任务已取消，本轮暂缓")
            except Exception as exc:
                if self._is_attempt_current(idx, token):
                    self._mark_check_error(status, f"检测任务异常: {exc}")
                    self._record_failure(status)
                    if self._is_platform_circuit_error(status.follower.platform, status.error):
                        self._record_platform_connectivity_failure(
                            status.follower.platform,
                            status.error,
                            source=idx,
                        )
                    self._finish_attempt(idx, token)
                return status

        task_started = status.is_checking and self._is_attempt_current(idx, token)
        if task_started:
            # Mark while this attempt still owns the token.  Cancelling first
            # can let a cooperative child finish and erase the token before
            # the timeout result is recorded.
            status = self._mark_deadline_timeout(idx, token, "轮询总时限已到，检测已取消")
            self._record_platform_connectivity_failure(
                status.follower.platform,
                "轮询总时限已到，检测已取消",
                source=idx,
            )
            await self._cancel_runner_task(idx, token, task)
            return status

        await self._cancel_runner_task(idx, token, task)
        return self._mark_deadline_deferred(idx, token, "轮询总时限已到，排队检测暂缓")

    def _follower_poll_interval(self, idx: int, status: FollowerStatus) -> float | None:
        """单个关注项 ``extra.poll_interval``（秒）覆盖；非法值告警一次并忽略。"""
        raw = (status.follower.extra or {}).get("poll_interval")
        if raw is None or raw == "":
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            if idx not in self._interval_warned:
                self._interval_warned.add(idx)
                self._logger.warning("关注项 poll_interval 无效，已忽略 idx=%s value=%r", idx, raw)
            return None
        return max(5.0, min(3600.0, value))

    def _is_follower_due(self, idx: int, status: FollowerStatus) -> bool:
        interval = self._follower_poll_interval(idx, status)
        if interval is None or status.last_check is None:
            return True
        return (datetime.now() - status.last_check).total_seconds() >= interval

    async def poll_all(
        self,
        tag: str | None = None,
        *,
        force: bool = False,
    ) -> list[tuple[int, FollowerStatus]]:
        """并发检测所有 follower（可选按标签筛选）。

        设置了 ``extra.poll_interval`` 的关注项按独立间隔节流；``force``
        用于手动刷新，忽略节流立即全量检测。
        """
        async with self._poll_lock:
            started = time.perf_counter()
            items = self.get_by_tag(tag)
            if not force:
                due = [(idx, status) for idx, status in items if self._is_follower_due(idx, status)]
                interval_skipped = len(items) - len(due)
                if interval_skipped:
                    self._logger.info("按独立轮询间隔跳过 %s 项", interval_skipped)
                items = due
            diagnostics = self._ensure_diagnostics()
            diagnostics.poll_rounds += 1
            diagnostics.checked_rooms += len(items)
            semaphore = asyncio.Semaphore(self.max_concurrent_checks)
            platform_semaphores = {
                self._platform_key(status.follower.platform): asyncio.Semaphore(1)
                for _, status in items
            }
            failed_recovery_probes: set[str] = set()
            poll_deadline = time.monotonic() + self.poll_deadline

            async def _skip_platform(idx: int, token: int, reason: str) -> FollowerStatus:
                self._finish_attempt(idx, token)
                return self._mark_platform_skipped(idx, reason)

            async def _check_with_limit(idx: int, token: int) -> FollowerStatus:
                follower = self.followers[idx].follower
                platform = self._platform_key(follower.platform)
                if self._platform_in_cooldown(platform):
                    return await _skip_platform(idx, token, self._platform_cooldown_reason(platform))

                async with semaphore:
                    if self._platform_in_cooldown(platform):
                        return await _skip_platform(idx, token, self._platform_cooldown_reason(platform))

                    async with platform_semaphores[platform]:
                        if self._platform_in_cooldown(platform):
                            return await _skip_platform(idx, token, self._platform_cooldown_reason(platform))
                        if platform in failed_recovery_probes:
                            return await _skip_platform(idx, token, "平台恢复探测尚未成功")

                        was_unhealthy = self.get_platform_health(platform).state != "healthy"
                        status = await self._run_started_check_with_deadline(idx, token)
                        if (
                            was_unhealthy
                            and status.check_state == "error"
                            and not self._is_platform_circuit_error(platform, status.error)
                        ):
                            failed_recovery_probes.add(platform)
                return status

            # Keep only one bounded coordination window alive at a time.  The
            # global semaphore still limits plugin execution, while this
            # window prevents N room-sized outer tasks and attempt tokens from
            # being created before the earlier rooms have made progress.
            window_size = max(1, int(self.max_concurrent_checks))
            results: list[object] = [None] * len(items)
            for start_index in range(0, len(items), window_size):
                batch = items[start_index : start_index + window_size]
                batch_tasks = [
                    self._run_check_with_deadline(idx, poll_deadline, _check_with_limit)
                    for idx, _ in batch
                ]
                diagnostics.coordination_tasks_current = len(batch_tasks)
                diagnostics.coordination_tasks_peak = max(
                    diagnostics.coordination_tasks_peak,
                    len(batch_tasks),
                )
                try:
                    batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                finally:
                    diagnostics.coordination_tasks_current = 0
                results[start_index : start_index + len(batch_results)] = batch_results

            for (idx, status), result in zip(items, results):
                if not isinstance(result, Exception):
                    continue
                message = f"检测任务异常: {result}"
                self._mark_check_error(status, message)
                self._record_failure(status)
                self._logger.error(
                    "主播检测任务未捕获异常 idx=%s name=%s error=%r",
                    idx,
                    status.follower.name,
                    result,
                )

            elapsed = time.perf_counter() - started
            diagnostics.last_poll_elapsed_seconds = max(0.0, elapsed)
            diagnostics.total_poll_elapsed_seconds += max(0.0, elapsed)
            diagnostics.max_poll_elapsed_seconds = max(
                diagnostics.max_poll_elapsed_seconds,
                max(0.0, elapsed),
            )
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
        """获取指定 follower 的完整播放信息，复用平台熔断与单项 deadline。"""
        status = self.followers[idx]
        f = status.follower
        platform = self._platform_key(f.platform)
        if self._platform_in_cooldown(platform):
            raise RuntimeError(f"平台暂不可用：{self._platform_cooldown_reason(platform)}")
        if status.is_checking:
            raise RuntimeError("主播状态检测进行中，请稍后重试")

        overdue_monitor_task = self._deadline_tasks.get(idx)
        if overdue_monitor_task is not None and not overdue_monitor_task.done():
            raise RuntimeError("前次状态检测仍未结束，请稍后重试")

        overdue_task = self._stream_deadline_tasks.get(idx)
        if overdue_task is not None and not overdue_task.done():
            raise RuntimeError("前次获取流地址仍未结束，请稍后重试")
        if overdue_task is not None:
            self._stream_deadline_tasks.pop(idx, None)

        task = asyncio.create_task(self._check_with_fallbacks(f))
        try:
            done, _ = await asyncio.wait({task}, timeout=self.check_task_timeout)
        except asyncio.CancelledError:
            self._ensure_diagnostics().cancellation_count += 1
            task.cancel()
            if not task.done():
                self._track_stream_deadline_task(idx, task)
            raise

        if task not in done:
            message = "获取流地址检测超时，已取消"
            diagnostics = self._ensure_diagnostics()
            diagnostics.timeout_count += 1
            diagnostics.cancellation_count += 1
            task.cancel()
            if not task.done():
                self._track_stream_deadline_task(idx, task)
            self._record_platform_connectivity_failure(platform, message, source=idx)
            raise RuntimeError("获取直播流信息超时，请稍后重试")

        if task.cancelled():
            raise RuntimeError("获取直播流信息已取消")
        try:
            info = task.result()
        except asyncio.CancelledError:
            raise RuntimeError("获取直播流信息已取消")
        except Exception as exc:
            message = str(exc) or "获取直播流信息失败"
            if self._is_platform_circuit_error(platform, message):
                self._record_platform_connectivity_failure(platform, message, source=idx)
            else:
                self._clear_platform_connectivity_candidates(platform)
            raise RuntimeError(message) from exc

        if not self._is_confirmed_result(info):
            message = info.extra.get("error") or "无法确认直播状态"
            if self._is_platform_circuit_error(platform, message):
                self._record_platform_connectivity_failure(platform, message, source=idx)
            else:
                self._clear_platform_connectivity_candidates(platform)
            raise RuntimeError(message)

        self._mark_platform_healthy(platform)
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
        self._ensure_scheduler_state()
        self._running = True
        self._scheduler_loop = asyncio.get_running_loop()
        self._scheduler_wake_event = asyncio.Event()
        poll_count = 0
        while self._running:
            # A polling round has no pending deadline.  If settings change
            # while it is running, apply_monitoring_settings() records the
            # next target itself and that target is retained below.
            self._clear_next_poll_deadline()
            poll_generation = self._scheduler_generation
            poll_count += 1
            try:
                await self._notify_poll_start(poll_count)
                await self.poll_all(None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.exception("轮询循环异常，第 %s 轮将继续重试", poll_count)
                await self._notify_error(f"轮询异常：{exc}")
            finally:
                await self._notify_poll_end()
            if self._running:
                if self._scheduler_generation == poll_generation:
                    self._schedule_next_poll_from_now()
                await self._wait_for_next_poll()

    def stop(self):
        self._running = False
        self._wake_scheduler()
