"""OpenDeck's profile files: contexts, slots, ids and listing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .jsonio import read_json
from .paths import Paths

CONTROLLERS = ("Keypad", "Encoder", "Infobar")
ARRAY_FOR = {"Keypad": "keys", "Encoder": "sliders", "Infobar": "infobars"}
PARENT_UUIDS = ("opendeck.multiaction", "opendeck.toggleaction")


class ProfileError(ValueError):
    """A request that can't be applied to a profile; the message is shown to the agent."""


@dataclass(frozen=True)
class Ctx:
    """A disk context "Keypad.6.0": controller, slot position, 0 or a child's 1-based index."""

    controller: str
    position: int
    index: int = 0

    @classmethod
    def parse(cls, text: str) -> Ctx:
        parts = text.split(".")
        if len(parts) == 5:  # legacy device.profile.controller.position.index
            parts = parts[2:]
        if len(parts) != 3 or parts[0] not in CONTROLLERS:
            raise ProfileError(f"bad context {text!r}")
        return cls(parts[0], int(parts[1]), int(parts[2]))

    def __str__(self) -> str:
        return f"{self.controller}.{self.position}.{self.index}"


# -- profile ids --------------------------------------------------------------- #

_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
# No "." at all: OpenDeck splits instance contexts (device.profile.Keypad.0.0) on dots.
_PART = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f. ][^<>:\"/\\|?*\x00-\x1f.]*$")


def check_profile_id(profile_id: str) -> str:
    """Validate a profile id ("Name" or "Folder/Name") and return it."""
    parts = profile_id.split("/")
    if len(parts) > 2:
        raise ProfileError("profile names may have at most one folder level: 'Folder/Name'")
    for part in parts:
        if not part or not _PART.match(part) or part.endswith((" ", ".")):
            raise ProfileError(f"invalid profile name part {part!r}: avoid . < > : \" / \\ | ? * and edge spaces")
        if part.lower() in _RESERVED or part.lower().endswith(".json"):
            raise ProfileError(f"{part!r} is not allowed as a profile name")
    return profile_id


def check_device_id(device: str) -> str:
    if not device or "." in device or "/" in device or "\\" in device:
        raise ProfileError(f"invalid device id {device!r}")
    return device


def profile_path(paths: Paths, device: str, profile_id: str) -> Path:
    return paths.profiles_dir / check_device_id(device) / (profile_id + ".json")


def device_file(paths: Paths, device: str) -> Path:
    return paths.profiles_dir / (check_device_id(device) + ".json")


def list_devices(paths: Paths) -> list[str]:
    if not paths.profiles_dir.is_dir():
        return []
    ids = {p.name for p in paths.profiles_dir.iterdir() if p.is_dir()}
    ids |= {p.stem for p in paths.profiles_dir.glob("*.json")}
    return sorted(ids)


def list_profiles(paths: Paths, device: str) -> list[str]:
    """Profile ids for a device, the way OpenDeck enumerates them (minus recovery files)."""
    root = paths.profiles_dir / device
    found: list[str] = []
    if root.is_dir():
        for entry in sorted(root.iterdir()):
            if entry.is_file() and entry.name.endswith(".json"):
                found.append(entry.name[:-5])
            elif entry.is_dir():
                for sub in sorted(entry.iterdir()):
                    if sub.is_file() and sub.name.endswith(".json"):
                        found.append(f"{entry.name}/{sub.name[:-5]}")
    return found or ["Default"]


def stray_files(paths: Paths, device: str) -> list[Path]:
    """OpenDeck recovery files (.json.bak/.json.temp) that it would also list as profiles."""
    root = paths.profiles_dir / device
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.is_file() and p.name.endswith((".json.bak", ".json.temp"))]


def selected_profile(paths: Paths, device: str) -> str:
    profiles = list_profiles(paths, device)
    try:
        selected = read_json(device_file(paths, device)).get("selected_profile")
    except (OSError, ValueError, AttributeError):
        selected = None
    return selected if selected in profiles else profiles[0]


def load_profile(paths: Paths, device: str, profile_id: str) -> dict[str, Any]:
    path = profile_path(paths, device, profile_id)
    if not path.exists():
        raise ProfileError(f"profile {profile_id!r} not found on device {device!r}")
    return read_json(path)


# -- instances ------------------------------------------------------------------ #

def is_parent(instance: dict[str, Any] | None) -> bool:
    return bool(instance) and instance["action"]["uuid"] in PARENT_UUIDS


def slot_array(profile: dict[str, Any], controller: str) -> list:
    return profile.setdefault(ARRAY_FOR[controller], [])


def reindex(instance: dict[str, Any], controller: str, position: int) -> None:
    """Point an instance (and its children, renumbered 1..n) at a new slot."""
    instance["context"] = str(Ctx(controller, position, 0))
    for number, child in enumerate(instance.get("children") or [], start=1):
        child["context"] = str(Ctx(controller, position, number))


def title_of(instance: dict[str, Any]) -> str:
    states = instance.get("states") or []
    if not states:
        return ""
    current = instance.get("current_state", 0)
    state = states[current] if 0 <= current < len(states) else states[0]
    return state.get("text", "") if state.get("show", True) else ""
