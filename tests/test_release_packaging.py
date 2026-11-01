import json
import re
import unittest
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleasePackagingTest(unittest.TestCase):
    EUFY_API_PORT = 1985
    EUFY_RTSP_PORT = 8556
    EUFY_WEBRTC_PORT = 8557

    def test_github_actions_are_pinned_to_commits(self):
        workflows = (ROOT / ".github/workflows").glob("*.yml")
        action_refs = []
        for workflow in workflows:
            action_refs.extend(
                re.findall(r"uses:\s+actions/[^@\s]+@([^\s#]+)", workflow.read_text())
            )
        self.assertTrue(action_refs)
        for ref in action_refs:
            self.assertRegex(ref, r"^[0-9a-f]{40}$")

    def test_reviewed_component_pair_and_vendored_build_source(self):
        manifest_version = json.loads(
            (ROOT / "custom_components/eufy_nvr/manifest.json").read_text()
        )["version"]
        config = (ROOT / "eufy_nvr/config.yaml").read_text()
        addon_version = yaml.safe_load(config)["version"]
        dockerfile = (ROOT / "eufy_nvr/Dockerfile").read_text()
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()

        self.assertEqual((addon_version, manifest_version),
                         ("0.8.0-alpha.2", "0.8.0-alpha.3"))
        self.assertFalse((ROOT / "eufy_nvr/build.yaml").exists())
        self.assertRegex(
            dockerfile,
            r'(?m)^ARG BUILD_FROM="ghcr\.io/home-assistant/base-debian:'
            r'bookworm@sha256:[0-9a-f]{64}"$',
        )
        self.assertNotIn("--build-arg BUILD_FROM=", workflow)
        self.assertIn("COPY bridge /opt/eufy/bridge", dockerfile)
        self.assertNotIn("ARG REPO_URL", dockerfile)
        self.assertNotIn("ARG REPO_COMMIT", dockerfile)
        self.assertNotIn("git fetch", dockerfile)

        source_dir = ROOT / "bridge"
        packaged_dir = ROOT / "eufy_nvr/bridge"
        source_files = sorted(
            path.relative_to(source_dir)
            for path in source_dir.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
        packaged_files = sorted(
            path.relative_to(packaged_dir)
            for path in packaged_dir.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
        self.assertIn("**/__pycache__", (ROOT / "eufy_nvr/.dockerignore").read_text())
        self.assertEqual(source_files, packaged_files)
        for relative in source_files:
            self.assertEqual(
                (source_dir / relative).read_bytes(),
                (packaged_dir / relative).read_bytes(),
                relative,
            )

    def test_addon_recovers_after_a_host_restart(self):
        config = (ROOT / "eufy_nvr/config.yaml").read_text()
        settings = yaml.safe_load(config)
        if settings["options"].get("setup_mode") == "guided":
            self.assertEqual(settings["boot"], "manual")  # test preview is opt-in at boot
        else:
            self.assertEqual(settings["boot"], "auto")

    def test_addon_does_not_request_unused_home_assistant_apis(self):
        config = (ROOT / "eufy_nvr/config.yaml").read_text()
        settings = yaml.safe_load(config)
        self.assertFalse(settings.get("hassio_api", False))
        if settings["options"].get("setup_mode") == "guided":
            self.assertTrue(settings["homeassistant_api"])
            self.assertTrue(settings["panel_admin"])
        else:
            self.assertFalse(settings.get("homeassistant_api", False))

    def test_runtime_state_is_persisted_in_addon_data(self):
        run_script = (ROOT / "eufy_nvr/run.sh").read_text()
        self.assertIn('STATE_DIR="/data"', run_script)
        self.assertIn('CONFIG_PATH="${STATE_DIR}/go2rtc.yaml"', run_script)
        self.assertIn('EUFY_AUTH="${STATE_DIR}/auth.json"', run_script)
        self.assertIn('auth_login.py --check-cache-live "${EUFY_AUTH}"', run_script)
        self.assertNotIn('if [ -s "${EUFY_AUTH}" ]; then', run_script)
        self.assertNotIn('rm -f "${EUFY_AUTH}"', run_script)
        self.assertIn('EUFY_CAMERAS="${STATE_DIR}/cameras.json"', run_script)
        self.assertIn('[ -s "${EUFY_CAMERAS}" ]', run_script)

    def test_addon_uses_ports_dedicated_to_eufy(self):
        config = (ROOT / "eufy_nvr/config.yaml").read_text()
        run_script = (ROOT / "eufy_nvr/run.sh").read_text()
        dockerfile = (ROOT / "eufy_nvr/Dockerfile").read_text()
        constants = (ROOT / "custom_components/eufy_nvr/const.py").read_text()

        self.assertIn(f"DEFAULT_API_PORT = {self.EUFY_API_PORT}", constants)
        self.assertIn(f"DEFAULT_RTSP_PORT = {self.EUFY_RTSP_PORT}", constants)
        self.assertIn(f"{self.EUFY_API_PORT}/tcp: {self.EUFY_API_PORT}", config)
        self.assertIn(f"{self.EUFY_RTSP_PORT}/tcp: {self.EUFY_RTSP_PORT}", config)
        self.assertIn(f"{self.EUFY_WEBRTC_PORT}/tcp: {self.EUFY_WEBRTC_PORT}", config)
        self.assertIn(f'GO2RTC_API_PORT="{self.EUFY_API_PORT}"', run_script)
        self.assertIn(f"GO2RTC_RTSP_PORT=\"$(bashio::config 'rtsp_port' '{self.EUFY_RTSP_PORT}')\"", run_script)
        self.assertIn('rtsp_port: int(1024,65535)?', config)
        self.assertIn(f'GO2RTC_WEBRTC_PORT="{self.EUFY_WEBRTC_PORT}"', run_script)
        self.assertIn(f"/dev/tcp/127.0.0.1/{self.EUFY_API_PORT}", dockerfile)
        self.assertNotIn(f"127.0.0.1:{self.EUFY_API_PORT}/api", dockerfile)

        for occupied_port in (1984, 8554, 8555):
            self.assertNotRegex(config, rf"(?m)^\s+{occupied_port}/(?:tcp|udp):")

    def test_addon_uses_open_source_framer_without_vendor_downloads(self):
        fetch_script = (ROOT / "bridge/fetch_deps.js").read_text()
        oracle = (ROOT / "bridge/sctp_oracle.js").read_text()
        dockerfile = (ROOT / "eufy_nvr/Dockerfile").read_text()
        run_script = (ROOT / "eufy_nvr/run.sh").read_text()
        for content in (fetch_script, oracle, dockerfile, run_script):
            self.assertNotIn("libsctp_", content)
            self.assertNotIn("security.eufy.com/plugin", content)
        self.assertIn('require("./ptcs_transport.js")', oracle)
        self.assertIn("ptcs_transport.js", run_script)
        self.assertIn("process.exitCode = 1", fetch_script)
        self.assertIn("node sctp_oracle.js selftest", dockerfile)

    def test_adaptive_warm_never_launches_one_transcoder_per_camera(self):
        run_script = (ROOT / "eufy_nvr/run.sh").read_text()
        warmer = (ROOT / "bridge/eufy_warm.py").read_text()

        self.assertIn("start_adaptive_warmer", run_script)
        self.assertIn("adaptive_warm_seconds", run_script)
        self.assertIn("external_consumer_counts", warmer)
        self.assertIn("EUFY_SESSION_PREEMPT", warmer)
        self.assertIn("consume_preempt_request", warmer)
        self.assertNotIn("for s in", run_script)
        self.assertNotIn("rtsp://127.0.0.1", run_script)
        self.assertNotIn("create_subprocess", warmer)
        self.assertIn("/api/stream.ts", warmer)

    def test_snapshot_primer_is_sequential_and_infrequent(self):
        setup = (ROOT / "custom_components/eufy_nvr/__init__.py").read_text()
        coordinator = (ROOT / "custom_components/eufy_nvr/coordinator.py").read_text()
        constants = (ROOT / "custom_components/eufy_nvr/const.py").read_text()
        snapshot = (ROOT / "custom_components/eufy_nvr/snapshot.py").read_text()

        self.assertIn("for stream in sorted(current - self._primed_streams)", coordinator)
        self.assertNotIn("asyncio.gather", coordinator)
        self.assertIn("FRAME_PRIME_INTERVAL = 30.0 * 60.0", constants)
        self.assertIn("FRAME_STALE_TTL = 60.0 * 60.0", constants)
        self.assertIn("FRAME_INITIAL_TIMEOUT = 95.0", constants)
        self.assertIn("FRAME_SETUP_PRIME_TIMEOUT = 180.0", constants)
        self.assertIn("self._start_refresh(key, capture)", snapshot)
        self.assertIn("current - self._primed_streams", coordinator)
        self.assertIn("retry_delay = min", coordinator)
        self.assertLess(
            setup.index("await coordinator.async_prime_frames()"),
            setup.index("await hass.config_entries.async_forward_entry_setups"),
        )
        self.assertIn("asyncio.timeout(FRAME_SETUP_PRIME_TIMEOUT)", setup)

    def test_token_refresh_does_not_depend_on_keep_warm(self):
        run_script = (ROOT / "eufy_nvr/run.sh").read_text()
        background_block = re.search(
            r'if \[ "\$\{BACKGROUND_TASKS_STARTED\}" -eq 0 \]; then(?P<body>.*?)\n\s*fi',
            run_script,
            re.DOTALL,
        ).group("body")

        self.assertIn("start_relogin_timer", background_block)
        self.assertIn("start_adaptive_warmer", background_block)
        self.assertLess(background_block.index("start_relogin_timer"), background_block.index("start_adaptive_warmer"))

    def test_one_shot_reauthentication_is_opt_in_and_cache_safe(self):
        config = (ROOT / "eufy_nvr/config.yaml").read_text()
        run_script = (ROOT / "eufy_nvr/run.sh").read_text()

        settings = yaml.safe_load(config)
        if settings["options"].get("setup_mode") == "guided":
            self.assertNotIn("force_reauth_once", settings["options"])
            self.assertNotIn("force_reauth_once", settings["schema"])
        else:
            self.assertFalse(settings["options"]["force_reauth_once"])
            self.assertEqual(settings["schema"]["force_reauth_once"], "bool?")
        self.assertIn("FORCE_REAUTH_MARKER", run_script)
        self.assertIn("bashio::config.true 'force_reauth_once'", run_script)
        self.assertIn('auth_login.py --check-cache-live "${EUFY_AUTH}"', run_script)
        self.assertGreaterEqual(run_script.count('touch "${FORCE_REAUTH_MARKER}"'), 2)
        self.assertNotIn('rm -f "${EUFY_AUTH}"', run_script)

    def test_status_100_diagnostic_never_logs_turn_values(self):
        stream_script = (ROOT / "bridge/eufy_stream.py").read_text()

        self.assertIn('turn_state = "present" if isinstance(turn, dict) else "absent"', stream_script)
        self.assertIn('f"turn_credentials={turn_state}"', stream_script)
        self.assertNotIn('log("turn credentials", turn)', stream_script)

    def test_signing_credentials_are_not_logged(self):
        stream_script = (ROOT / "bridge/eufy_stream.py").read_text()
        auth_script = (ROOT / "bridge/auth_login.py").read_text()

        self.assertNotIn("txt[:120]", stream_script)
        self.assertNotIn("sign_token[:20]", stream_script)
        self.assertNotIn('log("sign token:"', stream_script)
        self.assertIn('log("sign token acquired; channels", CHANNELS)', stream_script)
        self.assertIn("ws/sign rejected the current auth session", stream_script)
        self.assertIn("os.replace(temp_path, out_path)", auth_script)
        self.assertNotIn("user_id {user_id[:6]}", auth_script)
        self.assertNotIn("station_sn {station_sn}", auth_script)

    def test_known_hevc_input_skips_ffmpeg_probe_delay(self):
        stream_script = (ROOT / "bridge/eufy_stream.py").read_text()

        self.assertIn('"-probesize", "32", "-analyzeduration", "0"', stream_script)
        self.assertIn("await asyncio.sleep(0.15)", stream_script)
        self.assertNotIn("await asyncio.sleep(1.0)", stream_script)


if __name__ == "__main__":
    unittest.main()
