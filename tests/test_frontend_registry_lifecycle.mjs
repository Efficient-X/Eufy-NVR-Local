import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../frontend/eufy-shared-tiles.js', import.meta.url), 'utf8');
function registry() {
  const entries = new Map();
  return {entries, get: name => entries.get(name), define(name, cls) {
    assert(!entries.has(name), 'duplicate name');
    assert(![...entries.values()].includes(cls), 'constructor reused for another tag');
    entries.set(name, cls);
  }};
}
function api() {
  const context = vm.createContext({HTMLElement: class {}, customElements: registry(), performance});
  vm.runInContext(source.replaceAll('export ', '') +
    '\nglobalThis.api={registerCardElements,ensureCardRegistration};', context);
  return context.api;
}

test('registration follows the replaced registry, not a native whenDefined promise', async () => {
  const {ensureCardRegistration} = api();
  const old = registry(), replacement = registry();
  let current = old, time = 0, ticks = 0;
  old.whenDefined = () => assert.fail('old registry must not own readiness');
  await ensureCardRegistration({getRegistry:()=>current, now:()=>time, delay: async ms => {
    time += ms; ticks++;
    if (time === 500) current = replacement;
    if (time === 1500) replacement.define('home-assistant', class {});
  }});
  assert.equal(ticks, 6);
  assert(replacement.get('eufy-nvr-cameras'));
  assert(replacement.get('eufy-nvr-cameras-editor'));
  assert.equal(old.get('eufy-nvr-cameras'), undefined);
});

test('ready registry registers immediately; repeated calls preserve every existing definition', async () => {
  const {ensureCardRegistration,registerCardElements} = api();
  const current = registry();
  const legacy = class {}, unrelated = class {};
  current.define('home-assistant', class {});
  current.define('eufy-shared-tiles', legacy);
  current.define('unrelated-card', unrelated);
  await ensureCardRegistration({getRegistry:()=>current, delay:()=>assert.fail('no timer needed')});
  const card = current.get('eufy-nvr-cameras');
  registerCardElements(current);
  assert.equal(current.get('eufy-nvr-cameras'),card);
  assert.equal(current.get('eufy-shared-tiles'),legacy);
  assert.equal(current.get('unrelated-card'),unrelated);
});

test('missing HA reaches a bounded named diagnostic without registry mutation or reload', async () => {
  const {ensureCardRegistration} = api();
  const current = registry(); let time = 0, calls = 0;
  await assert.rejects(ensureCardRegistration({getRegistry:()=>current, now:()=>time,
    timeoutMs:1000, delay:async ms=>{time+=ms;calls++;}}), /timed out waiting for Home Assistant/);
  assert.equal(time,1000); assert.equal(calls,4); assert.equal(current.entries.size,0);
});

test('HA appearing at the deadline gets its definitions rather than a false timeout', async () => {
  const {ensureCardRegistration} = api();
  const current = registry(); let time=0;
  await ensureCardRegistration({getRegistry:()=>current, now:()=>time, timeoutMs:500,
    delay:async ms=>{time+=ms;if(time===500)current.define('home-assistant',class {});}});
  assert(current.get('eufy-nvr-cameras')); assert.equal(time,500);
});
