"""Actual worker loop regression; virtual time, no video or vendor networking."""
import asyncio
import inspect
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(os.environ.get("EFX_STARTUP_REPRO_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT / "bridge"))
import guided_launcher


def fake_worker(monkeypatch, *, ready_at=None, fails_at=None):
    now = [0.0]
    process = SimpleNamespace(pid=99999, returncode=None)
    monkeypatch.setattr(guided_launcher.sys, "platform", "win32")
    monkeypatch.setattr(guided_launcher.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    options = {"clock": lambda: now[0]} if "clock" in inspect.signature(guided_launcher.MediaWorker).parameters else {}
    worker = guided_launcher.MediaWorker(**options)
    probes = [0]
    async def probe():
        probes[0] += 1
        return probes[0] > 1 and ready_at is not None and now[0] >= ready_at
    async def advance(seconds):
        now[0] += seconds
        if fails_at is not None and now[0] >= fails_at:
            process.returncode = 1
    async def stop():
        process.returncode = 0
        worker.process = None
    worker.port_open = probe
    worker.stop = AsyncMock(side_effect=stop)
    monkeypatch.setattr(guided_launcher.asyncio, "sleep", advance)
    return worker, now


def test_slow_valid_discovery_not_killed_at_110_seconds(monkeypatch):
    # A permitted first failed outer attempt followed by a valid second discovery.
    worker, now = fake_worker(monkeypatch, ready_at=116)
    asyncio.run(worker.start())
    assert worker.running() and now[0] == 116
    worker.stop.assert_not_awaited()


def test_genuine_startup_failure_is_cleaned_up_without_waiting_full_budget(monkeypatch):
    worker, now = fake_worker(monkeypatch, fails_at=5)
    with pytest.raises(RuntimeError, match="stopped during startup"):
        asyncio.run(worker.start())
    assert now[0] == 5 and not worker.running()
    worker.stop.assert_awaited_once()


def test_no_listener_reaches_finite_deadline_and_stops(monkeypatch):
    worker, now = fake_worker(monkeypatch)
    with pytest.raises(RuntimeError, match="did not become ready"):
        asyncio.run(worker.start())
    assert now[0] == worker.startup_timeout
    assert not worker.running()
    worker.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_cancels_wait_immediately_and_finishes_cleanup(monkeypatch):
    worker, _ = fake_worker(monkeypatch)
    reached_wait = asyncio.Event()
    blocked = asyncio.Event()
    async def wait(seconds):
        reached_wait.set()
        await blocked.wait()
    monkeypatch.setattr(guided_launcher.asyncio, "sleep", wait)
    task = asyncio.create_task(worker.start())
    await reached_wait.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    worker.stop.assert_awaited_once()
    assert not worker.running()


@pytest.mark.asyncio
async def test_uncertain_previous_cleanup_is_not_retried_or_replaced(monkeypatch):
    worker = guided_launcher.MediaWorker()
    worker.process = SimpleNamespace(returncode=1)
    worker.stop = AsyncMock(side_effect=RuntimeError("old cleanup uncertain"))
    spawn = AsyncMock()
    monkeypatch.setattr(guided_launcher.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="cleanup uncertain"):
        await worker.start()
    worker.stop.assert_awaited_once()
    spawn.assert_not_awaited()
