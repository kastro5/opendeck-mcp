"""Staged operations: small JSON records replayed onto a Workspace.

Each op stores fully-resolved arguments (device and profile ids, slot
controller/position, action uuid, images as data URLs) plus `pre`, a
fingerprint of what it expected to find. Replaying onto a freshly read config
re-checks `pre`, so an edit made in OpenDeck's UI after staging is reported
as a conflict instead of being silently overwritten.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from ..core import images, knowledge
from ..core.jsonio import merge_patch
from ..core.manifest import FONT_STYLES, FONTS, make_state, new_instance
from ..core.profile import PARENT_UUIDS, ProfileError, check_profile_id
from ..core.secrets import restore_placeholders
from .workspace import Workspace

TOGGLE_IMAGE = "opendeck/toggle-action.png"
STYLE_KEYS = {"show": "show", "colour": "colour", "color": "colour", "size": "size", "family": "family",
              "font": "family", "style": "style", "alignment": "alignment", "underline": "underline",
              "stroke_colour": "stroke_colour", "stroke_color": "stroke_colour", "stroke_size": "stroke_size",
              "background_colour": "background_colour", "background_color": "background_colour",
              "background": "background_colour", "image_scale": "image_scale"}


class Conflict(Exception):
    """The config no longer looks the way it did when the op was staged."""


def fingerprint(instance: dict | None) -> str:
    if instance is None:
        return "EMPTY"
    kids = ",".join(k["action"]["uuid"] for k in instance.get("children") or [])
    return instance["action"]["uuid"] + (f"[{kids}]" if instance.get("children") is not None else "")


# -- helpers ----------------------------------------------------------------------- #

def _label(ws: Workspace, a: dict, position_key: str = "position", child: int | None = None) -> str:
    label = ws.layout(a["device"]).label(a["controller"], a[position_key])
    return label + (f"/child{child}" if child else "")


def _slot(ws: Workspace, a: dict, position_key: str = "position") -> dict | None:
    return ws.get_slot(a["device"], a["profile"], a["controller"], a[position_key])


def _target(ws: Workspace, a: dict) -> dict:
    """The instance an op addresses: a slot, or one child of a multi/toggle action."""
    inst = _slot(ws, a)
    if inst is None:
        raise Conflict(f"{_label(ws, a)} is empty")
    child = a.get("child")
    if child:
        kids = inst.get("children")
        if kids is None:
            raise ProfileError(f"{_label(ws, a)} isn't a multi/toggle action; it has no children")
        if not 1 <= child <= len(kids):
            raise ProfileError(f"{_label(ws, a)} has {len(kids)} children; there is no child{child}")
        return kids[child - 1]
    return inst


def _mark_images_stale(ws: Workspace, device: str, profile_id: str, inst: dict | None) -> None:
    if inst is None:
        return
    for item in [inst] + list(inst.get("children") or []):
        ws.stale_dirs.add(images.image_dir(ws.paths, device, profile_id, item["context"]))


def _inline(ws: Workspace, device: str, profile_id: str, inst: dict | None) -> None:
    """Make an instance's custom images travel with it (it's about to change context)."""
    if inst is not None:
        for folder in images.inline_images(inst, ws.paths, device, profile_id):
            ws.stale_dirs.add(folder)
        _mark_images_stale(ws, device, profile_id, inst)


def apply_style(states: list[dict], which: list[int], title: str | None, style: dict | None,
                image: str | None, action_states: list[dict]) -> list[str]:
    warnings = []
    for i in which:
        state = states[i]
        if title is not None:
            state["text"] = title
            if title and not (style and "show" in style):
                state["show"] = True
        for key, value in (style or {}).items():
            field = STYLE_KEYS.get(key)
            if field is None:
                raise ProfileError(f"unknown title_style key {key!r}; use {', '.join(sorted(set(STYLE_KEYS)))}")
            if field in ("size", "stroke_size", "image_scale"):
                value = int(value)
            if field in ("show", "underline"):
                value = bool(value)
            if field == "alignment" and value not in ("top", "middle", "bottom"):
                raise ProfileError("alignment must be top, middle or bottom")
            if field == "style" and value not in FONT_STYLES:
                raise ProfileError(f"style must be one of {', '.join(FONT_STYLES)}")
            if field == "family" and value not in FONTS:
                warnings.append(f"font {value!r} isn't bundled with OpenDeck ({', '.join(FONTS)}); "
                                "it may fall back to a default")
            if field in ("colour", "stroke_colour", "background_colour"):
                value = str(value)
                if not (value.startswith("#") and len(value) in (4, 7, 9)):
                    raise ProfileError(f"{key} must be a hex colour like #FF8800")
            state[field] = value
        if image is not None:
            if image == "default":
                state["image"] = action_states[min(i, len(action_states) - 1)]["image"]
            else:
                state["image"] = image
    return warnings


def _check_settings(settings: Any) -> dict:
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise ProfileError("settings must be a JSON object")
    return settings


# -- operations ----------------------------------------------------------------------- #

def op_set_key(ws: Workspace, a: dict) -> list[str]:
    entry = ws.catalog.actions.get(a["action"])
    if entry is None:
        raise Conflict(f"action {a['action']} is no longer installed")
    snap = entry.snapshot
    if a["controller"] not in snap["controllers"]:
        raise ProfileError(f"{entry.label} can't go on a {a['controller']} slot")
    old = _slot(ws, a)
    _mark_images_stale(ws, a["device"], a["profile"], old)
    inst = new_instance(snap, a["controller"], a["position"])
    inst["settings"] = copy.deepcopy(_check_settings(a.get("settings")))
    warnings = apply_style(inst["states"], list(range(len(inst["states"]))), a.get("title"), a.get("title_style"),
                           a.get("image"), snap["states"])
    ws.set_slot(a["device"], a["profile"], a["controller"], a["position"], inst, _label(ws, a))
    note = knowledge.danger_note(snap["uuid"], inst["settings"])
    if note:
        warnings.append(f"{_label(ws, a)} {note}")
    quirk = ws.layout(a["device"]).quirks.get(a["position"]) if a["controller"] == "Keypad" else None
    if quirk and quirk.startswith("unused"):
        warnings.append(f"{_label(ws, a)} is {quirk}; nothing placed there will be visible")
    return warnings


def op_update_key(ws: Workspace, a: dict) -> list[str]:
    inst = _target(ws, a)
    ws.touch(a["device"], a["profile"], _label(ws, a, child=a.get("child")))
    states = inst["states"]
    which = list(range(len(states))) if a.get("state") in (None, "all") else [int(a["state"])]
    if any(not 0 <= i < len(states) for i in which):
        raise ProfileError(f"state must be 0..{len(states) - 1} or 'all'")
    warnings = apply_style(states, which, a.get("title"), a.get("title_style"), a.get("image"),
                           inst["action"]["states"])
    if a.get("settings_replace") is not None:
        inst["settings"] = copy.deepcopy(_check_settings(a["settings_replace"]))
    if a.get("settings_merge") is not None:
        patch = restore_placeholders(_check_settings(a["settings_merge"]), inst.get("settings"))
        inst["settings"] = merge_patch(inst.get("settings") or {}, patch)
    for key in a.get("remove_settings") or []:
        (inst.get("settings") or {}).pop(key, None)
    if a.get("current_state") is not None:
        if not 0 <= int(a["current_state"]) < len(states):
            raise ProfileError(f"current_state must be 0..{len(states) - 1}")
        inst["current_state"] = int(a["current_state"])
    note = knowledge.danger_note(inst["action"]["uuid"], inst.get("settings"))
    if note:
        warnings.append(f"{_label(ws, a, child=a.get('child'))} {note}")
    return warnings


def op_clear_key(ws: Workspace, a: dict) -> list[str]:
    if a.get("child"):
        return _remove_child(ws, a)
    old = _slot(ws, a)
    if old is None:
        return []
    _mark_images_stale(ws, a["device"], a["profile"], old)
    ws.set_slot(a["device"], a["profile"], a["controller"], a["position"], None, _label(ws, a))
    return []


def _remove_child(ws: Workspace, a: dict) -> list[str]:
    parent = _slot(ws, a)
    _target(ws, a)  # validates the child number
    _inline(ws, a["device"], a["profile"], parent)
    n = a["child"]
    kids = parent["children"]
    kids.pop(n - 1)
    if parent["action"]["uuid"] == "opendeck.multiaction":
        delays = (parent.get("settings") or {}).get("delays")
        if isinstance(delays, list) and n - 1 < len(delays):
            delays.pop(n - 1)
    else:  # toggle: one state per child (at least one)
        if len(parent["states"]) > max(1, len(kids)):
            parent["states"].pop(n - 1 if n - 1 < len(parent["states"]) else -1)
        parent["current_state"] = min(parent.get("current_state", 0), len(parent["states"]) - 1)
    ws.set_slot(a["device"], a["profile"], a["controller"], a["position"], parent, _label(ws, a))
    ws.changed.setdefault((a["device"], a["profile"]), set()).add(_label(ws, a, child=n))
    return []


def op_add_child(ws: Workspace, a: dict) -> list[str]:
    parent = _slot(ws, a)
    if parent is None or parent["action"]["uuid"] not in PARENT_UUIDS:
        raise ProfileError(f"{_label(ws, a)} must hold a Multi Action or Toggle Action first (use set_key)")
    entry = ws.catalog.actions.get(a["action"])
    if entry is None:
        raise Conflict(f"action {a['action']} is no longer installed")
    snap = entry.snapshot
    if snap["uuid"] in PARENT_UUIDS:
        raise ProfileError("multi/toggle actions can't be nested")
    if parent["action"]["uuid"] == "opendeck.multiaction" and not snap["supported_in_multi_actions"]:
        raise ProfileError(f"{entry.label} doesn't support being in a multi action")
    if "Keypad" not in snap["controllers"]:
        raise ProfileError(f"{entry.label} isn't a key action")
    _inline(ws, a["device"], a["profile"], parent)
    kids = parent["children"]
    at = a.get("at") or len(kids) + 1
    if not 1 <= at <= len(kids) + 1:
        raise ProfileError(f"position must be 1..{len(kids) + 1}")
    child = new_instance(snap, "Keypad", a["position"], at)
    child["settings"] = copy.deepcopy(_check_settings(a.get("settings")))
    warnings = apply_style(child["states"], list(range(len(child["states"]))), a.get("title"),
                           a.get("title_style"), a.get("image"), snap["states"])
    kids.insert(at - 1, child)
    if parent["action"]["uuid"] == "opendeck.toggleaction":
        if len(parent["states"]) < len(kids):
            parent["states"].insert(at - 1, make_state({"image": TOGGLE_IMAGE}))
    else:
        delays = (parent.get("settings") or {}).get("delays")
        if a.get("delay_ms") is not None or isinstance(delays, list):
            delays = list(delays or [])
            while len(delays) < len(kids) - 1:
                delays.append(100)
            delays.insert(at - 1, int(a["delay_ms"]) if a.get("delay_ms") is not None else 100)
            parent.setdefault("settings", {})["delays"] = delays[: len(kids)]
    ws.set_slot(a["device"], a["profile"], a["controller"], a["position"], parent, _label(ws, a))
    ws.changed[(a["device"], a["profile"])].add(_label(ws, a, child=at))
    note = knowledge.danger_note(snap["uuid"], child["settings"])
    if note:
        warnings.append(f"{_label(ws, a, child=at)} {note}")
    return warnings


def op_set_multi_delays(ws: Workspace, a: dict) -> list[str]:
    parent = _slot(ws, a)
    if parent is None or parent["action"]["uuid"] != "opendeck.multiaction":
        raise ProfileError(f"{_label(ws, a)} isn't a Multi Action")
    delays = [int(d) for d in a["delays"]]
    if any(d < 0 for d in delays):
        raise ProfileError("delays can't be negative")
    if len(delays) > len(parent["children"]):
        raise ProfileError(f"{len(delays)} delays for {len(parent['children'])} children")
    ws.touch(a["device"], a["profile"], _label(ws, a))
    parent.setdefault("settings", {})["delays"] = delays
    return []


def op_move_key(ws: Workspace, a: dict) -> list[str]:
    src_dev, src_prof = a["device"], a["profile"]
    dst_dev, dst_prof = a.get("to_device") or src_dev, a.get("to_profile") or src_prof
    controller = a["controller"]
    src = ws.get_slot(src_dev, src_prof, controller, a["position"])
    if src is None:
        raise Conflict(f"{_label(ws, a)} is empty; nothing to move")
    dst_layout = ws.layout(dst_dev)
    limit = {"Keypad": dst_layout.keys, "Encoder": dst_layout.encoders, "Infobar": dst_layout.infobars}[controller]
    if not 0 <= a["to_position"] < limit:
        raise ProfileError(f"the destination slot doesn't exist on {dst_dev}")
    dst = ws.get_slot(dst_dev, dst_prof, controller, a["to_position"])
    mode = a.get("mode", "move")
    if mode in ("move", "copy") and dst is not None:
        raise ProfileError(f"{dst_layout.label(controller, a['to_position'])} is occupied; clear it first or "
                           "use mode='swap'")
    src_label = _label(ws, a)
    dst_label = dst_layout.label(controller, a["to_position"])
    moving = copy.deepcopy(src)
    _inline(ws, src_dev, src_prof, moving)
    if mode == "swap" and dst is not None:
        other = copy.deepcopy(dst)
        _inline(ws, dst_dev, dst_prof, other)
        ws.set_slot(src_dev, src_prof, controller, a["position"], other, src_label)
    elif mode == "move":
        ws.set_slot(src_dev, src_prof, controller, a["position"], None, src_label)
    if mode == "copy":
        # The original stays (and keeps its own image folder).
        ws.stale_dirs.difference_update({images.image_dir(ws.paths, src_dev, src_prof, i["context"])
                                         for i in [src] + list(src.get("children") or [])})
    ws.set_slot(dst_dev, dst_prof, controller, a["to_position"], moving, dst_label)
    return []


# -- profiles ------------------------------------------------------------------------- #

def op_create_profile(ws: Workspace, a: dict) -> list[str]:
    device, new = a["device"], check_profile_id(a["profile"])
    if ws.has_profile(device, new):
        raise Conflict(f"profile {new!r} already exists on {device}")
    if a.get("copy_from"):
        doc = ws.copy_doc(device, a["copy_from"])
        for array in ("keys", "sliders", "infobars"):
            for inst in doc.get(array) or []:
                if inst:
                    images.inline_images(inst, ws.paths, device, a["copy_from"])
        ws.notes.append(f"{device}: + profile {new} (copy of {a['copy_from']})")
    else:
        doc = ws.empty_doc(device)
        ws.notes.append(f"{device}: + profile {new} (empty)")
    ws.create_profile(device, new, doc)
    return []


def _references(ws: Workspace, device: str, profile_id: str) -> list[tuple[tuple[str, str], dict, str]]:
    """Starter Pack Switch Profile keys pointing at device/profile: ((dev, profile), instance, key)."""
    found = []
    for (dev, pid), doc in ws.loaded_docs().items():
        for array in ("keys", "sliders", "infobars"):
            for inst in doc.get(array) or []:
                for item in [inst] + list((inst or {}).get("children") or []):
                    if not item or item["action"]["uuid"] != "com.amansprojects.starterpack.switchprofile":
                        continue
                    settings = item.get("settings") or {}
                    target_device = settings.get("device") or dev
                    if target_device != device:
                        continue
                    for key in ("profile", "clockwise", "anticlockwise"):
                        if settings.get(key) == profile_id:
                            found.append(((dev, pid), item, key))
    return found


def op_rename_profile(ws: Workspace, a: dict) -> list[str]:
    device, old, new = a["device"], a["profile"], check_profile_id(a["new_name"])
    if not ws.has_profile(device, old):
        raise Conflict(f"profile {old!r} no longer exists on {device}")
    if ws.has_profile(device, new):
        raise Conflict(f"profile {new!r} already exists on {device}")
    doc = ws.copy_doc(device, old)
    for array in ("keys", "sliders", "infobars"):
        for inst in doc.get(array) or []:
            if inst:
                images.inline_images(inst, ws.paths, device, old)
    was_selected = ws.selected(device) == old
    ws.create_profile(device, new, doc)
    fixes = []
    # Includes keys inside the new copy itself; the old profile is about to go.
    refs = [r for r in _references(ws, device, old) if r[0] != (device, old)]
    for (dev, pid), item, key in refs:
        item["settings"][key] = new
        ws.touch(dev, pid)
    if refs:
        fixes.append(f"{len(refs)} Switch Profile key(s) updated")
    apps = ws.apps()
    hits = [app for app, mapping in apps.items() if mapping.get(device) == old]
    if hits:
        apps = ws.touch_apps()
        for app in hits:
            apps[app][device] = new
        fixes.append(f"{len(hits)} app rule(s) updated")
    ws.delete_profile(device, old)
    if was_selected:
        ws.set_selected(device, new)
    ws.notes.append(f"{device}: ~ profile {old} → {new}" + (f" ({'; '.join(fixes)})" if fixes else ""))
    return []


def op_delete_profile(ws: Workspace, a: dict) -> list[str]:
    device, pid = a["device"], a["profile"]
    if not ws.has_profile(device, pid):
        raise Conflict(f"profile {pid!r} no longer exists on {device}")
    if len(ws.profiles(device)) <= 1:
        raise ProfileError("can't delete a device's only profile")
    warnings = []
    refs = [r for r in _references(ws, device, pid) if r[0] != (device, pid)]
    if refs:
        warnings.append(f"{len(refs)} Switch Profile key(s) still point at the deleted profile {pid!r}")
    ws.delete_profile(device, pid)
    if ws.selected(device) == pid:
        remaining = ws.profiles(device)
        ws.set_selected(device, "Default" if "Default" in remaining else remaining[0])
    apps = ws.apps()
    hits = [app for app, mapping in apps.items() if mapping.get(device) == pid]
    if hits:
        apps = ws.touch_apps()
        for app in hits:
            del apps[app][device]
            if not apps[app]:
                del apps[app]
    ws.notes.append(f"{device}: - profile {pid}" + (f" ({len(hits)} app rule(s) removed)" if hits else ""))
    return warnings


def op_set_default_profile(ws: Workspace, a: dict) -> list[str]:
    if not ws.has_profile(a["device"], a["profile"]):
        raise Conflict(f"profile {a['profile']!r} doesn't exist on {a['device']}")
    ws.set_selected(a["device"], a["profile"])
    ws.notes.append(f"{a['device']}: selected profile → {a['profile']}")
    return []


def op_set_app_rule(ws: Workspace, a: dict) -> list[str]:
    apps = ws.touch_apps()
    app, device, pid = a["app"], a["device"], a.get("profile")
    if pid is None:
        if device in apps.get(app, {}):
            del apps[app][device]
            if not apps[app]:
                del apps[app]
        ws.notes.append(f"app rule removed: {app} / {device}")
        return []
    if not ws.has_profile(device, pid):
        raise Conflict(f"profile {pid!r} doesn't exist on {device}")
    apps.setdefault(app, {})[device] = pid
    name = "any other app" if app == "opendeck_default" else app
    ws.notes.append(f"app rule: {name} → {device}: {pid}")
    return []


def op_set_plugin_settings(ws: Workspace, a: dict) -> list[str]:
    folder = a["plugin"]
    current = ws.plugin_settings(folder) or {}
    if not isinstance(current, dict):
        current = {}
    values = restore_placeholders(_check_settings(a.get("values")), current)
    new = merge_patch(current, values)
    for key in a.get("remove_keys") or []:
        new.pop(key, None)
    ws.set_plugin_settings(folder, new)
    ws.notes.append(f"plugin settings: {folder} ({', '.join(sorted(values)) or 'keys removed'})")
    return []


def op_install_plugin(ws: Workspace, a: dict) -> list[str]:
    from pathlib import Path

    if not Path(a["source"], "manifest.json").is_file():
        raise Conflict(f"the downloaded copy of {a['folder']} is gone from the cache; stage the install again")
    ws.plugin_removals.discard(a["folder"])
    ws.plugin_installs[a["folder"]] = a
    return []


def op_remove_plugin(ws: Workspace, a: dict) -> list[str]:
    folder = a["folder"]
    cleared = 0
    for (device, pid), doc in ws.loaded_docs().items():
        layout = ws.layout(device)
        for controller, array in (("Keypad", "keys"), ("Encoder", "sliders"), ("Infobar", "infobars")):
            for position, inst in enumerate(doc.get(array) or []):
                if inst is None:
                    continue
                label = layout.label(controller, position)
                if inst["action"].get("plugin") == folder:
                    _mark_images_stale(ws, device, pid, inst)
                    ws.set_slot(device, pid, controller, position, None, label)
                    cleared += 1
                    continue
                kids = inst.get("children") or []
                for n in range(len(kids), 0, -1):
                    if kids[n - 1]["action"].get("plugin") == folder:
                        _remove_child(ws, {"device": device, "profile": pid, "controller": controller,
                                           "position": position, "child": n})
                        cleared += 1
    ws.plugin_installs.pop(folder, None)
    ws.plugin_removals.add(folder)
    ws.set_plugin_settings(folder, None)
    ws.notes.append(f"plugin {folder} removed; {cleared} key(s)/child action(s) cleared")
    return []


OPS: dict[str, Callable[[Workspace, dict], list[str]]] = {
    "install_plugin": op_install_plugin,
    "install_bridge": op_install_plugin,
    "remove_plugin": op_remove_plugin,
    "set_key": op_set_key,
    "update_key": op_update_key,
    "clear_key": op_clear_key,
    "add_child": op_add_child,
    "set_multi_delays": op_set_multi_delays,
    "move_key": op_move_key,
    "create_profile": op_create_profile,
    "rename_profile": op_rename_profile,
    "delete_profile": op_delete_profile,
    "set_default_profile": op_set_default_profile,
    "set_app_rule": op_set_app_rule,
    "set_plugin_settings": op_set_plugin_settings,
}


# -- preconditions ------------------------------------------------------------------- #

def precondition(ws: Workspace, op: str, a: dict) -> dict:
    """What the op expects to find, recorded at staging time."""
    pre: dict[str, Any] = {}
    if "controller" in a:
        pre["slot"] = fingerprint(ws.get_slot(a["device"], a["profile"], a["controller"], a["position"]))
        if op == "move_key":
            pre["to_slot"] = fingerprint(ws.get_slot(a.get("to_device") or a["device"],
                                                     a.get("to_profile") or a["profile"],
                                                     a["controller"], a["to_position"]))
    if op in ("rename_profile", "delete_profile", "set_default_profile", "create_profile"):
        pre["exists"] = ws.has_profile(a["device"], a["profile"])
    return pre


def check_precondition(ws: Workspace, op: str, a: dict, pre: dict) -> None:
    now = precondition(ws, op, a)
    for key, expected in pre.items():
        if now.get(key) != expected:
            where = a.get("profile", "")
            raise Conflict(f"{op} on {a.get('device', '')} {where}: expected {key}={expected}, "
                           f"found {now.get(key)} (changed in OpenDeck since it was staged)")


def run(ws: Workspace, record: dict, check: bool = True) -> list[str]:
    op, a = record["op"], record["args"]
    if check and record.get("pre"):
        check_precondition(ws, op, a, record["pre"])
    return OPS[op](ws, a)
