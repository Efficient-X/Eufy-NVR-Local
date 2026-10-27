/* Local candidate: one native HA WebRTC player, several canvas crops.
 * No credentials, external services, HLS fallback or extra camera producers.
 * Uses HA frontend elements; must be checked against the installed HA version.
 */
export function validateConfig(config) {
  if (!config || !/^camera\.[a-z0-9_]+$/.test(config.entity || '')) {
    throw new Error('An existing camera entity is required');
  }
  const columns = config.grid_columns ?? 2;
  const rows = config.grid_rows ?? 2;
  const tileColumns = config.tile_columns ?? null;
  if (tileColumns !== null && (!Number.isInteger(tileColumns) || tileColumns < 1 || tileColumns > 4)) {
    throw new Error('Tile display columns must be an integer from 1 to 4');
  }
  if (![columns, rows].every(n => Number.isInteger(n) && n >= 1 && n <= 4)) {
    throw new Error('Grid dimensions must be integers from 1 to 4');
  }
  const tiles = config.tiles ?? [0, 1, 2].map(index => ({ index, name: `View ${index + 1}` }));
  if (!Array.isArray(tiles) || !tiles.length || tiles.length > 16 ||
      tiles.some(t => !Number.isInteger(t.index) || t.index < 0 || t.index >= columns * rows) ||
      new Set(tiles.map(t => t.index)).size !== tiles.length) {
    throw new Error('Each tile needs a unique valid grid index');
  }
  return { entity: config.entity, title: String(config.title ?? 'Local cameras'),
    columns, rows, tileColumns, tiles: tiles.map(t => ({ index: t.index, name: String(t.name ?? `View ${t.index + 1}`) })),
    drawFps: Math.min(10, Math.max(1, Number(config.draw_fps) || 5)) };
}

export function cropRect(width, height, columns, rows, index) {
  const x = Math.floor((index % columns) * width / columns);
  const y = Math.floor(Math.floor(index / columns) * height / rows);
  const right = Math.floor(((index % columns) + 1) * width / columns);
  const bottom = Math.floor((Math.floor(index / columns) + 1) * height / rows);
  return [x, y, right - x, bottom - y];
}

export function automaticConfig(config, hass) {
  if (config.layout !== 'auto') return config;
  const state = hass?.states?.[config.entity];
  const layout = state?.attributes?.eufy_grid_layout;
  if (state?.attributes?.stream_name !== 'eufy_shared_grid' || !layout ||
      layout.version !== 1 || ![2, 3, 4].includes(layout.grid_columns) ||
      layout.grid_rows !== layout.grid_columns || !Array.isArray(layout.tiles) ||
      layout.tiles.some(t => !t || typeof t.name !== 'string' || !t.name.trim() ||
        t.name.length > 160 || /[\x00-\x1f\x7f]/.test(t.name))) return null;
  return { ...config, grid_columns: layout.grid_columns, grid_rows: layout.grid_rows,
    tile_columns: config.tile_columns ?? 2, tiles: layout.tiles };
}

function activeVideo(root) {
  for (const element of root.querySelectorAll('*')) {
    if (element.tagName === 'VIDEO' && element.readyState >= 2 &&
        element.videoWidth && !element.paused) return element;
    if (element.shadowRoot) {
      const found = activeVideo(element.shadowRoot);
      if (found) return found;
    }
  }
  return null;
}

export class EufySharedTiles extends HTMLElement {
  static getConfigElement() { return document.createElement('eufy-nvr-cameras-editor'); }

  static getStubConfig(hass) {
    const entity = Object.keys(hass?.states || {}).find(id =>
      id.startsWith('camera.') && hass.states[id].attributes?.stream_name === 'eufy_shared_grid');
    return { type: 'custom:eufy-nvr-cameras', entity: entity || '',
      layout: 'auto', title: 'NVR cameras', tile_columns: 2, draw_fps: 5 };
  }
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._visible = false;
    this._generation = 0;
    this._onVisibility = () => this._sync();
  }

  setConfig(config) {
    if (config?.layout !== 'auto') validateConfig(config);
    else if (!/^camera\.[a-z0-9_]+$/.test(config.entity || '')) {
      throw new Error('Choose the NVR shared-grid camera');
    }
    this._rawConfig = config;
    this._configKey = undefined;
    this._applyConfig();
  }

  _applyConfig() {
    if (!this._rawConfig) return;
    let resolved = automaticConfig(this._rawConfig, this._hass), validated = null;
    try { if (resolved) validated = validateConfig(resolved); } catch (_) { validated = null; }
    const key = JSON.stringify(validated);
    if (key === this._configKey) return;
    this._stop();
    this._configKey = key;
    this._config = validated;
    if (validated) this._render();
    else {
      this.shadowRoot.replaceChildren();
      const message = document.createElement('ha-card');
      message.style.padding = '16px';
      message.textContent = 'Waiting for the NVR camera layout. Check that the matching app and integration are running.';
      this.shadowRoot.append(message);
    }
    if (this.isConnected) this._sync();
  }

  set hass(value) {
    this._hass = value;
    this._applyConfig();
    // Legacy HA elements receive hass; current elements consume HA DOM context.
    if (this._player) this._player.hass = value;
    if (this._config && this.isConnected) this._sync();
  }

  connectedCallback() {
    document.addEventListener('visibilitychange', this._onVisibility);
    this._observer = new IntersectionObserver(entries => {
      this._visible = entries.some(e => e.isIntersecting);
      this._sync();
    });
    this._observer.observe(this);
  }

  disconnectedCallback() {
    document.removeEventListener('visibilitychange', this._onVisibility);
    this._observer?.disconnect();
    this._observer = undefined;
    this._stop();
  }

  getCardSize() { return this._config ? 2 + this._config.tiles.length * 2 : 4; }

  get _popupOpen() {
    return Boolean(this._usesPopover ? this._dialog?.matches(':popover-open') : this._dialog?.open);
  }

  _render() {
    // Keep enlargement non-modal so the original source is not made inert.
    // Keep its tiny playback surface inside the viewport as well: scrolling
    // the source offscreen pauses HEVC playback in the tested Chromium path.
    this._usesPopover = typeof HTMLElement.prototype.showPopover === 'function';
    const popupTag = this._usesPopover ? 'div' : 'dialog';
    const tileTemplate = this._config.tileColumns === null ?
      'repeat(auto-fit,minmax(min(230px,100%),1fr))' : `repeat(${this._config.tileColumns},minmax(0,1fr))`;
    this.shadowRoot.innerHTML = `<style>
      :host{display:block}ha-card{display:block;position:relative;padding:12px;overflow:hidden}
      h2{margin:0 0 8px;font-size:18px}.toolbar{display:flex;gap:12px;align-items:center;margin-bottom:10px}
      .status{font-size:12px;color:var(--secondary-text-color,#aaa)}
      .tiles{display:grid;grid-template-columns:${tileTemplate};gap:10px}
      .tile{min-width:0;padding:0;background:#111;color:#fff;border:1px solid #555;border-radius:10px;overflow:hidden;cursor:pointer}
      canvas{display:block;width:100%;aspect-ratio:16/9;background:#111}.label{display:block;padding:8px;text-align:left}
      .zoom{box-sizing:border-box;position:fixed;inset:0;margin:auto;width:min(96vw,1440px);height:fit-content;max-width:96vw;max-height:94vh;padding:12px;border:1px solid #666;border-radius:12px;background:var(--ha-card-background,#111);color:var(--primary-text-color,#fff)}
      .zoom::backdrop{background:rgba(0,0,0,.85)}.dialogbar{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:10px}
      .zoom canvas{width:100%;max-height:calc(90vh - 70px);object-fit:contain}button{touch-action:manipulation}
      .source{position:fixed;top:1px;left:1px;width:2px;height:2px;overflow:hidden;opacity:.001;pointer-events:none}
      .source ha-web-rtc-player{display:block;width:2px;height:2px}
    </style><ha-card><h2></h2><div class="toolbar"><span class="status" role="status">Paused</span><button class="retry" type="button">Reconnect</button></div><div class="source" aria-hidden="true"></div><div class="tiles"></div></ha-card><${popupTag} class="zoom" ${this._usesPopover ? 'popover="auto"' : ''} role="dialog" aria-label="Enlarged camera"><div class="dialogbar"><strong></strong><button class="close" type="button" autofocus>Close camera</button></div><canvas></canvas></${popupTag}>`;
    this.shadowRoot.querySelector('h2').textContent = this._config.title;
    this.shadowRoot.querySelector('.retry').onclick = () => { this._stop(); this._sync(); };
    this._dialog = this.shadowRoot.querySelector('.zoom');
    this.shadowRoot.querySelector('.close').onclick = () => this._closeTile();
    this._dialog.oncancel = event => { event.preventDefault(); this._closeTile(); };
    this._dialog.onclose = () => { this._expanded = undefined; this._sync(); };
    this._dialog.ontoggle = event => {
      if (event.newState === 'closed') {
        this._expanded = undefined;
        this._returnFocus?.focus();
        this._returnFocus = undefined;
        this._sync();
      }
    };
    this._canvases = this._config.tiles.map(tile => {
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'tile';
      button.setAttribute('aria-label', `Enlarge ${tile.name}`);
      const canvas = document.createElement('canvas');
      const label = document.createElement('span');
      label.className = 'label'; label.textContent = tile.name;
      button.append(canvas, label);
      button.setAttribute('aria-haspopup', 'dialog');
      button.onclick = () => this._openTile(tile, button);
      this.shadowRoot.querySelector('.tiles').append(button);
      return { canvas, tile };
    });
  }

  _openTile(tile, button) {
    if (typeof this._dialog?.[this._usesPopover ? 'showPopover' : 'showModal'] !== 'function') {
      this._status('This browser needs an update to open enlarged cameras');
      return;
    }
    this._expanded = tile;
    this._returnFocus = button;
    this._dialog.querySelector('strong').textContent = tile.name;
    this._dialog.setAttribute('aria-label', tile.name);
    if (!this._popupOpen) {
      if (this._usesPopover) this._dialog.showPopover();
      else this._dialog.showModal();
    }
    // The popup reuses the same decoded video; no second HA/NVR connection.
    this._sync();
  }

  _closeTile() {
    this._expanded = undefined;
    if (this._popupOpen) {
      if (this._usesPopover) this._dialog.hidePopover();
      else this._dialog.close();
    }
    this._returnFocus?.focus();
    this._returnFocus = undefined;
  }

  _status(text) {
    const status = this.shadowRoot.querySelector('.status');
    if (status) status.textContent = text;
  }

  _sync() {
    const state = this._hass?.states?.[this._config?.entity];
    if (!this.isConnected || (!this._visible && !this._popupOpen) || document.hidden || !state ||
        ['unavailable', 'unknown'].includes(state.state)) {
      this._stop();
      return;
    }
    if (!this._player && !this._starting) this._start();
  }

  async _start() {
    const generation = ++this._generation;
    this._starting = true;
    this._status('Connecting live video…');
    try {
      if (!customElements.get('ha-web-rtc-player')) {
        const helpers = await window.loadCardHelpers?.();
        if (!helpers) throw new Error('HA player is not loaded');
        // Load native modules without inserting a dummy card/starting a stream.
        helpers.createCardElement({ type: 'picture-entity', entity: this._config.entity });
        await Promise.race([customElements.whenDefined('ha-web-rtc-player'),
          new Promise((_, reject) => setTimeout(() => reject(new Error('Player load timeout')), 10000))]);
      }
      if (generation !== this._generation || !this.isConnected || document.hidden || !this._visible) return;
      const player = document.createElement('ha-web-rtc-player');
      player.hass = this._hass;
      player.entityid = this._config.entity;
      player.muted = true; player.controls = false;
      player.setAttribute('autoplay', ''); player.setAttribute('playsinline', '');
      this._player = player;
      this.shadowRoot.querySelector('.source').append(player);
      this._draw();
    } catch (_) {
      if (generation === this._generation) this._status('Live video unavailable — reconnect to retry');
    } finally {
      if (generation === this._generation) this._starting = false;
    }
  }

  _draw() {
    if (!this._player) return;
    const video = activeVideo(this._player.shadowRoot || this._player);
    if (video) {
      if (video !== this._lastVideo) {
        if (this._lastVideo && this._frameHandle != null) this._lastVideo.cancelVideoFrameCallback?.(this._frameHandle);
        this._lastAdvance = performance.now();
        this._lastVideo = video;
        this._trackFrames(video);
      }
      if (this._frameTracking && performance.now() - this._lastAdvance > 3000) {
        this._status('Video stalled — reconnect to retry');
        this._timer = setTimeout(() => this._draw(), 1000 / this._config.drawFps);
        return;
      }
      let drawn = true;
      const targets = this._popupOpen && this._expanded ?
        [{ canvas: this._dialog.querySelector('canvas'), tile: this._expanded }] : this._canvases;
      for (const { canvas, tile } of targets) {
        const rect = cropRect(video.videoWidth, video.videoHeight,
          this._config.columns, this._config.rows, tile.index);
        const width = Math.min(640, rect[2]);
        const height = Math.round(width * rect[3] / rect[2]);
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
        try { canvas.getContext('2d', { alpha: false }).drawImage(video, ...rect, 0, 0, width, height); }
        catch (_) { drawn = false; }
      }
      this._status(drawn ? (this._frameTracking ? 'Live · one shared connection · tap a tile to enlarge' :
        'Video playing · freshness unavailable in this browser') : 'Video drawing unavailable');
    } else {
      // Never label a cached poster or a frozen canvas as live.
      this._status('Waiting for live video…');
    }
    this._timer = setTimeout(() => this._draw(), 1000 / this._config.drawFps);
  }

  _trackFrames(video) {
    this._frameTracking = typeof video.requestVideoFrameCallback === 'function';
    if (!this._frameTracking) return;
    const presented = () => {
      if (this._lastVideo !== video || !this._player) return;
      this._lastAdvance = performance.now();
      this._frameHandle = video.requestVideoFrameCallback(presented);
    };
    this._frameHandle = video.requestVideoFrameCallback(presented);
  }

  _stop() {
    ++this._generation;
    this._starting = false;
    clearTimeout(this._timer);
    this._timer = undefined;
    if (this._lastVideo && this._frameHandle != null) this._lastVideo.cancelVideoFrameCallback?.(this._frameHandle);
    this._frameHandle = undefined;
    this._player?.remove(); // Native disconnectedCallback closes WebRTC/subscription.
    this._player = undefined;
    this._lastVideo = undefined;
    this._closeTile();
    for (const { canvas } of this._canvases || []) canvas.getContext('2d')?.clearRect(0, 0, canvas.width, canvas.height);
    this._status('Paused');
  }
}

export class EufySharedTilesEditor extends HTMLElement {
  setConfig(config) { this._config = { ...config }; this._render(); }
  set hass(value) { this._hass = value; this._render(); }
  _render() {
    if (!this._config || !this._hass) return;
    this.replaceChildren();
    const label = document.createElement('label');
    label.textContent = 'NVR camera group ';
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'NVR camera group');
    const empty = document.createElement('option');
    empty.value = ''; empty.textContent = 'Choose your recorder';
    select.append(empty);
    for (const [id, state] of Object.entries(this._hass.states || {})) {
      if (!id.startsWith('camera.') || state.attributes?.stream_name !== 'eufy_shared_grid') continue;
      const option = document.createElement('option');
      option.value = id; option.textContent = state.attributes.friendly_name || id;
      select.append(option);
    }
    select.value = this._config.entity || '';
    select.addEventListener('change', () => {
      if (!select.value) return;
      const { grid_columns, grid_rows, tiles, ...rest } = this._config;
      this._config = { ...rest, entity: select.value, layout: 'auto' };
      this.dispatchEvent(new CustomEvent('config-changed', {
        detail: { config: this._config }, bubbles: true, composed: true }));
    });
    label.append(select); this.append(label);
    const help = document.createElement('p');
    help.textContent = 'All fixed and tracking views share one player. Their names and positions come from the recorder. Click a tile to enlarge it.';
    this.append(help);
  }
}
if (!customElements.get('eufy-shared-tiles')) customElements.define('eufy-shared-tiles', EufySharedTiles);
// A distinct picker type avoids an older manually installed shared-tiles module
// silently winning the custom-element name and ignoring automatic layout.
export class EufyNvrCameras extends EufySharedTiles {}
if (!customElements.get('eufy-nvr-cameras')) customElements.define('eufy-nvr-cameras', EufyNvrCameras);
if (!customElements.get('eufy-nvr-cameras-editor')) customElements.define('eufy-nvr-cameras-editor', EufySharedTilesEditor);
if (typeof window !== 'undefined') {
  window.customCards = window.customCards || [];
  if (!window.customCards.some(card => card.type === 'eufy-nvr-cameras')) {
    window.customCards.push({ type: 'eufy-nvr-cameras', name: 'Eufy NVR cameras',
      description: 'All fixed and tracking views in one efficient live card', preview: true });
  }
}
