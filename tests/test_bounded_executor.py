import asyncio
import os
import operator
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from zhibo.plugins import streamlink_plugin, yt_dlp_plugin
from zhibo.plugins import bounded_executor
from zhibo.plugins.bounded_executor import (
    BoundedExecutor,
    ExecutorBusyError,
    WorkerProcessError,
    run_bounded,
    shutdown_plugin_workers,
)
from zhibo.plugins.yt_dlp_plugin import _live_info_from_ytdlp


def _send_progress_once(*, progress_sink=None):
    assert progress_sink is not None
    progress_sink("halfway")
    return "done"


def _spawn_grandchild_and_block(*, progress_sink=None):
    """Worker used to verify that timeout cleanup reaches a real child tree."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if progress_sink is not None:
        progress_sink(f"{os.getpid()}:{child.pid}")
    time.sleep(60)


def _pid_exists(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return str(pid) in result.stdout


async def _wait_for_pid_exit(pid: int, seconds: float = 3) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        await asyncio.sleep(0.05)
    return not _pid_exists(pid)


def _force_stop_pid(pid: int) -> None:
    if not _pid_exists(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


def test_taskkill_tree_requires_a_success_return_code(monkeypatch):
    monkeypatch.setattr(
        bounded_executor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    assert bounded_executor._taskkill_tree(12345) is False

    monkeypatch.setattr(
        bounded_executor.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    assert bounded_executor._taskkill_tree(12345) is True


@pytest.mark.asyncio
async def test_timed_out_work_is_terminated_and_its_slot_is_released():
    executor = BoundedExecutor(max_workers=1, thread_name_prefix="test-isolated")
    try:
        with pytest.raises(asyncio.TimeoutError):
            await run_bounded(executor, time.sleep, 10, timeout=0.1)

        # The timeout path waits for child-process cleanup rather than leaving
        # a hidden worker behind to consume this runner's only slot.
        assert executor.pending_count == 0
        assert executor.active_pids == ()
        assert await run_bounded(executor, operator.add, "recovered", "", timeout=2) == "recovered"
    finally:
        executor.shutdown()


@pytest.mark.asyncio
async def test_cancelling_parent_task_terminates_child_and_does_not_queue_more_work():
    executor = BoundedExecutor(max_workers=1, thread_name_prefix="test-isolated")
    task = asyncio.create_task(run_bounded(executor, time.sleep, 10, timeout=20))
    try:
        for _ in range(50):
            if executor.pending_count == 1:
                break
            await asyncio.sleep(0.01)
        assert executor.pending_count == 1

        with pytest.raises(ExecutorBusyError):
            await run_bounded(executor, operator.add, "must", " not queue", timeout=1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert executor.pending_count == 0
        assert executor.active_pids == ()
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        executor.shutdown()


@pytest.mark.asyncio
async def test_process_runner_rejects_unpickleable_closures_before_starting_a_worker():
    executor = BoundedExecutor(max_workers=1, thread_name_prefix="test-isolated")
    try:
        with pytest.raises(TypeError, match="module-level pickleable"):
            await run_bounded(executor, lambda: "not spawn-safe", timeout=1)
        assert executor.pending_count == 0
    finally:
        executor.shutdown()


@pytest.mark.asyncio
async def test_plugin_result_models_round_trip_through_spawned_worker():
    executor = BoundedExecutor(max_workers=1, thread_name_prefix="test-isolated")
    try:
        info = await run_bounded(
            executor,
            _live_info_from_ytdlp,
            {
                "is_live": True,
                "title": "test live",
                "channel": "test channel",
                "formats": [{"vcodec": "avc1", "url": "https://example.com/live.m3u8"}],
            },
            "best",
            None,
            timeout=2,
        )
        assert info.is_live is True
        assert info.m3u8_url == "https://example.com/live.m3u8"
    finally:
        executor.shutdown()


@pytest.mark.asyncio
async def test_progress_is_relayed_without_passing_parent_callback_to_child():
    executor = BoundedExecutor(max_workers=1, thread_name_prefix="test-isolated")
    messages = []
    try:
        result = await run_bounded(
            executor,
            _send_progress_once,
            timeout=2,
            progress_callback=messages.append,
            progress_kwarg="progress_sink",
        )
        assert result == "done"
        assert messages == ["halfway"]
    finally:
        executor.shutdown()


@pytest.mark.asyncio
async def test_timeout_cleans_real_worker_and_grandchild_processes():
    executor = BoundedExecutor(max_workers=1, thread_name_prefix="test-tree")
    pids: list[tuple[int, int]] = []
    task = asyncio.create_task(
        run_bounded(
            executor,
            _spawn_grandchild_and_block,
            timeout=2,
            progress_callback=lambda message: pids.append(tuple(map(int, message.split(":")))),
            progress_kwarg="progress_sink",
        )
    )
    worker_pid = child_pid = None
    try:
        for _ in range(100):
            if pids:
                worker_pid, child_pid = pids[0]
                break
            await asyncio.sleep(0.02)
        assert worker_pid is not None and child_pid is not None

        with pytest.raises(asyncio.TimeoutError):
            await task

        assert await _wait_for_pid_exit(worker_pid)
        assert await _wait_for_pid_exit(child_pid)
        assert executor.pending_count == 0
        assert executor.shutdown() == ()
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        if worker_pid is not None:
            _force_stop_pid(worker_pid)
        if child_pid is not None:
            _force_stop_pid(child_pid)
        executor.shutdown()


@pytest.mark.asyncio
async def test_shutdown_reaps_and_unregisters_workers_idempotently():
    executor = BoundedExecutor(max_workers=1, thread_name_prefix="test-shutdown")
    task = asyncio.create_task(run_bounded(executor, time.sleep, 60, timeout=90))
    try:
        for _ in range(100):
            if executor.pending_count == 1:
                break
            await asyncio.sleep(0.01)
        assert executor.pending_count == 1

        assert shutdown_plugin_workers() == ()
        assert executor.pending_count == 0
        assert executor.active_pids == ()
        assert shutdown_plugin_workers() == ()

        with pytest.raises(WorkerProcessError):
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        executor.shutdown()


def test_blocking_plugins_use_bounded_process_runners():
    assert isinstance(streamlink_plugin._executor, BoundedExecutor)
    assert isinstance(yt_dlp_plugin._executor, BoundedExecutor)
