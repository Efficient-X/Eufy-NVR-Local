"""Real /init -> MediaWorker -> with-contenv -> bashio -> /run.sh regression.

No household state or external network. Only discovery transport is replaced;
saved-cache validation, role selection, config generation, go2rtc, authentication
and process cleanup are the packaged implementation. This does not test video.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

FIXTURE_PASSWORD = "synthetic-local-video-password-0000"
PROTECTED = ("SUPERVISOR_TOKEN", "HASSIO_TOKEN", "EUFY_EMAIL", "EUFY_PASSWORD")
CACHE = {
    "cacheVersion": 2, "signalingIdentitySource": "nvr_admin",
    "authToken": "synthetic-account-token", "gtoken": "synthetic-gtoken",
    "userId": "synthetic-user", "signalingUserId": "synthetic-admin",
    "stationSn": "T8N00TEST000001", "region": "us-pr", "webCountry": "AU",
    "accountFingerprint": "a" * 64,
}
DISCOVERY = '''import json, os, pathlib, sys
if sys.argv[1:] != ["--discover"]:
    raise RuntimeError("Synthetic transport refuses any media request")
if any(key in os.environ for key in
       ("SUPERVISOR_TOKEN", "HASSIO_TOKEN", "EUFY_EMAIL", "EUFY_PASSWORD")):
    raise RuntimeError("Protected environment reached discovery")
state = {"nvr_sn": "T8N00TEST000001", "cameras": [
    {"channel": i, "name": "Synthetic camera " + str(i), "sn": "SYNTHETIC" + str(i),
     "status": 1, "sensor_num": 2} for i in range(4)]}
pathlib.Path(os.environ["EUFY_CAMERAS"]).write_text(json.dumps(state))
pathlib.Path("/tmp/efx-discovery-reached").write_text("protected variables absent")
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


async def inside(*, baseline=False):
    require(sys.platform.startswith("linux"), "Native Linux test required")
    require(Path("/run/s6/container_environment").is_dir(), "Real S6 /init was not used")
    require(not Path("/run/s6/container_environment/EUFY_GUIDED_WORKER").exists(),
            "Mode marker must not be in the original container environment")
    root = Path("/opt/eufy/bridge")
    sys.path.insert(0, str(root))
    import guided_launcher
    import startup_controller
    from aiohttp import BasicAuth, ClientSession, ClientTimeout

    state = Path("/data")
    state.mkdir(exist_ok=True)
    options = {
        "go2rtc_username": "synthetic", "go2rtc_password": FIXTURE_PASSWORD,
        "rtsp_port": 8556, "signaling_mode": "call", "signaling_identity": "nvr_admin",
        "log_level": "warning", "video_copy": True, "shared_grid": True,
        "grid_lens": "both", "adaptive_warm_seconds": 0, "keep_warm": False,
    }
    (state / "options.json").write_text(json.dumps(options))
    auth_path = state / "auth.json"
    auth_path.write_text(json.dumps(CACHE))
    original_cache = auth_path.read_bytes()
    # This writable layer belongs only to this disposable offline test container.
    # No real image or source file on the host is changed.
    (root / "eufy_run.py").write_text(DISCOVERY)
    accepted = []

    async def accepted_account(path):
        require(startup_controller.read_guided_cache(path) is not None,
                "The real saved guided-cache validator rejected the fixture")
        accepted.append(True)
        return "valid"  # Explicit account-transport test double, no Eufy request.

    worker = guided_launcher.MediaWorker(stop_timeout=5)
    controller = startup_controller.StartupController(auth_path, worker,
        checker=accepted_account, local_credentials_check=guided_launcher.local_video_ready)
    stopped_in = None
    try:
        async with asyncio.timeout(40):
            await controller.reconcile()
        require(bool(accepted), "Saved account was not checked")
        require(auth_path.read_bytes() == original_cache, "Startup changed saved account data")
        marker = Path("/tmp/efx-discovery-reached")
        if baseline:
            require(controller.state == "video_service_failed" and not marker.exists(),
                    "The original environment-marker failure was not reproduced")
            require(not await worker.port_open(), "Failed baseline left a video listener")
            print("PASS: baseline saved-account acceptance followed by legacy-gate failure", flush=True)
            return
        require(controller.state == "video_service_running", "Guided service did not start")
        require(marker.read_text() == "protected variables absent",
                "Actual guided discovery did not run with isolated environment")
        async with ClientSession(timeout=ClientTimeout(total=5)) as session:
            async with session.get("http://127.0.0.1:1985/api/streams") as response:
                require(response.status in {401, 403}, "Video API accepted unauthenticated access")
            async with session.get("http://127.0.0.1:1985/api/streams",
                                   auth=BasicAuth("synthetic", FIXTURE_PASSWORD)) as response:
                require(response.status == 200, "Authenticated video API is unavailable")
                streams = await response.json()
            require("eufy_shared_grid" in streams, "Shared stream was not generated")
        layout = json.loads((state / "grid_layout.json").read_text())
        require(layout["lens_views"] == 8, "Four synthetic dual-lens units did not produce eight views")
        print("PASS: real S6 guided worker, saved cache, protected environment and authenticated eight-view layout", flush=True)
    finally:
        started = time.monotonic()
        async with asyncio.timeout(12):
            await controller.close()
        stopped_in = time.monotonic() - started
        require(not worker.running() and not await worker.port_open(), "Stop left a worker or video port")
        print("PASS: real worker cleanup completed in %.2f seconds" % stopped_in, flush=True)

    # Normal legacy launches must still require explicit legacy account options,
    # even if a caller supplies a guided-looking environment variable.
    bashio = shutil.which("bashio")
    require(bashio is not None, "Packaged bashio executable is missing")
    for command in (("/run.sh",), (bashio, "/run.sh")):
        env = dict(os.environ, EUFY_GUIDED_WORKER="1")
        process = await asyncio.create_subprocess_exec(*command, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            async with asyncio.timeout(22):
                output, _ = await process.communicate()
        except BaseException:
            process.kill()
            await process.wait()
            raise
        require(process.returncode != 0 and b"Set 'email' and 'password'" in output,
                "A normal legacy launch bypassed its credential gate")
    print("PASS: genuine legacy credential gate remains strict with and without the S6 wrapper", flush=True)


def host(image, *, baseline=False):
    name = "efx-guided-entry-smoke-" + uuid.uuid4().hex[:12]
    command = ["docker", "run", "--name", name, "--rm", "--network", "none",
               "--pids-limit", "64", "--memory", "512m", "--cpus", "2",
               "--env", "SUPERVISOR_TOKEN=synthetic-container-token",
               "--env", "EUFY_PASSWORD=synthetic-container-password",
               "--mount", "type=bind,source=" + str(Path(__file__).resolve()) +
               ",target=/entrypoint-smoke.py,readonly",
               image, "python3", "/entrypoint-smoke.py", "--inside"]
    if baseline:
        command.append("--expect-env-marker-failure")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=110)
        output = result.stdout + result.stderr
        require("synthetic-container-token" not in output and
                "synthetic-container-password" not in output and FIXTURE_PASSWORD not in output,
                "A synthetic protected value was logged")
        print(output, end="" if output.endswith("\n") else "\n")
        require(result.returncode == 0, "Actual container-entrypoint regression failed")
        require("PASS: baseline saved-account" in output if baseline else
                "PASS: genuine legacy credential gate remains strict" in output,
                "The container did not finish its required assertions")
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--expect-env-marker-failure", action="store_true")
    args = parser.parse_args()
    try:
        if args.inside:
            asyncio.run(inside(baseline=args.expect_env_marker_failure))
        else:
            require(bool(args.image), "An explicit tested image is required")
            host(args.image, baseline=args.expect_env_marker_failure)
    except Exception as error:
        print("FAIL: real S6 guided-entrypoint check (%s)" % type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
