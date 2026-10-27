"""Synthetic /proc/pidfd boundary tests. Real Linux checks are separate."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import process_ownership as ownership
import guided_launcher


@pytest.fixture
def kernel(monkeypatch):
    # This is a synthetic kernel. Using the real PID makes the unrelated PID-1
    # record look like our child when pytest itself is container PID 1.
    parent = 42
    records = {100: (parent, 100, 1), 101: (100, 100, 2),
               102: (101, 102, 3), 103: (102, 102, 4), 900: (1, 900, 9)}
    opened, closed, killed, dead = [], [], [], set()
    def open_fd(pid):
        opened.append(pid)
        return pid + 1000
    monkeypatch.setattr(ownership, "process_record", lambda pid: records.get(pid))
    monkeypatch.setattr(ownership, "process_records", lambda: dict(records))
    monkeypatch.setattr(ownership, "os", SimpleNamespace(
        getpid=lambda: parent, pidfd_open=open_fd, close=closed.append))
    monkeypatch.setattr(ownership, "signal", SimpleNamespace(
        pidfd_send_signal=lambda fd, sig: killed.append(fd), SIGKILL=9))
    monkeypatch.setattr(ownership, "select", SimpleNamespace(
        select=lambda readers, *_: ([fd for fd in readers if fd in dead], [], [])))
    return SimpleNamespace(records=records, opened=opened, closed=closed, killed=killed, dead=dead)


def test_nested_sessions_and_reparented_children_stay_owned(kernel):
    tree = ownership.OwnedProcesses(100)
    tree.refresh()
    assert set(kernel.opened) == {100, 101, 102, 103}
    kernel.records.pop(100)
    kernel.records.pop(101)
    kernel.records[102] = (1, 102, 3)
    kernel.records[103] = (1, 102, 4)
    kernel.records[104] = (102, 102, 5)
    # Unrelated group reuses the old number after the original group has exited.
    kernel.records[900] = (1, 100, 99)
    tree.refresh()
    tree.kill()
    assert set(kernel.killed) == {1100, 1101, 1102, 1103, 1104}
    assert 900 not in tree.handles
    assert tree.running()
    kernel.dead.update(kernel.killed)
    assert not tree.running()
    tree.close()
    assert set(kernel.closed) == set(kernel.killed)
    tree.close()
    assert len(kernel.closed) == 5


def test_numeric_pid_reuse_does_not_retarget_old_handle(kernel):
    tree = ownership.OwnedProcesses(100)
    tree.refresh()
    kernel.records[102] = (1, 900, 999)
    kernel.records[103] = (1, 102, 4)
    kernel.dead.add(1102)
    tree.refresh()
    assert kernel.opened.count(102) == 1
    assert tree.handles[102] == (3, 1102)
    tree.kill()  # The old kernel handle cannot address the replacement process.
    assert 1102 in kernel.killed
    tree.close()


def test_capture_race_closes_handle_instead_of_adopting_new_identity(kernel, monkeypatch):
    original = ownership.os.pidfd_open
    def changed(pid):
        handle = original(pid)
        if pid == 101:
            kernel.records[pid] = (1, 900, 999)
        return handle
    monkeypatch.setattr(ownership.os, "pidfd_open", changed)
    tree = ownership.OwnedProcesses(100)
    tree.refresh()
    assert 101 not in tree.handles
    assert 1101 in kernel.closed
    tree.close()


def test_refuses_root_outside_the_launched_child_group(kernel):
    with pytest.raises(RuntimeError):
        ownership.OwnedProcesses(900)


@pytest.mark.asyncio
async def test_missing_linux_pidfd_support_prevents_worker_launch(monkeypatch):
    def unsupported():
        raise OSError("synthetic unsupported kernel")
    monkeypatch.setattr(ownership, "require_support", unsupported)
    monkeypatch.setattr(guided_launcher, "sys", SimpleNamespace(platform="linux"))
    launch = AsyncMock()
    monkeypatch.setattr(guided_launcher.asyncio, "create_subprocess_exec", launch)
    worker = guided_launcher.MediaWorker()
    monkeypatch.setattr(worker, "port_open", AsyncMock(return_value=False))
    with pytest.raises(OSError):
        await worker.start()
    launch.assert_not_awaited()
    assert worker.process is None


@pytest.mark.asyncio
async def test_shell_exit_does_not_interrupt_remaining_child_cleanup(monkeypatch):
    events = []
    remaining = [True, True, False, False]
    owner = SimpleNamespace(refresh=lambda: events.append("refresh"),
        running=lambda: remaining.pop(0), kill=lambda: events.append("kill"),
        close=lambda: events.append("close"))
    process = SimpleNamespace(pid=12345, returncode=0, wait=AsyncMock(return_value=0))
    worker = guided_launcher.MediaWorker(stop_timeout=2)
    worker.process, worker._owned = process, owner
    monkeypatch.setattr(guided_launcher.os, "killpg", lambda *_: events.append("term"), raising=False)
    async def allow_cleanup(_):
        events.append("cleanup time")
    monkeypatch.setattr(guided_launcher.asyncio, "sleep", allow_cleanup)
    await worker.stop()
    assert events.count("cleanup time") == 2
    assert events.index("cleanup time") < events.index("kill")
    assert worker.process is None and worker._owned is None


@pytest.mark.asyncio
async def test_retry_cleans_old_owner_before_any_new_launch(monkeypatch):
    worker = guided_launcher.MediaWorker()
    worker.process = SimpleNamespace(returncode=0)
    stop = AsyncMock()
    monkeypatch.setattr(worker, "stop", stop)
    monkeypatch.setattr(worker, "port_open", AsyncMock(return_value=True))
    with pytest.raises(RuntimeError, match="already in use"):
        await worker.start()
    stop.assert_awaited_once()
