"""Synthetic consent and renewal tests; no real accounts or network."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import account_setup
import remembered_login
import startup_controller
from setup_budget import SetupBudget

SERIAL = "T8N00TEST000001"
CREDS = {"auth_token": "new-token", "gtoken": "new-gtoken", "user_id": "same-user"}
RAW = {"data": [{"station_sn": SERIAL, "name": "Test NVR", "admin_user_id": "same-admin"}]}


@pytest.fixture
def saved(tmp_path, monkeypatch):
    login = AsyncMock(return_value=CREDS.copy())
    stations = AsyncMock(return_value=RAW)
    monkeypatch.setattr(remembered_login.ec, "login", login)
    monkeypatch.setattr(remembered_login.ec, "station_list", stations)
    path = tmp_path / "auth.json"
    cache = {"cacheVersion": 2, "signalingIdentitySource": "nvr_admin",
             "authToken": "old-token", "gtoken": "old-gtoken", "userId": "same-user",
             "signalingUserId": "same-admin", "stationSn": SERIAL, "region": "us-pr",
             "webCountry": "AU", "accountFingerprint":
                 remembered_login.account_fingerprint("owner@example.com", "us-pr", "AU"),
             "rememberedLogin": {"version": 1, "email": "owner@example.com",
                                "password": "synthetic-password", "paused": False, "reason": None}}
    path.write_text(json.dumps(cache))
    budget = SetupBudget(tmp_path / "budget.json")
    return path, cache, budget, login, stations


@pytest.mark.asyncio
@pytest.mark.parametrize("remember", [False, True])
async def test_explicit_consent_is_saved_atomically_only_when_recorder_is_selected(saved, remember):
    path, cache, _, login, stations = saved
    before = path.read_bytes()
    wizard = account_setup.AccountSetup()
    status = await wizard.start("owner@example.com", "synthetic-password",
                                country="AU", remember=remember)
    assert status["state"] == "select_nvr" and path.read_bytes() == before
    assert "synthetic-password" not in json.dumps(status)
    result = wizard.select_nvr(status["stations"][0]["id"], path)
    assert result == {"state": "complete"}
    written = json.loads(path.read_text())
    assert ("rememberedLogin" in written) is remember
    assert bool(wizard._password) is False
    assert "rememberedLogin" not in startup_controller.read_guided_cache(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("remember", ["true", 1, None, {}])
async def test_non_boolean_consent_is_rejected(saved, remember):
    _, _, _, login, _ = saved
    wizard = account_setup.AccountSetup()
    assert (await wizard.start("owner@example.com", "pw", remember=remember))["error"] == "invalid_input"
    login.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_renewal_preserves_selected_identity_and_never_exposes_password(saved):
    path, cache, budget, login, stations = saved
    renewal = remembered_login.RememberedLogin(path, budget)
    assert await renewal.renew() == "renewed"
    result = json.loads(path.read_text())
    assert result["authToken"] == "new-token"
    for key in ("stationSn", "signalingUserId", "userId", "region", "webCountry", "accountFingerprint"):
        assert result[key] == cache[key]
    assert result["rememberedLogin"]["paused"] is False
    assert renewal.public_status() == {"remembered": True, "renewal_paused": False}
    login.assert_awaited_once()
    assert login.await_args.kwargs["save_captcha"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    "captcha", "verification", "rate_limit", "password", "network", "identity", "recorder",
])
async def test_failed_renewal_pauses_across_process_restart(saved, failure):
    path, cache, budget, login, stations = saved
    exceptions = {
        "captcha": remembered_login.ec.EufyCaptchaRequired("synthetic private challenge"),
        "verification": remembered_login.ec.EufyVerificationRequired("synthetic email check"),
        "rate_limit": remembered_login.ec.EufyLoginRateLimited("synthetic rate limit"),
        "password": remembered_login.ec.EufyLoginRejected("synthetic rejection"),
        "network": TimeoutError("synthetic network detail"),
    }
    if failure in exceptions:
        login.side_effect = exceptions[failure]
    elif failure == "identity":
        login.return_value = {**CREDS, "user_id": "different-user"}
    else:
        stations.return_value = {"data": [{"station_sn": "T8N00OTHER00001", "admin_user_id": "same-admin"}]}
    renewal = remembered_login.RememberedLogin(path, budget)
    assert await renewal.renew() == "manual"
    after = json.loads(path.read_text())
    assert after["authToken"] == "old-token"
    assert after["rememberedLogin"]["paused"] is True
    restarted = remembered_login.RememberedLogin(path, SetupBudget(budget.path))
    assert await restarted.renew() == "manual"
    assert login.await_count == 1
    assert "synthetic" not in json.dumps(restarted.public_status())
    if failure == "rate_limit":
        assert SetupBudget(budget.path).reserve() > 86000


@pytest.mark.asyncio
async def test_forget_during_inflight_renewal_cannot_restore_credentials(saved):
    path, cache, budget, login, stations = saved
    waiting = asyncio.Event()
    proceed = asyncio.Event()
    async def slow(*args, **kwargs):
        waiting.set()
        await proceed.wait()
        return CREDS.copy()
    login.side_effect = slow
    renewal = remembered_login.RememberedLogin(path, budget)
    task = asyncio.create_task(renewal.renew())
    await waiting.wait()
    renewal.forget()
    proceed.set()
    assert await task == "manual"
    after = json.loads(path.read_text())
    assert "rememberedLogin" not in after
    assert after["authToken"] == "old-token"
    assert after["stationSn"] == SERIAL
    assert renewal.public_status() == {"remembered": False, "renewal_paused": False}


@pytest.mark.asyncio
async def test_cancelled_process_does_not_replay_login(saved):
    path, cache, budget, login, stations = saved
    waiting = asyncio.Event()
    async def hanging(*args, **kwargs):
        waiting.set()
        await asyncio.Event().wait()
    login.side_effect = hanging
    renewal = remembered_login.RememberedLogin(path, budget)
    task = asyncio.create_task(renewal.renew())
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await remembered_login.RememberedLogin(path, SetupBudget(budget.path)).renew() == "manual"
    assert login.await_count == 1


@pytest.mark.asyncio
async def test_no_optin_and_changed_fingerprint_make_no_login(saved):
    path, cache, budget, login, stations = saved
    for record in ({k: v for k, v in cache.items() if k != "rememberedLogin"},
                   {**cache, "accountFingerprint": "b" * 64}):
        path.write_text(json.dumps(record))
        assert await remembered_login.RememberedLogin(path, budget).renew() == "manual"
    login.assert_not_awaited()


class Worker:
    active = False
    def running(self):
        return self.active
    async def start(self):
        self.active = True
    async def stop(self):
        self.active = False


@pytest.mark.asyncio
async def test_runtime_renews_only_rejected_token_then_validates_before_video(saved):
    path, cache, budget, login, _ = saved
    checker = AsyncMock(side_effect=["rejected", "valid"])
    worker = Worker()
    renewal = remembered_login.RememberedLogin(path, budget)
    runtime = startup_controller.StartupController(path, worker, checker=checker, renewal=renewal)
    await runtime.reconcile()
    assert runtime.state == "video_service_running"
    assert worker.active and checker.await_count == 2
    assert runtime.public_status()["remembered"] is True
    await runtime.close()


@pytest.mark.asyncio
async def test_cloud_check_outage_does_not_attempt_password_login(saved):
    path, cache, budget, login, _ = saved
    worker = Worker()
    worker.active = True
    runtime = startup_controller.StartupController(path, worker,
        checker=AsyncMock(return_value="unavailable"),
        renewal=remembered_login.RememberedLogin(path, budget))
    await runtime.reconcile()
    assert runtime.state == "video_service_running" and worker.active
    login.assert_not_awaited()
    await runtime.close()


@pytest.mark.asyncio
async def test_rejected_replacement_token_disables_further_automatic_login(saved):
    path, cache, budget, login, _ = saved
    runtime = startup_controller.StartupController(path, Worker(),
        checker=AsyncMock(return_value="rejected"),
        renewal=remembered_login.RememberedLogin(path, budget))
    await runtime.reconcile()
    assert runtime.state == "sign_in_required"
    await runtime.reconcile()  # Simulate recheck/restart without a user sign-in.
    assert login.await_count == 1
    assert runtime.public_status()["renewal_paused"] is True
    await runtime.close()

@pytest.mark.asyncio
async def test_forget_http_requires_session_csrf_and_keeps_video_connection(saved, tmp_path, monkeypatch):
    import setup_web
    from aiohttp.test_utils import TestClient, TestServer
    path, cache, budget, login, _ = saved
    renewal = remembered_login.RememberedLogin(path, budget)
    worker = Worker()
    runtime = startup_controller.StartupController(path, worker,
        checker=AsyncMock(return_value="valid"), renewal=renewal)
    monkeypatch.setattr(setup_web, "_peer_ip", lambda request: "172.30.32.2")
    app = setup_web.create_app(auth_path=path, budget_path=budget.path,
        allowed_users={"admin"}, allowed_origins={"http://homeassistant.local"},
        runtime=runtime, setup_budget=budget)
    headers = {"X-Remote-User-Id": "admin", "Origin": "http://homeassistant.local"}
    async with TestClient(TestServer(app)) as http:
        await asyncio.sleep(0)
        assert (await http.post("/api/forget", json={}, headers=headers)).status == 401
        session = await (await http.post("/api/session", json={}, headers=headers)).json()
        protected = {**headers, "X-Setup-Session": session["session"], "X-Setup-CSRF": session["csrf"]}
        assert (await http.post("/api/forget", json={}, headers={
            **protected, "Origin": "https://other.example"})).status == 403
        response = await http.post("/api/forget", json={}, headers=protected)
        assert response.status == 200
        public = await response.json()
        assert public["runtime"]["remembered"] is False
        assert worker.active
        stored = json.loads(path.read_text())
        assert "rememberedLogin" not in stored
        assert stored["authToken"] == cache["authToken"]
    login.assert_not_awaited()


