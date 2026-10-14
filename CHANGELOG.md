# Changelog

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
