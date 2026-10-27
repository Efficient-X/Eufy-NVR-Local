"""Synthetic clock/expiry/renewal tests; no vendor request or household change."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from test_remembered_login import saved, Worker, CREDS
import account_setup
import eufy_cloud
import remembered_login
import startup_controller
from token_expiry import parse_expiry, next_check_delay

NOW = 1900000000


@pytest.mark.parametrize("value,expected", [
    (NOW, NOW), (str(NOW), NOW), (None, None), (True, None), (NOW * 1000, None),
    (float(NOW), None), ("3600", None), ("1900000000.0", None),
    ("١٩٠٠٠٠٠٠٠٠", None), (0, None), (4102444800, None), ({}, None),
])
def test_only_unambiguous_unix_seconds_are_used(value, expected):
    assert parse_expiry(value) == expected


@pytest.mark.parametrize("seconds,remembered,expected", [
    (7200, True, 6900), (600, True, 300), (120, True, 60),
    (600, False, 600), (-1, True, 900), (86400, True, 21600),
])
def test_scheduling_is_bounded(seconds, remembered, expected):
    assert next_check_delay({"authTokenExpiresAt": NOW + seconds},
                            NOW, 21600, remembered) == expected
    assert next_check_delay({}, NOW, 21600, remembered) == 900


@pytest.mark.asyncio
@pytest.mark.parametrize("value,expected", [(NOW + 600, NOW + 600),
                                         (str(NOW), NOW), (NOW * 1000, None)])
async def test_login_reply_preserves_only_valid_optional_expiry(value, expected):
    reply = {"code": 0, "data": {**CREDS, "token_expires_at": value}}
    with patch.object(eufy_cloud, "encrypted_post", AsyncMock(return_value=reply)):
        result = await eufy_cloud.login("synthetic@example.com", "test", key_obj=object())
    assert result.get("token_expires_at") == expected


@pytest.mark.asyncio
async def test_wizard_saves_vendor_expiry_without_exposing_it(saved):
    path, _, _, login, _ = saved
    login.return_value = {**CREDS, "token_expires_at": NOW + 600}
    wizard = account_setup.AccountSetup()
    status = await wizard.start("owner@example.com", "test", country="AU")
    wizard.select_nvr(status["stations"][0]["id"], path)
    assert json.loads(path.read_text())["authTokenExpiresAt"] == NOW + 600


@pytest.mark.asyncio
async def test_proactive_renewal_keeps_worker_until_new_token_is_valid(saved):
    path, cache, budget, login, _ = saved
    cache["authTokenExpiresAt"] = NOW + 240
    path.write_text(json.dumps(cache))
    events = []
    class ObservedWorker(Worker):
        active = True
        async def stop(self):
            events.append("stop")
            await super().stop()
        async def start(self):
            assert json.loads(path.read_text())["authToken"] == "new-token"
            events.append("start")
            await super().start()
    worker = ObservedWorker()
    async def signing_in(*args, **kwargs):
        assert worker.active
        events.append("login")
        return {**CREDS, "token_expires_at": NOW + 86400}
    async def check(path):
        assert worker.active
        events.append("check")
        return "valid"
    login.side_effect = signing_in
    renewal = remembered_login.RememberedLogin(path, budget, wall_clock=lambda: NOW)
    runtime = startup_controller.StartupController(path, worker, checker=check,
        renewal=renewal, wall_clock=lambda: NOW, clock=lambda: 100)
    await runtime.reconcile()
    assert events == ["check", "login", "check", "stop", "start"]
    assert runtime.state == "video_service_running"
    assert runtime._next_check == 21700
    assert json.loads(path.read_text())["authTokenExpiresAt"] == NOW + 86400


@pytest.mark.asyncio
async def test_challenge_before_expiry_keeps_existing_worker_and_pauses(saved):
    path, cache, budget, login, _ = saved
    cache["authTokenExpiresAt"] = NOW + 240
    path.write_text(json.dumps(cache))
    login.side_effect = eufy_cloud.EufyCaptchaRequired("synthetic")
    worker = Worker()
    worker.active = True
    worker.stop = AsyncMock(side_effect=AssertionError("Do not stop valid existing video"))
    renewal = remembered_login.RememberedLogin(path, budget, wall_clock=lambda: NOW)
    runtime = startup_controller.StartupController(path, worker,
        checker=AsyncMock(return_value="valid"), renewal=renewal,
        wall_clock=lambda: NOW, clock=lambda: 100)
    await runtime.reconcile()
    assert worker.active and runtime.state == "video_service_running"
    assert runtime.public_status()["renewal_paused"] is True
    assert runtime._next_check == 340
    await runtime.reconcile()
    assert login.await_count == 1


@pytest.mark.asyncio
async def test_renewed_token_check_outage_restarts_only_after_later_validation(saved):
    path, cache, budget, login, _ = saved
    cache["authTokenExpiresAt"] = NOW + 240
    path.write_text(json.dumps(cache))
    worker = Worker()
    worker.active = True
    worker.stop = AsyncMock(wraps=worker.stop)
    renewal = remembered_login.RememberedLogin(path, budget, wall_clock=lambda: NOW)
    runtime = startup_controller.StartupController(path, worker, renewal=renewal,
        checker=AsyncMock(side_effect=["valid", "unavailable", "valid"]),
        wall_clock=lambda: NOW)
    await runtime.reconcile()
    worker.stop.assert_not_awaited()
    assert worker.active and runtime._reload_after_renewal
    await runtime.reconcile()
    worker.stop.assert_awaited_once()
    assert worker.active and not runtime._reload_after_renewal
    assert login.await_count == 1


@pytest.mark.asyncio
async def test_success_without_expiry_clears_old_date_and_cooldown_survives_restart(saved):
    path, cache, budget, login, _ = saved
    cache["authTokenExpiresAt"] = NOW + 240
    path.write_text(json.dumps(cache))
    first = remembered_login.RememberedLogin(path, budget, wall_clock=lambda: NOW)
    assert await first.renew() == "renewed"
    assert "authTokenExpiresAt" not in json.loads(path.read_text())
    again = remembered_login.RememberedLogin(path, budget, wall_clock=lambda: NOW + 1)
    assert not again.can_renew()
    assert await again.renew() == "manual"
    assert login.await_count == 1
    assert again.public_status()["renewal_paused"]
