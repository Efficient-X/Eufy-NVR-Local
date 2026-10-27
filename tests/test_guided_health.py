"""Offline liveness checks: no vendor network, video, credentials or HA writes."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import guided_launcher
import setup_web

NONCE = "a" * 64


class Runtime:
    def __init__(self, state):
        self.state = state
        self.reconcile = AsyncMock()
        self.tick = AsyncMock()
        self.close = AsyncMock()

    def public_status(self):
        return {"state": self.state}

    def can_setup(self):
        return self.state == "setup_required"


def make_app(tmp_path, runtime=None, trust=None):
    return setup_web.create_app(
        auth_path=tmp_path / "auth.json", budget_path=tmp_path / "budget.json",
        allowed_users={"admin1"}, allowed_origins={"http://homeassistant.local"},
        health_nonce=NONCE, runtime=runtime, trust=trust,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    "setup_required", "sign_in_required", "connection_unavailable",
    "checking_account", "starting_video", "video_service_running", "service_error",
    "local_video_setup_required",
])
async def test_waiting_and_vendor_or_worker_failure_are_not_app_crashes(tmp_path, state):
    runtime = Runtime(state)
    async with TestClient(TestServer(make_app(tmp_path, runtime))) as http:
        response = await http.get("/healthz", headers={"X-Health-Nonce": NONCE})
        assert response.status == 200
        assert await response.json() == {"status": "ok"}
        assert response.headers["Cache-Control"] == "no-store"
        runtime.tick.assert_not_awaited()
    runtime.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_does_not_depend_on_ha_admin_lookup(tmp_path):
    trust = type("Trust", (), {"current": AsyncMock(side_effect=RuntimeError("private"))})()
    async with TestClient(TestServer(make_app(tmp_path, trust=trust))) as http:
        assert (await http.get("/healthz", headers={"X-Health-Nonce": NONCE})).status == 200
        trust.current.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("peer,nonce,method", [
    ("192.168.50.50", NONCE, "get"),
    ("172.30.32.2", NONCE, "get"),
    ("127.0.0.1", "b" * 64, "get"),
    ("127.0.0.1", "", "get"),
    ("127.0.0.1", NONCE, "post"),
    ("127.0.0.1", NONCE, "head"),
])
async def test_health_cannot_be_spoofed_or_used_by_browser(tmp_path, monkeypatch, peer, nonce, method):
    monkeypatch.setattr(setup_web, "_peer_ip", lambda request: peer)
    async with TestClient(TestServer(make_app(tmp_path))) as http:
        response = await getattr(http, method)("/healthz", headers={
            "X-Health-Nonce": nonce, "X-Forwarded-For": "127.0.0.1",
            "X-Remote-User-Id": "admin1",
        })
        assert response.status == 403


@pytest.mark.asyncio
async def test_health_nonce_does_not_grant_access_to_account_api(tmp_path):
    async with TestClient(TestServer(make_app(tmp_path))) as http:
        assert (await http.get("/", headers={"X-Health-Nonce": NONCE})).status == 403
        assert (await http.post("/api/session", json={},
                               headers={"X-Health-Nonce": NONCE})).status == 403


@pytest.mark.asyncio
async def test_failed_watch_task_is_unhealthy_and_cleanup_still_runs(tmp_path):
    runtime = Runtime("checking_account")
    runtime.reconcile.side_effect = RuntimeError("private details must not escape")
    app = make_app(tmp_path, runtime)
    async with TestClient(TestServer(app)) as http:
        await asyncio.sleep(0)
        response = await http.get("/healthz", headers={"X-Health-Nonce": NONCE})
        assert response.status == 503
        assert await response.json() == {"status": "unhealthy"}
        assert app[setup_web.HEALTH].tasks["watch"].done()
    runtime.close.assert_awaited_once()
    assert app[setup_web.MANAGER].sessions == {}


@pytest.mark.asyncio
async def test_watch_and_expiry_tasks_have_bounded_liveness(tmp_path):
    app = make_app(tmp_path, Runtime("starting_video"))
    async with TestClient(TestServer(app)) as http:
        health = app[setup_web.HEALTH]
        await asyncio.sleep(0)
        health.beats["watch"] -= 240
        assert (await http.get("/healthz", headers={"X-Health-Nonce": NONCE})).status == 200
        health.beats["watch"] -= 61
        assert (await http.get("/healthz", headers={"X-Health-Nonce": NONCE})).status == 503
        health.beat("watch")
        health.beats["sweep"] -= 31
        assert (await http.get("/healthz", headers={"X-Health-Nonce": NONCE})).status == 503
        health.beat("sweep")
        assert (await http.get("/healthz", headers={"X-Health-Nonce": NONCE})).status == 200
    assert not health.healthy()


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [
    None, [], {}, {"port": True, "nonce": NONCE}, {"port": 80, "nonce": NONCE},
    {"port": 65501, "nonce": NONCE}, {"port": 62001, "nonce": "bad"},
    {"port": 62001, "nonce": NONCE, "url": "https://external.invalid"},
])
async def test_probe_rejects_invalid_metadata_without_network(tmp_path, monkeypatch, saved):
    path = tmp_path / "health.json"
    if saved is not None:
        path.write_text(json.dumps(saved))
    def forbidden(*args, **kwargs):
        pytest.fail("invalid metadata must not make a network request")
    monkeypatch.setattr(guided_launcher, "ClientSession", forbidden)
    assert not await guided_launcher.probe_health(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,chunks,good", [
    (200, [b'{"status":', b'"ok"}'], True),
    (200, [b'{"status":"ok","token":"private"}'], False),
    (200, [b"x" * 1025], False),
    (200, [b"not json"], False),
    (302, [], False), (503, [], False),
])
async def test_probe_is_fixed_loopback_bounded_and_non_redirecting(tmp_path, monkeypatch, status, chunks, good):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({"port": 62001, "nonce": NONCE}))
    class Response:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    response = Response()
    response.status = status
    class Content:
        async def iter_chunked(self, size):
            for chunk in chunks:
                yield chunk
    response.content = Content()
    class Session(Response):
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["timeout"].total == 3
        def get(self, url, **kwargs):
            assert url == "http://127.0.0.1:62001/healthz"
            assert kwargs == {"headers": {"X-Health-Nonce": NONCE}, "allow_redirects": False}
            return response
    monkeypatch.setattr(guided_launcher, "ClientSession", Session)
    assert await guided_launcher.probe_health(path) is good


@pytest.mark.asyncio
async def test_probe_network_error_is_quiet(tmp_path, monkeypatch, capsys):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({"port": 62001, "nonce": NONCE}))
    def broken(**kwargs):
        raise OSError("private server information")
    monkeypatch.setattr(guided_launcher, "ClientSession", broken)
    assert not await guided_launcher.probe_health(path)
    assert capsys.readouterr() == ("", "")

