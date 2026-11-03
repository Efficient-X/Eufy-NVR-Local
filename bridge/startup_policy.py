"""Finite guided-startup policy and small, fixed-vocabulary status snapshots."""
from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile

# Guided discovery has one shared deadline, rather than two nested3-session
# retry loops. Four quick attempts still permit the reproduced116-second valid
# path, but slow attempts share two full session windows instead of multiplying it.
DISCOVERY_ATTEMPTS = 1
SESSION_ATTEMPTS = 4
SIGNAL_SECONDS = 25
CONNECTION_SECONDS = 35
DISCOVERY_SESSION_SECONDS = SIGNAL_SECONDS + CONNECTION_SECONDS
DISCOVERY_BUDGET_SECONDS = 2 * DISCOVERY_SESSION_SECONDS
SESSION_LOCK_SECONDS = 10
ENGINE_CLEANUP_SECONDS = 3 + 5 + 2 + 5
STDERR_DRAIN_SECONDS = 2
SESSION_RELEASE_SECONDS = 1
SESSION_POLL_SECONDS = 0.5
SESSION_RETRY_SECONDS = sum(min(2 ** (n - 2), 4) for n in range(2, SESSION_ATTEMPTS + 1))
# The shared timeout cancels a running attempt. Its finally block drains one
# owned engine tree and stderr; the session gate releases after the final beat.
DISCOVERY_CLEANUP_SECONDS = ENGINE_CLEANUP_SECONDS + STDERR_DRAIN_SECONDS + SESSION_RELEASE_SECONDS
# Ten seconds each for local launcher/config work and listener readiness.
LOCAL_STARTUP_SECONDS = 10 + 10 + 10
WORKER_READY_SECONDS = DISCOVERY_BUDGET_SECONDS + DISCOVERY_CLEANUP_SECONDS + LOCAL_STARTUP_SECONDS
WORKER_STOP_SECONDS = 30 + 5
WORKER_CONTROL_SECONDS = WORKER_READY_SECONDS + 2 * WORKER_STOP_SECONDS + 1
ACCOUNT_CHECK_SECONDS = 20
RENEWAL_SECONDS = 45
WATCH_LIVENESS_SECONDS = (2 * ACCOUNT_CHECK_SECONDS + RENEWAL_SECONDS
                          + 2 * WORKER_STOP_SECONDS + WORKER_CONTROL_SECONDS + 5)

STATES = frozenset({"checking_account", "renewing_sign_in", "setup_required",
    "sign_in_required", "starting_video", "video_service_running", "service_error",
    "local_video_setup_required", "connection_unavailable", "video_port_in_use", "stopped"})
PHASES = frozenset({"not_started", "launching_worker", "validating_account",
    "discovering", "generating_config", "starting_listener", "listener_ready", "stopping"})
TRANSPORTS = frozenset({"http_response", "timeout", "connection_refused", "connection_reset",
    "name_resolution", "network_unreachable", "tls_error", "transport_other"})


def transport_category(error):
    """Never stringify an exception: it can contain a URL, token or password."""
    if isinstance(error, TimeoutError):
        return "timeout"
    underlying = getattr(error, "os_error", error)
    if isinstance(underlying, TimeoutError):
        return "timeout"
    if isinstance(underlying, ssl.SSLError):
        return "tls_error"
    if isinstance(underlying, socket.gaierror):
        return "name_resolution"
    code = getattr(underlying, "errno", None)
    return {errno.ECONNREFUSED: "connection_refused", errno.ECONNRESET: "connection_reset",
            errno.ENETUNREACH: "network_unreachable", errno.EHOSTUNREACH: "network_unreachable",
            errno.ETIMEDOUT: "timeout"}.get(code, "transport_other")


def checked_status(value, area):
    if not isinstance(value, dict):
        raise ValueError("Invalid startup status")
    if area == "worker":
        if set(value) != {"phase"} or not isinstance(value["phase"], str) or value["phase"] not in PHASES:
            raise ValueError("Invalid startup phase")
        return dict(value)
    keys = {"state", "warning", "account_transport", "video_transport", "worker_phase"}
    def valid_result(result):
        return (isinstance(result, dict) and set(result) == keys
            and isinstance(result["state"], str) and result["state"] in STATES
            and result["warning"] in (None, "account_check_unavailable")
            and all(result[key] is None or isinstance(result[key], str) and result[key] in TRANSPORTS
                    for key in ("account_transport", "video_transport"))
            and (result["worker_phase"] is None or isinstance(result["worker_phase"], str)
                 and result["worker_phase"] in PHASES))
    if (area != "runtime" or set(value) != keys | {"last_result"}
            or not valid_result({key: value[key] for key in keys})
            or value["last_result"] is not None and not valid_result(value["last_result"])):
        raise ValueError("Invalid startup state")
    return {**value, "last_result": dict(value["last_result"]) if value["last_result"] else None}


def read_status(path, area):
    """At most one tiny snapshot; never expose arbitrary file fields or text."""
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(1025)
        if len(raw) > 1024:
            return None
        saved = json.loads(raw)
        if set(saved) != {"version", "status"} or type(saved["version"]) is not int or saved["version"] != 1:
            return None
        return checked_status(saved["status"], area)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def retain_status(path, area, value):
    """Latest status only, atomically replaced at0600; no unbounded event history."""
    value = checked_status(value, area)
    if path is None:
        return
    path = Path(path)
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".startup-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump({"version": 1, "status": value}, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        # A disk problem must not turn a status write into a new login/start loop.
        print("EUFY_STARTUP status_retention_unavailable", flush=True)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    print("EUFY_STARTUP " + area + "=" + value.get("state", value.get("phase")), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=sorted(PHASES))
    args = parser.parse_args()
    retain_status("/data/startup-worker.json", "worker", {"phase": args.phase})


if __name__ == "__main__":
    main()
