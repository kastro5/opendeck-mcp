"""Read-only tools: what's on the deck, what can go there, settings and logs."""

from __future__ import annotations

import json
import re
from collections import deque

from ..core import knowledge, pi_scrape
from ..core.devices import BRIDGE_ID, PLUGIN_ID, bridge_status
from ..core.profile import ARRAY_FOR, ProfileError
from ..core.render import grid, slot_list
from ..core.secrets import redact, redact_text
from ..plugin import describe_status as plugin_status
from ..runtime import process
from .view import View


def get_overview(view: View) -> str:
    paths = view.paths
    running = process.is_running(paths)
    lines = [
        f"OpenDeck {process.version(paths) or '(version unknown)'} · "
        f"{'running' if running else 'not running'} · config {paths.config_dir}",
    ]
    if view.plan_size:
        lines.append(f"{view.plan_size} staged change(s) waiting: preview_changes, then apply_changes.")
    lines.append("")
    lines.append("Devices:")
    for layout in view.layouts().values():
        shape = f"{layout.rows}x{layout.columns} keys" if layout.grid else f"{layout.keys} keys (grid size unknown)"
        extras = [f"{n} {what}" for n, what in ((layout.touchpoints, "touchpoints"), (layout.encoders, "dials"),
                                                 (layout.infobars, "infobars")) if n]
        state = {True: "connected", False: "not connected", None: "connection unknown"}[layout.connected]
        lines.append(f"- {layout.name} [{layout.id}]: {', '.join([shape] + extras)} · {state}"
                     f" · geometry from {layout.source}")
        selected = view.selected(layout.id)
        profiles = view.profiles(layout.id)
        lines.append("    profiles: " + ", ".join(p + (" (selected)" if p == selected else "") for p in profiles))
    lines.append("")
    plugins = view.catalog.plugins
    lines.append("Plugins: " + ", ".join(
        f"{info.name} {info.version}" + (" [pending install]" if folder in view.catalog.pending else "")
        + (f" [ERROR: {info.error}]" if info.error else "")
        for folder, info in sorted(plugins.items()) if folder not in (BRIDGE_ID, PLUGIN_ID)))
    lines.append(f"Bridge plugin (records device geometry): {bridge_status(paths)}")
    plugin_line = plugin_status(paths)
    if plugin_line:
        lines.append(plugin_line)
    lines.append("")
    lines.append("Next: show_profile to see a profile's keys; list_actions/describe_action to pick actions; "
                 "set_key etc. to stage edits; preview_changes and apply_changes to write them.")
    return "\n".join(lines)


def show_profile(view: View, device: str, profile: str | None, show_settings: bool, show_children: bool) -> str:
    layout = view.layout(device)
    profile_id = profile or view.selected(layout.id)
    doc = view.profile(layout.id, profile_id)
    changed = view.changed_slots(layout.id, profile_id)
    selected = view.selected(layout.id)
    head = (f"{layout.name} [{layout.id}] · profile \"{profile_id}\""
            + (" (selected)" if profile_id == selected else "")
            + (f" · {len(changed)} staged change(s) marked *" if changed else ""))
    body = grid(doc, layout, view.catalog, changed)
    listing = slot_list(doc, layout, view.catalog, show_settings, show_children, changed)
    note = ("Slot names (r1c1, key13, dial1, touch1, …/child2) are what the editing tools take."
            + ("" if layout.grid else " Grid size unknown: use key<N> or set_device_layout."))
    return f"{head}\n\n{body}\n\n{listing}\n\n{note}"


def list_actions(view: View, query: str | None, plugin: str | None, controller: str | None) -> str:
    entries = sorted(view.catalog.actions.values(), key=lambda e: (e.plugin_name.lower(), e.name.lower()))
    out = []
    for e in entries:
        snap = e.snapshot
        if controller and controller not in snap["controllers"]:
            continue
        if plugin and plugin.lower() not in (e.plugin_name.lower(), (e.plugin.folder if e.plugin else "opendeck")):
            if plugin.lower() not in e.plugin_name.lower():
                continue
        if query and query.lower() not in f"{e.label} {e.uuid} {snap.get('tooltip', '')}".lower():
            continue
        flags = ["/".join(snap["controllers"])]
        if len(snap["states"]) > 1:
            flags.append(f"{len(snap['states'])} states")
        if not snap["supported_in_multi_actions"]:
            flags.append("not in multi-actions")
        if e.plugin and e.plugin.folder in view.catalog.pending:
            flags.append("pending install")
        tip = f" — {snap['tooltip']}" if snap.get("tooltip") else ""
        out.append(f"- {e.label} [{e.uuid}] ({', '.join(flags)}){tip}")
    if not out:
        return "No actions match."
    return "\n".join(out) + "\n\nUse describe_action for an action's settings before placing it."


def _examples(view: View, uuid: str, limit: int = 3) -> list[dict]:
    """Settings of existing keys with this action (redacted), as concrete examples."""
    found = []
    for device in view.layouts():
        for profile_id in view.profiles(device):
            try:
                doc = view.profile(device, profile_id)
            except ProfileError:
                continue
            for array in ARRAY_FOR.values():
                for inst in doc.get(array) or []:
                    for item in [inst] + list((inst or {}).get("children") or []):
                        if item and item["action"]["uuid"] == uuid and item.get("settings"):
                            found.append(redact(item["settings"]))
                            if len(found) >= limit:
                                return found
    return found


def describe_action(view: View, action: str) -> str:
    entry = view.catalog.find(action)
    snap = entry.snapshot
    lines = [f"{entry.label}", f"uuid: {entry.uuid}", f"controllers: {', '.join(snap['controllers'])}",
             f"states: {len(snap['states'])}" + (" (OpenDeck toggles between them on each press)"
                                                 if len(snap["states"]) == 2 and not snap[
                                                     "disable_automatic_states"] else ""),
             f"usable in multi-actions: {'yes' if snap['supported_in_multi_actions'] else 'no'}"]
    if snap.get("tooltip"):
        lines.append(f"tooltip: {snap['tooltip']}")
    known = knowledge.KNOWN.get(entry.uuid)
    if known:
        lines.append("")
        lines.append(known["summary"])
        if known.get("settings"):
            lines.append("settings:")
            lines += [f"  {k}: {v}" for k, v in known["settings"].items()]
        for key in ("syntax", "notes"):
            if known.get(key):
                lines.append(f"{key}: {known[key]}")
        if known.get("example"):
            lines.append("example settings: " + json.dumps(known["example"], ensure_ascii=False))
        if known.get("danger"):
            lines.append(f"⚠ {known['danger']}")
    elif snap.get("property_inspector"):
        hints = pi_scrape.scrape(view.paths.config_dir / snap["property_inspector"])
        lines.append("")
        lines.append("No curated schema for this plugin. Hints from its settings page "
                     f"({snap['property_inspector']}):")
        if hints:
            lines.append(json.dumps(hints, ensure_ascii=False, indent=1)[:3000])
        else:
            lines.append("  (nothing recognisable; the page is probably built by a script)")
        lines.append("If unsure, place the action with no settings and configure it in OpenDeck's UI, "
                     "or copy the shape of an existing key (examples below).")
    examples = _examples(view, entry.uuid)
    if examples:
        lines.append("")
        lines.append("settings on your existing keys (secrets redacted):")
        lines += ["  " + json.dumps(ex, ensure_ascii=False)[:800] for ex in examples]
    return "\n".join(lines)


def _plugin_folder(view: View, plugin: str) -> str:
    plugins = view.catalog.plugins
    if plugin in plugins:
        return plugin
    matches = [f for f, info in plugins.items()
               if plugin.lower() in (f.lower(), info.name.lower()) or plugin.lower() in info.name.lower()]
    if len(matches) == 1:
        return matches[0]
    raise ProfileError(f"plugin {plugin!r} not found or ambiguous; installed: {', '.join(plugins)}")


def get_plugin_settings(view: View, plugin: str) -> str:
    folder = _plugin_folder(view, plugin)
    settings = view.plugin_settings(folder)
    if settings is None:
        return f"{folder} has no global settings saved."
    return (f"{folder} global settings (secrets redacted; write with set_plugin_settings):\n"
            + json.dumps(redact(settings), ensure_ascii=False, indent=2))


def read_logs(view: View, plugin: str | None, lines: int, grep: str | None) -> str:
    log_dir = view.paths.log_dir
    if not plugin or plugin.lower() == "opendeck":
        path = log_dir / "OpenDeck.log"
    else:
        path = log_dir / "plugins" / f"{_plugin_folder(view, plugin)}.log"
    if not path.exists():
        return f"No log at {path}."
    pattern = re.compile(grep, re.IGNORECASE) if grep else None
    tail: deque[str] = deque(maxlen=max(1, min(lines, 500)))
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if pattern is None or pattern.search(line):
                tail.append(line.rstrip("\n"))
    body = "\n".join(redact_text(line) for line in tail)
    return f"{path} (last {len(tail)} lines{', filtered' if grep else ''}):\n{body}"


def get_app_rules(view: View, running: bool = True) -> str:
    rules = view.app_rules()
    if not rules:
        lines = ["No automatic profile switching rules yet. Add one with set_app_rule."]
    else:
        lines = ["Automatic profile switching (when an app is focused, switch these devices):"]
        for app, mapping in sorted(rules.items()):
            name = "(any other app)" if app == "opendeck_default" else app
            lines.append(f"- {name}: " + ", ".join(f"{d} → {p}" for d, p in mapping.items()))
    if running:
        from ..core.apps import running_app_names

        names = running_app_names()
        if names:
            lines += ["", "Names to use for apps running now (exactly as OpenDeck matches them): "
                      + ", ".join(names)]
    lines.append("set_app_rule(app='opendeck_default', ...) sets the profile for every app without a rule.")
    return "\n".join(lines)
