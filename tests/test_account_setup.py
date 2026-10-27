"""Offline setup contract; never contacts Eufy or opens a media producer."""
import asyncio
import base64
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import account_setup as setup

SERIAL = "T8N00TEST000001"
OTHER = "T8N00TEST000002"
CREDS = {"auth_token": "private-token", "gtoken": "private-gtoken", "user_id": "private-user"}
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nexample").decode()


@pytest.fixture
def cloud(monkeypatch):
    login = AsyncMock(return_value=CREDS.copy())
    stations = AsyncMock(return_value={"data": [
        {"station_sn": SERIAL, "name": "House NVR", "admin_user_id": "private-admin"},
        {"station_sn": OTHER, "name": "Second NVR", "admin_user_id": "other-admin"},
        {"station_sn": "T9000OTHER00000", "name": "Unsupported HomeBase"},
    ]})
    monkeypatch.setattr(setup.ec, "login", login)
    monkeypatch.setattr(setup.ec, "station_list", stations)
    return login, stations


@pytest.mark.asyncio
async def test_success_requires_explicit_selection_and_private_atomic_persistence(cloud, tmp_path):
    wizard = setup.AccountSetup()
    status = await wizard.start("owner@example.com", "private-password", country="AU")
    assert status["state"] == "select_nvr"
    assert len(status["stations"]) == 2
    encoded = json.dumps(status)
    for secret in ["owner@example.com", "private-password", *CREDS.values(), SERIAL, OTHER, "private-admin"]:
        assert secret not in encoded
    assert wizard._password == ""
    destination = tmp_path / "auth.json"
    assert wizard.select_nvr("not-listed", destination)["error"] == "invalid_selection"
    assert not destination.exists()
    assert wizard.select_nvr(status["stations"][1]["id"], destination) == {"state": "complete"}
    saved = json.loads(destination.read_text())
    assert saved["stationSn"] == OTHER
    assert saved["signalingUserId"] == "other-admin"
    assert saved["webCountry"] == "AU"
    assert "private-password" not in destination.read_text()
    assert not wizard._creds and not wizard._email
    cloud[0].assert_awaited_once()
    assert cloud[0].await_args.kwargs["save_captcha"] is False


@pytest.mark.asyncio
async def test_captcha_and_email_code_are_explicit_steps(cloud):
    cloud[0].side_effect = [
        setup.ec.EufyCaptchaRequired("safe", captcha_id="private-captcha-id", image=PNG),
        setup.ec.EufyVerificationRequired("email verification"),
        CREDS.copy(),
    ]
    wizard = setup.AccountSetup()
    status = await wizard.start("owner@example.com", "private-password")
    assert status["state"] == "captcha"
    assert status["captcha_image"].startswith("data:image/png;base64,")
    assert "private-captcha-id" not in json.dumps(status)
    assert (await wizard.answer_captcha("answer"))["state"] == "email_code"
    assert cloud[0].await_args.kwargs["captcha_id"] == "private-captcha-id"
    assert (await wizard.verify_email("bad"))["error"] == "invalid_code"
    assert cloud[0].await_count == 2
    assert (await wizard.verify_email("123456"))["state"] == "select_nvr"
    assert cloud[0].await_args.kwargs["verification_code"] == "123456"
    assert not wizard._password and not wizard._captcha_id


@pytest.mark.parametrize("image", ["https://example.com/secret", "data:image/svg+xml;base64,PHN2Zz4=", "!", "A" * 350001], ids=["remote", "svg", "invalid", "oversized"])
def test_captcha_disallows_remote_active_invalid_or_oversized_content(image):
    assert setup.captcha_image(image) is None


@pytest.mark.asyncio
async def test_bad_captcha_fails_closed_and_drops_credentials(cloud):
    cloud[0].side_effect = setup.ec.EufyCaptchaRequired("safe", captcha_id="id", image="not-raster")
    wizard = setup.AccountSetup()
    assert (await wizard.start("owner@example.com", "password"))["error"] == "captcha_unavailable"
    assert not wizard._password


@pytest.mark.asyncio
async def test_provider_rate_limit_survives_cancel_and_restart(cloud):
    clock = [100.0]
    cloud[0].side_effect = setup.ec.EufyLoginRateLimited("private-vendor-error")
    wizard = setup.AccountSetup(clock=lambda: clock[0])
    assert (await wizard.start("owner@example.com", "password"))["state"] == "rate_limited"
    wizard.cancel()
    assert "retry_after_seconds" in await wizard.start("owner@example.com", "password")
    assert cloud[0].await_count == 1
    assert not wizard._password


@pytest.mark.asyncio
async def test_local_rate_budget_cannot_be_reset_by_restarting_flow(cloud):
    cloud[0].side_effect = setup.ec.EufyLoginRejected("private-vendor-message")
    wizard = setup.AccountSetup(max_attempts=2)
    for _ in range(2):
        assert (await wizard.start("owner@example.com", "password"))["error"] == "login_not_accepted"
    status = await wizard.start("owner@example.com", "password")
    assert status["state"] == "rate_limited" and cloud[0].await_count == 2
    assert "private" not in json.dumps(status)


@pytest.mark.asyncio
async def test_expired_selection_cannot_overwrite_existing_auth(cloud, tmp_path):
    clock = [100.0]
    wizard = setup.AccountSetup(clock=lambda: clock[0], ttl=10)
    status = await wizard.start("owner@example.com", "password")
    existing = tmp_path / "auth.json"
    existing.write_text("existing-private-cache")
    clock[0] += 11
    assert wizard.select_nvr(status["stations"][0]["id"], existing) == {"state": "expired"}
    assert existing.read_text() == "existing-private-cache"
    assert not wizard._creds and not wizard._password


@pytest.mark.asyncio
async def test_cancellation_discards_late_login_result(cloud):
    entered, release = asyncio.Event(), asyncio.Event()

    async def login(*args, **kwargs):
        entered.set()
        await release.wait()
        return CREDS.copy()

    cloud[0].side_effect = login
    wizard = setup.AccountSetup()
    task = asyncio.create_task(wizard.start("owner@example.com", "password"))
    await entered.wait()
    assert await wizard.start("other@example.com", "other") == {"state": "busy"}
    wizard.cancel()
    release.set()
    assert await task == {"state": "cancelled"}
    cloud[1].assert_not_awaited()
    assert not wizard._creds and not wizard._password


@pytest.mark.asyncio
async def test_task_cancellation_clears_sensitive_state(cloud):
    entered = asyncio.Event()

    async def login(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    cloud[0].side_effect = login
    wizard = setup.AccountSetup()
    task = asyncio.create_task(wizard.start("owner@example.com", "password"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert wizard.public_status() == {"state": "cancelled"}
    assert not wizard._password


@pytest.mark.asyncio
async def test_other_station_admin_is_not_used_as_fallback(cloud, tmp_path):
    cloud[1].return_value["data"][0].pop("admin_user_id")
    wizard = setup.AccountSetup()
    status = await wizard.start("owner@example.com", "password")
    destination = tmp_path / "auth.json"
    assert wizard.select_nvr(status["stations"][0]["id"], destination)["error"] == "nvr_owner_identity_unavailable"
    assert not destination.exists()


@pytest.mark.asyncio
async def test_errors_never_echo_raw_vendor_messages(cloud):
    cloud[1].side_effect = RuntimeError("https://user:private-password@example.com/private-token")
    wizard = setup.AccountSetup()
    status = await wizard.start("owner@example.com", "password")
    assert status == {"state": "credentials", "error": "connection_failed"}
    assert not wizard._creds


@pytest.mark.asyncio
async def test_invalid_region_type_never_contacts_vendor(cloud):
    assert (await setup.AccountSetup().start("a@b", "password", region=[]))["error"] == "invalid_input"
    cloud[0].assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_failure_preserves_retry_without_relogin(cloud, tmp_path, monkeypatch):
    wizard = setup.AccountSetup()
    status = await wizard.start("owner@example.com", "password")
    def fail(*args):
        raise OSError("private file path")
    monkeypatch.setattr(setup, "_write_private_json", fail)
    result = wizard.select_nvr(status["stations"][0]["id"], tmp_path / "auth.json")
    assert result["error"] == "storage_failed"
    assert "private" not in json.dumps(result)
    assert cloud[0].await_count == 1
