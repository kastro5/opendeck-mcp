"""Staging tools: every edit becomes an op in the plan; nothing touches OpenDeck until apply."""

from __future__ import annotations

import copy
import json
import time
from typing import Any

from ..core import images
from ..core.devices import parse_slot
from ..core.jsonio import atomic_write, read_json
from ..core.paths import Paths
from ..core.profile import ProfileError, check_profile_id
from ..core.validate import profile_errors
from ..staging import diff, ops, plan
from ..staging.workspace import DELETED, Workspace
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
                            + (" (" + " ".join(x for x in (a.get("to_device"), a.get("to_profile")) if x) + ")"
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
               remove_settings: list[str] | None, current_state: int | None, mode: str = "auto") -> str:
    if mode not in ("auto", "live", "staged"):
        raise ProfileError("mode must be auto, live or staged")
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
    if mode != "staged":
        reason = live_blocker(paths, ws, args)
        if reason is None:
            try:
                return update_live(paths, args)
            except LiveFallback as exc:
                reason = str(exc)
        if mode == "live":
            raise ProfileError(f"can't apply this live: {reason}")
        return f"Not applied live ({reason}), so it was staged instead.\n" + stage(paths, "update_key", args)
    return stage(paths, "update_key", args)


# -- live edits ---------------------------------------------------------------------- #

class LiveFallback(Exception):
    """The edit can't go live after all; stage it instead."""


def live_blocker(paths: Paths, ws, args: dict) -> str | None:
    """Why an update_key can't be applied to the running OpenDeck, or None if it can."""
    from ..runtime import process

    if args.get("title_style") is not None or args.get("current_state") is not None:
        return "title style and current state changes need a restart"
    if not process.is_running(paths):
        return "OpenDeck isn't running"
    if ws.layout(args["device"]).connected is not True:
        return f"{args['device']} isn't connected"
    if Workspace(paths).selected(args["device"]) != args["profile"]:
        return f"{args['profile']} isn't the profile {args['device']} is showing"
    with plan.locked(paths):
        records = plan.load(paths)["ops"]
    if any(r["args"].get("device") == args["device"] for r in records):
        return f"{args['device']} has staged changes; apply or discard them first"
    return None


def _live_changes(paths: Paths, args: dict) -> tuple[dict, dict, dict, list[str]]:
    """Replay the edit on the freshly flushed profile. Returns (before, after, sends, warnings)."""
    fresh = Workspace(paths)
    before = copy.deepcopy(ops._target(fresh, args))
    warnings = ops.op_update_key(fresh, args)
    after = ops._target(fresh, args)
    titles, images_ = {}, {}
    for i, (old, new) in enumerate(zip(before["states"], after["states"], strict=True)):
        if {k: v for k, v in old.items() if k not in ("text", "image")} != \
                {k: v for k, v in new.items() if k not in ("text", "image")}:
            raise LiveFallback("the title's look changes too, which needs a restart")
        if old.get("text") != new.get("text"):
            titles[i] = new.get("text", "")
        if old.get("image") != new.get("image"):
            default = after["action"]["states"][min(i, len(after["action"]["states"]) - 1)].get("image")
            image = new.get("image")
            images_[i] = image if image and image != default and image.startswith("data:") else None
    settings = after.get("settings") if after.get("settings") != before.get("settings") else None
    if after.get("current_state") != before.get("current_state"):
        raise LiveFallback("current state changes need a restart")
    return before, after, {"titles": titles, "images": images_, "settings": settings}, warnings


def update_live(paths: Paths, args: dict) -> str:
    from ..runtime import live, process

    device, profile = args["device"], args["profile"]
    with plan.locked(paths):
        process.flush(paths, {device: profile})  # disk first catches up with what OpenDeck holds
        before, after, sends, warnings = _live_changes(paths, args)
        if not sends["titles"] and not sends["images"] and sends["settings"] is None:
            return f"No change: {args['slot_label']} already looks like that."
        states = sorted(set(sends["titles"]) | set(sends["images"]))
        snap = live.snapshot(paths, device, profile, before, states)
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "device": device, "profile": profile,
                 "slot_label": args["slot_label"], "context": before["context"],
                 "action": before["action"]["uuid"],
                 "before": {"titles": {str(i): snap["titles"][str(i)] for i in sends["titles"]},
                            "images": {str(i): snap["images"][str(i)] for i in sends["images"]}},
                 "after": {"titles": {str(i): t for i, t in sends["titles"].items()}}}
        if sends["settings"] is not None:
            entry["before"]["settings"] = before.get("settings") or {}
            entry["after"]["settings"] = sends["settings"]
        live.write_journal(paths, live.read_journal(paths) + [entry])
        context = live.context_string(device, profile, before)
        live.push(paths, context, sends["titles"], sends["images"], sends["settings"])
        saved = process.flush(paths, {device: profile}).get(device, False)
        problem = _live_mismatch(paths, args, entry["after"]) if saved else None
    changed = [f'title "{t}"' + (f" (state {i})" if len(after["states"]) > 1 else "")
               for i, t in sends["titles"].items()]
    changed += ["image" + (f" (state {i})" if len(after["states"]) > 1 else "") for i in sends["images"]]
    if sends["settings"] is not None:
        changed.append("settings")
    lines = [f"Applied live, no restart: {args['slot_label']} → {', '.join(changed)} [{device} / {profile}]."]
    lines += [f"⚠ {w}" for w in warnings]
    if not saved:
        lines.append("OpenDeck hasn't written it to disk yet; it saves on its own within 30 s.")
    elif problem:
        lines.append(f"⚠ Check the deck: after saving, OpenDeck's file shows {problem}.")
    lines.append("Undo with undo_live.")
    return "\n".join(lines)


def _live_mismatch(paths: Paths, args: dict, expected: dict) -> str | None:
    from ..runtime import live

    inst = live.read_instance(paths, args["device"], args["profile"], args["controller"], args["position"],
                              args.get("child"))
    if inst is None:
        return "the key empty"
    for i, text in expected.get("titles", {}).items():
        if inst["states"][int(i)].get("text") != text:
            return f"a different title on state {i}"
    if "settings" in expected and inst.get("settings") != expected["settings"]:
        return "different settings"
    return None


def undo_live(paths: Paths, count: int) -> str:
    from ..runtime import live, process

    if count < 1:
        raise ProfileError("count must be at least 1")
    with plan.locked(paths):
        entries = live.read_journal(paths)
        if not entries:
            return "There are no live edits to undo."
        undo, keep = entries[-count:][::-1], entries[:-count]
        if not process.is_running(paths):
            raise ProfileError("OpenDeck isn't running; start it first")
        done, skipped, kept, flushed = [], [], [], set()
        for entry in undo:
            device, profile = entry["device"], entry["profile"]
            if Workspace(paths).selected(device) != profile:
                skipped.append(f"{entry['slot_label']} ({profile} isn't showing on {device})")
                kept.append(entry)
                continue
            if (device, profile) not in flushed:
                process.flush(paths, {device: profile})
                flushed.add((device, profile))
            inst = _instance_by_context(paths, device, profile, entry["context"])
            if inst is None or inst["action"]["uuid"] != entry["action"]:
                skipped.append(f"{entry['slot_label']} (the key holds a different action now)")
                continue  # nothing left to undo it onto
            titles = {int(i): t for i, t in entry["before"].get("titles", {}).items()}
            images_ = {int(i): img for i, img in entry["before"].get("images", {}).items()}
            live.push(paths, live.context_string(device, profile, inst), titles, images_,
                      entry["before"].get("settings"))
            done.append(f"{entry['slot_label']} ({entry['time']})")
        for device, profile in flushed:
            process.flush(paths, {device: profile})
        live.write_journal(paths, keep + kept[::-1])
    lines = []
    if done:
        lines.append("Undone live: " + "; ".join(done) + ".")
    if skipped:
        lines.append("Not undone: " + "; ".join(skipped) + ".")
    return "\n".join(lines)


def _instance_by_context(paths: Paths, device: str, profile: str, context: str) -> dict | None:
    from ..core.profile import Ctx
    from ..runtime import live

    ctx = Ctx.parse(context)
    return live.read_instance(paths, device, profile, ctx.controller, ctx.position, ctx.index or None)


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


# -- folders ------------------------------------------------------------------------- #

SWITCH = "com.amansprojects.starterpack.switchprofile"


def folder_profile_id(current: str, name: str) -> str:
    """OpenDeck allows one folder level, so a folder made from inside a folder becomes its sibling."""
    return check_profile_id(f"{current.split('/')[0]}/{name}")


def make_folder(paths: Paths, device: str, slot: str, name: str, profile: str | None, back_slot: str,
                image: str | None, back_image: str | None) -> str:
    ws, base = _base(paths, device, profile)
    current = base["profile"]
    new = folder_profile_id(current, name)
    if ws.has_profile(base["device"], new):
        raise ProfileError(f"profile {new!r} already exists; open it with a Switch Profile key instead")
    args = _slot_args(ws, base, slot)
    if args.get("child"):
        raise ProfileError("a folder key must be a whole key, not a child")
    if ws.get_slot(base["device"], current, args["controller"], args["position"]) is not None:
        raise ProfileError(f"{args['slot_label']} isn't empty; clear it first or pick another slot")
    if back_image is None:
        from ..core import icons

        try:
            spec = icons.parse("mdi:arrow-left")
            back_image = str(icons.save(paths, icons.render(paths, spec, None, None, 0.62), spec, None, None, 0.62))
        except Exception:  # offline: the Switch Profile action's own icon will do
            back_image = None
    elif back_image == "default":
        back_image = None
    lines = [manage_profile(paths, base["device"], "create", new, None, None).splitlines()[0]]
    # Titles at the bottom, small, so they don't cover the icon (Switch Profile centres them).
    under = {"alignment": "bottom", "size": 11}
    lines.append(set_key(paths, base["device"], slot, SWITCH, current, name,
                         {"device": base["device"], "profile": new}, image, under).splitlines()[0])
    lines.append(set_key(paths, base["device"], back_slot, SWITCH, new, "Back",
                         {"device": base["device"], "profile": current}, back_image, under).splitlines()[0])
    lines.append(f"Folder {new!r}: {args['slot_label']} opens it, {back_slot} inside goes back to {current!r}. "
                 "Fill it with set_key(profile=" + repr(new) + ", ...).")
    lines.append(STAGED_ONLY)
    return "\n".join(lines)
