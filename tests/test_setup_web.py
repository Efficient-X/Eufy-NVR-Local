"""Local HTTP tests with a fake Supervisor peer and mocked Eufy. No real accounts."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import account_setup
import setup_web
from setup_budget import SetupBudget

ORIGIN = "http://homeassistant.local"
HEADERS = {"X-Remote-User-Id": "admin1", "Origin": ORIGIN,
           "Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"}
CREDS = {"auth_token": "secret-token", "user_id": "private-user"}
SERIAL = "T8N00TEST000001"


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_web, "_peer_ip", lambda request: "172.30.32.2")
    monkeypatch.setattr(account_setup.ec, "login", AsyncMock(return_value=CREDS.copy()))
    monkeypatch.setattr(account_setup.ec, "station_list", AsyncMock(return_value={
        "data": [{"station_sn": SERIAL, "name": "<img src=x onerror=alert(1)>",
                  "admin_user_id": "private-admin"}],
    }))
    app = setup_web.create_app(auth_path=tmp_path / "auth.json",
        budget_path=tmp_path / "budget.json", allowed_users={"admin1", "admin2"},
        allowed_origins={ORIGIN})
    async with TestClient(TestServer(app)) as http:
        yield http, app, tmp_path


async def bootstrap(http, user="admin1"):
    headers = {**HEADERS, "X-Remote-User-Id": user}
    response = await http.post("/api/session", json={}, headers=headers)
    assert response.status == 200
    body = await response.json()
    return {**headers, "X-Setup-Session": body["session"], "X-Setup-CSRF": body["csrf"]}


async def start(http, headers):
    return await http.post("/api/start", json={
        "email": "owner@example.com", "password": "private-password",
        "country": "AU", "region": "US",
    }, headers=headers)


@pytest.mark.asyncio
async def test_actual_lan_peer_is_denied_even_with_forged_supervisor_headers(client, monkeypatch):
    http, app, _ = client
    monkeypatch.setattr(setup_web, "_peer_ip", lambda request: "192.168.50.50")
    response = await http.get("/", headers={**HEADERS, "X-Forwarded-For": "172.30.32.2"})
    assert response.status == 403
    assert "no-store" in response.headers["Cache-Control"]
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"X-Remote-User-Id": "guest"},
    {"X-Remote-User-Id": ""},
    {"Sec-Fetch-Site": "cross-site"},
    {"Content-Type": "text/plain"},
])
async def test_invalid_origin_user_or_request_is_denied(client, bad):
    http, _, _ = client
    response = await http.post("/api/session", data="{}", headers={**HEADERS, **bad})
    assert response.status == 403
    account_setup.ec.login.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_origin_is_denied(client):
    http, _, _ = client
    headers = {k: v for k, v in HEADERS.items() if k != "Origin"}
    assert (await http.post("/api/session", data="{}", headers=headers)).status == 403


@pytest.mark.asyncio
async def test_csrf_session_user_binding_and_strict_payload(client):
    http, _, _ = client
    headers = await bootstrap(http)
    for bad in [
        {"X-Setup-CSRF": "wrong"}, {"X-Setup-Session": "wrong"},
        {"X-Remote-User-Id": "admin2"},
    ]:
        assert (await start(http, {**headers, **bad})).status == 401
    response = await http.post("/api/start", json={"password": "private-password"}, headers=headers)
    assert response.status == 400
    response = await http.post("/api/start", data='{"password":"' + "x" * 9000 + '"}', headers=headers)
    assert response.status == 413
    account_setup.ec.login.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_flow_saves_only_after_selection_and_serves_protected_static_files(client):
    http, app, directory = client
    page = await http.get("/", headers=HEADERS)
    assert page.status == 200
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    assert "server group" in (await page.text()).lower()
    for asset in ("setup.js", "setup.css"):
        assert (await http.get("/" + asset, headers=HEADERS)).status == 200
    headers = await bootstrap(http)
    response = await start(http, headers)
    status = await response.json()
    assert status["state"] == "select_nvr"
    assert not (directory / "auth.json").exists()
    for private in ["private-password", "secret-token", SERIAL, "private-admin", "owner@example.com"]:
        assert private not in json.dumps(status)
    assert "station_sn" not in status["stations"][0]
    selected = await http.post("/api/select", json={"selection": status["stations"][0]["id"]}, headers=headers)
    assert await selected.json() == {"state": "complete"}
    assert json.loads((directory / "auth.json").read_text())["stationSn"] == SERIAL
    assert "private-password" not in (directory / "auth.json").read_text()
    assert "owner@example.com" not in (directory / "budget.json").read_text()


@pytest.mark.asyncio
async def test_country_not_in_list_cannot_reach_eufy(client):
    http, _, _ = client
    headers = await bootstrap(http)
    response = await http.post("/api/start", json={
        "email": "owner@example.com", "password": "private-password", "country": "EU", "region": "EU",
    }, headers=headers)
    assert (await response.json())["error"] == "invalid_input"
    account_setup.ec.login.assert_not_awaited()


@pytest.mark.asyncio
async def test_two_users_cannot_overwrite_each_others_setup(client):
    http, _, _ = client
    first, second = await bootstrap(http), await bootstrap(http, "admin2")
    assert (await start(http, first)).status == 200
    assert (await start(http, second)).status == 409
    assert (await http.post("/api/cancel", json={}, headers=first)).status == 200
    assert (await start(http, second)).status == 200


@pytest.mark.asyncio
async def test_expiry_cancel_reopen_and_shutdown_clear_sessions(client):
    http, app, _ = client
    headers = await bootstrap(http)
    await start(http, headers)
    manager = app[setup_web.MANAGER]
    wizard = manager.sessions["admin1"]["wizard"]
    manager.sessions["admin1"]["deadline"] = 0
    manager.expire()
    assert not wizard._creds and not wizard._password
    assert (await http.get("/api/status", headers=headers)).status == 401
    fresh = await bootstrap(http)
    assert fresh["X-Setup-Session"] != headers["X-Setup-Session"]
    await http.post("/api/cancel", json={}, headers=fresh)
    reopened = await bootstrap(http)
    assert reopened["X-Setup-Session"] != fresh["X-Setup-Session"]
    assert (await (await http.get("/api/status", headers=reopened)).json())["state"] == "credentials"
    manager.close()
    assert not manager.sessions


@pytest.mark.asyncio
async def test_vendor_exceptions_are_not_in_http_response(client, monkeypatch):
    http, _, _ = client
    monkeypatch.setattr(account_setup.ec, "login", AsyncMock(side_effect=RuntimeError("private-password secret-token")))
    response = await start(http, await bootstrap(http))
    assert await response.json() == {"state": "credentials", "error": "connection_failed"}


def test_budget_survives_new_sessions_and_service_restart(tmp_path):
    path = tmp_path / "budget.json"
    now = [100.0]
    first = SetupBudget(path, clock=lambda: now[0], limit=2)
    assert first.reserve() == first.reserve() == 0
    second = SetupBudget(path, clock=lambda: now[0], limit=2)
    assert second.reserve() == 60
    now[0] += 61
    assert second.reserve() == 0
    second.block(86400)
    third = SetupBudget(path, clock=lambda: now[0], limit=2)
    assert third.reserve() == 86400


@pytest.mark.parametrize("content", ['{', '{"version":1,"attempts":[NaN],"blocked_until":0}', '[]'])
def test_corrupt_protection_fails_closed(tmp_path, content):
    path = tmp_path / "budget.json"
    path.write_text(content)
    with pytest.raises(OSError, match="unavailable"):
        SetupBudget(path).reserve()


@pytest.mark.asyncio
async def test_budget_failure_stops_login_before_vendor_call(client, monkeypatch):
    http, app, _ = client
    app[setup_web.MANAGER].budget.failed = True
    response = await start(http, await bootstrap(http))
    assert (await response.json())["error"] == "protection_unavailable"
    account_setup.ec.login.assert_not_awaited()


@pytest.mark.asyncio
async def test_vendor_cooldown_is_saved_for_other_sessions(client, monkeypatch):
    http, app, directory = client
    monkeypatch.setattr(account_setup.ec, "login", AsyncMock(side_effect=account_setup.ec.EufyLoginRateLimited("private")))
    response = await start(http, await bootstrap(http))
    assert (await response.json())["state"] == "rate_limited"
    assert SetupBudget(directory / "budget.json").reserve() > 86000
    response = await start(http, await bootstrap(http, "admin2"))
    assert (await response.json())["state"] == "rate_limited"
    assert account_setup.ec.login.await_count == 1


def test_factory_requires_explicit_deployment_trust(tmp_path):
    with pytest.raises(ValueError):
        setup_web.create_app(auth_path=tmp_path / "auth", budget_path=tmp_path / "budget",
                             allowed_users=set(), allowed_origins={ORIGIN})
    with pytest.raises(ValueError):
        setup_web.create_app(auth_path=tmp_path / "auth", budget_path=tmp_path / "budget",
                             allowed_users={"admin1"}, allowed_origins=set())


@pytest.mark.asyncio
async def test_late_vendor_lockout_after_cancel_still_persists(client, monkeypatch):
    http, app, directory = client
    entered, release = asyncio.Event(), asyncio.Event()
    async def refuse(*args, **kwargs):
        entered.set()
        await release.wait()
        raise account_setup.ec.EufyLoginRateLimited("not printed")
    monkeypatch.setattr(account_setup.ec, "login", refuse)
    headers = await bootstrap(http)
    task = asyncio.create_task(start(http, headers))
    await entered.wait()
    await http.post("/api/cancel", json={}, headers=headers)
    release.set()
    response = await task
    assert (await response.json())["state"] == "cancelled"
    assert SetupBudget(directory / "budget.json").reserve() > 86000


@pytest.mark.asyncio
async def test_http_email_challenge_and_code(client, monkeypatch):
    http, _, directory = client
    login = AsyncMock(side_effect=[
        account_setup.ec.EufyVerificationRequired("verification required"),
        CREDS.copy(),
    ])
    monkeypatch.setattr(account_setup.ec, "login", login)
    headers = await bootstrap(http)
    assert (await (await start(http, headers)).json())["state"] == "email_code"
    bad = await http.post("/api/verify", json={"code": "123"}, headers=headers)
    assert (await bad.json())["error"] == "invalid_code"
    assert login.await_count == 1
    good = await http.post("/api/verify", json={"code": "123456"}, headers=headers)
    assert (await good.json())["state"] == "select_nvr"
    assert not (directory / "auth.json").exists()


def test_selected_nvr_conflicting_owners_fail_closed():
    raw = {"data": [{"station_sn": SERIAL, "admin_user_id": "one",
                    "permissions": {"adminUserId": "two"}}]}
    assert account_setup.selected_admin_id(raw, SERIAL) == ""


def test_nested_other_nvr_identity_is_not_a_fallback():
    raw = {"station_sn": SERIAL, "children": [
        {"station_sn": "T8N00OTHER00000", "admin_user_id": "other"}]}
    assert account_setup.selected_admin_id(raw, SERIAL) == ""


@pytest.mark.asyncio
async def test_slow_request_cannot_start_after_another_user_claims_setup(client):
    http, app, _ = client
    first, second = await bootstrap(http), await bootstrap(http, "admin2")
    sent, release = asyncio.Event(), asyncio.Event()
    body = json.dumps({"email": "owner@example.com", "password": "private-password",
                       "country": "AU", "region": "US"}).encode()
    async def chunks():
        yield body[:10]
        sent.set()
        await release.wait()
        yield body[10:]
    slow = asyncio.create_task(http.post("/api/start", data=chunks(), headers=first))
    await sent.wait()
    assert (await start(http, second)).status == 200
    release.set()
    assert (await slow).status == 409
    assert account_setup.ec.login.await_count == 1


@pytest.mark.asyncio
async def test_slow_request_cannot_revive_expired_session(client):
    http, app, _ = client
    headers = await bootstrap(http)
    sent, release = asyncio.Event(), asyncio.Event()
    body = json.dumps({"email": "owner@example.com", "password": "private-password",
                       "country": "AU", "region": "US"}).encode()
    async def chunks():
        yield body[:10]
        sent.set()
        await release.wait()
        yield body[10:]
    slow = asyncio.create_task(http.post("/api/start", data=chunks(), headers=headers))
    await sent.wait()
    manager = app[setup_web.MANAGER]
    manager.sessions["admin1"]["deadline"] = 0
    manager.expire()
    release.set()
    assert (await slow).status == 401
    account_setup.ec.login.assert_not_awaited()

