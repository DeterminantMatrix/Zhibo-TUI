"""Offline coverage for bounded poll_all coordination windows."""

from __future__ import annotations

import asyncio
from collections import defaultdict

import pytest

from zhibo.monitor import MonitorService
from zhibo.plugins import _plugins, register_plugin
from zhibo.plugins.base import LiveInfo, LiveStreamPlugin


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
    path = tmp_path / "scheduler-window.csv"
    path.write_text(CSV_HEADER + rows, encoding="utf-8")
    return MonitorService(path)


def make_rows(count: int, plugin: str) -> str:
    platforms = ("huya", "twitch", "bilibili")
    return "".join(
        f"true,房间-{index},window,{plugin},,{platforms[index % len(platforms)]},"
        f"https://offline.invalid/window/{index},best,\n"
        for index in range(count)
    )


@pytest.mark.parametrize(
    ("room_count", "concurrency", "rounds"),
    ((10, 1, 5), (50, 4, 5), (100, 8, 5), (500, 16, 3), (1000, 16, 3)),
)
def test_windowed_soak_bounds_coordination_and_preserves_all_rooms(
    tmp_path,
    isolated_plugin_registry,
    room_count,
    concurrency,
    rounds,
):
    class StablePlugin(LiveStreamPlugin):
        name = f"window_stable_{room_count}"

        def __init__(self):
            self.calls = 0
            self.active = 0
            self.max_active = 0
            self.platform_active = defaultdict(int)
            self.platform_max_active = defaultdict(int)

        async def check_live(self, url, platform=None, **kwargs):
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            platform_key = (platform or "").casefold()
            self.platform_active[platform_key] += 1
            self.platform_max_active[platform_key] = max(
                self.platform_max_active[platform_key],
                self.platform_active[platform_key],
            )
            try:
                await asyncio.sleep(0)
                return LiveInfo(is_live=False)
            finally:
                self.active -= 1
                self.platform_active[platform_key] -= 1

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    plugin = StablePlugin()
    register_plugin(plugin)
    service = make_service(tmp_path, make_rows(room_count, plugin.name))
    service.max_concurrent_checks = concurrency
    service.check_task_timeout = 1.0
    service.poll_deadline = 10.0

    async def scenario():
        for _ in range(rounds):
            result = await service.poll_all()
            assert [index for index, _ in result] == list(range(room_count))
            assert not service._active_attempts
            assert not service._deadline_tasks
            assert not service._stream_deadline_tasks
            assert not service._state_tasks
        await service.shutdown(timeout=0.1)

    asyncio.run(scenario())
    snapshot = service.diagnostics_snapshot()

    assert plugin.calls == room_count * rounds
    assert plugin.max_active <= concurrency
    assert all(value <= 1 for value in plugin.platform_max_active.values())
    assert snapshot["checked_rooms"] == room_count * rounds
    assert snapshot["plugin_calls"] == room_count * rounds
    assert snapshot["tasks"]["coordination_peak"] <= concurrency
    assert snapshot["tasks"]["active_attempts_peak"] <= concurrency
    assert snapshot["tasks"]["coordination_current"] == 0
    assert snapshot["tasks"]["active_attempts_current"] == 0
    assert snapshot["tasks"]["deadline_current"] == 0
    assert snapshot["tasks"]["stream_deadline_current"] == 0
    assert snapshot["tasks"]["state_current"] == 0


def test_windowed_results_keep_order_tag_and_disabled_semantics(
    tmp_path,
    isolated_plugin_registry,
):
    class ResultPlugin(LiveStreamPlugin):
        name = "window_result"

        async def check_live(self, url, **kwargs):
            return LiveInfo(is_live=True, stream_url=f"{url}/fresh")

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(ResultPlugin())
    service = make_service(
        tmp_path,
        "true,一,blue,window_result,,huya,https://offline.invalid/one,best,\n"
        "false,二,blue,window_result,,huya,https://offline.invalid/two,best,\n"
        "true,三,red,window_result,,twitch,https://offline.invalid/three,best,\n"
        "true,四,blue,window_result,,bilibili,https://offline.invalid/four,best,\n",
    )
    service.max_concurrent_checks = 2
    service.poll_deadline = 1.0
    service.check_task_timeout = 0.2

    async def scenario():
        blue = await service.poll_all("blue")
        no_match = await service.poll_all("does-not-exist")
        all_enabled = await service.poll_all(None)
        await service.shutdown(timeout=0.1)
        return blue, no_match, all_enabled

    blue, no_match, all_enabled = asyncio.run(scenario())

    assert [index for index, _ in blue] == [0, 3]
    assert no_match == []
    assert [index for index, _ in all_enabled] == [0, 2, 3]
    assert all(service.followers[index].check_state == "online" for index in (0, 2, 3))
    assert service.followers[1].check_state == "disabled"


def test_windowed_poll_deadline_defers_unstarted_rooms_without_overlaunch(
    tmp_path,
    isolated_plugin_registry,
):
    class HungPlugin(LiveStreamPlugin):
        name = "window_hung"

        def __init__(self):
            self.calls = 0
            self.started = asyncio.Event()

        async def check_live(self, url, **kwargs):
            self.calls += 1
            self.started.set()
            await asyncio.Event().wait()

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    plugin = HungPlugin()
    register_plugin(plugin)
    room_count = 12
    service = make_service(tmp_path, make_rows(room_count, plugin.name))
    service.max_concurrent_checks = 2
    service.check_task_timeout = 1.0
    service.poll_deadline = 0.03

    async def scenario():
        poll_task = asyncio.create_task(service.poll_all())
        await asyncio.wait_for(plugin.started.wait(), timeout=0.2)
        result = await poll_task
        await service.shutdown(timeout=0.1)
        return result

    result = asyncio.run(scenario())
    snapshot = service.diagnostics_snapshot()
    states = [status.check_state for _, status in result]

    assert plugin.calls <= service.max_concurrent_checks
    assert snapshot["timeout_count"] >= 1
    assert snapshot["deferred_count"] >= room_count - service.max_concurrent_checks
    assert states.count("backoff") >= room_count - service.max_concurrent_checks
    assert snapshot["tasks"]["coordination_peak"] <= service.max_concurrent_checks
    assert snapshot["tasks"]["coordination_current"] == 0
    assert snapshot["tasks"]["active_attempts_current"] == 0


def test_windowed_poll_cancellation_reaps_coordination_and_attempts(
    tmp_path,
    isolated_plugin_registry,
):
    class DelayedCancelPlugin(LiveStreamPlugin):
        name = "window_delayed_cancel"

        def __init__(self):
            self.calls = 0
            self.started = asyncio.Event()

        async def check_live(self, url, **kwargs):
            self.calls += 1
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.02)
                return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    plugin = DelayedCancelPlugin()
    register_plugin(plugin)
    service = make_service(tmp_path, make_rows(32, plugin.name))
    service.max_concurrent_checks = 4
    service.check_task_timeout = 10.0
    service.poll_deadline = 10.0

    async def scenario():
        poll_task = asyncio.create_task(service.poll_all())
        await asyncio.wait_for(plugin.started.wait(), timeout=0.2)
        poll_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await poll_task
        await service.shutdown(timeout=0.1)
        await asyncio.sleep(0.05)
        return service.diagnostics_snapshot()

    snapshot = asyncio.run(scenario())

    assert plugin.calls <= service.max_concurrent_checks
    assert snapshot["tasks"]["coordination_current"] == 0
    assert snapshot["tasks"]["active_attempts_current"] == 0
    assert snapshot["tasks"]["deadline_current"] == 0
    assert snapshot["tasks"]["stream_deadline_current"] == 0
    assert snapshot["tasks"]["state_current"] == 0
    assert snapshot["tasks"]["coordination_peak"] <= service.max_concurrent_checks


def test_windowed_preserves_fallback_cooldown_and_recovery(
    tmp_path,
    isolated_plugin_registry,
):
    calls = []

    class HealthPlugin(LiveStreamPlugin):
        name = "window_health"

        def __init__(self):
            self.mode = "failure"

        async def check_live(self, url, **kwargs):
            calls.append(("health", url))
            if self.mode == "failure":
                return LiveInfo(is_live=False, extra={"error": "connection timed out"})
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    class PrimaryPlugin(LiveStreamPlugin):
        name = "window_primary"

        async def check_live(self, url, **kwargs):
            calls.append(("primary", url))
            return LiveInfo(is_live=False, extra={"error": "primary failed"})

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    class FallbackPlugin(LiveStreamPlugin):
        name = "window_fallback"

        async def check_live(self, url, **kwargs):
            calls.append(("fallback", url))
            return LiveInfo(is_live=True, stream_url="https://offline.invalid/fallback")

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    health_plugin = HealthPlugin()
    register_plugin(health_plugin)
    register_plugin(PrimaryPlugin())
    register_plugin(FallbackPlugin())
    service = make_service(
        tmp_path,
        "true,健康一,,window_health,,huya,https://offline.invalid/health-1,best,\n"
        "true,健康二,,window_health,,huya,https://offline.invalid/health-2,best,\n"
        "true,回退,,window_primary,,bilibili,https://offline.invalid/fallback-room,best,\n",
    )
    service.followers[2].follower.fallback_plugins = ["window_fallback"]
    service.max_concurrent_checks = 2
    service.check_task_timeout = 0.2
    service.poll_deadline = 1.0

    async def scenario():
        first = await service.poll_all()
        health = service.get_platform_health("huya")
        assert health.state == "degraded"
        assert [index for index, _ in first] == [0, 1, 2]
        assert service.followers[2].check_state == "online"

        second = await service.poll_all()
        assert [index for index, _ in second] == [0, 1, 2]
        assert health_plugin.mode == "failure"

        health.next_allowed_monotonic = 0
        health_plugin.mode = "offline"
        third = await service.poll_all()
        await service.shutdown(timeout=0.1)
        return third

    third = asyncio.run(scenario())
    snapshot = service.diagnostics_snapshot()

    assert [index for index, _ in third] == [0, 1, 2]
    assert health_plugin.mode == "offline"
    assert service.get_platform_health("huya").state == "healthy"
    assert snapshot["fallback_calls"] >= 1
    assert snapshot["cooldown_events"] >= 1
    assert snapshot["tasks"]["active_attempts_current"] == 0
    assert snapshot["tasks"]["coordination_peak"] <= 2
    # The room is checked once in each of the three rounds; each round has one
    # primary-to-fallback transition and must not duplicate fallback within the
    # same chain.
    assert calls.count(("fallback", "https://offline.invalid/fallback-room")) == 3


def test_windowed_diagnostics_shape_is_stable_after_many_rounds(
    tmp_path,
    isolated_plugin_registry,
):
    class OfflinePlugin(LiveStreamPlugin):
        name = "window_diagnostics"

        async def check_live(self, url, **kwargs):
            await asyncio.sleep(0)
            return LiveInfo(is_live=False)

        async def get_stream_url(self, url, quality, **kwargs):
            raise AssertionError

    register_plugin(OfflinePlugin())
    service = make_service(tmp_path, make_rows(100, "window_diagnostics"))
    service.max_concurrent_checks = 8
    service.check_task_timeout = 0.5
    service.poll_deadline = 2.0

    async def scenario():
        snapshots = []
        for _ in range(10):
            await service.poll_all()
            snapshots.append(service.diagnostics_snapshot())
        await service.shutdown(timeout=0.1)
        return snapshots

    snapshots = asyncio.run(scenario())
    final = service.diagnostics_snapshot()

    assert {tuple(snapshot.keys()) for snapshot in snapshots} == {tuple(snapshots[0].keys())}
    assert all(snapshot["tasks"]["coordination_peak"] <= 8 for snapshot in snapshots)
    assert all(snapshot["tasks"]["coordination_current"] == 0 for snapshot in snapshots)
    assert all(snapshot["tasks"]["active_attempts_current"] == 0 for snapshot in snapshots)
    assert final["plugins"].keys() == snapshots[-1]["plugins"].keys()
    assert final["tasks"]["coordination_current"] == 0
