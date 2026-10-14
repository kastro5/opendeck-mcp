"""Human-readable summaries of what applying the plan will change."""

from __future__ import annotations

import difflib
import json

from ..core.jsonio import dumps, read_json
from ..core.profile import ARRAY_FOR, profile_path, title_of
from ..core.render import describe_instance
from ..core.secrets import redact
from .workspace import DELETED, Workspace

RESTART_NOTE = ("Applying stops OpenDeck for a few seconds (keys go dark), writes the files, then starts it "
                "again in the background. Unsaved edits in an open OpenDeck settings panel are lost.")


def _base_doc(ws: Workspace, device: str, profile_id: str) -> dict | None:
    path = profile_path(ws.paths, device, profile_id)
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def _slot_desc(inst: dict | None, ws: Workspace) -> str:
    return "(empty)" if inst is None else describe_instance(inst, ws.catalog, show_settings=True)


def profile_changes(ws: Workspace) -> list[str]:
    lines = []
    for device, profile_id in sorted(ws.dirty_docs):
        new = ws._docs.get((device, profile_id))
        if new is DELETED or new is None:
            continue
        base = _base_doc(ws, device, profile_id)
        layout = ws.layout(device)
        header = f"{layout.name} [{device}] / {profile_id}" + (" (new profile)" if base is None else "")
        entries = []
        for controller, array in ARRAY_FOR.items():
            old_arr = (base or {}).get(array) or []
            new_arr = new.get(array) or []
            for position in range(max(len(old_arr), len(new_arr))):
                before = old_arr[position] if position < len(old_arr) else None
                after = new_arr[position] if position < len(new_arr) else None
                if dumps(before) == dumps(after):
                    continue
                label = layout.label(controller, position)
                if base is None and after is None:
                    continue
                if before is None:
                    entries.append(f"  {label:<8} + {_slot_desc(after, ws)}")
                elif after is None:
                    entries.append(f"  {label:<8} - {_slot_desc(before, ws)}")
                else:
                    entries.append(f"  {label:<8} ~ {_slot_desc(after, ws)}")
                    if before["action"]["uuid"] != after["action"]["uuid"]:
                        entries.append(f"  {'':<8}   (was {_slot_desc(before, ws)})")
                    elif title_of(before) != title_of(after):
                        entries.append(f"  {'':<8}   (title was \"{title_of(before)}\")")
                for n, kid in enumerate(after.get("children") or [] if after else [], start=1):
                    entries.append(f"  {label}/child{n:<2} {_slot_desc(kid, ws)}")
        if entries or base is None:
            lines.append(header)
            lines += entries or ["  (no keys)"]
    return lines


def summary(ws: Workspace, warnings: list[str], conflicts: list[str], op_count: int) -> str:
    if not op_count:
        return "Nothing is staged."
    out = [f"{op_count} staged operation(s):", ""]
    out += profile_changes(ws)
    if ws.notes:
        out += [""] + [f"• {note}" for note in ws.notes]
    for device in sorted(ws.dirty_selected):
        out.append(f"• {device} will start on profile {ws.selected(device)!r}")
    for folder in sorted(ws.plugin_installs):
        info = ws.plugin_installs[folder]
        out.append(f"• install plugin {folder} {info.get('version', '')}".rstrip())
    for folder in sorted(ws.plugin_removals):
        out.append(f"• remove plugin {folder} (its keys are cleared everywhere)")
    stale = [d for d in ws.stale_dirs if d.is_dir()]
    if stale:
        out.append(f"• {len(stale)} old image folder(s) will be cleaned up")
    if conflicts:
        out += ["", "CONFLICTS (the config changed since these were staged; apply will refuse unless "
                    "on_conflict='skip'):"] + [f"  ! {c}" for c in conflicts]
    if warnings:
        out += ["", "WARNINGS:"] + [f"  ⚠ {w}" for w in dict.fromkeys(warnings)]
    out += ["", RESTART_NOTE]
    return "\n".join(out)


def file_diffs(ws: Workspace, max_lines: int = 200) -> str:
    chunks = []
    for device, profile_id in sorted(ws.dirty_docs):
        path = profile_path(ws.paths, device, profile_id)
        new = ws._docs.get((device, profile_id))
        old_text = dumps(redact(_base_doc(ws, device, profile_id))).decode() if path.exists() else ""
        new_text = "" if new is DELETED or new is None else dumps(redact(new)).decode()
        diff = list(difflib.unified_diff(old_text.splitlines(), new_text.splitlines(),
                                         ws.paths.rel(path), ws.paths.rel(path), lineterm="", n=2))
        if len(diff) > max_lines:
            diff = diff[:max_lines] + [f"... ({len(diff) - max_lines} more lines)"]
        if diff:
            chunks.append("\n".join(diff))
    if ws.dirty_apps:
        chunks.append("applications.json → " + json.dumps(ws.apps(), ensure_ascii=False))
    for folder in sorted(ws.dirty_settings):
        value = ws.plugin_settings(folder)
        chunks.append(f"settings/{folder}.json → " + json.dumps(redact(value), ensure_ascii=False))
    return "\n\n".join(chunks) or "No file changes."
