"""Offline startup/permission tests: no HA, Eufy or video processes."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import account_setup
import guided_launcher
import ha_setup_trust as trust
import setup_web
import startup_controller as startup

@pytest.mark.parametrize("options,ready", [
    ({"go2rtc_username": "eufy", "go2rtc_password": "a" * 43}, True),
    ({"go2rtc_username": "eufy", "go2rtc_password": ""}, False),
    ({"go2rtc_username": "eufy", "go2rtc_password": "short"}, False),
    ({"go2rtc_username": "", "go2rtc_password": "a" * 43}, False),
    ({"go2rtc_username": "eufy", "go2rtc_password": "a" * 42 + "\n"}, False),
    ([], False), ({}, False),
])
def test_local_video_preflight_is_read_only(tmp_path, options, ready):
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    before = path.read_bytes()
    assert guided_launcher.local_video_ready(path) is ready
    assert path.read_bytes() == before


def test_local_video_preflight_fails_closed_without_options(tmp_path):
    assert not guided_launcher.local_video_ready(tmp_path / "missing.json")


@pytest.mark.asyncio
async def test_missing_local_video_password_does_not_attempt_eufy_login(tmp_path):
    worker = Worker()
    checker = AsyncMock(return_value="valid")
    controller = startup.StartupController(tmp_path / "auth", worker, checker=checker,
                                           local_credentials_check=lambda: False)
    await controller.reconcile()
    assert controller.state == "local_video_setup_required"
    assert not controller.can_setup()
    assert not controller.public_status()["can_retry"]
    checker.assert_not_awaited()
    assert worker.starts == 0
    await controller.tick()
    checker.assert_not_awaited()
    await controller.close()


@pytest.mark.asyncio
async def test_configured_local_video_password_proceeds_to_account_setup(tmp_path):
    worker = Worker()
    checker = AsyncMock(return_value="missing")
    controller = startup.StartupController(tmp_path / "auth", worker, checker=checker,
                                           local_credentials_check=lambda: True)
    await controller.reconcile()
    assert controller.state == "setup_required"
    assert controller.can_setup()
    checker.assert_awaited_once()
    await controller.close()

CACHE = {
    "cacheVersion": 2, "signalingIdentitySource": "nvr_admin",
    "authToken": "private-token", "gtoken": "private-gtoken", "userId": "private-user",
    "signalingUserId": "private-admin", "stationSn": "T8N00TEST000001",
    "region": "us-pr", "webCountry": "AU", "accountFingerprint": "a" * 64,
}
ADMIN = {"id": "admin1", "is_owner": True, "is_active": True,
         "system_generated": False, "group_ids": ["system-admin"]}
CONFIG = {"internal_url": "http://homeassistant.local/", "external_url": "https://ha.example:443"}
HEADERS = {"X-Remote-User-Id": "admin1", "Origin": "http://homeassistant.local",
           "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}


class Worker:
    def __init__(self):
        self.active = False
        self.starts = self.stops = 0
        self.fail = False

    def running(self):
        return self.active

    async def start(self):
        self.starts += 1
        if self.fail:
            raise RuntimeError("private failure not shown")
        self.active = True

    async def stop(self):
        self.stops += 1
        self.active = False


@pytest.mark.parametrize("status,body,outcome", [
    (200, {"code": 0, "data": "sign"}, "valid"),
    (401, {}, "rejected"), (403, {}, "rejected"),
    (200, {"code": 26084}, "rejected"),
    (500, {}, "unavailable"), (429, {}, "unavailable"),
    (200, {"code": 0, "data": ""}, "unavailable"), (200, [], "unavailable"),
    (302, {}, "unavailable"),
])
def test_cached_account_result_is_not_password_guessing(status, body, outcome):
    assert startup.classify_sign_response(status, body) == outcome


def test_cache_validation_keeps_private_file_unchanged(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(CACHE))
    assert startup.read_guided_cache(path) == CACHE
    original = path.read_bytes()
    for key, value in [("authToken", "line\nbreak"), ("region", "https://evil.example"),
                       ("cacheVersion", True), ("stationSn", "../other"),
                       ("accountFingerprint", ""), ("signalingIdentitySource", "passport")]:
        path.write_text(json.dumps({**CACHE, key: value}))
        before = path.read_bytes()
        assert startup.read_guided_cache(path) is None
        assert path.read_bytes() == before
    path.write_bytes(original)
    assert startup.read_guided_cache(path)["authToken"] == "private-token"


@pytest.mark.asyncio
async def test_no_cache_waits_for_human_without_launching_or_login(tmp_path):
    worker = Worker()
    checker = AsyncMock(return_value="missing")
    control = startup.StartupController(tmp_path / "auth", worker, checker=checker)
    await control.reconcile()
    assert control.public_status()["state"] == "setup_required"
    assert control.can_setup() and worker.starts == 0
    await control.tick()
    assert checker.await_count == 1


@pytest.mark.asyncio
async def test_saved_setup_starts_exactly_once_without_restart(tmp_path):
    worker = Worker()
    control = startup.StartupController(tmp_path / "auth", worker,
                                       checker=AsyncMock(return_value="valid"))
    control.state = "setup_required"
    control.saved()
    assert not control.can_setup()
    await control._task
    assert worker.starts == 1
    assert control.state == "video_service_running"
    await control.reconcile()
    assert worker.starts == 1
    assert not control.can_setup()
    assert "live" not in control.public_status()
    await control.close()
    assert not worker.running()


@pytest.mark.asyncio
async def test_media_worker_owns_one_group_and_drops_inherited_tokens(monkeypatch):
    import process_ownership
    from types import SimpleNamespace
    class Process:
        pid = 12345
        returncode = None
        async def wait(self):
            self.returncode = 0
            return 0
    process = Process()
    spawn = AsyncMock(return_value=process)
    signals = []
    monkeypatch.setattr(guided_launcher.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(guided_launcher.os, "killpg", lambda pid, sig: signals.append((pid, sig)), raising=False)
    monkeypatch.setattr(guided_launcher.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(process_ownership, "require_support", lambda: None)
    monkeypatch.setattr(process_ownership, "OwnedProcesses", lambda pid: SimpleNamespace(
        refresh=lambda: None, running=lambda: False,
        kill=lambda: signals.append((pid, 9)), close=lambda: None))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "private-supervisor")
    monkeypatch.setenv("EUFY_PASSWORD", "private-password")
    worker = guided_launcher.MediaWorker()
    monkeypatch.setattr(worker, "port_open", AsyncMock(side_effect=[False, True]))
    await worker.start()
    assert spawn.await_args.args == ("/run.sh", "--guided-worker")
    assert spawn.await_args.kwargs["start_new_session"] is True
    assert "SUPERVISOR_TOKEN" not in spawn.await_args.kwargs["env"]
    assert "EUFY_PASSWORD" not in spawn.await_args.kwargs["env"]
    await worker.start()
    assert spawn.await_count == 1
    await worker.stop()
    assert signals and all(pid == 12345 for pid, sig in signals)
    assert worker.process is None


@pytest.mark.asyncio
async def test_existing_video_port_prevents_launch(monkeypatch):
    worker = guided_launcher.MediaWorker()
    spawn = AsyncMock()
    monkeypatch.setattr(guided_launcher.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(worker, "port_open", AsyncMock(return_value=True))
    with pytest.raises(RuntimeError, match="already in use"):
        await worker.start()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_supervisor_lookup_sends_only_auth_and_read_commands(monkeypatch):
    sent = []
    replies = iter([
        {"type": "auth_required"}, {"type": "auth_ok"},
        {"type": "result", "id": 1, "success": True, "result": [ADMIN]},
        {"type": "result", "id": 2, "success": True, "result": CONFIG},
    ])
    class Socket:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def receive_json(self): return next(replies)
        async def send_json(self, message): sent.append(message)
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def ws_connect(self, url, **kwargs):
            assert url == "http://supervisor/core/websocket"
            assert kwargs["max_msg_size"] == 1024 * 1024
            return Socket()
    monkeypatch.setattr(trust.aiohttp, "ClientSession", Session)
    allowed, origins = await trust.fetch_trust("private-supervisor")
    assert allowed == {"admin1"}
    assert [message["type"] for message in sent] == ["auth", "config/auth/list", "get_config"]
    assert "private-supervisor" not in repr((allowed, origins))


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks,expected", [
    ([b'{"code":0,', b'"data":"sign"}'], "valid"),
    ([b"x" * 65537], "unavailable"),
    ([b"not json"], "unavailable"),
])
async def test_saved_account_check_reads_split_body_with_limit(tmp_path, monkeypatch, chunks, expected):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(CACHE))
    class Content:
        async def iter_chunked(self, size):
            for chunk in chunks:
                yield chunk
    class Response:
        status = 200
        content = Content()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def get(self, url, **kwargs):
            assert url.startswith("https://")
            assert kwargs["allow_redirects"] is False
            return Response()
    monkeypatch.setattr(startup.aiohttp, "ClientSession", Session)
    before = path.read_bytes()
    assert await startup.check_saved_account(path) == expected
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_network_outage_keeps_running_service_and_cache(tmp_path):
    path = tmp_path / "auth"
    path.write_text("private cache")
    worker = Worker()
    checker = AsyncMock(side_effect=["valid", "unavailable", "valid"])
    control = startup.StartupController(path, worker, checker=checker)
    await control.reconcile()
    await control.reconcile()
    assert worker.running() and worker.starts == 1 and worker.stops == 0
    assert control.public_status()["warning"] == "account_check_unavailable"
    assert path.read_text() == "private cache"
    await control.reconcile()
    assert control.warning is None and worker.starts == 1


@pytest.mark.asyncio
async def test_offline_boot_retries_checks_not_passwords(tmp_path):
    worker = Worker()
    now = [0.0]
    checker = AsyncMock(side_effect=["unavailable", "valid"])
    control = startup.StartupController(tmp_path / "auth", worker, checker=checker,
                                       clock=lambda: now[0])
    await control.reconcile()
    assert control.state == "connection_unavailable"
    assert not control.can_setup() and worker.starts == 0
    now[0] = 59
    await control.tick()
    assert checker.await_count == 1
    now[0] = 61
    await control.tick()
    assert worker.starts == 1


@pytest.mark.asyncio
async def test_rejected_session_stops_only_owned_worker_and_requires_human(tmp_path):
    worker = Worker()
    checker = AsyncMock(side_effect=["valid", "rejected"])
    control = startup.StartupController(tmp_path / "auth", worker, checker=checker)
    await control.reconcile()
    await control.reconcile()
    assert not worker.running() and worker.stops == 1
    assert control.state == "sign_in_required" and control.can_setup()
    await control.tick()
    assert checker.await_count == 2


@pytest.mark.asyncio
async def test_worker_failure_needs_explicit_retry_not_restart_storm(tmp_path):
    worker = Worker()
    worker.fail = True
    control = startup.StartupController(tmp_path / "auth", worker,
                                       checker=AsyncMock(return_value="valid"))
    await control.reconcile()
    assert control.state == "service_error" and worker.starts == 1
    await control.tick()
    assert worker.starts == 1
    worker.fail = False
    control.retry()
    await control._task
    assert worker.starts == 2 and worker.running()
    worker.active = False
    await control.tick()
    assert control.state == "service_error"


@pytest.mark.asyncio
async def test_concurrent_check_does_not_start_two_workers(tmp_path):
    worker = Worker()
    entered, release = asyncio.Event(), asyncio.Event()
    async def checker(path):
        entered.set()
        await release.wait()
        return "valid"
    control = startup.StartupController(tmp_path / "auth", worker, checker=checker)
    first = asyncio.create_task(control.reconcile())
    await entered.wait()
    await control.reconcile()
    assert not control.can_setup()
    release.set()
    await first
    assert worker.starts == 1


def test_child_environment_drops_cloud_and_supervisor_secrets():
    clean = guided_launcher.child_environment({
        "SUPERVISOR_TOKEN": "private", "HASSIO_TOKEN": "private",
        "EUFY_PASSWORD": "private", "EUFY_REGION": "old", "PATH": "/bin",
    })
    assert clean == {"PATH": "/bin", "EUFY_GUIDED_WORKER": "1"}
    script = (Path(__file__).resolve().parents[1] / "eufy_nvr/run.sh").read_text()
    assert "unset SUPERVISOR_TOKEN HASSIO_TOKEN" in script
    assert "if [ \"${GUIDED_WORKER}\" = 1 ]; then return 0; fi" in script
    assert "python3 guided_launcher.py --validate-cache" in script


def test_trust_uses_only_active_human_admins_and_configured_origins():
    users = [
        ADMIN, {**ADMIN, "id": "disabled", "is_active": False},
        {**ADMIN, "id": "system", "system_generated": True},
        {**ADMIN, "id": "guest", "is_owner": False, "group_ids": ["system-users"]},
        {**ADMIN, "id": "bad-groups", "is_owner": False, "group_ids": "not-system-admin"},
    ]
    allowed, origins = trust.trust_from_metadata(users, CONFIG)
    assert allowed == {"admin1"}
    assert origins == {"http://homeassistant.local", "https://ha.example"}


@pytest.mark.parametrize("url", ["https://name:password@ha.example", "file:///config",
                                "https://ha.example/private", "http://ha.example:bad"])
def test_untrusted_or_invalid_config_urls_are_not_used(url):
    with pytest.raises(trust.TrustUnavailable):
        trust.trust_from_metadata([ADMIN], {"internal_url": url})


@pytest.mark.asyncio
async def test_permission_cache_expires_and_fails_closed():
    now = [100.0]
    fetcher = AsyncMock(side_effect=[({"admin1"}, {"http://homeassistant.local"}),
                                    RuntimeError("private-token")])
    policy = trust.HomeAssistantTrust("private-token", fetcher=fetcher, clock=lambda: now[0])
    assert (await policy.current())[0] == {"admin1"}
    await policy.current()
    assert fetcher.await_count == 1
    now[0] += 31
    with pytest.raises(trust.TrustUnavailable) as caught:
        await policy.current()
    assert "private" not in str(caught.value)
    assert policy._policy is None


@pytest.mark.asyncio
async def test_http_save_drives_startup_and_running_service_blocks_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_web, "_peer_ip", lambda request: "172.30.32.2")
    monkeypatch.setattr(account_setup.ec, "login", AsyncMock(return_value={
        "auth_token": "private-token", "gtoken": "private-gtoken", "user_id": "private-user"}))
    monkeypatch.setattr(account_setup.ec, "station_list", AsyncMock(return_value={
        "data": [{"station_sn": CACHE["stationSn"], "name": "Recorder", "admin_user_id": "private-admin"}]}))
    worker = Worker()
    checker = AsyncMock(side_effect=["missing", "valid"])
    runtime = startup.StartupController(tmp_path / "auth", worker, checker=checker)
    app = setup_web.create_app(auth_path=tmp_path / "auth", budget_path=tmp_path / "budget",
        allowed_users={"admin1"}, allowed_origins={"http://homeassistant.local"}, runtime=runtime)
    async with TestClient(TestServer(app)) as http:
        await asyncio.sleep(0)
        body = await (await http.post("/api/session", json={}, headers=HEADERS)).json()
        headers = {**HEADERS, "X-Setup-Session": body["session"], "X-Setup-CSRF": body["csrf"]}
        status = await (await http.post("/api/start", json={
            "email": "owner@example.com", "password": "private-password", "country": "AU", "region": "US",
        }, headers=headers)).json()
        result = await http.post("/api/select", json={"selection": status["stations"][0]["id"]}, headers=headers)
        assert (await result.json())["state"] == "complete"
        await runtime._task
        assert worker.starts == 1
        status = await (await http.get("/api/status", headers=headers)).json()
        assert status["runtime"]["state"] == "video_service_running"
        assert not status["runtime"]["can_setup"]
        previous = (tmp_path / "auth").read_bytes()
        assert (await http.post("/api/start", json={
            "email": "other@example.com", "password": "private-password", "country": "AU", "region": "US",
        }, headers=headers)).status == 409
        assert (tmp_path / "auth").read_bytes() == previous
    assert not worker.running()



@pytest.mark.asyncio
async def test_occupied_video_port_is_distinct_from_bad_account_and_does_not_loop(tmp_path):
    worker = Worker()
    async def occupied():
        worker.starts += 1
        raise startup.VideoPortInUseError()
    worker.start = occupied
    checker = AsyncMock(return_value="valid")
    controller = startup.StartupController(tmp_path / "auth", worker, checker=checker)
    await controller.reconcile()
    assert controller.state == "video_port_in_use"
    assert controller.public_status()["can_retry"]
    assert not controller.can_setup()
    checker.assert_awaited_once()
    await controller.tick()
    await controller.tick()
    assert worker.starts == 1
    assert checker.await_count == 1
    assert set(controller.public_status()) <= {"state", "warning", "can_setup", "can_retry"}
    await controller.close()


@pytest.mark.asyncio
async def test_busy_port_never_spawns_a_video_process(monkeypatch):
    worker = guided_launcher.MediaWorker()
    monkeypatch.setattr(worker, "port_open", AsyncMock(return_value=True))
    spawn = AsyncMock()
    monkeypatch.setattr(guided_launcher.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(startup.VideoPortInUseError):
        await worker.start()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_reopened_local_session_reports_actual_remaining_life(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_web, "_peer_ip", lambda request: "172.30.32.2")
    app = setup_web.create_app(auth_path=tmp_path / "auth", budget_path=tmp_path / "budget",
        allowed_users={"admin1"}, allowed_origins={"http://homeassistant.local"})
    clock = [1000.0]
    manager = app[setup_web.MANAGER]
    manager.clock = lambda: clock[0]
    async with TestClient(TestServer(app)) as http:
        first = await (await http.post("/api/session", json={}, headers=HEADERS)).json()
        assert first["expires_in_seconds"] == 600
        deadline = manager.sessions["admin1"]["deadline"]
        clock[0] += 270
        second = await (await http.post("/api/session", json={}, headers=HEADERS)).json()
        assert second["expires_in_seconds"] == 330
        assert first["session"] == second["session"]
        assert manager.sessions["admin1"]["deadline"] == deadline


@pytest.mark.asyncio
async def test_reopen_completed_setup_refreshes_only_local_session(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_web, "_peer_ip", lambda request: "172.30.32.2")
    login = AsyncMock()
    monkeypatch.setattr(account_setup.ec, "login", login)
    auth = tmp_path / "auth"
    auth.write_text("synthetic unchanged saved account")
    before = auth.read_bytes()
    app = setup_web.create_app(auth_path=auth, budget_path=tmp_path / "budget",
        allowed_users={"admin1"}, allowed_origins={"http://homeassistant.local"})
    clock = [1000.0]
    manager = app[setup_web.MANAGER]
    manager.clock = lambda: clock[0]
    async with TestClient(TestServer(app)) as http:
        first = await (await http.post("/api/session", json={}, headers=HEADERS)).json()
        wizard = manager.sessions["admin1"]["wizard"]
        wizard.state = "complete"
        clock[0] += 270
        second = await (await http.post("/api/session", json={}, headers=HEADERS)).json()
        assert second["session"] != first["session"]
        assert second["expires_in_seconds"] == 600
        assert wizard.state == "cancelled"
        assert auth.read_bytes() == before
        login.assert_not_awaited()


@pytest.mark.parametrize("phase", ["connecting", "captcha", "email_code", "select_nvr"])
def test_reopen_does_not_reset_in_progress_signin_lifetime(tmp_path, phase):
    clock = [1000.0]
    manager = setup_web.SetupSessions(tmp_path / "auth", None, clock=lambda:clock[0])
    first = manager.open("owner")
    first["wizard"].state = phase
    clock[0] += 270
    second = manager.open("owner")
    assert first is second
    assert second["deadline"] == 1600
    assert second["wizard"].state == phase
