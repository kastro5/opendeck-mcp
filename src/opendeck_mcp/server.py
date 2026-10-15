"""The MCP server: tool definitions. Logic lives in tools/ and runtime/."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .core.paths import Paths, discover
from .runtime import applier
from .tools import edit_tools, plugin_tools, read_tools
from .tools.view import View

INSTRUCTIONS = """\
Configure OpenDeck (open-source Stream Deck software) and the decks it drives.

Workflow:
1. get_overview first: devices, their grids, profiles, plugins.
2. show_profile to see a profile's keys. Slots are named r<row>c<col> (1-based grid keys), key<N>
   (0-based raw position), touch<N>, dial<N>, infobar<N>, and <slot>/child<N> inside multi/toggle actions.
3. list_actions / describe_action before placing an action: describe_action explains its settings.
4. Edit with set_key, update_key, clear_key, move_key, add_child, remove_child, set_multi_delays,
   manage_profile, set_app_rule, set_plugin_settings, stage_plugin. These STAGE changes, except that
   update_key applies title/image/settings tweaks on the showing profile instantly (undo_live undoes them).
5. preview_changes, show the user the summary, then apply_changes(confirm=true) once they agree.
   Applying restarts OpenDeck (a few seconds) and takes a backup; restore_backup undoes it.

Secrets in plugin settings are shown as «redacted»; passing «redacted» back keeps the stored value.
Run Command / Simulate Input keys execute things on the user's computer: say so when staging them.
Installing plugins runs third-party code: always get the user's explicit go-ahead.
"""

log = logging.getLogger("opendeck_mcp")
mcp = MCPServer("opendeck", title="OpenDeck", version=__version__, instructions=INSTRUCTIONS, log_level="WARNING")

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
STAGE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                        open_world_hint=False)
STAGE_NET = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                            open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False,
                              open_world_hint=False)

_paths: Paths | None = None


def paths() -> Paths:
    global _paths
    if _paths is None:
        _paths = discover()
        if not _paths.config_dir.is_dir():
            raise RuntimeError(f"OpenDeck's config folder wasn't found at {_paths.config_dir}; "
                               "is OpenDeck installed? (override with OPENDECK_CONFIG_DIR)")
    return _paths


Device = Annotated[str, Field(description="Device id or name, e.g. 'ulanzi-d200h' (see get_overview).")]
Slot = Annotated[str, Field(description="Slot: r1c1 (row/column, 1-based), key13, touch1, dial1 or infobar1.")]
Profile = Annotated[str | None, Field(description="Profile id; defaults to the device's selected profile.")]
Style = Annotated[dict[str, Any] | None, Field(
    description="Title look: show (bool), colour (#RRGGBB), size (int), family (font), style "
                "(Regular|Bold|Italic|Bold Italic), alignment (top|middle|bottom), underline (bool), "
                "stroke_colour, stroke_size, background_colour.")]
Image = Annotated[str | None, Field(
    description="Key image: a file path, an http(s) URL, or a data: URL; 'default' restores the action's own.")]


# -- read ------------------------------------------------------------------------------ #

@mcp.tool(annotations=READ)
def get_overview() -> str:
    """Start here. Lists devices (grid size, dials, touchpoints, connected), each device's profiles and
    which is selected, installed plugins, whether OpenDeck is running, and how many changes are staged."""
    return read_tools.get_overview(View(paths()))


@mcp.tool(annotations=READ)
def show_profile(device: Device, profile: Profile = None,
                 show_settings: Annotated[bool, Field(description="Include each key's settings (redacted).")] = False,
                 show_children: bool = True) -> str:
    """Render a profile: a grid sketch plus every occupied slot with its action, title and state.
    Includes staged (not yet applied) changes, marked with *. Slot labels shown are the ones the
    editing tools accept."""
    return read_tools.show_profile(View(paths()), device, profile, show_settings, show_children)


@mcp.tool(annotations=READ)
def list_actions(query: Annotated[str | None, Field(description="Filter by text in name/plugin/uuid.")] = None,
                 plugin: Annotated[str | None, Field(description="Only this plugin (name or id).")] = None,
                 controller: Annotated[Literal["Keypad", "Encoder"] | None, Field(
                     description="Keypad for keys, Encoder for dials.")] = None) -> str:
    """List actions that can be placed on keys or dials: name, plugin, uuid, controllers, states.
    Includes OpenDeck's Multi Action and Toggle Action, and plugins staged for install."""
    return read_tools.list_actions(View(paths()), query, plugin, controller)


@mcp.tool(annotations=READ)
def describe_action(action: Annotated[str, Field(description="Action uuid or name, e.g. 'Open URL'.")]) -> str:
    """Explain one action's settings before placing it: a curated schema for OpenDeck and Starter Pack
    actions, otherwise hints read from the plugin's settings page, plus real settings from the user's
    existing keys (secrets redacted)."""
    return read_tools.describe_action(View(paths()), action)


@mcp.tool(annotations=READ)
def get_plugin_settings(plugin: Annotated[str, Field(description="Plugin name or folder id.")]) -> str:
    """Show a plugin's global (plugin-wide) settings with secret values redacted."""
    return read_tools.get_plugin_settings(View(paths()), plugin)


@mcp.tool(annotations=READ)
def read_logs(plugin: Annotated[str | None, Field(description="Plugin name/id, or omit for OpenDeck's own log.")]
              = None, lines: int = 80,
              grep: Annotated[str | None, Field(description="Only lines matching this regex.")] = None) -> str:
    """Tail OpenDeck's log or a plugin's log (tokens redacted). Useful after apply_changes."""
    return read_tools.read_logs(View(paths()), plugin, lines, grep)


@mcp.tool(annotations=READ)
def get_app_rules() -> str:
    """Show automatic profile switching: which profile each device switches to when an app is focused."""
    return read_tools.get_app_rules(View(paths()))


# -- staging ----------------------------------------------------------------------------- #

@mcp.tool(annotations=STAGE)
def set_key(device: Device, slot: Slot,
            action: Annotated[str, Field(description="Action uuid or name (see list_actions).")],
            profile: Profile = None,
            title: Annotated[str | None, Field(description="Text drawn on the key; \\n for a new line.")] = None,
            settings: Annotated[dict[str, Any] | None, Field(
                description="The action's settings object (see describe_action).")] = None,
            image: Image = None, title_style: Style = None) -> str:
    """Place an action on a key or dial, replacing whatever is there. For dials use dial<N> with an action
    that supports Encoder. For a Multi/Toggle Action, set it here, then add_child. Staged only."""
    return edit_tools.set_key(paths(), device, slot, action, profile, title, settings, image, title_style)


@mcp.tool(annotations=STAGE)
def update_key(device: Device, slot: Annotated[str, Field(description="Slot, or <slot>/child<N> for a child.")],
               profile: Profile = None,
               state: Annotated[int | Literal["all"] | None, Field(
                   description="Which state to restyle (0-based) for multi-state actions; default all.")] = None,
               title: str | None = None, title_style: Style = None, image: Image = None,
               settings_merge: Annotated[dict[str, Any] | None, Field(
                   description="Merged into the settings (JSON merge patch: null deletes a key).")] = None,
               settings_replace: Annotated[dict[str, Any] | None, Field(
                   description="Replaces the settings entirely.")] = None,
               remove_settings: list[str] | None = None,
               current_state: int | None = None,
               mode: Annotated[Literal["auto", "live", "staged"], Field(
                   description="auto: apply instantly when possible, else stage; live: instantly or fail; "
                               "staged: always stage.")] = "auto") -> str:
    """Change an existing key without replacing its action: title, title style, image, settings,
    or which state it shows.

    Title text, image and settings changes on the profile a connected device is showing apply
    instantly, with no restart (undo with undo_live). Anything else (title style, current state,
    another profile, a device with staged changes) is staged for apply_changes; the result says
    which happened. A live settings change reaches the plugin as if edited in OpenDeck's own panel;
    if that key's settings panel is open in OpenDeck, reopen it to see the change."""
    return edit_tools.update_key(paths(), device, slot, profile, state, title, title_style, image, settings_merge,
                                 settings_replace, remove_settings, current_state, mode)


@mcp.tool(annotations=DESTRUCTIVE)
def undo_live(count: Annotated[int, Field(description="How many of the latest live edits to undo.")] = 1) -> str:
    """Undo the latest instant (live) edits made by update_key, newest first: titles, images and
    settings go back to what they were. Staged changes and applied plans are undone with
    discard_changes and restore_backup instead."""
    return edit_tools.undo_live(paths(), count)


@mcp.tool(annotations=STAGE)
def clear_key(device: Device, slot: Slot, profile: Profile = None) -> str:
    """Remove the action from a key or dial. Staged only."""
    return edit_tools.clear_key(paths(), device, slot, profile)


@mcp.tool(annotations=STAGE)
def move_key(device: Device, from_slot: Slot, to_slot: Slot,
             mode: Annotated[Literal["move", "swap", "copy"], Field(
                 description="move (destination must be empty), swap, or copy.")] = "move",
             profile: Profile = None,
             to_profile: Annotated[str | None, Field(description="Destination profile (default: same).")] = None,
             to_device: Annotated[str | None, Field(description="Destination device (default: same).")] = None
             ) -> str:
    """Move, swap or copy a key (with its settings, title, image and children), within a profile or to
    another profile/device. Staged only."""
    return edit_tools.move_key(paths(), device, from_slot, to_slot, mode, profile, to_profile, to_device)


@mcp.tool(annotations=STAGE)
def add_child(device: Device, slot: Annotated[str, Field(description="Slot holding a Multi/Toggle Action.")],
              action: Annotated[str, Field(description="Action uuid or name to add inside it.")],
              profile: Profile = None, settings: dict[str, Any] | None = None, title: str | None = None,
              title_style: Style = None, image: Image = None,
              position: Annotated[int | None, Field(description="1-based position; default last.")] = None,
              delay_ms: Annotated[int | None, Field(
                  description="Multi Action only: wait after this child, in ms (default 100).")] = None) -> str:
    """Add an action inside a Multi Action (runs all children in order) or Toggle Action (cycles through
    them, one per press). Put the Multi/Toggle Action on the key with set_key first. Staged only."""
    return edit_tools.add_child(paths(), device, slot, action, profile, settings, title, title_style, image,
                                position, delay_ms)


@mcp.tool(annotations=STAGE)
def remove_child(device: Device, slot: Slot, child: Annotated[int, Field(description="1-based child number.")],
                 profile: Profile = None) -> str:
    """Remove one action from inside a Multi/Toggle Action. Staged only."""
    return edit_tools.remove_child(paths(), device, slot, child, profile)


@mcp.tool(annotations=STAGE)
def set_multi_delays(device: Device, slot: Slot,
                     delays_ms: Annotated[list[int], Field(
                         description="delays_ms[i] = wait after child i+1, in ms (default 100 each).")],
                     profile: Profile = None) -> str:
    """Set the pauses between the children of a Multi Action. Staged only."""
    return edit_tools.set_multi_delays(paths(), device, slot, delays_ms, profile)


@mcp.tool(annotations=STAGE)
def manage_profile(device: Device,
                   operation: Literal["create", "duplicate", "rename", "delete", "set_default"],
                   profile: Annotated[str, Field(
                       description="The profile to act on (for create/duplicate: the new name). "
                                   "Names may be 'Folder/Name' (one level).")],
                   new_name: Annotated[str | None, Field(description="For rename.")] = None,
                   copy_from: Annotated[str | None, Field(
                       description="For duplicate: the source (default: the selected profile).")] = None) -> str:
    """Create, duplicate, rename or delete a profile, or make one the device's selected profile.
    Renaming also updates app rules and Switch Profile keys that point at it. Staged only."""
    return edit_tools.manage_profile(paths(), device, operation, profile, new_name, copy_from)


@mcp.tool(annotations=STAGE)
def set_app_rule(app: Annotated[str, Field(
                     description="The app's display name as OpenDeck sees it (e.g. 'Visual Studio Code', "
                                 "'Chatterino'), or 'opendeck_default' for the fallback when no rule matches.")],
                 device: Device,
                 profile: Annotated[str | None, Field(description="Profile to switch to; null removes the rule.")]
                 ) -> str:
    """Switch a device to a profile automatically when an app is focused. Staged only."""
    return edit_tools.set_app_rule(paths(), app, device, profile)


@mcp.tool(annotations=STAGE)
def set_plugin_settings(plugin: Annotated[str, Field(description="Plugin name or folder id.")],
                        values: Annotated[dict[str, Any], Field(
                            description="Merged into the plugin's global settings. «redacted» keeps a secret.")],
                        remove_keys: list[str] | None = None) -> str:
    """Change a plugin's global (plugin-wide) settings, e.g. a server URL. Staged only."""
    return edit_tools.set_plugin_settings(paths(), plugin, values, remove_keys)


@mcp.tool(annotations=STAGE)
def set_device_layout(device: Device, rows: int, columns: int) -> str:
    """Tell opendeck-mcp a device's key grid size when it can't detect it (stored locally; doesn't
    touch OpenDeck). Usually unnecessary: the bridge plugin records it automatically."""
    return edit_tools.set_device_layout(paths(), device, rows, columns)


@mcp.tool(annotations=READ)
def preview_changes(detail: Annotated[Literal["summary", "files"], Field(
        description="summary: per-key changes and warnings; files: JSON diffs.")] = "summary") -> str:
    """Show everything staged: per device/profile/slot changes, warnings (commands that will run, plugins
    that will be installed), and conflicts with edits made in OpenDeck since staging."""
    return edit_tools.preview_changes(paths(), detail)


@mcp.tool(annotations=STAGE)
def discard_changes(which: Annotated[str | list[str], Field(
        description="'all', 'last', or staged op ids like ['op3', 'op5'] (see preview_changes).")] = "all") -> str:
    """Drop staged changes without applying them."""
    return edit_tools.discard_changes(paths(), which)


# -- plugins ---------------------------------------------------------------------------- #

@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
def find_plugins(query: Annotated[str | None, Field(description="Search text.")] = None,
                 source: Annotated[Literal["catalogue", "archive", "installed"], Field(
                     description="catalogue: open-source OpenAction plugins (same list as OpenDeck's "
                                 "Plugin Manager); archive: rehosted Elgato Stream Deck plugins; "
                                 "installed: what's installed.")] = "catalogue",
                 limit: int = 15) -> str:
    """Search for plugins to install, with author, repository and installed/available versions."""
    return plugin_tools.find_plugins(paths(), query, source, limit)


@mcp.tool(annotations=STAGE_NET)
def stage_plugin(operation: Literal["install", "update", "remove"],
                 plugin_id: Annotated[str, Field(description="Catalogue id (see find_plugins) or installed id.")],
                 source: Literal["catalogue", "archive", "url"] = "catalogue",
                 url: Annotated[str | None, Field(description="Direct .streamDeckPlugin/.zip URL for source=url.")]
                 = None,
                 confirm: Annotated[bool, Field(
                     description="false: download and inspect only. true: stage it (after the user agreed).")]
                 = False) -> str:
    """Install, update or remove a plugin. Installing runs third-party code on the user's computer: call
    first with confirm=false, show the user what was found (author, version, checksum, AI-assisted flag),
    and only stage it with confirm=true after they agree. Removal clears the plugin's keys everywhere."""
    return plugin_tools.stage_plugin(paths(), operation, plugin_id, source, url, confirm)


# -- apply ------------------------------------------------------------------------------ #

@mcp.tool(annotations=DESTRUCTIVE)
def apply_changes(confirm: Annotated[bool, Field(
                      description="false: show what would happen. true: apply (only after the user agreed).")]
                  = False,
                  show_window: Annotated[bool, Field(description="Open OpenDeck's window after restarting.")]
                  = False,
                  on_conflict: Literal["abort", "skip"] = "abort",
                  allow_untested_version: bool = False) -> str:
    """The only tool that changes OpenDeck. Stops OpenDeck, backs up, writes all staged changes, verifies
    them and restarts OpenDeck. Call with confirm=false first and show the user the result."""
    return applier.apply(paths(), confirm, show_window, on_conflict, allow_untested_version)


@mcp.tool(annotations=READ)
def list_backups(limit: int = 10) -> str:
    """Backups taken by apply_changes and restore_backup, newest first."""
    return applier.list_backups(paths(), limit)


@mcp.tool(annotations=DESTRUCTIVE)
def restore_backup(backup_id: str, confirm: bool = False, show_window: bool = False) -> str:
    """Undo an apply: put the files it changed back as they were (restarts OpenDeck). Call with
    confirm=false first to see what would be restored."""
    return applier.restore(paths(), backup_id, confirm, show_window)


def run() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs full request URLs at INFO, and GitHub download URLs carry signed tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    mcp.run("stdio")
