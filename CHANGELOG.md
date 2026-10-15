# Changelog

## 0.2.0 — 2026-10-15

- **Instant edits.** `update_key` now changes a key's title, image and settings in the running
  OpenDeck without restarting it, when the key is on the profile a connected device is showing.
  Settings reach the plugin the same way OpenDeck's own settings panel sends them. Everything else
  is still staged, and the result says which happened. A new `mode` parameter (`auto`, `live`,
  `staged`) overrides the choice.
- New `undo_live` tool: puts back the titles, images and settings changed by instant edits. Images
  are saved as bytes before each edit, so a custom key image can always be restored.
- **Home Assistant awareness.** `ha_search` finds entities by name, domain or area,
  `ha_describe_entity` shows an entity's state and services and prints ready-made settings for a
  Home Assistant key, and `ha_call_service` calls a service on request (after a confirmation step).
  The server uses the login already saved in the Home Assistant plugin, and the token never appears
  in any output.
- `describe_action` now explains the Home Assistant plugin's key settings.
- **Pictures of the deck.** `render_profile` draws a profile the way OpenDeck draws it (icons,
  titles with their font, colour, stroke and alignment, background colours, the wide key, dials and
  touchpoints), with staged keys outlined, or before and after side by side. OpenDeck's title fonts
  are downloaded once from Google Fonts; offline, a system font with the same metrics is used.
- New dependencies: `websockets`, `resvg-py` (SVG icons in previews).

## 0.1.0 — 2026-10-14

First release.

- Read devices, profiles, keys, plugins, plugin settings (redacted), logs and app rules.
- Stage key edits (set, update, clear, move/swap/copy), Multi/Toggle Action children and delays,
  profile create/duplicate/rename/delete/select, app switching rules and plugin settings.
- Preview staged changes as a per-key diff with warnings; apply them with OpenDeck stopped, a backup,
  verification and an automatic rollback; undo with `restore_backup`.
- Search the OpenAction catalogue and the Elgato archive; install, update and remove plugins after
  inspecting them.
- Companion *OpenDeck MCP Bridge* plugin that records device geometry.
- Tested on Windows 11 with OpenDeck 2.14.1.
