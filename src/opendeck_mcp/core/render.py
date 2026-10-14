"""Text views of profiles for the agent: a grid sketch plus a per-slot list."""

from __future__ import annotations

import json
from typing import Any

from .catalog import Catalog
from .devices import DeviceLayout
from .knowledge import danger_note
from .profile import ARRAY_FOR, title_of
from .secrets import redact

CELL = 14


def _short(text: str, width: int = CELL) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def cell_text(instance: dict[str, Any] | None, catalog: Catalog) -> str:
    if instance is None:
        return "·"
    title = title_of(instance).replace("\n", " ").strip()
    name = catalog.display_name(instance)
    return _short(f'"{title}"' if title else name)


def grid(doc: dict[str, Any], layout: DeviceLayout, catalog: Catalog, changed: set[str] | None = None) -> str:
    """A rows x columns sketch (touchpoints and dials on their own lines)."""
    changed = changed or set()
    keys = doc.get("keys") or []
    lines = []
    if layout.grid:
        header = "     " + "".join(f"c{c + 1}".ljust(CELL + 2) for c in range(layout.columns))
        lines.append(header.rstrip())
        for r in range(layout.rows):
            cells = []
            for c in range(layout.columns):
                pos = r * layout.columns + c
                mark = "*" if layout.label("Keypad", pos) in changed else " "
                text = cell_text(keys[pos] if pos < len(keys) else None, catalog)
                if layout.quirks.get(pos, "").startswith("unused") and text == "·":
                    text = "(wide →)"
                cells.append((mark + text).ljust(CELL + 2))
            lines.append(f"r{r + 1}   " + "".join(cells).rstrip())
    extras = []
    for controller, count, prefix in (("Keypad", layout.touchpoints, "touch"), ("Encoder", layout.encoders, "dial"),
                                      ("Infobar", layout.infobars, "infobar")):
        arr = doc.get(ARRAY_FOR[controller]) or []
        start = (layout.grid or 0) if controller == "Keypad" else 0
        if count:
            items = []
            for i in range(count):
                inst = arr[start + i] if start + i < len(arr) else None
                label = f"{prefix}{i + 1}"
                items.append(("*" if label in changed else "") + f"{label}={cell_text(inst, catalog)}")
            extras.append("  ".join(items))
    return "\n".join(lines + extras)


def describe_instance(instance: dict[str, Any], catalog: Catalog, show_settings: bool) -> str:
    name = catalog.display_name(instance)
    entry = catalog.entry_for_instance(instance)
    parts = [name if not entry or not entry.plugin else f"{name} ({entry.plugin_name})"]
    title = title_of(instance)
    if title:
        parts.append(f'title "{title}"')
    states = instance.get("states") or []
    if len(states) > 1:
        parts.append(f"state {instance.get('current_state', 0) + 1}/{len(states)}")
    state = states[0] if states else {}
    if state.get("image", "") and not state["image"].startswith(("plugins/", "opendeck/")):
        parts.append("custom image")
    if show_settings and instance.get("settings"):
        parts.append("settings " + json.dumps(redact(instance["settings"]), ensure_ascii=False))
    danger = danger_note(instance["action"]["uuid"], instance.get("settings"))
    if danger:
        parts.append(f"⚠ {danger}")
    return " · ".join(parts)


def slot_list(doc: dict[str, Any], layout: DeviceLayout, catalog: Catalog, show_settings: bool = False,
              show_children: bool = True, changed: set[str] | None = None) -> str:
    changed = changed or set()
    lines = []
    for controller, position in layout.slots():
        arr = doc.get(ARRAY_FOR[controller]) or []
        inst = arr[position] if position < len(arr) else None
        label = layout.label(controller, position)
        note = layout.quirks.get(position) if controller == "Keypad" else None
        mark = "*" if label in changed else " "
        if inst is None:
            if label in changed:
                lines.append(f"{mark}{label:<8} (empty)")
            elif note and note.startswith("unused"):
                lines.append(f" {label:<8} — {note}")
            continue
        alias = f" key{position}" if controller == "Keypad" and label != f"key{position}" else ""
        quirk = f" [{note}]" if note else ""
        lines.append(f"{mark}{label:<8}{alias}{quirk}  {describe_instance(inst, catalog, show_settings)}")
        if show_children and inst.get("children") is not None:
            kids = inst["children"]
            if not kids:
                lines.append("    (no children yet: add them with add_child)")
            delays = (inst.get("settings") or {}).get("delays") or []
            for n, kid in enumerate(kids, start=1):
                delay = ""
                if inst["action"]["uuid"] == "opendeck.multiaction":
                    delay = f" · then wait {delays[n - 1] if n - 1 < len(delays) else 100} ms"
                lines.append(f"    {label}/child{n}  {describe_instance(kid, catalog, show_settings)}{delay}")
    return "\n".join(lines) if lines else "(every slot is empty)"
