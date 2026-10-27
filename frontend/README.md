# Shared camera tiles — development package

`eufy-shared-tiles.js` displays configurable crops from ONE native HA WebRTC
player. There are no separate server encoders, hardcoded RTSP credentials or
third-party JavaScript downloads. Offscreen/hidden/disconnected cards remove
the player and clear drawing timers. A paused/frozen picture is not labelled live.

This uses Home Assistant's frontend custom elements, which are version-sensitive.
Local synthetic validation is not proof that it works on the installed HA build
or Galaxy tablet. Production registration/dashboard changes require approval.

The matching development integration now bundles and loads this file.
Use **Add card → Eufy NVR cameras**, select the NVR camera group and save.
Its `custom:eufy-nvr-cameras` type uses automatic layout metadata. The distinct
name prevents an older manually installed shared-tiles card from overriding it.
No manual resource entry is needed with that integration. Real HA acceptance
is still pending.

The following manual steps are a developer fallback for the legacy
`custom:eufy-shared-tiles` type, not the normal new-user setup:

1. Copy `eufy-shared-tiles.js` into Home Assistant's `/config/www/` folder.
   This lets the browser load the card. Never put passwords or account files in
   `www`: files there are served as web content.
2. Register `/local/eufy-shared-tiles.js?v=0.8.0-dev.4` as a **JavaScript module**
   in dashboard resources. Update an existing entry for this file instead of
   adding another. The version at the end helps avoid loading an old cached copy.
3. Reload the browser page. A JavaScript refresh does not require an HA restart.
4. Add **one** Manual card containing all lens tiles. Do not add one live card
   per lens: that would open extra players.

Home Assistant's [custom-card guide](https://contributors.home-assistant.io/frontend/custom-ui/card/)
explains resource registration (reviewed 26 October 2026).

Use the actual shared-grid entity and generated `grid_layout.json`. Developers
can run `python bridge/dashboard_config.py grid_layout.json --entity camera.example_shared_grid`
in the source tree to produce the card configuration. Its JSON output is also
valid YAML. The generator places fixed/tracking pairs next to each other when
both are reported. Getting that layout file is still a developer step; this is
not necessary for the new automatic card.

Illustrative 2×2 example only (replace the entity and layout for your recorder):

```yaml
type: custom:eufy-shared-tiles
entity: camera.example_shared_grid
title: NVR cameras
grid_columns: 2
grid_rows: 2
draw_fps: 5
tiles:
  - index: 0
    name: View 1
  - index: 1
    name: View 2
  - index: 2
    name: View 3
```

Names deliberately do not guess physical camera/position mapping. Confirm each
scene and requested lens before naming it Driveway/Side Gate/Front Porch. This
card cannot select a fixed/movable NVR lens by itself; that is a producer setting.
Do not install one copy per tile: use ONE card containing all the crops.

The source may be a 2×2, 3×3 or 4×4 mosaic. Sixteen tiles in a 1920×1080 source
have at most 480×270 pixels each. Enlarging a tile does not recover original
full-resolution detail. The browser must support the source codec, normally HEVC.

Validate geometry/configuration: `node tests/test_shared_tiles.mjs`.
Synthetic browser fixture: serve repository root on loopback only, open
`frontend/test-fixture.html`, verify one active mock player, distinct RGB crops,
enlarge without a new player, and hidden/disconnected cleanup. No real HA/NVR
or network authentication is used in that fixture.
