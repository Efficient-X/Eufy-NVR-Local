// No browser storage, analytics, external scripts, or credentials in URLs.
export const MESSAGES = Object.freeze({
  invalid_input: "Check the email, password and account country. Nothing has been saved yet.",
  invalid_code: "Enter exactly six numbers from Eufy's email. This is not your account password.",
  login_not_accepted: "Eufy did not accept this sign-in step. Check the details for this step before trying again. Do not keep guessing.",
  connection_failed: "We could not finish contacting Eufy. Check your internet connection, then use Check progress before trying again.",
  captcha_unavailable: "Eufy's picture check could not be displayed safely. Cancel and try later. Do not paste a token or private link.",
  no_supported_nvr: "This account did not return a supported T8N00 recorder. Check that you used its owner account.",
  nvr_owner_identity_unavailable: "We could not confirm permission to control this recorder. Use its owner account, not a shared guest account.",
  invalid_selection: "Choose a recorder from this list. Nothing has been saved.",
  storage_failed: "The app could not save the connection. Check Home Assistant's free space, then try saving again. Your old connection was not replaced.",
  protection_unavailable: "The sign-in safety check could not be saved or read. Stop here and ask for support; restarting repeatedly will not help.",
  session_expired: "This setup session has closed. Open setup again to continue. An already saved connection is not deleted.",
  access_denied: "Open this page through Home Assistant using an approved administrator account. A direct link to the app is not allowed.",
  other_setup_active: "Someone else is setting up this recorder connection. Wait for them to finish so you do not overwrite each other's choice.",
  request_too_large: "The information sent was too large. Enter only the requested account details.",
  setup_busy: "Setup is busy. Wait a moment, then check progress.",
  setup_unavailable: "Setup could not complete that step. Check progress before trying again.",
  invalid_request: "This request was not accepted. Open setup again.",
  home_assistant_check_unavailable: "We could not check your Home Assistant permissions. Wait until Home Assistant has finished starting, then open setup again.",
  home_assistant_url_missing: "Home Assistant has no saved website address for setup to check. Ask an administrator to check its internal or external URL under Network settings. Do not paste a private access token.",
});
export function explainRuntime(runtime) {
  const message = ({
    local_video_setup_required: "Set up the local video password first. In Home Assistant, open Settings → Devices & services → Add integration → Eufy NVR → Connect installed Eufy NVR app. Select this app and follow the confirmation. It creates a random local password only if none exists, then restarts this app—not Home Assistant. If a password already exists but is invalid, check the app Configuration; it will not replace it.",
    checking_account: "Checking the saved sign-in. You do not need to type the password again while this check runs.",
    renewing_sign_in: "Renewing access using the sign-in you chose to remember. A picture check or email code will stop automatic renewal and ask you to sign in here.",
    setup_required: "There is no usable saved connection. Sign in below to connect your recorder.",
    sign_in_required: "Eufy rejected the saved sign-in. Sign in below again; restarting Home Assistant will not renew it.",
    starting_video: "Starting the camera service. This may take up to two minutes.",
    video_service_running: "The camera service is running. Open a camera tile to check that the picture is live.",
    connection_unavailable: "We cannot check the saved sign-in right now. Your details are kept. Check the internet connection; the app will retry later without guessing your password.",
    service_error: "The camera service could not start or stopped. It will not keep restarting in a loop. Check the app log, then try the connection again.",
    stopped: "The camera service has stopped.",
  })[runtime.state] || "Checking the camera service.";
  return message + (runtime.warning === "account_check_unavailable" && runtime.state === "video_service_running"
    ? " Eufy's sign-in check is temporarily unavailable; the running service has been left alone." : "");
}
export function explainStatus(status) {
  if (status.retry_after_seconds > 0) {
    return "Sign-in is paused to protect your account. Wait at least " +
      Math.ceil(status.retry_after_seconds / 60) + " minute(s) before trying again. " +
      "This is a safety delay, not a promise that Eufy will accept the next attempt.";
  }
  return ({
    credentials: "Ready to sign in. Nothing is changed until you choose and save a recorder.",
    connecting: "Checking with Eufy. This can take up to 45 seconds.",
    busy: "A check is already running. Wait, then check progress.",
    captcha: "Eufy needs a picture check.",
    email_code: "Eufy needs a code from your email.",
    select_nvr: "Sign-in worked. Choose the recorder you want to use.",
    complete: "Account connection saved. Video has not been tested by this setup.",
    cancelled: "Setup cancelled. Any previously saved connection is still there.",
    expired: "Setup timed out. Open it again when you are ready.",
    rate_limited: "The local safety delay has ended. Check your details before trying again; Eufy may still ask you to wait.",
  })[status.state] || "Check progress to see which step comes next.";
}

export function mountSetup(doc, request = globalThis.fetch.bind(globalThis)) {
  const $ = id => doc.getElementById(id);
  let session = null, busy = false, expiryTimer, retryTimer, sequence = 0;
  function clearSensitive() {
    for (const id of ["password", "answer", "code"]) $(id).value = "";
    $("remember").checked = false;
  }
  function render(status) {
    const state = status.state;
    const waiting = Number(status.retry_after_seconds) > 0;
    const runtime = status.runtime;
    const setupAllowed = !runtime || runtime.can_setup;
    $("service").hidden = !runtime;
    $("service-status").textContent = runtime ? explainRuntime(runtime) : "";
    $("retry-service").hidden = !runtime?.can_retry;
    $("forget-sign-in").hidden = !runtime?.remembered;
    $("remembered-status").hidden = !runtime?.remembered;
    $("remembered-status").textContent = runtime?.remembered
      ? (runtime.state === "renewing_sign_in" ? "Automatic renewal is in progress. You can still forget the saved sign-in."
        : runtime.renewal_paused ? "Automatic renewal is paused. Video may continue until sign-in is needed. When the sign-in form appears, complete Eufy's checks; remembering is off by default for the new attempt."
        : "Sign-in is remembered for automatic renewal. Forgetting it keeps the current access token and camera settings, but future expiry will need you to sign in.")
      : "";
    for (const [id, visible] of Object.entries({
      credentials: ["credentials", "rate_limited"].includes(state) && !waiting,
      captcha: state === "captcha" && !waiting,
      "email-code": state === "email_code" && !waiting,
      recorder: state === "select_nvr",
      complete: state === "complete",
    })) $(id).hidden = !visible || (id !== "complete" && !setupAllowed);
    $("status").textContent = runtime && !setupAllowed ? explainRuntime(runtime) : explainStatus(status);
    $("error").hidden = !status.error;
    $("error").textContent = status.error ? (MESSAGES[status.error] || MESSAGES.setup_unavailable) : "";
    $("check").hidden = !session || (!runtime && ["complete", "cancelled", "expired"].includes(state));
    $("reopen").hidden = !["cancelled", "expired"].includes(state) && status.error !== "session_expired";
    $("cancel").disabled = !session || ["complete", "cancelled", "expired"].includes(state);
    if (state === "captcha") $("captcha-image").src = status.captcha_image;
    else $("captcha-image").removeAttribute("src");
    if (state === "select_nvr" && status.stations) {
      const previous = $("selection").value;
      $("selection").replaceChildren();
      const prompt = doc.createElement("option");
      prompt.value = ""; prompt.textContent = "Choose your recorder";
      $("selection").append(prompt);
      for (const station of status.stations) {
        const option = doc.createElement("option");
        option.value = station.id;
        option.textContent = station.name; // Device names are untrusted, never HTML.
        $("selection").append(option);
      }
      if (status.stations.some(s => s.id === previous)) $("selection").value = previous;
    }
    clearTimeout(retryTimer);
    if (waiting) retryTimer = setTimeout(() => send("status"), Math.min(status.retry_after_seconds * 1000, 86400000));
    else if (runtime && ["checking_account", "renewing_sign_in", "starting_video"].includes(runtime.state))
      retryTimer = setTimeout(() => send("status"), 5000);
  }
  async function send(operation, data = {}) {
    if (busy && operation !== "cancel") return;
    const ticket = ++sequence;
    busy = true;
    for (const button of doc.querySelectorAll("[data-action]")) button.disabled = true;
    $("error").hidden = true;
    if (!["status", "cancel"].includes(operation)) $("status").textContent = "Checking with Eufy. Please wait…";
    try {
      const response = await request("api/" + operation, {
        method: operation === "status" ? "GET" : "POST",
        credentials: "same-origin", cache: "no-store", redirect: "error",
        headers: { "Content-Type": "application/json", ...(session ? {
          "X-Setup-Session": session.session, "X-Setup-CSRF": session.csrf,
        } : {}) },
        ...(operation === "status" ? {} : { body: JSON.stringify(data) }),
        signal: AbortSignal.timeout(60000),
      });
      const result = await response.json();
      if (ticket !== sequence) return;
      if (!response.ok) {
        if (response.status === 401) {
          session = null;
          render({ state: "expired", error: "session_expired" });
        } else {
          $("error").textContent = MESSAGES[result.error] || MESSAGES.setup_unavailable;
          $("error").hidden = false;
          $("check").hidden = !session;
        }
        return;
      }
      if (operation === "session") {
        session = result;
        clearTimeout(expiryTimer);
        expiryTimer = setTimeout(() => {
          clearSensitive(); session = null; render({ state: "expired" });
        }, 600000);
        render(result.status);
      } else render(result);
    } catch {
      if (ticket !== sequence) return;
      $("error").textContent = "The reply did not arrive. Do not sign in again yet: use Check progress to see whether the first request worked.";
      $("error").hidden = false;
      $("check").hidden = !session;
      $("reopen").hidden = !!session;
    } finally {
      if (ticket === sequence) {
        clearSensitive();
        busy = false;
        for (const button of doc.querySelectorAll("[data-action]")) button.disabled = false;
      }
    }
  }
  for (const [form, operation, fields] of [
    ["credentials", "start", ["email", "password", "country", "region"]],
    ["captcha", "captcha", ["answer"]],
    ["email-code", "verify", ["code"]],
    ["recorder", "select", ["selection"]],
  ]) $(form).addEventListener("submit", event => {
    event.preventDefault();
    if ($(form).reportValidity()) {
      const values = Object.fromEntries(fields.map(id => [id, $(id).value]));
      if (operation === "start") values.remember = $("remember").checked === true;
      send(operation, values);
      clearSensitive();
    }
  });
  $("cancel").addEventListener("click", () => { clearSensitive(); send("cancel"); });
  $("check").addEventListener("click", () => send("status"));
  $("retry-service").addEventListener("click", () => send("retry"));
  $("forget-sign-in").addEventListener("click", () => { $("remember").checked = false; send("forget"); });
  $("reopen").addEventListener("click", () => send("session"));
  globalThis.addEventListener?.("pagehide", () => {
    ++sequence; clearSensitive(); session = null; clearTimeout(expiryTimer); clearTimeout(retryTimer);
  });
  send("session");
  return { render, send, clearSensitive };
}
if (typeof document !== "undefined") mountSetup(document);

