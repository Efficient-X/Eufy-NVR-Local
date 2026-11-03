"""Finite discovery and fixed-vocabulary retention; no HA or Eufy requests."""
import asyncio
import errno
import json
from pathlib import Path
import socket
import ssl
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import eufy_run
import guided_launcher
import startup_controller
import startup_policy as policy

PRIVATE = "password token http://private.invalid/?secret=private"


def test_deadlines_include_every_retry_cleanup_release_and_control_margin():
    assert policy.SESSION_ATTEMPTS == 4 and policy.DISCOVERY_ATTEMPTS == 1
    assert policy.DISCOVERY_BUDGET_SECONDS == 2 * (25 + 35) == 120
    assert policy.DISCOVERY_CLEANUP_SECONDS == (3+5+2+5) + 2 + 1 == 18
    assert policy.WORKER_READY_SECONDS == 120 + 18 + 30 == 168
    assert policy.WORKER_CONTROL_SECONDS == 168 + 2*35 + 1 == 239
    assert policy.WATCH_LIVENESS_SECONDS == 399



@pytest.mark.asyncio
async def test_guided_discovery_has_absolute_cap_even_with_progress(monkeypatch):
    # SDP and data-channel stay active, so old per-progress timeouts never fire.
    proc = SimpleNamespace(returncode=None, pid=99999)
    async def wait():
        if proc.returncode is not None:
            return proc.returncode
        await asyncio.sleep(0)
        raise TimeoutError
    proc.wait = wait
    monkeypatch.setattr(eufy_run.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    clock = iter([0, 59.5, 60])
    monkeypatch.setattr(eufy_run, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    async def relay(proc, state, discovery):
        state.sdp_seen = state.datachannel_seen = True
    monkeypatch.setattr(eufy_run, "_relay_stderr", relay)
    async def terminate(proc):
        proc.returncode = 0
    monkeypatch.setattr(eufy_run, "_terminate_tree", AsyncMock(side_effect=terminate))
    monkeypatch.setattr(eufy_run, "GUIDED_DISCOVERY", True)
    rc, reason, state = await eufy_run.run_attempt(["--discover"], True)
    assert rc == 0 and reason == "discovery_timeout"
    assert state.sdp_seen and state.datachannel_seen


@pytest.mark.asyncio
async def test_discovery_lock_deadline_and_cancellation_release_handle(monkeypatch, capsys):
    class Gate:
        released = 0
        async def acquire(self, event):
            raise TimeoutError(PRIVATE)
        def release(self): self.released += 1
    gate = Gate()
    monkeypatch.setattr(eufy_run, "SessionGate", lambda: gate)
    monkeypatch.setattr(eufy_run, "GUIDED_DISCOVERY", True)
    monkeypatch.setattr(eufy_run, "run_attempt", AsyncMock())
    assert await eufy_run.main(["--discover"]) == 75
    assert gate.released == 1
    eufy_run.run_attempt.assert_not_awaited()
    assert capsys.readouterr().err.strip() == "EUFY_STARTUP discovery_lock_timeout"
    async def cancelled(event): raise asyncio.CancelledError
    gate.acquire = cancelled
    with pytest.raises(asyncio.CancelledError):
        await eufy_run.main(["--discover"])
    assert gate.released == 2


@pytest.mark.asyncio
async def test_real_lock_deadline_ends_a_hung_acquisition(monkeypatch):
    blocked = asyncio.Event()
    gate = SimpleNamespace(acquire=AsyncMock(side_effect=lambda event: None), release=lambda: None)
    async def acquire(event):
        await blocked.wait()
    gate.acquire = acquire
    released = []
    gate.release = lambda: released.append(True)
    monkeypatch.setattr(eufy_run, "SessionGate", lambda: gate)
    monkeypatch.setattr(eufy_run, "GUIDED_DISCOVERY", True)
    monkeypatch.setattr(eufy_run, "SESSION_LOCK_SECONDS", .02)
    assert await asyncio.wait_for(eufy_run.main(["--discover"]), 1) == 75
    assert released == [True]


@pytest.mark.asyncio
async def test_shared_budget_stops_hung_session_without_a_second_retry_group(monkeypatch, capsys):
    blocked = asyncio.Event()
    cancelled = []
    async def sessions(*args):
        try:
            await blocked.wait()
        finally:
            cancelled.append(True)
    released = []
    monkeypatch.setattr(eufy_run, "_run_sessions", sessions)
    monkeypatch.setattr(eufy_run, "SessionGate", lambda: SimpleNamespace(release=lambda: released.append(True)))
    monkeypatch.setattr(eufy_run, "GUIDED_DISCOVERY", True)
    monkeypatch.setattr(eufy_run, "DISCOVERY_BUDGET_SECONDS", .02)
    assert await asyncio.wait_for(eufy_run.main(["--discover"]), 1) == 1
    assert cancelled == [True] and released == [True]
    assert capsys.readouterr().err.strip() == "EUFY_STARTUP discovery_budget_expired"


@pytest.mark.asyncio
async def test_live_producer_does_not_inherit_discovery_deadline(monkeypatch):
    proc = SimpleNamespace(returncode=None, pid=99999)
    calls = 0
    async def wait():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        if calls < 3:
            raise TimeoutError
        proc.returncode = 0
        return 0
    proc.wait = wait
    monkeypatch.setattr(eufy_run.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    clock = iter([0, 59.5, 60.5])
    monkeypatch.setattr(eufy_run, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    async def relay(proc, state, discovery):
        state.sdp_seen = state.datachannel_seen = True
        state.first_video_at = state.last_video_at = 60
    monkeypatch.setattr(eufy_run, "_relay_stderr", relay)
    monkeypatch.setattr(eufy_run, "_terminate_tree", AsyncMock())
    monkeypatch.setattr(eufy_run, "GUIDED_DISCOVERY", True)
    rc, reason, _ = await eufy_run.run_attempt([], False)
    assert rc == 0 and reason == "exit"


@pytest.mark.parametrize("error,category", [
    (ConnectionRefusedError(errno.ECONNREFUSED, PRIVATE), "connection_refused"),
    (ConnectionResetError(errno.ECONNRESET, PRIVATE), "connection_reset"),
    (socket.gaierror(-2, PRIVATE), "name_resolution"),
    (TimeoutError(PRIVATE), "timeout"),
    (ssl.SSLError(PRIVATE), "tls_error"),
    (OSError(errno.ENETUNREACH, PRIVATE), "network_unreachable"),
    (OSError(PRIVATE), "transport_other"),
])
def test_transport_subtypes_cannot_contain_exception_text(error, category):
    assert policy.transport_category(error) == category
    assert policy.transport_category(SimpleNamespace(os_error=error)) == category


@pytest.mark.asyncio
async def test_loopback_probe_keeps_only_transport_subtype(monkeypatch):
    monkeypatch.setattr(guided_launcher.asyncio, "open_connection", AsyncMock(
        side_effect=ConnectionRefusedError(errno.ECONNREFUSED, PRIVATE)))
    worker = guided_launcher.MediaWorker()
    assert not await worker.port_open()
    assert worker.last_transport == "connection_refused"


def result(state="service_error"):
    return {"state": state, "warning": None, "account_transport": "http_response",
            "video_transport": "connection_refused", "worker_phase": "discovering"}


def test_retained_snapshot_is_bounded_and_whitelisted(tmp_path, capsys):
    path = tmp_path / "runtime.json"
    value = {**result(), "last_result": result()}
    for _ in range(40):
        policy.retain_status(path, "runtime", value)
    assert path.stat().st_size < 1024
    assert policy.read_status(path, "runtime") == value
    assert PRIVATE not in path.read_text() + capsys.readouterr().out
    assert list(tmp_path.iterdir()) == [path]
    path.write_text(json.dumps({"version":1,"status":{**value,"account":PRIVATE}}))
    assert policy.read_status(path, "runtime") is None
    path.write_bytes(b"x" * 1025)
    assert policy.read_status(path, "runtime") is None


@pytest.mark.parametrize("value", [
    {"phase":PRIVATE}, {"phase":"discovering","url":PRIVATE},
    {"phase":[]}, {"phase":None},
])
def test_arbitrary_worker_details_are_never_written(tmp_path, value, capsys):
    with pytest.raises((ValueError, TypeError)):
        policy.retain_status(tmp_path/"worker.json", "worker", value)
    assert not list(tmp_path.iterdir())
    assert capsys.readouterr() == ("", "")


def test_disk_failure_only_emits_fixed_message(tmp_path, monkeypatch, capsys):
    def fail(*args, **kwargs): raise OSError(PRIVATE)
    monkeypatch.setattr(policy.tempfile, "mkstemp", fail)
    policy.retain_status(tmp_path/"worker.json", "worker", {"phase":"discovering"})
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "EUFY_STARTUP status_retention_unavailable"
    assert json.loads(lines[1].removeprefix("EUFY_STARTUP ")) == {"area":"worker","status":{"phase":"discovering"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("account,state", [("missing","setup_required"),("rejected","sign_in_required")])
async def test_account_gate_retained_without_worker_or_sensitive_details(tmp_path, account, state):
    worker = SimpleNamespace(running=lambda:False, start=AsyncMock(), stop=AsyncMock())
    path = tmp_path/"startup-runtime.json"
    runtime = startup_controller.StartupController(tmp_path/"auth", worker,
        checker=AsyncMock(return_value=account),status_path=path)
    await runtime.reconcile()
    await runtime.close()
    worker.start.assert_not_awaited()
    saved = policy.read_status(path, "runtime")
    assert saved["state"] == "stopped" and saved["last_result"]["state"] == state
    reopened = startup_controller.StartupController(tmp_path/"auth", worker,
        checker=AsyncMock(return_value=account),status_path=path)
    assert reopened.last_result["state"] == state


def test_source_keeps_legacy_and_live_session_policy_separate():
    source = (Path(__file__).resolve().parents[1]/"bridge/eufy_run.py").read_text()
    assert "attempts = SESSION_ATTEMPTS if discovery and GUIDED_DISCOVERY else MAX_ATTEMPTS" in source
    assert "release_delay = SESSION_RELEASE_SECONDS if discovery and GUIDED_DISCOVERY else SESSION_RELEASE_DELAY" in source


@pytest.mark.asyncio
async def test_flat_retry_sequence_preserves_the_116_second_valid_path(monkeypatch):
    now = [0]
    calls = []
    state = SimpleNamespace(turn_pending=True)
    async def attempt(args, discovery, stop_event):
        calls.append(True)
        now[0] += 25 if len(calls) < 4 else 30
        return (1, "signaling_timeout", state) if len(calls) < 4 else (0, "exit", state)
    async def sleep(seconds): now[0] += seconds
    async def retry_wait(awaitable, timeout):
        awaitable.close()  # Synthetic unset stop-event wait; do not leave a coroutine.
        now[0] += timeout
        raise TimeoutError
    proxy = SimpleNamespace(**{name:getattr(asyncio,name) for name in dir(asyncio)})
    proxy.sleep, proxy.wait_for = sleep, retry_wait
    monkeypatch.setattr(eufy_run, "asyncio", proxy)
    monkeypatch.setattr(eufy_run, "GUIDED_DISCOVERY", True)
    monkeypatch.setattr(eufy_run, "run_attempt", attempt)
    gate = SimpleNamespace(acquire=AsyncMock(return_value=True),release=lambda:None)
    assert await eufy_run._run_sessions(["--discover"], True, asyncio.Event(), gate) == 0
    assert len(calls) == 4 and now[0] == 116
    assert now[0] < policy.DISCOVERY_BUDGET_SECONDS


@pytest.mark.parametrize("state,warning,account,video", [
    ("connection_unavailable","account_check_unavailable","connection_reset",None),
    ("service_error",None,"http_response","connection_refused"),
    ("service_error",None,None,"timeout"),
])
def test_status_log_exposes_only_fixed_schema_before_video_or_ingress(tmp_path,capsys,state,warning,account,video):
    value = {**result(state),"warning":warning,"account_transport":account,"video_transport":video}
    policy.retain_status(tmp_path/"runtime", "runtime", {**value,"last_result":value})
    line = capsys.readouterr().out.strip()
    assert line.startswith("EUFY_STARTUP {") and len(line.encode()) <= 1100
    record = json.loads(line.removeprefix("EUFY_STARTUP "))
    assert set(record) == {"area","status"} and record["area"] == "runtime"
    checked = policy.checked_status(record["status"], "runtime")
    assert (checked["state"],checked["warning"],checked["account_transport"],checked["video_transport"]) == (state,warning,account,video)
    assert PRIVATE not in line and str(tmp_path) not in line


@pytest.mark.parametrize("field", ["warning","account_transport","video_transport","worker_phase"])
def test_untrusted_runtime_text_cannot_enter_log_or_file(tmp_path,capsys,field):
    value = {**result(),field:PRIVATE,"last_result":None}
    with pytest.raises((ValueError,TypeError)):
        policy.retain_status(tmp_path/"runtime", "runtime", value)
    assert capsys.readouterr() == ("","")
    assert not list(tmp_path.iterdir())
