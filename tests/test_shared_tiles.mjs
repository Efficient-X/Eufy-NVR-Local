import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Pure geometry/validation tests: no browser, HA instance or camera access.
const source = fs.readFileSync(new URL('../frontend/eufy-shared-tiles.js', import.meta.url), 'utf8');
const context = vm.createContext({ HTMLElement: class {}, customElements: { get: () => true } });
vm.runInContext(source.replaceAll('export ', '') + '\nglobalThis.api={validateConfig,cropRect,EufySharedTiles,automaticConfig};', context);
const { validateConfig, cropRect } = context.api;
const plain = value => JSON.parse(JSON.stringify(value));
assert.deepEqual(plain(cropRect(1280, 720, 2, 2, 0)), [0, 0, 640, 360]);
assert.deepEqual(plain(cropRect(1280, 720, 2, 2, 1)), [640, 0, 640, 360]);
assert.deepEqual(plain(cropRect(1280, 720, 2, 2, 2)), [0, 360, 640, 360]);
assert.deepEqual(plain(cropRect(1280, 720, 2, 2, 3)), [640, 360, 640, 360]);
assert.throws(() => validateConfig({ entity: 'rtsp://unsafe.example' }));
assert.throws(() => validateConfig({ entity: 'camera.test', tiles: [{ index: 4 }] }));
assert.throws(() => validateConfig({ entity: 'camera.test', tiles: [{ index: 0 }, { index: 0 }] }));
assert.throws(() => validateConfig({ entity: 'camera.test', grid_columns: 0 }));
assert.equal(validateConfig({ entity: 'camera.test', draw_fps: 100 }).drawFps, 10);
assert.equal(validateConfig({ entity: 'camera.test' }).tiles.length, 3);
assert.equal(validateConfig({ entity: 'camera.test' }).tileColumns, null);
assert.equal(validateConfig({ entity: 'camera.test', tile_columns: 2 }).tileColumns, 2);
for (const tile_columns of [0, 5, -1, 1.5, '2', NaN]) {
  assert.throws(() => validateConfig({ entity: 'camera.test', tile_columns }));
}
const both = validateConfig({ entity: 'camera.test', grid_columns: 3, grid_rows: 3,
  tiles: [0, 2, 1, 3, 4, 6, 5, 7].map(index => ({ index, name: `Lens ${index}` })) });
assert.equal(both.tiles.length, 8);
const full = validateConfig({ entity: 'camera.test', grid_columns: 4, grid_rows: 4,
  tiles: Array.from({ length: 16 }, (_, index) => ({ index, name: `Lens ${index}` })) });
assert.equal(full.tiles.length, 16);
for (let index = 0; index < 16; index++) {
  assert.deepEqual(plain(cropRect(1920, 1080, 4, 4, index)),
    [(index % 4) * 480, Math.floor(index / 4) * 270, 480, 270]);
}
assert.throws(() => validateConfig({ entity: 'camera.test', grid_columns: 4, grid_rows: 4,
  tiles: Array.from({ length: 17 }, (_, index) => ({ index })) }));
assert.deepEqual(plain(cropRect(1280, 720, 3, 3, 0)), [0, 0, 426, 240]);
assert.deepEqual(plain(cropRect(1280, 720, 3, 3, 7)), [426, 480, 427, 240]);
assert.equal((source.match(/document.createElement\('ha-web-rtc-player'\)/g) || []).length, 1);
assert.ok(!source.includes("document.createElement('video')"));
assert.ok(source.includes('this._player?.remove()'));
// The one hidden decoding surface must not scroll offscreen with lower tiles.
// Hardware playback acceptance is separate from this CSS regression check.
assert.ok(source.includes('.source{position:fixed;top:1px;left:1px;width:2px;height:2px;'));
// Exercise actual popup methods. Enlarging must reuse the existing player.
const card = Object.create(context.api.EufySharedTiles.prototype);
let synced = 0, focused = 0, title = '';
card._player = { existing: true };
const player = card._player;
card._sync = () => synced++;
card._dialog = { open: false, querySelector: () => ({ set textContent(v) { title = v; } }),
  setAttribute() {}, showModal() { this.open = true; }, close() { this.open = false; } };
const tile = { index: 7, name: 'Garage tracking' };
card._openTile(tile, { focus() { focused++; } });
assert.equal(card._dialog.open, true);
assert.equal(card._expanded, tile);
assert.equal(title, 'Garage tracking');
assert.equal(synced, 1);
assert.equal(card._player, player);
card._closeTile();
assert.equal(card._dialog.open, false);
assert.equal(card._expanded, undefined);
assert.equal(focused, 1);
assert.equal(card._player, player);
// Top-layer non-modal popovers must preserve the same player as well. Unlike
// showModal, they do not make the external HEVC source inert.
let showing = false, shows = 0, hides = 0;
card._usesPopover = true;
card._dialog = { querySelector: () => ({ set textContent(v) { title = v; } }),
  setAttribute() {}, matches(selector) { assert.equal(selector, ':popover-open'); return showing; },
  showPopover() { showing = true; shows++; }, hidePopover() { showing = false; hides++; } };
card._openTile(tile, { focus() { focused++; } });
assert.equal(card._popupOpen, true);
assert.equal(shows, 1);
assert.equal(card._player, player);
card._closeTile();
assert.equal(card._popupOpen, false);
assert.equal(hides, 1);
assert.equal(card._player, player);
assert.ok(source.includes('popover="auto"'));
assert.ok(source.includes("event.newState === 'closed'"));
console.log('Shared tile geometry, validation, modal lifecycle and one-player checks passed.');

// Automatic layout uses only the selected shared-grid entity, not guessed crops.
const auto = { entity: 'camera.nvr', layout: 'auto' };
const metadata = { version: 1, grid_columns: 4, grid_rows: 4,
  tiles: Array.from({ length: 16 }, (_, index) => ({ index, name: 'Lens ' + index })) };
const hass = { states: { 'camera.nvr': { state: 'idle',
  attributes: { stream_name: 'eufy_shared_grid', eufy_grid_layout: metadata } } } };
assert.equal(context.api.automaticConfig(auto, {}), null);
assert.equal(validateConfig(context.api.automaticConfig(auto, hass)).tiles.length, 16);
assert.equal(context.api.EufySharedTiles.getStubConfig(hass).entity, 'camera.nvr');
assert.equal(context.api.EufySharedTiles.getStubConfig(hass).layout, 'auto');
const wrong = structuredClone(hass);
wrong.states['camera.nvr'].attributes.stream_name = 'eufy_individual';
assert.equal(context.api.automaticConfig(auto, wrong), null);
const invalid = structuredClone(hass);
invalid.states['camera.nvr'].attributes.eufy_grid_layout.tiles[0].name = 'bad\nname';
assert.equal(context.api.automaticConfig(auto, invalid), null);
const updating = Object.create(context.api.EufySharedTiles.prototype);
let stops = 0, renders = 0, waits = 0;
updating._stop = () => stops++;
updating._render = () => renders++;
updating.shadowRoot = { replaceChildren() {}, append() { waits++; } };
context.document = { createElement: () => ({ style: {} }) };
updating.hass = hass;
updating.setConfig(auto);
assert.equal(renders, 1);
updating.hass = structuredClone(hass);
assert.equal(stops, 1); // Normal HA state updates do not restart the player.
const fewer = structuredClone(hass);
fewer.states['camera.nvr'].attributes.eufy_grid_layout.tiles.pop();
updating.hass = fewer;
assert.equal(renders, 2);
updating.hass = { states: {} };
assert.equal(updating._config, null);
assert.equal(waits, 1);
assert.ok(source.includes("name: 'Eufy NVR cameras'"));
console.log('Automatic layout, missing metadata and stable state-update checks passed.');
