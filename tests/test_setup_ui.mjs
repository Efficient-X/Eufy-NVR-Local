import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../bridge/setup_ui/setup.js', import.meta.url), 'utf8');
const html = fs.readFileSync(new URL('../bridge/setup_ui/index.html', import.meta.url), 'utf8');
const pendingTimers = new Map();
let timerId = 0;
const context = vm.createContext({
  AbortSignal,
  setTimeout(fn) { pendingTimers.set(++timerId, fn); return timerId; },
  clearTimeout(id) { pendingTimers.delete(id); },
});
vm.runInContext(source.replaceAll('export ', '') + '\nglobalThis.api={mountSetup,explainStatus,explainRuntime,MESSAGES};', context);
const { mountSetup, explainStatus, explainRuntime, MESSAGES } = context.api;
assert.match(explainStatus({state: 'complete'}), /not been tested/);
assert.match(explainStatus({state: 'rate_limited', retry_after_seconds: 61}), /2 minute/);
assert.match(MESSAGES.storage_failed, /old connection was not replaced/);
assert.match(MESSAGES.protection_unavailable, /restarting repeatedly will not help/);
assert.match(explainRuntime({state: 'connection_unavailable'}), /details are kept/);
assert.match(explainRuntime({state: 'sign_in_required'}), /restarting Home Assistant will not/);
assert.match(explainRuntime({state: 'local_video_setup_required'}), /only if none exists/);
assert.match(explainRuntime({state: 'local_video_setup_required'}), /not replace it/);
assert.match(explainRuntime({state: 'video_service_running'}), /check that the picture is live/);
assert.ok(!source.includes('innerHTML'));
for (const forbidden of ['localStorage', 'sessionStorage', 'console.log', 'http://', 'https://']) {
  assert.ok(!source.includes(forbidden), forbidden);
}
assert.ok(html.includes('different from the app&#39;s') || html.includes("different from the app's"));
assert.ok(html.includes('not a fully offline connection'));
assert.ok(html.includes('Saving replaces'));
assert.ok(!/<script[^>]+src=["']https?:/.test(html));
assert.ok(!/<form[^>]+action=/.test(html));
assert.ok(html.includes('aria-live="polite"'));
assert.ok(html.includes('The app does not encrypt this saved password'));
assert.ok(html.includes('not from older backups'));
assert.ok(!/id="remember"[^>]*\\bchecked\\b/.test(html));

class Element {
  constructor() { this.value = ''; this.hidden = false; this.children = []; this.events = {}; this.textContent = ''; }
  addEventListener(name, fn) { this.events[name] = fn; }
  removeAttribute(name) { delete this[name]; }
  replaceChildren() { this.children = []; this.value = ''; }
  append(node) { this.children.push(node); }
  reportValidity() { return true; }
}
const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
const buttons = [new Element(), new Element()];
const doc = {
  getElementById(id) { assert.ok(nodes.has(id), 'Unknown HTML id ' + id); return nodes.get(id); },
  createElement() { return new Element(); },
  querySelectorAll() { return buttons; },
};
const requests = [];
let responses = [{ session: 'session1', csrf: 'csrf1', status: {state: 'credentials'} }];
const request = async (url, options) => {
  requests.push({url, options});
  const result = responses.shift();
  if (typeof result === 'function') return result();
  return { ok: true, status: 200, json: async () => result };
};
const ui = mountSetup(doc, request);
await new Promise(resolve => setImmediate(resolve));
assert.equal(requests[0].url, 'api/session');
assert.equal(requests[0].options.redirect, 'error');
assert.equal(nodes.get('credentials').hidden, false);

nodes.get('password').value = 'test-password';
nodes.get('code').value = '123456';
ui.clearSensitive();
assert.equal(nodes.get('password').value, '');
assert.equal(nodes.get('code').value, '');
ui.render({state: 'select_nvr', stations: [{id: 'opaque', name: '<img src=x onerror=alert(1)>'}]});
assert.equal(nodes.get('selection').children[1].textContent, '<img src=x onerror=alert(1)>');
assert.equal(nodes.get('selection').children[1].value, 'opaque');
assert.equal(nodes.get('recorder').hidden, false);
assert.equal(nodes.get('credentials').hidden, true);
ui.render({state: 'captcha', captcha_image: 'data:image/png;base64,example'});
assert.equal(nodes.get('captcha-image').src, 'data:image/png;base64,example');
ui.render({state: 'complete'});
assert.equal(nodes.get('captcha-image').src, undefined);
assert.equal(nodes.get('complete').hidden, false);
assert.equal(nodes.get('cancel').disabled, true);
ui.render({state: 'rate_limited', retry_after_seconds: 60});
assert.equal(nodes.get('credentials').hidden, true);
ui.render({state: 'rate_limited'});
assert.equal(nodes.get('credentials').hidden, false);
ui.render({state: 'credentials', runtime: {state: 'video_service_running', can_setup: false, can_retry: false}});
assert.equal(nodes.get('credentials').hidden, true);
assert.equal(nodes.get('service').hidden, false);
assert.equal(nodes.get('retry-service').hidden, true);
ui.render({state: 'credentials', runtime: {state: 'video_service_running', remembered: true, renewal_paused: false}});
assert.equal(nodes.get('forget-sign-in').hidden, false);
assert.match(nodes.get('remembered-status').textContent, /current access token/);
ui.render({state: 'credentials', runtime: {state: 'sign_in_required', remembered: true, renewal_paused: true, can_setup: true}});
assert.match(nodes.get('remembered-status').textContent, /paused/);
nodes.get('remember').checked = true;
ui.clearSensitive();
assert.equal(nodes.get('remember').checked, false);
ui.render({state: 'complete', runtime: {state: 'service_error', can_setup: false, can_retry: true}});
assert.equal(nodes.get('retry-service').hidden, false);
assert.match(nodes.get('service-status').textContent, /not keep restarting/);

responses.push({state: 'email_code'});
await ui.send('start', {email: 'owner@example.com', password: 'test-password', country: 'AU', region: 'US'});
const sent = requests.at(-1);
assert.equal(sent.url, 'api/start');
assert.equal(sent.options.headers['X-Setup-CSRF'], 'csrf1');
assert.equal(sent.options.headers['X-Setup-Session'], 'session1');
assert.equal(nodes.get('email-code').hidden, false);
assert.equal(nodes.get('password').value, '');

// A delayed response from an old request must not replace the newer Cancel result.
let release;
responses.push(() => new Promise(resolve => { release = resolve; }));
const old = ui.send('verify', {code: '123456'});
await new Promise(resolve => setImmediate(resolve));
responses.push({state: 'cancelled'});
await ui.send('cancel');
release({ok: true, status: 200, json: async () => ({state: 'select_nvr', stations: []})});
await old;
assert.equal(nodes.get('recorder').hidden, true);
assert.equal(nodes.get('reopen').hidden, false);
assert.match(nodes.get('status').textContent, /cancelled/);

// An expired session must not keep a login form or its password on screen.
responses.push(() => ({ok: false, status: 401, json: async () => ({error: 'session_expired'})}));
await ui.send('status');
assert.equal(nodes.get('credentials').hidden, true);
assert.equal(nodes.get('reopen').hidden, false);
assert.match(nodes.get('error').textContent, /closed/);
console.log('Setup wording, safe rendering, session headers, cancellation and expiry checks passed.');

