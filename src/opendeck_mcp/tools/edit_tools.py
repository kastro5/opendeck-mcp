"""Staging tools: every edit becomes an op in the plan; nothing touches OpenDeck until apply."""

from __future__ import annotations

import json
from typing import Any

from ..core import images
from ..core.devices import parse_slot
from ..core.jsonio import atomic_write, read_json
from ..core.paths import Paths
from ..core.profile import ProfileError, check_profile_id
from ..core.validate import profile_errors
from ..staging import diff, ops, plan
from ..staging.workspace import DELETED
from . import bridge_hook
from .view import build_workspace

STAGED_ONLY = "Staged only — nothing changed yet. Review with preview_changes, then apply_changes."


def describe(record: dict) -> str:
    a = record["args"]
    where = " / ".join(str(a[k]) for k in ("device", "profile") if a.get(k))
    slot = a.get("slot_label", "")
    detail = {
        "set_key": lambda: f"set {slot} → {a.get('action_label', a.get('action'))}"
                           + (f' "{a["title"]}"' if a.get("title") else ""),
        "update_key": lambda: f"update {slot}",
        "clear_key": lambda: f"clear {slot}",
        "add_child": lambda: f"add {a.get('action_label', a.get('action'))} to {slot}",
        "set_multi_delays": lambda: f"delays of {slot} → {a['delays']}",
        "move_key": lambda: f"{a.get('mode', 'move')} {slot} → {a.get('to_label')}"
                            + (f" ({a.get('to_device') or ''} {a.get('to_profile') or ''})".rstrip()
                               if a.get("to_device") or a.get("to_profile") else ""),
        "create_profile": lambda: f"create profile {a['profile']}"
                                  + (f" from {a['copy_from']}" if a.get("copy_from") else ""),
        "rename_profile": lambda: f"rename profile → {a['new_name']}",
        "delete_profile": lambda: "delete profile",
        "set_default_profile": lambda: "make selected profile",
        "set_app_rule": lambda: f"app rule {a['app']} → {a.get('profile') or '(removed)'}",
        "set_plugin_settings": lambda: f"plugin settings {a['plugin']}",
        "install_plugin": lambda: f"install plugin {a['folder']} {a.get('version', '')}",
        "install_bridge": lambda: f"install bridge plugin {a.get('version', '')}",
        "remove_plugin": lambda: f"remove plugin {a['folder']}",
    }.get(record["op"], lambda: record["op"])()
    return f"{record['id']}: {detail}" + (f" [{where}]" if where else "")


def stage(paths: Paths, op: str, args: dict[str, Any]) -> str:
    with plan.locked(paths):
        current = plan.load(paths)
        records = list(current["ops"])
        extra = bridge_hook.bridge_records(paths, records, current)
        ws, _warnings, conflicts = build_workspace(paths, records + extra)
        record = {"id": f"op{current['next_id'] + len(extra)}", "op": op, "args": args}
        record["pre"] = ops.precondition(ws, op, args)
        warnings = ops.run(ws, record, check=False)
        problems = []
        for key in ws.dirty_docs:
            doc = ws._docs.get(key)
            if doc is not None and doc is not DELETED:
                problems += [f"{key[0]}/{key[1]}: {e}" for e in profile_errors(doc, ws.catalog.plugin_exists)]
        if problems:
            raise ProfileError("that change would leave an invalid profile: " + "; ".join(problems[:5]))
        current["next_id"] += 1 + len(extra)
        current["ops"] = records + extra + [record]
        plan.save(paths, current)
    lines = [f"Staged {describe(record)}."]
    if extra:
        lines.append("Also staged: " + "; ".join(describe(r) for r in extra)
                     + " (records device geometry; installed with the same restart).")
    lines += [f"⚠ {w}" for w in warnings]
    if conflicts:
        lines.append(f"Note: {len(conflicts)} earlier staged op(s) currently conflict; see preview_changes.")
    lines.append(STAGED_ONLY)
    return "\n".join(lines)


# -- argument resolution ------------------------------------------------------------- #

def _base(paths: Paths, device: str, profile: str | None) -> tuple[Any, dict]:
    with plan.locked(paths):
        ws, _, _ = build_workspace(paths, plan.load(paths)["ops"])
    layout = ws.layout(device)
    profile_id = profile or ws.selected(layout.id)
    if not ws.has_profile(layout.id, profile_id):
        raise ProfileError(f"profile {profile_id!r} doesn't exist on {layout.id}; "
                           f"existing: {', '.join(ws.profiles(layout.id))}")
    return ws, {"device": layout.id, "profile": profile_id}


def _slot_args(ws, base: dict, slot: str) -> dict:
    layout = ws.layout(base["device"])
    ref = parse_slot(slot, layout)
    args = dict(base, controller=ref.controller, position=ref.position)
    if ref.child:
        args["child"] = ref.child
    args["slot_label"] = layout.label(ref.controller, ref.position) + (f"/child{ref.child}" if ref.child else "")
    return args


def _image(image: str | None) -> str | None:
    if image is None or image == "default":
        return image
    return images.load_image(image)


def _obj(value: Any, name: str) -> Any:
    """Accept an object, or a JSON string of one (some clients send strings)."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ProfileError(f"{name} must be a JSON object") from exc
    if value is not None and not isinstance(value, dict):
        raise ProfileError(f"{name} must be a JSON object")
    return value


# -- tools --------------------------------------------------------------------------- #

def set_key(paths: Paths, device: str, slot: str, action: str, profile: str | None, title: str | None,
            settings: Any, image: str | None, title_style: Any) -> str:
    ws, base = _base(paths, device, profile)
    args = _slot_args(ws, base, slot)
    if args.get("child"):
        raise ProfileError("use add_child to put an action inside a multi/toggle action")
    entry = ws.catalog.find(action, args["controller"])
    args.update(action=entry.uuid, action_label=entry.label, settings=_obj(settings, "settings"),
                title=title, title_style=_obj(title_style, "title_style"), image=_image(image))
    return stage(paths, "set_key", args)


def update_key(paths: Paths, device: str, slot: str, profile: str | None, state: Any, title: str | None,
               title_style: Any, image: str | None, settings_merge: Any, settings_replace: Any,
               remove_settings: list[str] | None, current_state: int | None) -> str:
    ws, base = _base(paths, device, profile)
    args = _slot_args(ws, base, slot)
    args.update(state=state, title=title, title_style=_obj(title_style, "title_style"), image=_image(image),
                settings_merge=_obj(settings_merge, "settings_merge"),
                settings_replace=_obj(settings_replace, "settings_replace"),
                remove_settings=remove_settings, current_state=current_state)
    if all(args.get(k) is None for k in ("title", "title_style", "image", "settings_merge", "settings_replace",
                                         "remove_settings", "current_state")):
        raise ProfileError("nothing to update: pass title, title_style, image, settings_merge, "
                           "settings_replace, remove_settings or current_state")
    return stage(paths, "update_key", args)


def clear_key(paths: Paths, device: str, slot: str, profile: str | None) -> str:
    ws, base = _base(paths, device, profile)
    return stage(paths, "clear_key", _slot_args(ws, base, slot))


def move_key(paths: Paths, device: str, from_slot: str, to_slot: str, mode: str, profile: str | None,
             to_profile: str | None, to_device: str | None) -> str:
    if mode not in ("move", "swap", "copy"):
        raise ProfileError("mode must be move, swap or copy")
    ws, base = _base(paths, device, profile)
    args = _slot_args(ws, base, from_slot)
    if args.get("child"):
        raise ProfileError("move whole keys; to reorder children remove and re-add them")
    dest_layout = ws.layout(to_device or base["device"])
    dest_profile = to_profile or (ws.selected(dest_layout.id) if to_device else base["profile"])
    if not ws.has_profile(dest_layout.id, dest_profile):
        raise ProfileError(f"profile {dest_profile!r} doesn't exist on {dest_layout.id}")
    ref = parse_slot(to_slot, dest_layout)
    if ref.controller != args["controller"]:
        raise ProfileError(f"can't move a {args['controller']} slot to a {ref.controller} slot")
    args.update(to_position=ref.position, mode=mode, to_label=dest_layout.label(ref.controller, ref.position),
                to_device=dest_layout.id if dest_layout.id != base["device"] else None,
                to_profile=dest_profile if dest_profile != base["profile"] or to_device else None)
    return stage(paths, "move_key", args)


def add_child(paths: Paths, device: str, slot: str, action: str, profile: str | None, settings: Any,
              title: str | None, title_style: Any, image: str | None, position: int | None,
              delay_ms: int | None) -> str:
    ws, base = _base(paths, device, profile)
    args = _slot_args(ws, base, slot)
    if args.get("child"):
        raise ProfileError("give the parent's slot (e.g. r1c1), not a child")
    entry = ws.catalog.find(action, "Keypad")
    args.update(action=entry.uuid, action_label=entry.label, settings=_obj(settings, "settings"), title=title,
                title_style=_obj(title_style, "title_style"), image=_image(image), at=position,
                delay_ms=delay_ms)
    return stage(paths, "add_child", args)


def remove_child(paths: Paths, device: str, slot: str, child: int, profile: str | None) -> str:
    ws, base = _base(paths, device, profile)
    args = _slot_args(ws, base, f"{slot.split('/')[0]}/child{child}")
    return stage(paths, "clear_key", args)


def set_multi_delays(paths: Paths, device: str, slot: str, delays_ms: list[int], profile: str | None) -> str:
    ws, base = _base(paths, device, profile)
    args = _slot_args(ws, base, slot)
    args["delays"] = [int(d) for d in delays_ms]
    return stage(paths, "set_multi_delays", args)


def manage_profile(paths: Paths, device: str, operation: str, profile: str, new_name: str | None,
                   copy_from: str | None) -> str:
    with plan.locked(paths):
        ws, _, _ = build_workspace(paths, plan.load(paths)["ops"])
    layout = ws.layout(device)
    args: dict[str, Any] = {"device": layout.id, "profile": profile}
    if operation in ("create", "duplicate"):
        check_profile_id(profile)
        if operation == "duplicate":
            source = copy_from or ws.selected(layout.id)
            if not ws.has_profile(layout.id, source):
                raise ProfileError(f"profile {source!r} doesn't exist on {layout.id}")
            args["copy_from"] = source
        elif copy_from:
            args["copy_from"] = copy_from
        return stage(paths, "create_profile", args)
    if not ws.has_profile(layout.id, profile):
        raise ProfileError(f"profile {profile!r} doesn't exist on {layout.id}; "
                           f"existing: {', '.join(ws.profiles(layout.id))}")
    if operation == "rename":
        if not new_name:
            raise ProfileError("rename needs new_name")
        args["new_name"] = check_profile_id(new_name)
        return stage(paths, "rename_profile", args)
    if operation == "delete":
        return stage(paths, "delete_profile", args)
    if operation in ("set_default", "select"):
        return stage(paths, "set_default_profile", args)
    raise ProfileError("operation must be create, duplicate, rename, delete or set_default")


def set_app_rule(paths: Paths, app: str, device: str, profile: str | None) -> str:
    with plan.locked(paths):
        ws, _, _ = build_workspace(paths, plan.load(paths)["ops"])
    layout = ws.layout(device)
    if not app.strip():
        raise ProfileError("app name is empty")
    return stage(paths, "set_app_rule", {"app": app.strip(), "device": layout.id, "profile": profile})


def set_plugin_settings(paths: Paths, plugin: str, values: Any, remove_keys: list[str] | None) -> str:
    from .read_tools import _plugin_folder
    from .view import View

    folder = _plugin_folder(View(paths), plugin)
    return stage(paths, "set_plugin_settings",
                 {"plugin": folder, "values": _obj(values, "values") or {}, "remove_keys": remove_keys or []})


def set_device_layout(paths: Paths, device: str, rows: int, columns: int) -> str:
    if not (1 <= rows <= 16 and 1 <= columns <= 16):
        raise ProfileError("rows and columns must be 1-16")
    file = paths.data_dir / "device_overrides.json"
    try:
        data = read_json(file)
    except (OSError, ValueError):
        data = {}
    data[device] = {"rows": rows, "columns": columns}
    atomic_write(file, json.dumps(data, indent=1).encode())
    return f"Saved: {device} is {rows}x{columns}. (Local to opendeck-mcp; OpenDeck is unchanged.)"


def preview_changes(paths: Paths, detail: str) -> str:
    with plan.locked(paths):
        records = plan.load(paths)["ops"]
    ws, warnings, conflicts = build_workspace(paths, records)
    if not records:
        return "Nothing is staged."
    listing = "\n".join("  " + describe(r) for r in records)
    body = diff.file_diffs(ws) if detail == "files" else diff.summary(ws, warnings, conflicts, len(records))
    return f"{body}\n\nStaged ops (ids work with discard_changes):\n{listing}"


def discard_changes(paths: Paths, which: Any) -> str:
    with plan.locked(paths):
        current = plan.load(paths)
        records = current["ops"]
        if not records:
            return "Nothing is staged."
        if which in (None, "all"):
            plan.clear(paths)
            return f"Discarded all {len(records)} staged op(s)."
        if which == "last":
            ids = {records[-1]["id"]}
        else:
            ids = set(which if isinstance(which, list) else str(which).replace(",", " ").split())
        keep = [r for r in records if r["id"] not in ids]
        if len(keep) == len(records):
            raise ProfileError(f"no staged op with id {', '.join(sorted(ids))}")
        current["ops"] = keep
        plan.save(paths, current)
    dropped = [describe(r) for r in records if r["id"] in ids]
    return "Discarded: " + "; ".join(dropped) + f". {len(keep)} op(s) still staged."
