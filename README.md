# opendeck-mcp

<!-- mcp-name: io.github.kastro5/opendeck-mcp -->

Set up your [OpenDeck](https://github.com/nekename/OpenDeck) stream controller by asking an AI agent:

> "Put a Multi Action on the top-left key that mutes my mic and opens Discord, make a 'Gaming'
> profile from my default one, and switch to it whenever Steam is focused."

`opendeck-mcp` is an [MCP](https://modelcontextprotocol.io) server. It lets Claude Code (or any MCP client)
read your decks, profiles and plugins, **stage** changes, show you a diff, and then apply them, with a
backup you can undo. It works with any device OpenDeck drives: Elgato Stream Decks and plugin-provided
devices such as the [Ulanzi D200H/D200X](https://github.com/kastro5/opendeck-ulanzi-d200).

## Contents

- [What it can do](#what-it-can-do)
- [Install](#install)
  - [Upgrading](#upgrading)
  - [Other MCP clients](#other-mcp-clients)
  - [From another computer](#from-another-computer)
- [Using it](#using-it)
- [How it works](#how-it-works)
- [Safety](#safety)
- [Tools](#tools)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## What it can do

- **See everything:** devices with their grid size, dials and touchpoints; profiles; every key's action,
  title and settings; and installed plugins with their actions.
- **Edit keys:** place, update (title, font, colour, image, settings), clear, move, swap or copy keys, dials
  and touchpoints, across profiles and devices.
- **Multi and Toggle Actions:** add and remove the actions inside them, and set the delays between them.
- **Profiles:** create, duplicate, rename (references to the old name are updated too) and delete
  profiles, including `Folder/Name` profiles, and choose which one each device starts on.
- **App switching:** switch a device to a profile automatically when an app is focused.
- **Plugins:**
  - search the same catalogue as OpenDeck's Plugin Manager, plus the Elgato plugin archive;
  - install, update and remove plugins;
  - read and write plugin settings, with secrets redacted;
  - read plugin logs.
- **Instant edits:** title, image and settings tweaks to a key on the profile a device is showing
  apply at once, with no restart; `undo_live` takes them back.
- **Icons and folders:** make key images from text, emoji, ~3,400 brand logos or ~7,400 symbols, and
  folder keys that open a sub-profile with a Back key.
- **Pictures:** `render_profile` shows the deck as OpenDeck draws it, with staged changes outlined or
  before and after side by side, so you can check a layout before applying it.
- **Home Assistant:** find entities by name, area or domain, and get ready-made settings for a key
  that controls one, using the login already saved in the Home Assistant plugin.
- **Undo:** every apply takes a backup, and `restore_backup` puts things back.

## Install

You need OpenDeck 2.14, [uv](https://docs.astral.sh/uv/) and Claude Code (or another MCP client, see
below). uv fetches a suitable Python by itself.

```sh
claude mcp add opendeck --scope user -- uvx opendeck-mcp@0.3.1
```

Then restart Claude Code and ask it something like *"what's on my OpenDeck?"*.

It's on [PyPI](https://pypi.org/project/opendeck-mcp/) and in the
[MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.kastro5/opendeck-mcp`. To run
straight from GitHub instead: `uvx --from git+https://github.com/kastro5/opendeck-mcp@v0.3.1 opendeck-mcp`.

OpenDeck's folders are found automatically. If yours live somewhere unusual, override them with
environment variables (`claude mcp add … -e NAME=value`):

| Variable | Default (Windows) |
|---|---|
| `OPENDECK_CONFIG_DIR` | `%APPDATA%\opendeck` |
| `OPENDECK_EXE` | `%LOCALAPPDATA%\OpenDeck\opendeck.exe` |
| `OPENDECK_LOG_DIR` | `%LOCALAPPDATA%\opendeck\logs` |
| `OPENDECK_MCP_DATA_DIR` | `%LOCALAPPDATA%\opendeck-mcp` (the server's own plan, backups and cache) |
| `OPENDECK_MCP_HA_VERIFY_TLS` | `1`; set `0` for a Home Assistant on https with a self-signed certificate |
| `OPENDECK_MCP_TOKEN` | the HTTP token (default: generated once, see below) |

### Upgrading

The version is pinned in the command, so replace the registration with the new tag:

```sh
claude mcp remove opendeck --scope user
claude mcp add opendeck --scope user -- uvx opendeck-mcp@0.3.1
```

The first apply after an upgrade also updates the companion bridge plugin (see
[How it works](#how-it-works)), in the same restart.

### Other MCP clients

Any client that can start a stdio server works. The usual JSON configuration (Claude Desktop,
Cursor, VS Code and others) is:

```json
{
  "mcpServers": {
    "opendeck": {
      "command": "uvx",
      "args": ["opendeck-mcp@0.3.1"]
    }
  }
}
```

### From another computer

The server has to run on the PC that runs OpenDeck, inside your logged-in desktop session: it
restarts OpenDeck and talks to it through its command line, which doesn't work from a service or an
SSH session. To use it from another machine (a laptop, a dev VM), serve it over HTTP there and
connect to it. [Tailscale](https://tailscale.com) or another private network is the recommended way
in; every request also needs a token.

On the OpenDeck PC (Windows):

```sh
uvx opendeck-mcp@0.3.1 install-autostart --host <tailscale-ip>
uvx opendeck-mcp@0.3.1 token --host <tailscale-ip>
```

The first command puts a small script in your Startup folder that starts the server, without a
window, at every logon, and starts it now. The second prints the command to run on the other
machine:

```sh
claude mcp add --transport http opendeck http://<tailscale-ip>:8765/mcp --header "Authorization: Bearer <token>"
```

- `tailscale ip -4` shows the address. The server refuses `0.0.0.0` (every network) unless you add
  `--allow-all-interfaces`.
- To connect by name (e.g. a MagicDNS name), add `--allowed-host <name>`; other `Host` headers are
  rejected.
- `opendeck-mcp token --reset` makes a new token, and `opendeck-mcp uninstall-autostart` removes
  the autostart and stops the server.
- Windows Firewall must allow inbound TCP 8765 on that network; it asks the first time if it isn't
  already allowed.
- On Linux or macOS, run `opendeck-mcp serve --host <ip>` from a systemd user service or LaunchAgent.

## Using it

Just describe what you want. Under the hood, the agent:

1. calls `get_overview` and `show_profile` to see your devices and keys;
2. uses `list_actions` / `describe_action` to find the right action and learn its settings;
3. stages edits with `set_key`, `add_child`, `manage_profile` and so on;
4. calls `preview_changes` (and `render_profile` for a picture), shows you the result, and waits for your
   OK;
5. calls `apply_changes`, which restarts OpenDeck for a few seconds and reports the backup id.

Small tweaks skip all that. Changing an existing key's title, image or settings on the profile a
connected device is showing happens instantly, without a restart, and `undo_live` reverses it. Title
style, the shown state, other profiles and every structural change still go through staging.

For example, *"make a Fusion 360 profile with keys for my most-used sketch and solid tools, put the
rest in a folder, and switch to it whenever Fusion is in front"* builds a profile of Simulate Input
keys sending Fusion's shortcuts, with icons from `make_icon`, a "More" folder and the app rule.

Keys are addressed the way `show_profile` draws them:

```
     c1              c2              c3              c4              c5
r1    Multi Action    "GitHub"        Toggle Action   ·               ·
r2    ·               Entity (custo…  ·               Switch Profile  ·
r3    Device Bright…  ·               "rew"           ·               (wide →)
```

| Slot | Meaning |
|---|---|
| `r1c1` | row 1, column 1 |
| `key13` | raw position 13 (0-based) |
| `dial1` | first dial |
| `touch1` | first touchpoint |
| `r1c1/child2` | second action inside a Multi or Toggle Action |

## How it works

OpenDeck has no API for changing its layout. It keeps profiles in memory and overwrites any file edited
while it runs. So `opendeck-mcp` works on the files, with OpenDeck stopped:

1. **Staging.** Each edit is recorded as an operation in a plan. Nothing is written yet.
   `show_profile` shows staged edits marked with `*`.
2. **Applying.**
   - First, OpenDeck is asked to save its in-memory state. This sends a profile switch through its command
     line, which makes it write the selected profile.
   - Then OpenDeck and its plugin processes are stopped.
   - The plan is replayed onto the freshly read files. If something you staged was changed in OpenDeck's
     UI in the meantime, apply reports a conflict and doesn't write anything.
   - The files are backed up, written atomically, read back and re-validated. If anything fails, the
     backup is restored automatically.
   - Finally, OpenDeck is started again, detached and hidden.
3. **Faithful files.** Action data is rebuilt from plugin manifests exactly as OpenDeck builds it, and
   JSON is written byte-for-byte in OpenDeck's format. A profile OpenDeck can't read gets replaced by an
   empty one, so every write is validated twice.
4. **Device geometry.** OpenDeck doesn't store a device's rows × columns anywhere, so the first apply also
   installs a tiny companion plugin, *OpenDeck MCP Bridge* (no actions, about 60 lines of JavaScript). It
   records the geometry OpenDeck reports to plugins into its own settings file, where the server reads it.
   It's versioned with the server, so a new release updates it on its next apply.
5. **Images.** New key images are stored as `data:` URLs. OpenDeck accepts these and converts them to
   files itself on its next save.

### Instant edits

Titles and images are sent to the running OpenDeck through its command line (`--process-message`),
as `setTitle` and `setImage` events. Settings go over OpenDeck's local plugin websocket, posing as
the key's settings panel, because that is the only path on which OpenDeck tells the plugin its
settings changed. OpenDeck is then asked to save, and the file is checked. Before each edit the old
titles, images (as bytes) and settings are written to `live_journal.jsonl` in the server's data
folder, which `undo_live` replays.

## Safety

- **Nothing changes until you confirm.** Staging tools never touch OpenDeck. `apply_changes`,
  `restore_backup` and plugin installs all need a second call with `confirm=true`.
- **Backups.** Every apply first copies `profiles/`, `images/`, `applications.json` and anything else it
  touches into `%LOCALAPPDATA%\opendeck-mcp\backups`. It keeps the last 30 backups (always at least 5).
  Restoring also backs up the current state first. Backups contain your plugin settings, including
  secrets, so they stay on your machine.
- **Secrets are redacted.** Anything that looks like a credential (token, password, API key, JWT, Bearer
  value, …) is shown as `«redacted»` in settings, diffs, examples and logs. Passing `«redacted»` back keeps
  the stored value.
- **Risky keys are flagged.** Run Command keys run commands, Simulate Input keys type into the focused
  window, and Open URL keys with non-web URIs can launch programs. Each is marked ⚠ when staged and in the
  preview.
- **Plugins are third-party code.** `stage_plugin` first downloads and inspects the plugin: author, version,
  sha256, how it runs, which platforms it supports, and the catalogue's *AI-assisted* flag. It only stages
  the install after you agree. Built-in plugins can't be removed.
- **Home Assistant.** The Home Assistant tools reuse the server URL and access token saved by the
  Home Assistant plugin. The token is only sent to that server, never shown. `ha_call_service` acts
  on your real home, so it needs a second call with `confirm=true`.
- **Instant edits aren't backed up.** They don't go through apply, so instead the previous titles,
  images and settings are kept in a journal (the last 200 edits) that `undo_live` replays.
- **The HTTP token is a key to your deck.** Anyone with it who can reach the port can change keys,
  install plugins and, through Run Command keys, run programs. Keep the server on a private address,
  and `opendeck-mcp token --reset` if the token leaks.
- **Version check.** Apply refuses to run on OpenDeck versions other than 2.14.x unless you allow it
  explicitly.

### Network and files

- **Internet:** only the plugin catalogues and plugin downloads (`find_plugins`, `stage_plugin`), and
  the title fonts for `render_profile` and the icon sets for `make_icon`, fetched once each from
  jsDelivr and cached. Nothing else, and no telemetry.
- **Local network:** your Home Assistant, only when the Home Assistant tools are used.
- **Files** in `%LOCALAPPDATA%\opendeck-mcp`: `plan.json` (staged changes), `backups/`,
  `live_journal.jsonl`, `icons/` (made by `make_icon`), `cache/` (catalogues, fonts, icon sets),
  `device_overrides.json`, and for HTTP
  `http_token` and `http.log`. Delete the folder to reset everything.

## Tools

| Kind | Tools |
|---|---|
| Read | `get_overview`, `show_profile`, `render_profile`, `list_actions`, `describe_action`, `get_plugin_settings`, `read_logs`, `get_app_rules`, `find_plugins`, `find_icons`, `preview_changes`, `list_backups` |
| Stage | `set_key`, `update_key`, `clear_key`, `move_key`, `add_child`, `remove_child`, `set_multi_delays`, `make_folder`, `manage_profile`, `set_app_rule`, `set_plugin_settings`, `stage_plugin`, `discard_changes`, `set_device_layout` |
| Icons | `make_icon` (saves a PNG to pass as `image`) |
| Apply | `apply_changes`, `restore_backup`, `undo_live` |
| Home Assistant | `ha_search`, `ha_describe_entity`, `ha_call_service` |

## Limitations

- **Apply restarts OpenDeck** (about 5 seconds). Unsaved edits in an open settings panel are lost, and
  plugins restart.
- **Some settings need examples.** Settings are known in detail for OpenDeck's own actions and the Starter
  Pack. For other plugins, the agent works from hints on the plugin's settings page and from your existing
  keys. If those aren't enough, place the action and finish setting it up in OpenDeck's UI.
- **App rule names.** App rules match the name OpenDeck gives the focused app: on Windows the
  executable's description (e.g. `Autodesk Fusion`, `Visual Studio Code`), otherwise its file name.
  `get_app_rules` lists the exact names of the apps you have open.
- **Tested platform.** Tested on Windows 11 with OpenDeck 2.14.1, a Ulanzi D200H and Home Assistant.
  The macOS and Linux paths and process handling are written but untested.
- **Limited live control.** Title, image and settings edits apply instantly; everything else needs
  a restart. It doesn't press keys or switch profiles live.
- **Pictures are close, not exact.** `render_profile` can't see images a plugin draws while running (a
  clock, a live Home Assistant state) unless OpenDeck has saved them, and OpenDeck's own Multi/Toggle
  Action icons are drawn as look-alikes.
- **An open settings panel goes quiet.** After an instant settings edit, a settings panel already open
  in OpenDeck's window for that key stops updating until it's reopened.

## Troubleshooting

- **"OpenDeck's executable wasn't found" / "config folder wasn't found"**: set `OPENDECK_EXE` /
  `OPENDECK_CONFIG_DIR` (see [Install](#install)).
- **Apply refuses: untested OpenDeck version.** Check the changelog for a release that supports
  yours; `apply_changes(allow_untested_version=true)` overrides it at your own risk, with a backup.
- **Apply reports a conflict.** A key you staged was changed in OpenDeck meanwhile. Look at
  `preview_changes`, then discard and re-stage it, or apply with `on_conflict="skip"`.
- **A device shows "geometry unknown".** It's learned by the bridge plugin, installed with the first
  apply. Until then `set_device_layout` sets rows and columns by hand.
- **Undo an apply:** `list_backups`, then `restore_backup`.
- **HTTP client gets 401 / 421.** 401: wrong or missing token (`opendeck-mcp token`). 421: the client
  used a host name the server doesn't know; add it with `--allowed-host`. The server logs to
  `http.log` in its data folder.

## Development

```sh
git clone https://github.com/kastro5/opendeck-mcp && cd opendeck-mcp
uv sync
uv run pytest                 # unit tests, against a scrubbed fixture config
uv run pytest -m live         # also check snapshots against your real OpenDeck install (read-only)
claude mcp add opendeck-dev -- uv --directory /path/to/opendeck-mcp run opendeck-mcp
```

Test fixtures are real OpenDeck profiles with every setting value replaced
(`scripts/scrub_fixture.py`), plus trimmed plugin manifests with empty icon files.

## License

MIT. Not affiliated with OpenDeck or Elgato.

Icons and fonts are downloaded at run time, not bundled:
[Material Design Icons](https://pictogrammers.com/library/mdi/) (Apache 2.0),
[Simple Icons](https://simpleicons.org) (CC0; brand logos remain their owners' trademarks),
[Twemoji](https://github.com/jdecked/twemoji) (graphics CC-BY 4.0, © Twitter/X and contributors), and
OpenDeck's title fonts from [Google Fonts](https://github.com/google/fonts) (OFL).
