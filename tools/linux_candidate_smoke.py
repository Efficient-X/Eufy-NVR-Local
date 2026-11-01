"""Exercise candidate Linux process ownership and health with synthetic state only.

Run inside the built image with --network none. The test substitutes a tiny
loopback worker for /run.sh; it never calls Eufy, HA, ffmpeg or a real camera.
It does not prove video, ingress permissions or the first-time installer work.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def alive(pid):
    try:
        # A killed child may briefly be a zombie until init reaps it.
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].strip().split()[0] != "Z"
    except FileNotFoundError:
        return False


CHILD = r'''
import json, os, pathlib, signal, socket, subprocess, sys, time
folder = pathlib.Path(sys.argv[1])
port = int(sys.argv[2])
mode = sys.argv[3]
grandchild = r"""
import os,pathlib,signal,sys,time
def stop(signum, frame):
    time.sleep(.35)
    pathlib.Path(sys.argv[1] + ".closed").write_text("done")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop if sys.argv[2] == "graceful" else signal.SIG_IGN)
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
while True: time.sleep(1)
"""
process = subprocess.Popen([sys.executable, "-u", "-c", grandchild,
    str(folder / "descendant"), mode], start_new_session=(mode == "nested"))
deadline = time.monotonic() + 5
while not (folder / "descendant").exists():
    if time.monotonic() >= deadline: raise SystemExit(2)
    time.sleep(.01)
keys = ("SUPERVISOR_TOKEN", "HASSIO_TOKEN", "EUFY_EMAIL", "EUFY_PASSWORD")
(folder / "environment.json").write_text(json.dumps({key: key in os.environ for key in keys}))
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", port))
server.listen()
while True:
    connection, _ = server.accept()
    connection.close()
'''


async def process_group_check(module, folder, mode="stubborn", stop_event=None):
    # Reserve an ephemeral loopback port, then hand it to our own synthetic child.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    worker = module.MediaWorker(port=port, stop_timeout=1 if mode == "graceful" else .25)
    original_spawn = asyncio.create_subprocess_exec
    spawned = []

    async def substitute(*args, **kwargs):
        require(args == ("/run.sh", "--guided-worker"), "Unexpected worker command")
        require(kwargs.get("start_new_session") is True, "Worker must own its session")
        process = await original_spawn(sys.executable, "-u", "-c", CHILD,
            str(folder), str(port), mode, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, **kwargs)
        spawned.append(process)
        return process

    protected = {key: os.environ.get(key) for key in
                 ("SUPERVISOR_TOKEN", "HASSIO_TOKEN", "EUFY_EMAIL", "EUFY_PASSWORD")}
    try:
        for key in protected:
            os.environ[key] = "synthetic-smoke-test-value"
        asyncio.create_subprocess_exec = substitute
        async with asyncio.timeout(15):
            await worker.start()
        require(worker.running(), "Synthetic worker did not start")
        await worker.start()
        require(len(spawned) == 1, "Repeated start created another worker")
        parent = spawned[0].pid
        descendant = int((folder / "descendant").read_text())
        require(os.getpgid(parent) == parent, "Worker is outside its own process group")
        expected_group = descendant if mode == "nested" else parent
        require(os.getpgid(descendant) == expected_group, "Incorrect synthetic process group")
        require(not any(json.loads((folder / "environment.json").read_text()).values()),
                "A protected environment variable reached the worker")
        if stop_event is not None:
            print("READY: synthetic worker waiting for container stop", flush=True)
            async with asyncio.timeout(90):
                await stop_event.wait()
        await worker.stop()
        for _ in range(100):
            if not alive(descendant):
                break
            await asyncio.sleep(.02)
        require(not alive(parent) and not alive(descendant), "Owned process survived stop")
        if mode == "graceful":
            require((folder / "descendant.closed").read_text() == "done",
                    "Shell exit cut off the child's graceful cleanup")
        require(worker.process is None and not await worker.port_open(), "Stop left video port open")
        await worker.stop()  # Repeated cleanup is harmless.
    finally:
        asyncio.create_subprocess_exec = original_spawn
        for key, value in protected.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        with contextlib.suppress(Exception):
            await worker.stop()
        if worker._owned is not None:
            worker._owned.kill()
            worker._owned.close()
        # Only the session created by this test may be stopped.
        for process in spawned:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


async def health_check(module, folder):
    from aiohttp import ClientSession, web
    import setup_web
    import startup_controller

    worker = module.MediaWorker()
    runtime = startup_controller.StartupController(folder / "auth.json", worker,
        local_credentials_check=lambda: False)
    nonce = "a" * 64  # Synthetic nonce, not a household secret.
    app = setup_web.create_app(auth_path=folder / "auth.json",
        budget_path=folder / "budget.json", runtime=runtime,
        allowed_users={"test_admin"}, allowed_origins={"http://test.invalid"}, health_nonce=nonce)
    runner = web.AppRunner(app, access_log=None)
    listener = socket.socket()
    try:
        for port in range(62000, 65501):
            try:
                listener.bind(("127.0.0.1", port))
                break
            except OSError:
                continue
        else:
            raise RuntimeError("No private test port available")
        listener.setblocking(False)
        await runner.setup()
        await web.SockSite(runner, listener).start()
        await asyncio.sleep(0)
        require(runtime.state == "local_video_setup_required", "Missing local password should wait")
        health = folder / "health.json"
        health.write_text(json.dumps({"port": port, "nonce": nonce}))
        require(await module.probe_health(health), "Waiting setup failed its real HTTP probe")
        async with ClientSession() as session:
            for path, headers in [("/", {"X-Health-Nonce": nonce}),
                                  ("/healthz", {"X-Health-Nonce": "b" * 64})]:
                async with session.get(f"http://127.0.0.1:{port}{path}", headers=headers) as reply:
                    require(reply.status == 403, "Loopback request crossed the setup boundary")
        require(not worker.running(), "Health probe unexpectedly started video")
    finally:
        await runner.cleanup()
        listener.close()
    require(runtime.state == "stopped", "Web cleanup did not close the runtime")
    require(not await module.probe_health(health), "Health endpoint survived shutdown")


async def run(bridge, *, wait_for_stop=False):
    sys.path.insert(0, str(bridge))
    import guided_launcher
    with tempfile.TemporaryDirectory(prefix="eufy-smoke-") as temporary:
        folder = Path(temporary)
        if wait_for_stop:
            # S6 consumes and removes its own S6_* environment variables before
            # starting CMD. Only an actual stop proves forwarding is configured.
            event = asyncio.Event()
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGTERM, event.set)
            loop.add_signal_handler(signal.SIGINT, event.set)
            try:
                await process_group_check(guided_launcher, folder, "graceful", event)
            finally:
                loop.remove_signal_handler(signal.SIGTERM)
                loop.remove_signal_handler(signal.SIGINT)
            print("PASS: container stop reached CMD and completed graceful child cleanup", flush=True)
            return
        for mode in ("graceful", "stubborn", "nested"):
            case = folder / mode
            case.mkdir()
            await process_group_check(guided_launcher, case, mode)
        print("PASS: real Linux graceful stop, stubborn/nested descendants and environment isolation")
        await health_check(guided_launcher, folder)
        print("PASS: real loopback health probe, waiting state and endpoint shutdown")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", type=Path, default=Path("/opt/eufy/bridge"))
    parser.add_argument("--wait-for-stop", action="store_true",
                        help="Run through the image's S6 entry point and await docker stop")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        print("NOT RUN: this smoke test requires Linux.")
        return 2
    try:
        asyncio.run(run(args.bridge.resolve(), wait_for_stop=args.wait_for_stop))
    except Exception as error:
        # Do not emit environment, file contents or exception messages.
        print(f"FAIL: candidate lifecycle smoke check ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
