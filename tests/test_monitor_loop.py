import asyncio
from types import SimpleNamespace

import pytest

from zhibo import monitor as monitor_module
from zhibo.monitor import MonitorService
from zhibo.models import AppConfig


def make_monitor_service(tmp_path) -> MonitorService:
    config_path = tmp_path / "followers.csv"
    config_path.write_text(
        "enabled,name,tags,plugin,fallback_plugins,platform,url,quality,sport_id\n"
        "true,test,,streamlink,,twitch,https://www.twitch.tv/test,best,\n",
        encoding="utf-8",
    )
    return MonitorService(config_path)


def test_run_reports_poll_error_and_continues_to_next_iteration():
    service = object.__new__(MonitorService)
    service._running = True
    service.poll_interval = 0
    service._logger = SimpleNamespace(exception=lambda *args, **kwargs: None)
    starts = []
    ends = []
    errors = []
    calls = 0

    async def notify_start(count):
        starts.append(count)

    async def poll_all(tag):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        service._running = False

    async def notify_end():
        ends.append(True)

    async def notify_error(message):
        errors.append(message)

    service._notify_poll_start = notify_start
    service.poll_all = poll_all
    service._notify_poll_end = notify_end
    service._notify_error = notify_error

    asyncio.run(service.run())

    assert starts == [1, 2]
    assert len(ends) == 2
    assert errors == ["轮询异常：temporary failure"]


def test_apply_monitoring_settings_is_a_final_runtime_bound_guard():
    service = object.__new__(MonitorService)
    service.cfg = AppConfig(
        poll_interval=1,
        max_concurrent_checks=999999,
        failure_backoff_after=999999,
        failure_backoff_polls=0,
    )
    service._running = False

    service.apply_monitoring_settings()

    assert (
        service.poll_interval,
        service.max_concurrent_checks,
        service.failure_backoff_after,
        service.failure_backoff_polls,
    ) == (5, 16, 20, 1)


@pytest.mark.asyncio
async def test_apply_monitoring_settings_wakes_and_recalculates_pending_wait(tmp_path, monkeypatch):
    """A settings confirmation must not leave the runner asleep on old timing."""
    service = make_monitor_service(tmp_path)
    service.cfg.poll_interval = 10
    service.apply_monitoring_settings()

    first_poll_finished = asyncio.Event()
    original_wait_for = asyncio.wait_for
    wait_timeouts: list[float] = []

    async def record_wait_for(awaitable, timeout):
        wait_timeouts.append(timeout)
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(monitor_module.asyncio, "wait_for", record_wait_for)

    async def poll_all(_tag):
        first_poll_finished.set()

    service.poll_all = poll_all
    runner = asyncio.create_task(service.run())

    await original_wait_for(first_poll_finished.wait(), timeout=1)
    for _ in range(20):
        if service.next_poll_at is not None and wait_timeouts:
            break
        await asyncio.sleep(0)
    old_deadline = service.next_poll_at
    assert old_deadline is not None
    assert wait_timeouts[0] > 9

    service.cfg.poll_interval = 5
    service.apply_monitoring_settings()
    new_deadline = service.next_poll_at

    assert new_deadline is not None
    assert new_deadline < old_deadline
    for _ in range(20):
        if len(wait_timeouts) >= 2:
            break
        await asyncio.sleep(0)
    # The second wait appears immediately because the event interrupted the
    # old ten-second timeout; it now uses the persisted five-second interval.
    assert len(wait_timeouts) >= 2
    assert 4.8 < wait_timeouts[1] <= 5

    service.stop()
    await original_wait_for(runner, timeout=1)


@pytest.mark.asyncio
async def test_stop_wakes_a_pending_scheduler_wait(tmp_path):
    service = make_monitor_service(tmp_path)
    service.cfg.poll_interval = 10
    service.apply_monitoring_settings()
    first_poll_finished = asyncio.Event()

    async def poll_all(_tag):
        first_poll_finished.set()

    service.poll_all = poll_all
    runner = asyncio.create_task(service.run())

    await asyncio.wait_for(first_poll_finished.wait(), timeout=1)
    for _ in range(20):
        if service.next_poll_at is not None:
            break
        await asyncio.sleep(0)
    assert service.next_poll_at is not None

    service.stop()
    await asyncio.wait_for(runner, timeout=1)
