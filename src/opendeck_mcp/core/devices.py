"""Device geometry and slot naming.

OpenDeck keeps no device geometry on disk. Rows x columns come from the
companion bridge plugin (which records what OpenDeck tells plugins), a local
override, or a small table of known devices; dial, touchpoint and infobar
counts come from the profile array lengths, which OpenDeck normalises
whenever the device connects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .jsonio import read_json
from .paths import Paths
from .profile import ProfileError, list_devices, list_profiles, load_profile, selected_profile

BRIDGE_ID = "dev.kastro5.opendeck-mcp-bridge.sdPlugin"

# device-id prefix -> (rows, columns, name)
KNOWN = {
    "ulanzi-d200x": (3, 5, "Ulanzi D200X"),
    "ulanzi-d200h": (3, 5, "Ulanzi D200H"),
}
# device-id prefix -> {position: note}
QUIRKS = {
    "ulanzi-d200": {13: "wide screen (double width)", 14: "unused: right half of the wide screen"},
}


@dataclass
class DeviceLayout:
    id: str
    name: str
    rows: int | None
    columns: int | None
    keys: int  # keys array length = rows*columns + touchpoints
    encoders: int
    infobars: int
    connected: bool | None
    source: str
    quirks: dict[int, str] = field(default_factory=dict)

    @property
    def grid(self) -> int | None:
        return self.rows * self.columns if self.rows and self.columns else None

    @property
    def touchpoints(self) -> int:
        return max(0, self.keys - self.grid) if self.grid is not None else 0

    def label(self, controller: str, position: int) -> str:
        if controller == "Encoder":
            return f"dial{position + 1}"
        if controller == "Infobar":
            return f"infobar{position + 1}"
        grid = self.grid
        if grid is None:
            return f"key{position}"
        if position < grid:
            return f"r{position // self.columns + 1}c{position % self.columns + 1}"
        return f"touch{position - grid + 1}"

    def slots(self) -> list[tuple[str, int]]:
        return ([("Keypad", i) for i in range(self.keys)] + [("Encoder", i) for i in range(self.encoders)]
                + [("Infobar", i) for i in range(self.infobars)])


@dataclass(frozen=True)
class SlotRef:
    controller: str
    position: int
    child: int | None = None  # 1-based child of a multi/toggle action


_SLOT = re.compile(r"^(?:r(\d+)c(\d+)|key(\d+)|touch(\d+)|dial(\d+)|infobar(\d+))(?:/child(\d+))?$", re.IGNORECASE)


def parse_slot(text: str, layout: DeviceLayout) -> SlotRef:
    """'r1c3', 'key13', 'touch1', 'dial2', 'infobar1', optionally '/child2'."""
    m = _SLOT.match(text.strip().replace(" ", ""))
    if not m:
        raise ProfileError(f"bad slot {text!r}; use r<row>c<col>, key<N>, touch<N>, dial<N> or infobar<N>")
    row, col, key, touch, dial, infobar, child = m.groups()
    if row:
        if not layout.grid:
            raise ProfileError(f"{layout.id}'s grid size is unknown; use key<N> or set_device_layout")
        r, c = int(row) - 1, int(col) - 1
        if not (0 <= r < layout.rows and 0 <= c < layout.columns):
            raise ProfileError(f"{text} is outside the {layout.rows}x{layout.columns} grid")
        ref = SlotRef("Keypad", r * layout.columns + c)
    elif key is not None:
        ref = SlotRef("Keypad", int(key))
    elif touch:
        if layout.grid is None:
            raise ProfileError("touchpoint slots need the grid size; use key<N>")
        ref = SlotRef("Keypad", layout.grid + int(touch) - 1)
    elif dial:
        ref = SlotRef("Encoder", int(dial) - 1)
    else:
        ref = SlotRef("Infobar", int(infobar) - 1)
    limit = {"Keypad": layout.keys, "Encoder": layout.encoders, "Infobar": layout.infobars}[ref.controller]
    if not 0 <= ref.position < limit:
        raise ProfileError(f"{text} doesn't exist on {layout.id} ({limit} {ref.controller} slots)")
    if child is not None:
        if int(child) < 1:
            raise ProfileError("child numbers start at 1")
        ref = SlotRef(ref.controller, ref.position, int(child))
    return ref


def _bridge_devices(paths: Paths) -> dict[str, Any]:
    try:
        data = read_json(paths.settings_dir / f"{BRIDGE_ID}.json")
        return data.get("devices") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def _overrides(paths: Paths) -> dict[str, Any]:
    try:
        return read_json(paths.data_dir / "device_overrides.json")
    except (OSError, ValueError):
        return {}


def bridge_status(paths: Paths) -> str:
    plugin = paths.plugins_dir / BRIDGE_ID
    if not plugin.is_dir():
        return "not installed"
    return "installed" + (", reporting" if _bridge_devices(paths) else ", no data yet (restart OpenDeck)")


def layouts(paths: Paths, profile_overrides: dict[str, dict] | None = None) -> dict[str, DeviceLayout]:
    """All devices with any profile data or bridge record. profile_overrides: staged profile docs."""
    bridge = _bridge_devices(paths)
    overrides = _overrides(paths)
    out: dict[str, DeviceLayout] = {}
    for device in sorted(set(list_devices(paths)) | set(bridge)):
        doc = (profile_overrides or {}).get(device)
        if doc is None:
            try:
                doc = load_profile(paths, device, selected_profile(paths, device))
            except (ProfileError, OSError, ValueError):
                doc = None
        if doc is None:
            for pid in list_profiles(paths, device):
                try:
                    doc = load_profile(paths, device, pid)
                    break
                except (ProfileError, OSError, ValueError):
                    continue
        doc = doc or {"keys": [], "sliders": [], "infobars": []}
        rows = columns = None
        name, source, connected = device, "unknown", None
        if device in bridge:
            b = bridge[device]
            rows, columns, name = b.get("rows"), b.get("columns"), b.get("name") or device
            source, connected = "bridge", b.get("connected")
        if device in overrides:
            rows, columns = overrides[device]["rows"], overrides[device]["columns"]
            source = "override"
        if rows is None:
            for prefix, (r, c, known_name) in KNOWN.items():
                if device.startswith(prefix):
                    rows, columns, name, source = r, c, known_name, "known device"
        quirks = next((q for prefix, q in QUIRKS.items() if device.startswith(prefix)), {})
        out[device] = DeviceLayout(device, name, rows, columns, len(doc.get("keys") or []),
                                   len(doc.get("sliders") or []), len(doc.get("infobars") or []),
                                   connected, source, quirks)
    return out


def get_layout(paths: Paths, device: str, profile_overrides: dict[str, dict] | None = None) -> DeviceLayout:
    all_layouts = layouts(paths, profile_overrides)
    if device in all_layouts:
        return all_layouts[device]
    matches = [d for d in all_layouts.values() if device.lower() in (d.id.lower(), d.name.lower())]
    if len(matches) == 1:
        return matches[0]
    raise ProfileError(f"unknown device {device!r}; known: {', '.join(all_layouts) or 'none'}")
