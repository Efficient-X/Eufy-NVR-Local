#!/usr/bin/with-contenv bashio
set -o errexit
set -o nounset
set -o pipefail

BRIDGE_DIR="/opt/eufy/bridge"
STATE_DIR="/data"
CONFIG_PATH="${STATE_DIR}/go2rtc.yaml"
export GO2RTC_API_PORT="1985"
export GO2RTC_RTSP_PORT="$(bashio::config 'rtsp_port' '8556')"
export GO2RTC_WEBRTC_PORT="8557"
mkdir -p "${STATE_DIR}"
cd "${BRIDGE_DIR}"

LOG_LEVEL="$(bashio::config 'log_level' 'info')"
export EUFY_LOG_LEVEL="${LOG_LEVEL}"
GUIDED_WORKER="${EUFY_GUIDED_WORKER:-0}"

if [ "${GUIDED_WORKER}" != 1 ] && { ! bashio::config.has_value 'email' || ! bashio::config.has_value 'password'; }; then
    bashio::log.fatal "Set 'email' and 'password' (your eufy account) in the add-on configuration."
    sleep 15
    exit 1
fi
if ! bashio::config.has_value 'go2rtc_username' || ! bashio::config.has_value 'go2rtc_password'; then
    bashio::log.fatal "Set go2rtc_username and a go2rtc_password of at least 16 characters. These protect camera video and the management API on your LAN."
    sleep 15
    exit 1
fi

if [ "${GUIDED_WORKER}" != 1 ]; then
    export EUFY_EMAIL="$(bashio::config 'email')"
    export EUFY_PASSWORD="$(bashio::config 'password')"
    export EUFY_REGION="$(bashio::config 'region' 'US')"
else
    # Guided startup owns authentication; media reads region from the saved cache.
    # with-contenv may restore container variables before this script begins.
    unset SUPERVISOR_TOKEN HASSIO_TOKEN
    unset EUFY_EMAIL EUFY_PASSWORD EUFY_REGION EUFY_COUNTRY EUFY_STATION_SN
    unset EUFY_CAPTCHA_ID EUFY_CAPTCHA_ANSWER EUFY_VERIFICATION_CODE EUFY_BROWSER_SESSION_URL
fi
export EUFY_CALL_TYPE="$(bashio::config 'signaling_mode' 'call')"
export EUFY_SIGNALING_IDENTITY="$(bashio::config 'signaling_identity' 'nvr_admin')"
export EUFY_AUTH="${STATE_DIR}/auth.json"
export EUFY_CAMERAS="${STATE_DIR}/cameras.json"
export EUFY_STREAM_NAMES="${STATE_DIR}/stream_names.json"
export EUFY_DISABLED_CHANNELS="$(bashio::config 'disabled_channels' '')"
export EUFY_SHARED_GRID=0
if bashio::config.true 'shared_grid'; then export EUFY_SHARED_GRID=1; fi
export EUFY_GRID_LENS="$(bashio::config 'grid_lens' 'movable')"
export EUFY_GRID_COLUMNS="$(bashio::config 'grid_columns' '0')"
export EUFY_SESSION_LOCK="${STATE_DIR}/eufy-session.lock"
export GO2RTC_USERNAME="$(bashio::config 'go2rtc_username')"
export GO2RTC_PASSWORD="$(bashio::config 'go2rtc_password')"
if [ "${#GO2RTC_PASSWORD}" -lt 16 ]; then
    bashio::log.fatal "go2rtc_password must contain at least 16 characters."
    sleep 15
    exit 1
fi
if [ "${GUIDED_WORKER}" != 1 ]; then
if bashio::config.has_value 'country'; then export EUFY_COUNTRY="$(bashio::config 'country')"; fi
if bashio::config.has_value 'station_sn'; then export EUFY_STATION_SN="$(bashio::config 'station_sn')"; fi
if bashio::config.has_value 'captcha_id'; then export EUFY_CAPTCHA_ID="$(bashio::config 'captcha_id')"; fi
if bashio::config.has_value 'captcha_answer'; then export EUFY_CAPTCHA_ANSWER="$(bashio::config 'captcha_answer')"; fi
if bashio::config.has_value 'verification_code'; then export EUFY_VERIFICATION_CODE="$(bashio::config 'verification_code')"; fi
fi

umask 077
BROWSER_SESSION_PROBE=0
if [ "${GUIDED_WORKER}" = 1 ]; then
    if ! python3 guided_launcher.py --validate-cache; then
        bashio::log.fatal "No valid guided account connection. Open setup to sign in."
        exit 1
    fi
else
python3 browser_session_probe.py restore
trap 'python3 browser_session_probe.py restore' EXIT
BROWSER_SESSION_PROBE=0
if bashio::config.has_value 'browser_session_url'; then
    export EUFY_BROWSER_SESSION_URL="$(bashio::config 'browser_session_url')"
    python3 browser_session_probe.py import
    unset EUFY_BROWSER_SESSION_URL
    BROWSER_SESSION_PROBE=1
fi
FORCE_REAUTH_MARKER="${STATE_DIR}/force-reauth-once.done"
if ! bashio::config.true 'force_reauth_once'; then
    rm -f "${FORCE_REAUTH_MARKER}" 2>/dev/null || true
fi

if [ "${BROWSER_SESSION_PROBE}" = 1 ]; then
    bashio::log.info "Isolated browser-session probe; headless login skipped. Original auth restores on exit."
elif bashio::config.true 'force_reauth_once' && [ ! -e "${FORCE_REAUTH_MARKER}" ]; then
    bashio::log.warning "One-shot fresh Eufy authentication requested; the existing private cache remains available unless login succeeds."
    if python3 auth_login.py; then
        touch "${FORCE_REAUTH_MARKER}"
        chmod 600 "${FORCE_REAUTH_MARKER}" 2>/dev/null || true
        bashio::log.info "One-shot fresh authentication succeeded; refreshed auth.json."
    elif python3 auth_login.py --check-cache-live "${EUFY_AUTH}"; then
        touch "${FORCE_REAUTH_MARKER}"
        chmod 600 "${FORCE_REAUTH_MARKER}" 2>/dev/null || true
        bashio::log.warning "Fresh authentication was not accepted; retained the previously validated live cache and will not retry until force_reauth_once is toggled off and on."
    else
        bashio::log.fatal "Fresh authentication failed and no live account-bound cache exists. Refusing to start producers with rejected credentials."
        bashio::log.fatal "For mailbox verification, enter the emailed six-digit verification_code and restart."
        bashio::log.fatal "For a graphic CAPTCHA, set captcha_id + captcha_answer and restart."
        unset EUFY_PASSWORD
        unset EUFY_VERIFICATION_CODE
        sleep 15
        exit 1
    fi
elif python3 auth_login.py --check-cache-live "${EUFY_AUTH}"; then
    bashio::log.info "Reusing the live account-bound auth session; skipped a redundant passport login."
elif ! python3 auth_login.py; then
    bashio::log.fatal "Headless login failed and no live account-bound cache exists. Refusing to start producers with rejected credentials."
    bashio::log.fatal "For mailbox verification, enter the emailed six-digit verification_code and restart."
    bashio::log.fatal "For a graphic CAPTCHA, set captcha_id + captcha_answer and restart."
    unset EUFY_PASSWORD
    unset EUFY_VERIFICATION_CODE
    sleep 15
    exit 1
else
    bashio::log.info "Logged in; refreshed auth.json (region $(bashio::config 'region' 'US'))."
fi
fi # Legacy account setup; never run together with the guided setup owner.
unset EUFY_PASSWORD
unset EUFY_VERIFICATION_CODE
chmod 600 "${EUFY_AUTH}" 2>/dev/null || true
bashio::log.info "Credentials kept out of logs; runtime state is persisted under /data."

if [ ! -s "${BRIDGE_DIR}/ptcs_transport.js" ]; then
    bashio::log.fatal "Required Apache PTCS transport is missing; the image is invalid."
    exit 1
fi
if [ ! -x "${BRIDGE_DIR}/bin/go2rtc" ]; then
    bashio::log.fatal "go2rtc binary not found at bin/go2rtc — the image build did not complete."
    sleep 15
    exit 1
fi

DISCOVERY_FATAL=0
discover_and_generate() {
    local attempt rc log_file
    # eufy_run.py already opens up to three fresh signaling sessions per call.
    # Two outer attempts allow one complete retry after a transient cloud/NVR busy period.
    for attempt in 1 2; do
        log_file="${STATE_DIR}/discovery-attempt-${attempt}.log"
        : > "${log_file}"
        bashio::log.info "Auto-discovering NVR + cameras (supervised attempt ${attempt}/2)..."

        set +o errexit
        python3 eufy_run.py --discover 2> >(tee "${log_file}" >&2)
        rc=$?
        set -o errexit

        if grep -q 'EUFY_AUTHORIZATION_ERROR_-104' "${log_file}"; then
            DISCOVERY_FATAL=1
            bashio::log.fatal "Eufy NVR rejected application commands with the shared/member authorization pattern."
            bashio::log.fatal "Use the eufy account that OWNS/administers this NVR. Shared/member accounts can authenticate but cannot open the NVR command session."
            return 2
        fi

        if grep -q 'signaling timed out' "${log_file}"; then
            bashio::log.warning "Eufy signaling did not progress from call/TURN to an SDP offer; fresh signaling sessions were attempted automatically."
        fi

        if [ "${rc}" -eq 0 ] && [ -s "${EUFY_CAMERAS}" ]; then
            bashio::log.info "Discovery OK -> ${EUFY_CAMERAS}"
            if python3 gen_go2rtc.py "127.0.0.1"; then
                install -m 600 "${BRIDGE_DIR}/go2rtc.yaml" "${CONFIG_PATH}"
                bashio::log.info "Generated go2rtc.yaml from discovered cameras."
                rm -f "${log_file}" 2>/dev/null || true
                return 0
            fi
            bashio::log.warning "gen_go2rtc.py failed; will retry."
        else
            bashio::log.warning "Discovery failed after supervised signaling retries (rc=${rc})."
        fi

        sleep "$((attempt * 5))"
    done
    return 1
}

if ! discover_and_generate; then
    if [ "${DISCOVERY_FATAL}" -eq 1 ]; then
        bashio::log.fatal "Discovery stopped because the configured account is not authorized by the NVR."
        sleep 15
        exit 1
    fi
    if [ -s "${EUFY_CAMERAS}" ] && python3 gen_go2rtc.py "127.0.0.1"; then
        install -m 600 "${BRIDGE_DIR}/go2rtc.yaml" "${CONFIG_PATH}"
        bashio::log.warning "Discovery failed; regenerated go2rtc.yaml from the last discovered camera list (${EUFY_CAMERAS})."
    elif [ -f "${CONFIG_PATH}" ]; then
        bashio::log.warning "Discovery failed but a previous go2rtc.yaml exists — starting with it."
    else
        bashio::log.fatal "Could not discover cameras and no cached go2rtc.yaml is present. Aborting."
        bashio::log.fatal "Check the signaling messages above. Common causes are region mismatch, expired auth, a busy NVR session, or a stalled call/TURN negotiation."
        sleep 15
        exit 1
    fi
fi

if command -v sed >/dev/null 2>&1; then
    sed -i "s/^  level: .*/  level: ${LOG_LEVEL}/" "${CONFIG_PATH}" || true
fi

# Opt-in, one-shot metadata-only shared-session test. It completes before
# go2rtc accepts any consumer, so normal producers cannot compete with it.
MULTICHANNEL_PROBE_MARKER="${STATE_DIR}/multichannel-probe.done"
if bashio::config.true 'multichannel_probe_once'; then
    if [ ! -f "${MULTICHANNEL_PROBE_MARKER}" ]; then
        touch "${MULTICHANNEL_PROBE_MARKER}"
        bashio::log.info "Starting bounded shared-session probe (channels 0,1,2; no video export)."
        EUFY_SESSION_ATTEMPTS=1 python3 eufy_run.py 0,1,2 --multichannel-probe || 
            bashio::log.warning "Shared-session probe did not complete; normal service will resume."
    fi
else
    rm -f "${MULTICHANNEL_PROBE_MARKER}"
fi

bashio::log.info "Discovered streams:"
grep -E '^[[:space:]]+eufy_[a-z0-9_]+:' "${CONFIG_PATH}" | sed 's/:.*$//' | sed 's/^/    /' || true

if bashio::config.true 'video_copy'; then
    export EUFY_VIDEO_COPY=1
    bashio::log.info "video_copy=true -> H.265 pass-through; live view requires a compatible HEVC browser. No server video transcoding."
fi

RELOGIN_PID=""
ADAPTIVE_WARM_PID=""

start_adaptive_warmer() {
    local seconds
    seconds="$(bashio::config 'adaptive_warm_seconds' '30')"
    if bashio::config.true 'keep_warm'; then
        bashio::log.warning "keep_warm is deprecated; using a bounded 300-second one-camera lease instead."
        seconds=300
    fi
    if ! [ "${seconds}" -gt 0 ] 2>/dev/null; then
        bashio::log.info "adaptive warm lease disabled."
        return 0
    fi
    if [ "${seconds}" -gt 600 ]; then seconds=600; fi
    python3 eufy_warm.py --seconds "${seconds}" &
    ADAPTIVE_WARM_PID=$!
    bashio::log.info "adaptive warm lease enabled (${seconds}s, one camera maximum)."
}

start_relogin_timer() {
    if [ "${GUIDED_WORKER}" = 1 ]; then return 0; fi
    if [ "${BROWSER_SESSION_PROBE}" = 1 ]; then return 0; fi
    local hours; hours="$(bashio::config 'token_refresh_hours' '6')"
    if ! [ "${hours}" -gt 0 ] 2>/dev/null; then
        bashio::log.warning "token_refresh_hours='${hours}' invalid/<=0; periodic re-login disabled."
        return 0
    fi
    ( while true; do
        sleep "$(( hours * 3600 ))"
        if python3 auth_login.py --check-cache-live "${EUFY_AUTH}" >/dev/null 2>&1; then
            bashio::log.info "Periodic auth check: cached session is still live; skipped login."
        elif EUFY_PASSWORD="$(bashio::config 'password')" python3 auth_login.py >/dev/null 2>&1; then
            chmod 600 "${EUFY_AUTH}" 2>/dev/null || true
            bashio::log.info "Expired auth session renewed."
        else
            bashio::log.warning "Expired auth session could not be renewed; will retry next cycle."
        fi
      done ) &
    RELOGIN_PID=$!
}

term() {
    bashio::log.info "Received stop signal; shutting down go2rtc (pid ${GO2RTC_PID:-?}) + warmers."
    [ -n "${RELOGIN_PID:-}" ] && kill "${RELOGIN_PID}" 2>/dev/null || true
    [ -n "${ADAPTIVE_WARM_PID:-}" ] && kill "${ADAPTIVE_WARM_PID}" 2>/dev/null || true
    [ -n "${GO2RTC_PID:-}" ] && kill -TERM "${GO2RTC_PID}" 2>/dev/null || true
    exit 0
}
trap term SIGTERM SIGINT

BACKGROUND_TASKS_STARTED=0
backoff=2
while true; do
    bashio::log.info "Starting go2rtc (RTSP :${GO2RTC_RTSP_PORT}, WebRTC :${GO2RTC_WEBRTC_PORT}, API/UI :${GO2RTC_API_PORT}, log=${LOG_LEVEL})..."
    started=$(date +%s)
    ./bin/go2rtc -config "${CONFIG_PATH}" &
    GO2RTC_PID=$!

    if [ "${BACKGROUND_TASKS_STARTED}" -eq 0 ]; then
        start_relogin_timer
        start_adaptive_warmer
        BACKGROUND_TASKS_STARTED=1
    fi

    set +o errexit
    wait "${GO2RTC_PID}"
    rc=$?
    set -o errexit

    if [ "${rc}" -eq 0 ] || [ "${rc}" -eq 143 ]; then
        bashio::log.info "go2rtc exited cleanly (rc=${rc}). Done."
        break
    fi

    now=$(date +%s)
    if [ "$((now - started))" -ge 60 ]; then
        backoff=2
    fi

    bashio::log.warning "go2rtc exited unexpectedly (rc=${rc}); restarting in ${backoff}s."
    sleep "${backoff}"
    backoff=$(( backoff * 2 ))
    [ "${backoff}" -gt 60 ] && backoff=60
done
