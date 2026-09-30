"""Bounded, cancellable process isolation for blocking plugin work.

The public names intentionally retain the earlier ``BoundedExecutor`` API so
call sites do not need to know whether the work is isolated by a thread or a
process.  Unlike a thread pool, a spawned worker process can be terminated
when an asyncio timeout or cancellation occurs.  A non-blocking semaphore
also declines excess work instead of building an unbounded queue.

Workers must be module-level, pickleable callables.  This is required by the
``spawn`` start method used on Windows and deliberately catches accidental
closures before a child process is launched.
"""
from __future__ import annotations

import asyncio
import itertools
import multiprocessing as multiprocessing
import os
import pickle
import signal
import subprocess
import threading
import time
import traceback
import weakref
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any, TypeVar


T = TypeVar("T")

_POLL_SECONDS = 0.02
_EXIT_GRACE_SECONDS = 1.0
_MISSING_RESULT_GRACE_SECONDS = 0.2

_RESULT = "result"
_ERROR = "error"
_PROGRESS = "progress"
_RUNNERS: weakref.WeakSet["BoundedExecutor"] = weakref.WeakSet()


class ExecutorBusyError(RuntimeError):
    """Raised when every isolated worker slot is still occupied."""


class WorkerProcessError(RuntimeError):
    """Raised when an isolated worker exits with an exception or no result."""


def _send_message(connection: Connection, kind: str, payload: Any) -> None:
    """Send a child-to-parent message without letting a closed pipe crash work."""
    try:
        connection.send((kind, payload))
    except (BrokenPipeError, EOFError, OSError):
        pass


def _process_is_alive(process: multiprocessing.Process) -> bool:
    """Return process liveness without failing if another cleanup path closed it."""
    try:
        return process.is_alive()
    except (AssertionError, ValueError):
        return False


def _process_pid(process: multiprocessing.Process) -> int | None:
    """Read a process PID safely after another path may have closed its handle."""
    try:
        return process.pid
    except ValueError:
        return None


def _join_and_close_process(process: multiprocessing.Process) -> bool:
    """Close a stopped process handle; return whether it is fully reaped."""
    if _process_is_alive(process):
        return False
    try:
        process.join(timeout=0)
    except (OSError, ValueError):
        pass
    try:
        process.close()
    except (OSError, ValueError):
        # It may already have been closed by a concurrent/repeated shutdown.
        pass
    return True


def _worker_entry(
    connection: Connection,
    worker: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    progress_kwarg: str | None,
) -> None:
    """Process entry point.  It must remain module-level for Windows spawn."""
    if os.name != "nt":
        # Give the worker and any subprocesses it starts their own process
        # group, so cancellation can terminate the complete process tree.
        try:
            os.setsid()
        except OSError:
            pass

    worker_kwargs = dict(kwargs)
    if progress_kwarg:
        def send_progress(message: Any) -> None:
            _send_message(connection, _PROGRESS, str(message))

        worker_kwargs[progress_kwarg] = send_progress

    try:
        result = worker(*args, **worker_kwargs)
        _send_message(connection, _RESULT, result)
    except BaseException as exc:  # serialize failures rather than exceptions
        _send_message(
            connection,
            _ERROR,
            {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
    finally:
        try:
            connection.close()
        except OSError:
            pass


def _taskkill_tree(pid: int) -> bool:
    """Terminate a Windows worker and descendants such as ffmpeg.

    A false result is significant: callers must retry tree cleanup *before*
    falling back to ``Process.terminate()``, otherwise killing only the Python
    worker can orphan a child ffmpeg process.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


async def _wait_until_stopped(process: multiprocessing.Process, seconds: float) -> bool:
    """Wait without blocking the event loop; return whether the process exited."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while _process_is_alive(process) and loop.time() < deadline:
        await asyncio.sleep(_POLL_SECONDS)
    return not _process_is_alive(process)


def _wait_until_stopped_sync(process: multiprocessing.Process, seconds: float) -> bool:
    """Synchronous reaping wait used by the public shutdown() fallback."""
    deadline = time.monotonic() + seconds
    while _process_is_alive(process) and time.monotonic() < deadline:
        time.sleep(_POLL_SECONDS)
    return not _process_is_alive(process)


async def _terminate_process_tree(process: multiprocessing.Process) -> bool:
    """Terminate the worker and any decoder/downloader children it owns.

    Return whether the worker itself has stopped.  On Windows, retry
    ``taskkill /T`` while the parent is still alive before any direct process
    termination, preserving the parent/child relationship needed by taskkill.
    """
    if not _process_is_alive(process):
        return True

    pid = _process_pid(process)
    if os.name == "nt" and pid:
        # taskkill /T is required because Process.terminate() only kills the
        # Python worker, not an ffmpeg process launched by yt-dlp.
        tree_killed = await asyncio.to_thread(_taskkill_tree, pid)
        if not tree_killed and _process_is_alive(process):
            # Retry before severing the tree with Process.terminate().
            tree_killed = await asyncio.to_thread(_taskkill_tree, pid)
    elif pid:
        tree_killed = True
        try:
            os.killpg(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            tree_killed = False
    else:
        tree_killed = False

    if _process_is_alive(process):
        try:
            process.terminate()
        except OSError:
            pass

    if await _wait_until_stopped(process, _EXIT_GRACE_SECONDS):
        # If taskkill reported failure, make a final best-effort attempt.  It
        # is intentionally after the primary retries; the primary retries ran
        # while the worker was alive and therefore could still traverse /T.
        if os.name == "nt" and pid and not tree_killed:
            await asyncio.to_thread(_taskkill_tree, pid)
        return True

    # A forceful fallback keeps a pathological worker from retaining a slot.
    if os.name == "nt" and pid:
        await asyncio.to_thread(_taskkill_tree, pid)
    elif pid:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
    except (AttributeError, OSError):
        pass
    return await _wait_until_stopped(process, _EXIT_GRACE_SECONDS)


def _terminate_process_tree_sync(process: multiprocessing.Process, *, wait: bool = True) -> bool:
    """Synchronous counterpart used by the public shutdown() escape hatch."""
    if not _process_is_alive(process):
        return True
    pid = _process_pid(process)
    if os.name == "nt" and pid:
        tree_killed = _taskkill_tree(pid)
        if not tree_killed and _process_is_alive(process):
            tree_killed = _taskkill_tree(pid)
    elif pid:
        tree_killed = True
        try:
            os.killpg(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            tree_killed = False
    else:
        tree_killed = False
    if _process_is_alive(process):
        try:
            process.terminate()
        except OSError:
            pass

    if not wait:
        return not _process_is_alive(process)
    if _wait_until_stopped_sync(process, _EXIT_GRACE_SECONDS):
        if os.name == "nt" and pid and not tree_killed:
            _taskkill_tree(pid)
        return True

    if os.name == "nt" and pid:
        _taskkill_tree(pid)
    elif pid:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        process.kill()
    except (AttributeError, OSError):
        pass
    return _wait_until_stopped_sync(process, _EXIT_GRACE_SECONDS)


class BoundedExecutor:
    """Run at most ``max_workers`` pickleable blocking jobs in child processes.

    ``thread_name_prefix`` is kept as a backwards-compatible constructor
    argument from the old thread-backed implementation.  It now forms the
    child process name prefix.
    """

    def __init__(self, max_workers: int, *, thread_name_prefix: str = "zhibo"):
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self.max_workers = max_workers
        self._context = multiprocessing.get_context("spawn")
        self._slots = threading.BoundedSemaphore(max_workers)
        self._active: dict[int, multiprocessing.Process] = {}
        self._lock = threading.Lock()
        self._names = itertools.count(1)
        self._name_prefix = thread_name_prefix
        self._last_start_seconds = 0.0
        _RUNNERS.add(self)

    def _acquire_slot(self) -> None:
        if not self._slots.acquire(blocking=False):
            raise ExecutorBusyError("all isolated workers are still occupied")

    def _register(self, process: multiprocessing.Process) -> None:
        with self._lock:
            self._active[id(process)] = process

    def _unregister(self, process: multiprocessing.Process) -> None:
        removed = False
        with self._lock:
            removed = self._active.pop(id(process), None) is not None
        if removed:
            self._slots.release()

    @property
    def pending_count(self) -> int:
        """Number of worker processes that have not finished or been reaped."""
        with self._lock:
            return len(self._active)

    @property
    def active_pids(self) -> tuple[int, ...]:
        """A snapshot useful for diagnostics and resource-cleanup tests."""
        with self._lock:
            return tuple(pid for process in self._active.values() if (pid := _process_pid(process)))

    @property
    def last_start_seconds(self) -> float:
        """Duration of the most recent synchronous Process.start() call."""
        return self._last_start_seconds

    async def run(
        self,
        worker: Callable[..., T],
        /,
        *args: Any,
        timeout: float,
        progress_callback: Callable[[str], None] | None = None,
        progress_kwarg: str | None = None,
        **kwargs: Any,
    ) -> T:
        """Execute a module-level worker and reap it on success, timeout or cancel."""
        try:
            pickle.dumps((worker, args, kwargs))
        except (pickle.PicklingError, TypeError, AttributeError) as exc:
            raise TypeError(
                "isolated workers must be module-level pickleable callables; "
                "pass data as arguments rather than closing over it"
            ) from exc

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        self._acquire_slot()
        process: multiprocessing.Process | None = None
        parent_connection: Connection | None = None
        child_connection: Connection | None = None
        started = False
        registered = False
        try:
            # Give a caller that was cancelled immediately after scheduling a
            # task a chance to stop before the synchronous spawn call starts.
            await asyncio.sleep(0)
            if loop.time() >= deadline:
                raise asyncio.TimeoutError("worker startup exceeded timeout")
            parent_connection, child_connection = self._context.Pipe(duplex=False)
            process = self._context.Process(
                target=_worker_entry,
                args=(child_connection, worker, args, kwargs, progress_kwarg),
                name=f"{self._name_prefix}-{next(self._names)}",
            )
            # yt-dlp may launch ffmpeg; daemon processes are not permitted to
            # create children, so explicitly keep the default non-daemon mode.
            process.daemon = False
            start_started = loop.time()
            process.start()
            self._last_start_seconds = loop.time() - start_started
            started = True
            self._register(process)
            registered = True
            child_connection.close()
            child_connection = None

            # Spawn startup is now included in the caller's timeout budget.
            if loop.time() >= deadline:
                raise asyncio.TimeoutError("worker startup exceeded timeout")
            terminal_message: tuple[str, Any] | None = None
            worker_exited_at: float | None = None
            terminal_sent_at: float | None = None

            while True:
                try:
                    while parent_connection.poll():
                        kind, payload = parent_connection.recv()
                        if kind == _PROGRESS:
                            if progress_callback is not None:
                                # Existing UI callbacks use Textual's
                                # call_from_thread(), so preserve the old
                                # non-event-loop invocation semantics.
                                try:
                                    await asyncio.to_thread(progress_callback, str(payload))
                                except Exception:
                                    # A display callback must not abort a
                                    # download that is otherwise healthy.
                                    pass
                        elif kind in {_RESULT, _ERROR}:
                            terminal_message = (kind, payload)
                            terminal_sent_at = loop.time()
                except (EOFError, OSError):
                    pass

                if not _process_is_alive(process):
                    _join_and_close_process(process)
                    if terminal_message is not None:
                        kind, payload = terminal_message
                        if kind == _RESULT:
                            return payload
                        error_type = payload.get("type", "WorkerError") if isinstance(payload, dict) else "WorkerError"
                        error_text = payload.get("message", "") if isinstance(payload, dict) else str(payload)
                        raise WorkerProcessError(f"{error_type}: {error_text}".strip())
                    if worker_exited_at is None:
                        worker_exited_at = loop.time()
                    elif loop.time() - worker_exited_at >= _MISSING_RESULT_GRACE_SECONDS:
                        raise WorkerProcessError("worker exited without returning a result")
                elif terminal_sent_at is not None and loop.time() - terminal_sent_at >= _EXIT_GRACE_SECONDS:
                    # A worker should exit immediately after producing its
                    # terminal result; do not leave it alive indefinitely.
                    raise WorkerProcessError("worker did not exit after returning a result")

                if loop.time() >= deadline:
                    raise asyncio.TimeoutError
                await asyncio.sleep(_POLL_SECONDS)
        except BaseException:
            if process is not None and started:
                await _terminate_process_tree(process)
            raise
        finally:
            if child_connection is not None:
                try:
                    child_connection.close()
                except OSError:
                    pass
            if parent_connection is not None:
                try:
                    parent_connection.close()
                except OSError:
                    pass
            if process is not None:
                if _process_is_alive(process):
                    await _terminate_process_tree(process)
                stopped = _join_and_close_process(process)
                if registered and stopped:
                    self._unregister(process)
                elif not registered:
                    # Process.start() can raise after a slot was acquired.
                    # It was never added to _active, so release the slot here.
                    self._slots.release()
            elif not started:
                self._slots.release()

    def shutdown(self, wait: bool = True) -> tuple[int, ...]:
        """Terminate, reap and unregister active workers; safe to call repeatedly.

        The returned PIDs are workers that could not be reaped in this call.
        They remain registered so a later ``shutdown()`` can retry rather than
        silently releasing a slot while a process is still alive.
        """
        with self._lock:
            processes = list(self._active.values())
        remaining: list[int] = []
        for process in processes:
            stopped = _terminate_process_tree_sync(process, wait=wait)
            if stopped and _join_and_close_process(process):
                self._unregister(process)
            elif (pid := _process_pid(process)):
                remaining.append(pid)
        return tuple(remaining)


def shutdown_plugin_workers(wait: bool = True) -> tuple[int, ...]:
    """Reap every live bounded worker runner in this process.

    This is the stable application-shutdown entry point.  It is safe to call
    repeatedly; callers should invoke it after cancelling and awaiting their
    own tasks.  Returned PIDs could not be reaped and should be reported.
    """
    remaining: list[int] = []
    for runner in list(_RUNNERS):
        remaining.extend(runner.shutdown(wait=wait))
    return tuple(remaining)


def bounded_worker_snapshot() -> dict[str, object]:
    """Return aggregate worker state without exposing commands or arguments."""
    runners = list(_RUNNERS)
    active_pids = sorted(
        {
            pid
            for runner in runners
            for pid in runner.active_pids
            if pid
        }
    )
    return {
        "runner_count": len(runners),
        "pending_count": sum(runner.pending_count for runner in runners),
        "active_pids": active_pids,
        "active_pid_count": len(active_pids),
    }


async def run_bounded(
    executor: BoundedExecutor,
    fn: Callable[..., T],
    /,
    *args: Any,
    timeout: float,
    progress_callback: Callable[[str], None] | None = None,
    progress_kwarg: str | None = None,
    **kwargs: Any,
) -> T:
    """Compatibility wrapper around :meth:`BoundedExecutor.run`."""
    return await executor.run(
        fn,
        *args,
        timeout=timeout,
        progress_callback=progress_callback,
        progress_kwarg=progress_kwarg,
        **kwargs,
    )
