"""Opt-in real go2rtc test: loopback API only, synthetic data, no media modules."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest
import yaml

from test_dashboard_discovery import generate


@pytest.mark.skipif(not os.environ.get("EUFY_TEST_GO2RTC"),
                    reason="Set EUFY_TEST_GO2RTC to the verified local test binary")
def test_real_go2rtc_serves_only_dedicated_metadata(tmp_path):
    executable = Path(os.environ["EUFY_TEST_GO2RTC"]).resolve()
    assert executable.is_file()
    settings, metadata, expected = generate(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings["app"]["modules"] = ["api"]
    settings["streams"] = {}
    settings["api"]["listen"] = f"127.0.0.1:{port}"
    # Force auth even on loopback to exercise the same middleware used for LAN.
    settings["api"]["local_auth"] = True
    config = tmp_path / "go2rtc-test.yaml"
    config.write_text(yaml.safe_dump(settings))
    (tmp_path / "auth.json").write_text("synthetic-secret-outside-static-directory")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen([str(executable), "-config", str(config)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    token = base64.b64encode(b"test:synthetic-password-only").decode()

    def request(path, authenticated=True, method="GET"):
        headers = {"Authorization": "Basic " + token} if authenticated else {}
        req = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
                                     headers=headers, method=method)
        try:
            with opener.open(req, timeout=1) as response:
                return response.status, response.read(65537)
        except urllib.error.HTTPError as err:
            return err.code, err.read(65537)

    try:
        deadline = time.monotonic() + 8
        while True:
            assert process.poll() is None, "Test server exited"
            try:
                status, body = request("/eufy-grid-layout.json")
                break
            except urllib.error.URLError:
                assert time.monotonic() < deadline, "Loopback test server did not start"
                time.sleep(0.05)
        assert status == 200 and json.loads(body) == expected
        assert request("/eufy-grid-layout.json", authenticated=False)[0] == 401
        for path in ("/auth.json", "/go2rtc-test.yaml", "/api/config", "/api/log",
                     "/../auth.json", "/%2e%2e/auth.json"):
            status, body = request(path)
            assert status == 404
            assert b"synthetic-secret-outside-static-directory" not in body
        assert request("/api/restart", method="POST")[0] == 404
        assert process.poll() is None
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
