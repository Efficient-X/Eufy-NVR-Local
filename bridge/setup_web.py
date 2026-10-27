"""Ingress-only setup application factory; intentionally not started by run.sh.

The production launcher must supply verified administrator IDs and trusted HA
origins. No CLI, public listener, or permissive development authentication mode.
Run a single process with HTTP access logging disabled.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from account_setup import AccountSetup
from setup_budget import SetupBudget
from ha_setup_trust import TrustUnavailable

STATIC = Path(__file__).with_name("setup_ui")
COUNTRIES = {
    "AU", "NZ", "US", "CA", "GB", "IE", "DE", "FR", "NL", "ES", "IT",
    "AT", "BE", "CH", "SE", "DK", "NO", "FI", "PL", "PT", "JP", "SG",
}
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; "
        "base-uri 'none'; form-action 'none'; object-src 'none'",
}
MANAGER = web.AppKey("setup_manager", object)
HEALTH = web.AppKey("setup_health", object)


class ServiceHealth:
    """Liveness of our own control tasks, not Eufy or camera availability."""
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.tasks = {}
        self.beats = {}
        self.closing = False

    def beat(self, name):
        self.beats[name] = self.clock()

    def healthy(self):
        if self.closing or not self.tasks:
            return False
        for name, task in self.tasks.items():
            if task.done():
                # Retrieve failures so private exception text is not logged by asyncio.
                if not task.cancelled():
                    task.exception()
                return False
            limit = 300 if name == "watch" else 30
            if self.clock() - self.beats.get(name, float("-inf")) > limit:
                return False
        return True


def _peer_ip(request):
    peer = request.transport.get_extra_info("peername") if request.transport else None
    return peer[0] if peer else None


class SetupSessions:
    def __init__(self, auth_path, budget, *, clock=time.monotonic, ttl=600):
        self.auth_path, self.budget = Path(auth_path), budget
        self.clock, self.ttl = clock, ttl
        self.sessions = {}

    def expire(self):
        for user, session in list(self.sessions.items()):
            if self.clock() >= session["deadline"]:
                session["wizard"].cancel()
                del self.sessions[user]

    def open(self, user):
        self.expire()
        previous = self.sessions.get(user)
        if previous and previous["wizard"].state in {"cancelled", "expired"}:
            previous["wizard"].cancel()
            del self.sessions[user]
        if user not in self.sessions:
            if len(self.sessions) >= 8:
                raise web.HTTPServiceUnavailable()
            self.sessions[user] = {
                "id": secrets.token_urlsafe(32), "csrf": secrets.token_urlsafe(32),
                "deadline": self.clock() + self.ttl,
                "wizard": AccountSetup(clock=self.clock, ttl=self.ttl, budget=self.budget),
            }
        return self.sessions[user]

    def get(self, user, session_id, csrf):
        self.expire()
        session = self.sessions.get(user)
        if (not session or not hmac.compare_digest(session["id"], session_id)
                or not hmac.compare_digest(session["csrf"], csrf)):
            raise web.HTTPUnauthorized()
        return session

    def other_active(self, user):
        return any(owner != user and s["wizard"].state in
                   {"connecting", "captcha", "email_code", "select_nvr"}
                   for owner, s in self.sessions.items())

    def close(self):
        for session in self.sessions.values():
            session["wizard"].cancel()
        self.sessions.clear()


def create_app(*, auth_path, budget_path, allowed_users=(), allowed_origins=(),
               runtime=None, trust=None, health_nonce=None, setup_budget=None):
    """Trusted deployment configuration only; never values supplied by the page."""
    users, origins = frozenset(allowed_users), frozenset(allowed_origins)
    if trust is None and (not users or any(
            not isinstance(u, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", u)
            for u in users)):
        raise ValueError("Verified administrator IDs are required")
    if not origins and trust is None:
        raise ValueError("Trusted Home Assistant origins are required")
    for origin in origins:
        parsed = urlsplit(origin)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path
                or parsed.query or parsed.fragment):
            raise ValueError("Use an exact Home Assistant origin")
    manager = SetupSessions(auth_path, setup_budget if setup_budget is not None else SetupBudget(budget_path))
    health = ServiceHealth()
    if health_nonce is not None and not re.fullmatch(r"[0-9a-f]{64}", health_nonce):
        raise ValueError("Invalid health nonce")

    def snapshot(session):
        result = session["wizard"].public_status()
        if runtime is not None:
            result["runtime"] = runtime.public_status()
            if result["state"] == "complete" and result["runtime"]["can_setup"]:
                result["state"] = "credentials"
        return result

    @web.middleware
    async def guard(request, handler):
        try:
            if request.path == "/healthz" and health_nonce is not None:
                # Only the in-container probe, never an ingress/browser/LAN request.
                # Ignore forwarded headers; the nonce changes on every launch.
                if (request.method != "GET" or _peer_ip(request) not in {"127.0.0.1", "::1"}
                        or not hmac.compare_digest(
                            request.headers.get("X-Health-Nonce", ""), health_nonce)):
                    raise web.HTTPForbidden()
                response = await handler(request)
                response.headers.update(SECURITY_HEADERS)
                return response
            # Forwarded headers cannot bypass this actual TCP peer check.
            if (_peer_ip(request) != "172.30.32.2"
                    or request.headers.get("Sec-Fetch-Site") == "cross-site"):
                raise web.HTTPForbidden()
            current_users, current_origins = await trust.current() if trust else (users, origins)
            if request.headers.get("X-Remote-User-Id") not in current_users:
                raise web.HTTPForbidden()
            if request.method == "POST":
                if (request.headers.get("Origin") not in current_origins
                        or request.content_type != "application/json"):
                    raise web.HTTPForbidden()
            response = await handler(request)
        except TrustUnavailable as exc:
            response = web.json_response({"error": exc.code}, status=503)
        except web.HTTPException as exc:
            response = web.json_response({"error": {
                401: "session_expired", 403: "access_denied", 409: "other_setup_active",
                413: "request_too_large", 503: "setup_busy",
            }.get(exc.status, "invalid_request")}, status=exc.status)
        except Exception:
            # Do not print exceptions containing credential bodies, paths or URLs.
            response = web.json_response({"error": "setup_unavailable"}, status=500)
        response.headers.update(SECURITY_HEADERS)
        return response

    app = web.Application(middlewares=[guard], client_max_size=8192)
    app[MANAGER] = manager
    app[HEALTH] = health

    async def page(request):
        files = {"": ("index.html", "text/html"), "setup.js": ("setup.js", "text/javascript"),
                 "setup.css": ("setup.css", "text/css")}
        name, mime = files[request.match_info.get("asset", "")]
        return web.Response(body=(STATIC / name).read_bytes(), content_type=mime)

    async def payload(request, keys, optional=()):
        try:
            async with asyncio.timeout(10):
                value = await request.json()
        except TimeoutError:
            raise web.HTTPRequestTimeout() from None
        except (ValueError, UnicodeError):
            raise web.HTTPBadRequest() from None
        if (not isinstance(value, dict) or not set(keys) <= set(value)
                or not set(value) <= set(keys) | set(optional)):
            raise web.HTTPBadRequest()
        return value

    async def bootstrap(request):
        await payload(request, [])
        session = manager.open(request.headers["X-Remote-User-Id"])
        return web.json_response({
            "session": session["id"], "csrf": session["csrf"],
            "status": snapshot(session),
        })

    async def action(request):
        user = request.headers["X-Remote-User-Id"]
        session = manager.get(user, request.headers.get("X-Setup-Session", ""),
                              request.headers.get("X-Setup-CSRF", ""))
        wizard = session["wizard"]
        operation = request.match_info["operation"]
        async def read(keys, optional=()):
            data = await payload(request, keys, optional)
            # Reading a slow body yields control. Recheck expiry and ownership
            # afterward, immediately before starting a login or saving a cache.
            manager.get(user, request.headers.get("X-Setup-Session", ""),
                        request.headers.get("X-Setup-CSRF", ""))
            if operation != "cancel" and manager.other_active(user):
                raise web.HTTPConflict()
            if (runtime is not None and operation not in {"cancel", "retry", "forget"}
                    and not runtime.can_setup()):
                raise web.HTTPConflict()
            return data
        if operation != "cancel" and manager.other_active(user):
            raise web.HTTPConflict()
        if operation == "start":
            data = await read(["email", "password", "country", "region"], ["remember"])
            if (not isinstance(data["country"], str) or data["country"] not in COUNTRIES
                    or not isinstance(data["region"], str) or data["region"] not in {"US", "EU", "IE"}):
                return web.json_response({"state": "credentials", "error": "invalid_input"})
            result = await wizard.start(**data)
        elif operation == "captcha":
            data = await read(["answer"])
            result = await wizard.answer_captcha(data["answer"])
        elif operation == "verify":
            data = await read(["code"])
            result = await wizard.verify_email(data["code"])
        elif operation == "select":
            data = await read(["selection"])
            result = wizard.select_nvr(data["selection"], manager.auth_path)
            if result["state"] == "complete" and runtime is not None:
                runtime.saved()
        elif operation == "retry" and runtime is not None:
            await read([])
            runtime.retry()
            result = wizard.public_status()
        elif operation == "forget" and runtime is not None:
            await read([])
            runtime.forget()
            result = wizard.public_status()
        elif operation == "cancel":
            await read([])
            wizard.cancel()
            result = wizard.public_status()
        else:
            raise web.HTTPBadRequest()
        if runtime is not None:
            result["runtime"] = runtime.public_status()
        return web.json_response(result)

    async def lifetime(app):
        async def sweep():
            while True:
                health.beat("sweep")
                await asyncio.sleep(5)
                manager.expire()
        task = asyncio.create_task(sweep())
        health.beat("sweep")
        health.tasks["sweep"] = task
        async def watch():
            health.beat("watch")
            await runtime.reconcile()
            while True:
                health.beat("watch")
                await asyncio.sleep(5)
                await runtime.tick()
        watcher = asyncio.create_task(watch()) if runtime is not None else None
        if watcher:
            health.beat("watch")
            health.tasks["watch"] = watcher
        yield
        health.closing = True
        for owned in health.tasks.values():
            owned.cancel()
        # A failed watch task must not bypass session and worker cleanup.
        await asyncio.gather(*health.tasks.values(), return_exceptions=True)
        manager.close()
        if runtime is not None:
            await runtime.close()

    app.cleanup_ctx.append(lifetime)
    if health_nonce is not None:
        async def health_status(request):
            good = health.healthy()
            return web.json_response({"status": "ok" if good else "unhealthy"},
                                     status=200 if good else 503)
        app.router.add_get("/healthz", health_status, allow_head=False)
    app.router.add_get("/", page)
    app.router.add_get("/{asset:setup.js|setup.css}", page)
    app.router.add_post("/api/session", bootstrap)
    async def status(request):
        session = manager.get(request.headers["X-Remote-User-Id"],
                              request.headers.get("X-Setup-Session", ""),
                              request.headers.get("X-Setup-CSRF", ""))
        return web.json_response(snapshot(session))
    app.router.add_get("/api/status", status)
    # Dynamic path is restricted to a fixed list, never a file name.
    app.router.add_post("/api/{operation:start|captcha|verify|select|cancel|retry|forget}", action)
    return app

