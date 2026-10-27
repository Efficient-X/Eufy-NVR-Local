"""Candidate guided entry point. Not enabled by the current app configuration."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import time

from aiohttp import ClientSession, ClientTimeout, web

from ha_setup_trust import HomeAssistantTrust
from setup_web import create_app
from startup_controller import StartupController, read_guided_cache
from auth_login import _write_private_json
from setup_budget import SetupBudget
from remembered_login import RememberedLogin

STATE = Path("/data")
HEALTH_FILE = Path("/run/eufy-guided-health.json")


def local_video_ready(path=STATE / "options.json"):
    """Read only this app's startup options; never return or print credentials."""
    try:
        path = Path(path)
        if path.stat().st_size > 65536:
            return False
        options = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(options, dict):
            return False
        username, password = options.get("go2rtc_username"), options.get("go2rtc_password")
        return (isinstance(username, str) and 1 <= len(username) <= 64
                and isinstance(password, str) and 16 <= len(password) <= 256
                and not any(ord(char) < 32 or ord(char) == 127 for char in username + password))
    except (OSError, ValueError, TypeError):
        return False


async def probe_health(path=HEALTH_FILE):
    """Bounded loopback-only Docker probe. Never print response or private paths."""
    try:
        path = Path(path)
        if path.stat().st_size > 1024:
            return False
        saved = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(saved, dict) or set(saved) != {"port", "nonce"}
                or type(saved["port"]) is not int or not 62000 <= saved["port"] <= 65500
                or not isinstance(saved["nonce"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", saved["nonce"])):
            return False
        async with ClientSession(timeout=ClientTimeout(total=3), trust_env=False) as session:
            async with session.get(
                f"http://127.0.0.1:{saved['port']}/healthz",
                headers={"X-Health-Nonce": saved["nonce"]}, allow_redirects=False,
            ) as response:
                if response.status != 200:
                    return False
                body = bytearray()
                async for chunk in response.content.iter_chunked(256):
                    body.extend(chunk)
                    if len(body) > 1024:
                        return False
                return json.loads(body) == {"status": "ok"}
    except Exception:
        return False


def child_environment(source):
    result = dict(source)
    for key in list(result):
        if key in {"SUPERVISOR_TOKEN", "HASSIO_TOKEN", "EUFY_EMAIL", "EUFY_PASSWORD",
                   "EUFY_REGION", "EUFY_COUNTRY", "EUFY_STATION_SN",
                   "EUFY_CAPTCHA_ID", "EUFY_CAPTCHA_ANSWER", "EUFY_VERIFICATION_CODE",
                   "EUFY_BROWSER_SESSION_URL"}:
            result.pop(key, None)
    result["EUFY_GUIDED_WORKER"] = "1"
    return result


class MediaWorker:
    """One isolated Linux process group, including every child video producer."""
    def __init__(self, *, port=1985, stop_timeout=30):
        self.port = port
        self.process = None
        self.stop_timeout = stop_timeout
        self._owned = None

    def running(self):
        return self.process is not None and self.process.returncode is None

    async def port_open(self):
        try:
            async with asyncio.timeout(1):
                _, writer = await asyncio.open_connection("127.0.0.1", self.port)
                writer.close()
                await writer.wait_closed()
                return True
        except (OSError, TimeoutError):
            return False

    async def start(self):
        if self.running():
            return
        if self.process is not None:
            await self.stop()  # Finish the old tree before replacing its ownership handles.
        if await self.port_open():
            raise RuntimeError("The video service port is already in use")
        if sys.platform.startswith("linux"):
            from process_ownership import OwnedProcesses, require_support
            require_support()  # Refuse to launch a tree we cannot safely track.
        self.process = await asyncio.create_subprocess_exec(
            "/run.sh", env=child_environment(os.environ), start_new_session=True,
        )
        try:
            if sys.platform.startswith("linux"):
                self._owned = OwnedProcesses(self.process.pid)
                self._owned.refresh()
            for _ in range(110):
                if not self.running():
                    raise RuntimeError("The video service stopped during startup")
                if await self.port_open():
                    if self._owned is not None:
                        self._owned.refresh()
                    return
                await asyncio.sleep(1)
            raise RuntimeError("The video service did not become ready")
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        process = self.process
        if process is None:
            return
        if self._owned is not None:
            # eufy_run creates a separate engine session. Capture it before the
            # shell exits and its children are reparented. Give that supervisor
            # time to perform its bounded closeLive/engine cleanup first.
            self._owned.refresh()
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            deadline = time.monotonic() + self.stop_timeout
            while self._owned.running() and time.monotonic() < deadline:
                self._owned.refresh()
                await asyncio.sleep(.1)
            self._owned.refresh()
            self._owned.kill()  # pidfds also cover descendants in other sessions.
            async with asyncio.timeout(5):
                await process.wait()
                while self._owned.running():
                    await asyncio.sleep(.05)
            self._owned.close()
            self._owned = None
            self.process = None
            return
        # Non-Linux test boundary; production uses the pidfd-owned path above.
        # Signal only the group created by start_new_session, never the app/HA group.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            async with asyncio.timeout(10):
                await process.wait()
        except TimeoutError:
            pass
        # The shell may exit before its descendants. Kill only that same owned group.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        self.process = None


def build_app(token, *, health_nonce=None):
    budget = SetupBudget(STATE / "setup-budget.json")
    runtime = StartupController(STATE / "auth.json", MediaWorker(),
                                local_credentials_check=local_video_ready,
                                renewal=RememberedLogin(STATE / "auth.json", budget))
    return create_app(
        auth_path=STATE / "auth.json", budget_path=STATE / "setup-budget.json",
        runtime=runtime, trust=HomeAssistantTrust(token), health_nonce=health_nonce,
        setup_budget=budget,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate-cache", action="store_true")
    mode.add_argument("--health", action="store_true")
    args = parser.parse_args(argv)
    if args.validate_cache:
        return 0 if read_guided_cache(STATE / "auth.json") else 1
    if args.health:
        return 0 if asyncio.run(probe_health()) else 1
    if args.port is None or not 62000 <= args.port <= 65500:
        parser.error("Use the ingress port assigned by Home Assistant")
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        print("Guided setup needs Home Assistant API permission.", file=sys.stderr)
        return 2
    # fcntl is intentionally imported only on the Linux runtime path.
    import fcntl
    STATE.mkdir(exist_ok=True)
    with open(STATE / "guided-setup.lock", "a", encoding="utf-8") as lock:
        os.chmod(STATE / "guided-setup.lock", 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Another guided setup process is already running.", file=sys.stderr)
            return 2
        nonce = secrets.token_hex(32)
        _write_private_json(str(HEALTH_FILE), {"port": args.port, "nonce": nonce})
        try:
            web.run_app(build_app(token, health_nonce=nonce), host="0.0.0.0",
                        port=args.port, access_log=None, print=None, shutdown_timeout=5)
        finally:
            # The single-process lock is still held; no later launcher owns this file.
            with contextlib.suppress(FileNotFoundError):
                HEALTH_FILE.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

