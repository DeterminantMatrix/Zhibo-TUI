"""Fully offline long-running and resource-observability regression tests."""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict

import pytest

from zhibo.monitor import MonitorService
from zhibo.plugins import _plugins, register_plugin
from zhibo.plugins.base import LiveInfo, LiveStreamPlugin
from zhibo.plugins.bounded_executor import bounded_worker_snapshot, shutdown_plugin_workers


CSV_HEADER = "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\n"


@pytest.fixture
def isolated_plugin_registry():
    previous_plugins = dict(_plugins)
    try:
        yield
    finally:
        _plugins.clear()
        _plugins.update(previous_plugins)


def make_service(tmp_path, rows: str) -> MonitorService:
    config_path = tmp_path / "soak-followers.csv"
    config_path.write_text(CSV_HEADER + rows, encoding="utf-8")
    return MonitorService(config_path)


def rows_for(room_count: int, plugin: str, *, fallback: str = "") -> str:
    platforms = ("huya", "twitch", "bilibili")
    return "".join(
        f"true,房间-{index},soak,{plugin},{fallback},{platforms[index % len(platforms)]},"
        f"https://offline.invalid/room/{index},best,\n"
        for index in range(room_count)
    )


@pytest.mark.parametrize(
    ("room_count", "concurrency", "poll_interval"),
    ((10, 1, 5), (50, 4, 7), (100, 8, 11)),
)
def test_offline_soak_scales_without_task_or_history_growth(
    tmp_path,
    isolated_plugin_registry,
    room_count,
    concurrency,
    poll_interval,
):
    class StableOfflinePlugin(LiveStreamPlugin):
        name = f"soak_stable_offline_{room_count}"

        def __init__(self):
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            await asyncio.sleep(0)
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError("offline soak must not request a stream URL")

    plugin = StableOfflinePlugin()
    register_plugin(plugin)
    service = make_service(tmp_path, rows_for(room_count, plugin.name))
    service.cfg.poll_interval = poll_interval
    service.cfg.max_concurrent_checks = concurrency
    service.cfg.failure_backoff_after = 20
    service.apply_monitoring_settings()
    service.check_task_timeout = 0.2
    service.poll_deadline = 1.0

    rounds = 8

    async def scenario():
        for _ in range(rounds):
            result = await service.poll_all()
            assert len(result) == room_count
            await asyncio.sleep(0)
            assert not service._deadline_tasks
            assert not service._stream_deadline_tasks
            assert not service._state_tasks
            assert not service._active_attempts
        await service.shutdown(timeout=0.1)

    asyncio.run(scenario())

    snapshot = service.diagnostics_snapshot()
    assert plugin.calls == room_count * rounds
    assert snapshot["poll_rounds"] == rounds
    assert snapshot["checked_rooms"] == room_count * rounds
    assert snapshot["plugin_calls"] == room_count * rounds
    assert snapshot["status_counts"] == {"offline": room_count}
    assert snapshot["tasks"]["deadline_current"] == 0
    assert snapshot["tasks"]["stream_deadline_current"] == 0
    assert snapshot["tasks"]["state_current"] == 0
    assert snapshot["tasks"]["active_attempts_current"] == 0
    # Windowed poll_all() keeps both the coordination window and plugin
    # attempts bounded by the configured global concurrency.
    assert snapshot["tasks"]["active_attempts_peak"] <= concurrency
    assert snapshot["tasks"]["coordination_peak"] <= concurrency
    assert snapshot["bounded_workers"]["pending_count"] == 0
    assert snapshot["bounded_workers"]["active_pids"] == []
    assert all(len(status.history) == 1 for status in service.followers.values())
    assert all(len(status.history) <= 20 for status in service.followers.values())


def test_alternating_states_slow_callbacks_and_callback_errors_are_reaped(
    tmp_path,
    isolated_plugin_registry,
):
    class AlternatingPlugin(LiveStreamPlugin):
        name = "soak_alternating"

        def __init__(self):
            self.calls = defaultdict(int)

        async def check_live(self, url, **kwargs):
            self.calls[url] += 1
            await asyncio.sleep(0)
            if self.calls[url] % 2:
                return LiveInfo(is_live=True, stream_url="https://offline.invalid/fresh.m3u8")
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    plugin = AlternatingPlugin()
    register_plugin(plugin)
    service = make_service(
        tmp_path,
        "true,一,,soak_alternating,,huya,https://offline.invalid/one,best,\n"
        "true,二,,soak_alternating,,huya,https://offline.invalid/two,best,\n",
    )
    callback_errors = []

    async def failing_callback(idx, status):
        await asyncio.sleep(0.002)
        raise RuntimeError("synthetic callback failure")

    service.on_status_change(failing_callback)
    service.on_error(callback_errors.append)

    async def scenario():
        for _ in range(6):
            await service.poll_all()
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.03)
        snapshot = service.diagnostics_snapshot()
        await service.shutdown(timeout=0.1)
        return snapshot

    snapshot = asyncio.run(scenario())

    assert plugin.calls["https://offline.invalid/one"] == 6
    assert plugin.calls["https://offline.invalid/two"] == 6
    assert snapshot["status_notifications"] >= 10
    assert snapshot["callback_error_count"] >= 10
    assert len(callback_errors) >= 10
    assert snapshot["tasks"]["state_current"] == 0
    assert snapshot["tasks"]["state_peak"] >= 1
    assert snapshot["tasks"]["active_attempts_current"] == 0
    assert all(len(status.history) <= 20 for status in service.followers.values())


def test_slow_ui_callback_does_not_extend_network_poll_deadline(
    tmp_path,
    isolated_plugin_registry,
):
    class LivePlugin(LiveStreamPlugin):
        name = "soak_slow_ui"

        async def check_live(self, url, **kwargs):
            return LiveInfo(is_live=True, stream_url="https://offline.invalid/live.m3u8")

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(LivePlugin())
    service = make_service(
        tmp_path,
        "true,主播,,soak_slow_ui,,huya,https://offline.invalid/room,best,\n",
    )
    service.check_task_timeout = 0.1
    service.poll_deadline = 0.01
    callback_finished = asyncio.Event()

    async def slow_callback(idx, status):
        await asyncio.sleep(0.05)
        callback_finished.set()

    service.on_status_change(slow_callback)

    async def scenario():
        started = time.perf_counter()
        await service.poll_all()
        elapsed = time.perf_counter() - started
        assert service.followers[0].check_state == "online"
        assert service.get_platform_health("huya").state == "healthy"
        await asyncio.wait_for(callback_finished.wait(), timeout=0.2)
        await service.shutdown(timeout=0.1)
        return elapsed

    elapsed = asyncio.run(scenario())

    assert elapsed < 0.04
    snapshot = service.diagnostics_snapshot()
    assert snapshot["tasks"]["state_peak"] >= 1
    assert snapshot["tasks"]["state_current"] == 0
    assert snapshot["timeout_count"] == 0


def test_fallback_success_all_failure_deduplication_and_offline_candidates(
    tmp_path,
    isolated_plugin_registry,
):
    calls = []

    class ResultPlugin(LiveStreamPlugin):
        def __init__(self, name, result):
            self.name = name
            self.result = result

        async def check_live(self, url, **kwargs):
            calls.append(self.name)
            await asyncio.sleep(0)
            return self.result

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    primary = ResultPlugin(
        "soak_primary",
        LiveInfo(is_live=False, extra={"error": "synthetic primary failure"}),
    )
    fallback = ResultPlugin(
        "soak_fallback",
        LiveInfo(is_live=True, stream_url="https://offline.invalid/fallback.m3u8"),
    )
    all_fail = ResultPlugin(
        "soak_all_fail",
        LiveInfo(is_live=False, extra={"error": "synthetic fallback failure"}),
    )
    final_fail = ResultPlugin(
        "soak_final_fail",
        LiveInfo(is_live=False, extra={"error": "synthetic final failure"}),
    )
    offline_candidates = ResultPlugin(
        "soak_offline_candidates",
        LiveInfo(
            is_live=False,
            extra={"stream_candidates": ["https://offline.invalid/cdn.m3u8"]},
        ),
    )
    for plugin in (primary, fallback, all_fail, final_fail, offline_candidates):
        register_plugin(plugin)

    service = make_service(
        tmp_path,
        "true,成功,,soak_primary,,huya,https://offline.invalid/success,best,\n"
        "true,失败,,soak_all_fail,,huya,https://offline.invalid/failure,best,\n"
        "true,离线候选,,soak_offline_candidates,,bilibili,https://offline.invalid/offline,best,\n",
    )
    service.followers[0].follower.fallback_plugins = [
        "soak_fallback",
        "soak_primary",
        "soak_fallback",
    ]
    service.followers[1].follower.fallback_plugins = ["soak_final_fail"]
    service.followers[2].follower.fallback_plugins = ["soak_fallback"]

    async def scenario():
        statuses = [await service.check_one(index) for index in range(3)]
        await service.shutdown(timeout=0.1)
        return statuses

    statuses = asyncio.run(scenario())
    snapshot = service.diagnostics_snapshot()

    assert calls[:2] == ["soak_primary", "soak_fallback"]
    assert calls[2:4] == ["soak_all_fail", "soak_final_fail"]
    assert calls[4:] == ["soak_offline_candidates"]
    assert statuses[0].check_state == "online"
    assert statuses[0].live_info.extra["plugin_used"] == "soak_fallback"
    assert statuses[1].check_state == "error"
    assert statuses[2].check_state == "offline"
    assert statuses[2].live_info.is_live is False
    # The offline result carries CDN candidates but is still confirmed offline
    # and must not enter the playback-only fallback chain.
    assert snapshot["fallback_calls"] == 2
    assert snapshot["fallback_deduplicated"] == 2
    assert snapshot["tasks"]["active_attempts_current"] == 0


def test_timeout_cooldown_recovery_and_delayed_cancel_do_not_accumulate_tasks(
    tmp_path,
    isolated_plugin_registry,
):
    class HealthPlugin(LiveStreamPlugin):
        name = "soak_health"

        def __init__(self):
            self.mode = "failure"
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            if self.mode == "failure":
                return LiveInfo(is_live=False, extra={"error": "connection timed out"})
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    class DelayedCancelPlugin(LiveStreamPlugin):
        name = "soak_delayed_cancel"

        async def check_live(self, url, **kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.03)
                return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    health_plugin = HealthPlugin()
    register_plugin(health_plugin)
    register_plugin(DelayedCancelPlugin())
    service = make_service(
        tmp_path,
        "true,健康一,,soak_health,,huya,https://offline.invalid/health-1,best,\n"
        "true,健康二,,soak_health,,huya,https://offline.invalid/health-2,best,\n"
        "true,延迟取消,,soak_delayed_cancel,,bilibili,https://offline.invalid/hung,best,\n",
    )
    service.check_task_timeout = 0.005
    service.poll_deadline = 0.1

    async def scenario():
        await service.poll_all()
        health = service.get_platform_health("huya")
        assert health.state == "degraded"
        assert health_plugin.calls == 1

        await service.poll_all()
        assert health_plugin.calls == 1

        health.next_allowed_monotonic = time.monotonic() - 1
        health_plugin.mode = "offline"
        await service.poll_all()
        assert health_plugin.calls == 3
        assert health.state == "healthy"

        await asyncio.sleep(0.06)
        await service.shutdown(timeout=0.1)

    asyncio.run(scenario())
    snapshot = service.diagnostics_snapshot()
    assert snapshot["timeout_count"] >= 1
    assert snapshot["cancellation_count"] >= 1
    assert snapshot["cooldown_events"] >= 1
    assert snapshot["tasks"]["deadline_current"] == 0
    assert snapshot["tasks"]["stream_deadline_current"] == 0
    assert snapshot["tasks"]["state_current"] == 0
    assert snapshot["tasks"]["active_attempts_current"] == 0
    assert not service._active_attempts


def test_repeated_get_stream_info_uses_fresh_results_without_stream_task_growth(
    tmp_path,
    isolated_plugin_registry,
):
    class FreshStreamPlugin(LiveStreamPlugin):
        name = "soak_fresh_stream"

        def __init__(self):
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            await asyncio.sleep(0)
            return LiveInfo(
                is_live=True,
                stream_url=f"https://offline.invalid/fresh-{self.calls}.m3u8",
                extra={
                    "stream_candidates": [
                        f"https://offline.invalid/cdn-{self.calls}.m3u8",
                        f"https://offline.invalid/fresh-{self.calls}.m3u8",
                    ]
                },
            )

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    plugin = FreshStreamPlugin()
    register_plugin(plugin)
    service = make_service(
        tmp_path,
        "true,主播,,soak_fresh_stream,,huya,https://offline.invalid/room,best,\n",
    )

    async def scenario():
        infos = [await service.get_stream_info(0) for _ in range(20)]
        await service.shutdown(timeout=0.1)
        return infos

    infos = asyncio.run(scenario())
    snapshot = service.diagnostics_snapshot()

    assert plugin.calls == 20
    assert infos[0].stream_url != infos[-1].stream_url
    assert snapshot["plugins"][plugin.name]["calls"] == 20
    assert snapshot["tasks"]["stream_deadline_current"] == 0
    assert snapshot["tasks"]["active_attempts_current"] == 0


def test_monitor_run_can_stop_and_restart_without_retaining_tasks(
    tmp_path,
    isolated_plugin_registry,
):
    class RestartablePlugin(LiveStreamPlugin):
        name = "soak_restartable"

        def __init__(self):
            self.calls = 0

        async def check_live(self, url, **kwargs):
            self.calls += 1
            await asyncio.sleep(0)
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    plugin = RestartablePlugin()
    register_plugin(plugin)
    service = make_service(
        tmp_path,
        "true,主播,,soak_restartable,,huya,https://offline.invalid/room,best,\n",
    )
    # The production validator enforces a safe persisted minimum, but this
    # in-memory scheduler test intentionally uses an immediate interval so two
    # complete rounds do not require real-time waiting.
    service.poll_interval = 0

    async def wait_for_calls(target):
        deadline = asyncio.get_running_loop().time() + 0.5
        while plugin.calls < target:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(f"plugin did not reach {target} calls")
            await asyncio.sleep(0.001)

    async def scenario():
        first_runner = asyncio.create_task(service.run())
        await wait_for_calls(2)
        service.stop()
        await asyncio.wait_for(first_runner, timeout=0.5)
        first_count = plugin.calls

        second_runner = asyncio.create_task(service.run())
        await wait_for_calls(first_count + 2)
        service.stop()
        await asyncio.wait_for(second_runner, timeout=0.5)
        await service.shutdown(timeout=0.1)

    asyncio.run(scenario())

    snapshot = service.diagnostics_snapshot()
    assert snapshot["poll_rounds"] >= 4
    assert snapshot["tasks"]["deadline_current"] == 0
    assert snapshot["tasks"]["stream_deadline_current"] == 0
    assert snapshot["tasks"]["state_current"] == 0
    assert snapshot["tasks"]["active_attempts_current"] == 0
    assert not service._running


def test_diagnostics_snapshot_is_fixed_field_aggregate_and_redacted(
    tmp_path,
    isolated_plugin_registry,
):
    secret_token = "secret-token-value"
    signed_url = "https://offline.invalid/live.m3u8?signature=secret-signature"

    class SensitiveResultPlugin(LiveStreamPlugin):
        name = "soak_sensitive"

        async def check_live(self, url, **kwargs):
            return LiveInfo(
                is_live=False,
                extra={
                    "error": f"Authorization: Bearer {secret_token}",
                    "stream_candidates": [signed_url],
                },
            )

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(SensitiveResultPlugin())
    service = make_service(
        tmp_path,
        "true,主播,,soak_sensitive,,huya,https://offline.invalid/room,best,\n",
    )

    async def scenario():
        await service.check_one(0)
        await service.shutdown(timeout=0.1)

    asyncio.run(scenario())
    snapshot = service.diagnostics_snapshot()
    rendered = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)

    assert set(snapshot) == {
        "poll_rounds",
        "checked_rooms",
        "status_counts",
        "plugin_calls",
        "fallback_calls",
        "fallback_deduplicated",
        "timeout_count",
        "cancellation_count",
        "deferred_count",
        "cooldown_events",
        "status_notifications",
        "callback_error_count",
        "tasks",
        "bounded_workers",
        "poll_timing",
        "plugins",
    }
    assert secret_token not in rendered
    assert signed_url not in rendered
    assert "Authorization" not in rendered
    assert "signature" not in rendered
    assert snapshot["plugin_calls"] == 1


def test_worker_snapshot_is_empty_after_idempotent_shutdown():
    assert shutdown_plugin_workers() == ()
    assert shutdown_plugin_workers() == ()
    snapshot = bounded_worker_snapshot()
    assert snapshot["pending_count"] == 0
    assert snapshot["active_pids"] == []
    assert snapshot["active_pid_count"] == 0
