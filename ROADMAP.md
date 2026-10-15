# opendeck-mcp roadmap

Notes for picking this up later. State as of **2026-10-14**: v0.1.0 published, tested live on a Ulanzi
D200H with OpenDeck 2.14.1 on Windows 11, and registered in Claude Code as `opendeck`.

## Recommended order

1. **Features first** (A1–A3 below). They're core features that don't depend on how the server is
   launched, and they're what will make the plugin worth installing. A store listing is a first
   impression, so publish the plugin once the tool is at its best.
2. **HTTP transport** (B1). Both the OpenDeck plugin and remote use from the dev VM need it, so it's the
   bridge between the two halves.
3. **OpenDeck plugin + catalogue listing** (B2, B3).
4. Everything else as wanted.

Independent of all this, the Ulanzi plugin can go into the catalogue any time (C1). It probably helps more
people than anything else here.

---

## A. Features

### A1. Instant edits without restarting OpenDeck ⭐ — done in 0.2.0

**What:** changes to *existing* keys take effect immediately: settings, title and image. Adding, removing
or moving keys still goes through stage → apply → restart.

**How:** `opendeck.exe --process-message '<json>'` runs any inbound event with auth skipped (verified for
`switchProfile`). Use:
- `setSettings` with `context` = `<device>.<profile>.Keypad.<pos>.<index>` (the full 5-part context);
- `setTitle` and `setImage` the same way.

OpenDeck updates its in-memory profile, marks it stale, and saves within 30 s or on the next flush.
`update_key` would choose "live" whenever the target is on the device's **selected** profile and only
settings, title or image change. Otherwise it stages as today.

**Verify first (spike):**
- The change survives an OpenDeck restart (stale-flush timing).
- `setTitle` writes `states[n].text` the way the UI does, and whether it can target one state.
- `setImage` with a `data:` URL persists to `images/…`.
- What the plugin receives (`didReceiveSettings`), and whether plugins like Home Assistant re-render.
- A context on a non-selected profile is rejected or ignored, so it falls back to staging.

**Risks:**
- Live edits bypass the backup. Take a lightweight backup of that profile file first, or record the
  previous values so it can be undone.
- `--process-message` must always get an argv list, never a shell string: Windows PowerShell 5.1 strips
  JSON quotes.

**Effort:** small to medium.

### A2. Home Assistant awareness ⭐ — done in 0.2.0

**What:** the server reads the user's Home Assistant (entities, areas, scenes, services) so *"add a key
that toggles the office lights"* uses the real `entity_id` and service.

**How:**
- Read `serverUrl` and `accessToken` from `settings/de.perdoctus.streamdeck.homeassistant.sdPlugin.json`
  inside the server. The token is never returned or logged.
- Call HA's REST API: `/api/states` and `/api/services`. Optionally use the websocket API for areas.
- New tools:
  - `ha_search_entities(query, domain?)`;
  - `ha_describe_entity(id)`;
  - optionally `ha_call_service` for testing a key.
- Add a curated schema to `core/knowledge.py` for `…homeassistant.entity` and `…dual-state-entity`.
  - Settings shape seen on a real key: `button.serviceShortPress/serviceLongPress/serviceTap/
    serviceRotation{entityId, serviceId, serviceData}`, `display{entityId, buttonTitle, iconLayout,
    iconSettings, labelFontSize, useStateImagesForOnOffStates, enableServiceIndicator, backgroundColor,
    backgroundColorEnd}`, `controllerType`, `rotationTickBucketSizeMs`, `rotationTickMultiplier`,
    `version: 8`.
  - Check against the plugin source (github.com/cgiesche/streamdeck-homeassistant) for the meaning of
    `iconLayout` / `iconSettings` values.

**Generalise:** a small "integration" interface so other local-API plugins (OBS websocket, Spotify, …)
can be added the same way later.

**Risks:**
- `homeassistant.local` resolution.
- HTTPS with a self-signed certificate (offer a verify toggle).
- Keep the token out of exception messages.

**Effort:** medium.

### A3. Rendered preview image of a profile ⭐ — done in 0.2.0

**What:** `render_profile(device, profile, staged=true)` returns a PNG of the deck as it will look: icons,
titles (font, colour, alignment, stroke), background colours, the wide key, dials.

**How:**
- Pillow.
- Resolve state images: config-relative paths, `images/<dev>/<profile>/<ctx>/<n>.png`, `data:` URLs and
  SVG. SVG needs rendering, e.g. via `resvg-py` or `cairosvg`; check Windows wheels, or fall back to the
  action icon.
- Fonts: OpenDeck's bundled fonts (Liberation Sans, Archivo Black, …). Find them in the OpenDeck install,
  or ship OFL-licensed copies.
- Return MCP image content (`ImageContent`, base64 PNG) so Claude sees it. Also offer `diff=true`,
  before/after side by side.

**Effort:** medium. Fidelity can grow over time; start with icons + titles.

### A4. Generated icons

`make_icon(text|emoji|brand, fg, bg, style)` → `data:` URL usable with `set_key`/`update_key`. Brand
icons from Simple Icons (CC0 SVGs; download on demand and cache). Pairs naturally with A3. **Effort:**
small to medium.

### A5. Folder keys

One tool: `make_folder(device, slot, name, back_slot="r1c1")`. It creates a profile `<current>/<name>`,
or a top-level one if the current profile is already in a folder, since OpenDeck allows only one folder
level. It puts a Switch Profile key on `slot` and a "Back" key in the new profile. This is just a
composite of existing ops. **Effort:** small.

### A6. Export / import profiles

Export a profile to a portable JSON (actions by uuid, settings, titles, inlined images). Import it with
remapping when the grid differs: same row/column where possible, otherwise fill in order and report
leftovers. Refuse if a needed plugin isn't installed, and offer `stage_plugin`. Redact secrets on export
by default. **Effort:** medium.

### A7. Live test tools

`press_key(device, slot)` (keyDown + keyUp via `--process-message`), `switch_profile_now`,
`set_brightness_now`. These let the agent check its own work. Gate `press_key` on Run Command / Simulate
Input keys behind `confirm`. **Effort:** small.

---

## B. Distribution

### B1. HTTP transport (prerequisite for B2 and remote use)

- `opendeck-mcp --http --host <addr> --port 8765 --token <secret>` using MCPServer's streamable-http
  transport.
- **Auth:** bearer token on every request (constant-time compare). Refuse to start on `0.0.0.0` without
  `--allow-all-interfaces`.
- **Recommended setup:** bind to the **Tailscale IP** (Tailscale is already installed on the OpenDeck PC);
  the token is a second lock.
- **Must run in the user's logged-in desktop session.** Not a Windows service and not over SSH: those run
  in a separate, desktop-less Windows session, so the OpenDeck restarts would be invisible to the user.
  Provide `opendeck-mcp install-logon-task` (Task Scheduler, "at log on", run only when logged on).
- **Client:** `claude mcp add --transport http opendeck http://<ip>:8765/mcp --header "Authorization:
  Bearer <token>"`.
- Concurrency: the plan lock already serialises sessions.

### B2. OpenDeck plugin version

Same core code, launched by OpenDeck as a plugin (e.g. `dev.kastro5.opendeck-mcp.sdPlugin`).

- **Transport:** HTTP (B1) on `127.0.0.1` by default, with an option for a LAN or Tailscale address.
  Generate the token on first run and store it in the plugin's global settings.
- **Settings UI:** set `"HasSettingsInterface": true` in the manifest. OpenDeck then shows a gear button
  that sends `showSettingsInterface` to the plugin. The UI shows status, the copy-paste
  `claude mcp add …` command with the token, the bind address, and a token reset. It can be a plugin web
  page (HTML plugin) or a small local page the server hosts.
- **Device geometry** comes straight from the plugin's own `-info` / `deviceDidConnect`, so the separate
  bridge plugin isn't needed in this mode.
- **Apply kills the server mid-request.** Apply stops OpenDeck and all its plugins, including this one.
  Solution:
  - `apply_changes` spawns a detached helper (`opendeck-mcp apply-helper --plan …`, with job breakaway
    like today) and returns *"applying; OpenDeck restarts in ~10 s"*.
  - The helper runs flush → stop → write → verify → start and writes a result file.
  - The next tool call (or `get_overview`) reports the result and the backup id.
  - `restore_backup` works the same way.
- **Packaging:** PyInstaller per OS, built by GitHub Actions on tags (copy the Ulanzi repo's workflow),
  with `CodePathWin`/`CodePathMac`/`CodePathLin`. Release assets are named `.streamDeckPlugin`/`.zip`
  containing the `*.sdPlugin` folder, as the catalogue check expects.
- Keep the `uvx` stdio server too: same package, different entry point.

### B3. Catalogue listing (OpenAction / OpenDeck's plugin store)

The process is in github.com/openactionapi/plugins README:
1. Add the GitHub topic `openaction` to the plugin repo.
2. Fork `openactionapi/plugins` and add a `catalogue.json` entry keyed by the bundle ID:
   - `name` and `author` must equal the manifest's;
   - `description` must equal the GitHub repo description;
   - add `"flags": {"ai_assisted": true}` (this code is AI-assisted).
3. Put the entry in the **"Native OpenAction plugins"** section, sorted by repo URL.
4. Add `icons/<bundle-id>.png` (high resolution, matching the manifest icon).
5. Open the PR. An automated check verifies the topic, manifest, description, bundle ID, latest release
   artifacts, icon and ordering before review.

### B4. MCP registries

List the stdio server in the official MCP registry and Smithery to reach MCP users who don't know
OpenDeck yet. Small effort, once README and tags are stable.

---

## C. Other

### C1. Ulanzi plugin in the catalogue

`kastro5/opendeck-ulanzi-d200` goes in the **"Device support plugins"** section through the same B3
process: `openaction` topic, entry, icon, `ai_assisted` flag. Its releases already build in CI. Consider
coordinating with upstream (edubox/opendeck-ulanzi-d200x), and note josbol's fork (OpenDeck issue #446)
also targets the D200X.

### C2. Testing gaps

- Live install + remove of a catalogue plugin. Download and inspect are verified; "Counter"
  (`me.amankhanna.oacounter`) is a good small test.
- Dials and touchpoints on a connected D200X.
- macOS and Linux paths and process handling: written, never run.
- A GitHub Actions workflow running `pytest` on push (Windows + Linux).

### C3. Watch upstream

- **OpenDeck PR #359** (explicit device layouts). The Ulanzi plugin already sends a layout. If it merges,
  `show_profile` could render real layouts too.
- **OpenDeck version bumps.** Apply is gated to 2.14.x; re-run `uv run pytest -m live` and widen
  `runtime/applier.py:TESTED` after checking `store/` and `shared.rs` for format changes.

## Things learned the hard way (don't re-discover)

- OpenDeck replaces a profile it can't parse with an **empty** one. Always validate before writing.
- It lists `*.json.bak` / `*.json.temp` as profiles, so never leave those around. The server's temp
  suffix is `.mcptmp`.
- Profile and device ids can't contain `.`, because contexts are dot-separated.
- Action snapshots must match OpenDeck's byte for byte, including its font-remap case bug and dropped
  `Encoder.Layout`. `pytest -m live` checks this against the real install.
- Enigo key names in Simulate Input are case-sensitive PascalCase (`Shift`, `Return`). Only `ctrl`, `cmd`
  and `uni` have lowercase aliases.
- httpx logs full URLs at INFO, and GitHub download URLs carry signed tokens, so keep it at WARNING.
- MCP SDK 2.x: `from mcp.server.mcpserver import MCPServer` (FastMCP was renamed).
- Some plugin icons are 16-bit greyscale PNGs (Home Assistant's): Pillow's plain `convert("RGBA")` turns
  them solid white, so scale to 8 bits first.
- OpenDeck's title fonts are compiled into its executable; Liberation Sans/Serif are Arimo/Tinos, and all
  of them are in the google/fonts repository.
- On Windows, `homeassistant.local` takes ~3 s to resolve because the IPv6 lookup fails slowly; the
  server resolves IPv4 once for plain-http `.local` hosts. Zigbee lights report their new state after
  a service call returns, so read the state back rather than trusting the call's response.
- `setSettings` sent through `--process-message` only reaches the property inspector, never the plugin.
  Send it over the plugin websocket after `registerPropertyInspector` with the full context as uuid.
- OpenDeck marks only the *selected* profile as unsaved, so live edits elsewhere could be lost.
- Key images live as files (`images/<dev>/<profile>/<ctx>/<state>.png`) and a new image overwrites them,
  so anything that changes an image live must save the old bytes first.
