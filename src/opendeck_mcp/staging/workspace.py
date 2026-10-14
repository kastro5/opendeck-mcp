"""A virtual copy of the OpenDeck config that staged operations are replayed onto.

Files are loaded lazily from disk and only the ones an operation touches are
kept. Nothing here writes to the config directory; the applier turns the
final workspace into file writes.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ..core import profile as prof
from ..core.catalog import Catalog
from ..core.devices import DeviceLayout, get_layout, layouts
from ..core.jsonio import read_json
from ..core.paths import Paths

DELETED = object()


class Workspace:
    def __init__(self, paths: Paths, catalog: Catalog | None = None):
        self.paths = paths
        self.catalog = catalog or Catalog(paths)
        self._docs: dict[tuple[str, str], Any] = {}  # (device, profile) -> doc | DELETED
        self._selected: dict[str, str] = {}
        self._profiles: dict[str, list[str]] = {}
        self._apps: dict | None = None
        self._plugin_settings: dict[str, Any] = {}  # folder -> settings | DELETED
        self.dirty_docs: set[tuple[str, str]] = set()
        self.dirty_selected: set[str] = set()
        self.dirty_apps = False
        self.dirty_settings: set[str] = set()
        self.stale_dirs: set[Path] = set()  # image folders to delete
        self.plugin_installs: dict[str, dict] = {}  # folder -> {"source": cache dir, ...}
        self.plugin_removals: set[str] = set()
        self.changed: dict[tuple[str, str], set[str]] = {}  # slot labels touched, for display
        self.notes: list[str] = []  # profile-level changes, for display

    # -- devices & profiles -------------------------------------------------- #

    def layouts(self) -> dict[str, DeviceLayout]:
        return layouts(self.paths)

    def layout(self, device: str) -> DeviceLayout:
        return get_layout(self.paths, device)

    def profiles(self, device: str) -> list[str]:
        if device not in self._profiles:
            self._profiles[device] = list(prof.list_profiles(self.paths, device))
        return self._profiles[device]

    def selected(self, device: str) -> str:
        if device not in self._selected:
            self._selected[device] = prof.selected_profile(self.paths, device)
        return self._selected[device]

    def set_selected(self, device: str, profile_id: str) -> None:
        self._selected[device] = profile_id
        self.dirty_selected.add(device)

    def has_profile(self, device: str, profile_id: str) -> bool:
        return profile_id in self.profiles(device)

    def doc(self, device: str, profile_id: str) -> dict[str, Any]:
        key = (device, profile_id)
        value = self._docs.get(key)
        if value is DELETED or (value is None and not self.has_profile(device, profile_id)):
            raise prof.ProfileError(f"profile {profile_id!r} doesn't exist on {device}; "
                                    f"existing: {', '.join(self.profiles(device))}")
        if value is None:
            path = prof.profile_path(self.paths, device, profile_id)
            if path.exists():
                value = read_json(path)
            else:  # OpenDeck's implicit "Default" before it has been saved
                value = self.empty_doc(device)
            self._docs[key] = value
        return value

    def touch(self, device: str, profile_id: str, label: str | None = None) -> dict[str, Any]:
        doc = self.doc(device, profile_id)
        self.dirty_docs.add((device, profile_id))
        if label:
            self.changed.setdefault((device, profile_id), set()).add(label)
        return doc

    def empty_doc(self, device: str) -> dict[str, Any]:
        layout = self.layout(device)
        return {"keys": [None] * layout.keys, "sliders": [None] * layout.encoders,
                "infobars": [None] * layout.infobars}

    def create_profile(self, device: str, profile_id: str, doc: dict[str, Any]) -> None:
        self.profiles(device).append(profile_id)
        self._docs[(device, profile_id)] = doc
        self.dirty_docs.add((device, profile_id))

    def delete_profile(self, device: str, profile_id: str) -> None:
        self.profiles(device).remove(profile_id)
        self._docs[(device, profile_id)] = DELETED
        self.dirty_docs.add((device, profile_id))
        self.stale_dirs.add(self.paths.images_dir / device / profile_id)

    def loaded_docs(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Every profile, loading all of them (used for cross-profile fix-ups and searches)."""
        out = {}
        for device in self.layouts():
            for profile_id in list(self.profiles(device)):
                try:
                    out[(device, profile_id)] = self.doc(device, profile_id)
                except prof.ProfileError:
                    continue
        return out

    def deleted_docs(self) -> list[tuple[str, str]]:
        return [key for key, value in self._docs.items() if value is DELETED]

    # -- app rules and plugin settings --------------------------------------- #

    def apps(self) -> dict[str, dict[str, str]]:
        if self._apps is None:
            try:
                self._apps = read_json(self.paths.applications_file)
            except (OSError, ValueError):
                self._apps = {}
        return self._apps

    def touch_apps(self) -> dict[str, dict[str, str]]:
        self.dirty_apps = True
        return self.apps()

    def plugin_settings(self, folder: str) -> Any:
        if folder not in self._plugin_settings:
            path = self.paths.settings_dir / f"{folder}.json"
            try:
                self._plugin_settings[folder] = read_json(path)
            except (OSError, ValueError):
                self._plugin_settings[folder] = None
        value = self._plugin_settings[folder]
        return None if value is DELETED else value

    def set_plugin_settings(self, folder: str, value: Any) -> None:
        self._plugin_settings[folder] = DELETED if value is None else value
        self.dirty_settings.add(folder)

    def plugin_settings_state(self, folder: str) -> Any:
        return self._plugin_settings.get(folder)

    # -- slots ------------------------------------------------------------------ #

    def get_slot(self, device: str, profile_id: str, controller: str, position: int) -> dict | None:
        arr = self.doc(device, profile_id).get(prof.ARRAY_FOR[controller]) or []
        return arr[position] if position < len(arr) else None

    def set_slot(self, device: str, profile_id: str, controller: str, position: int, value: dict | None,
                 label: str) -> None:
        doc = self.touch(device, profile_id, label)
        arr = prof.slot_array(doc, controller)
        while len(arr) <= position:
            arr.append(None)
        if value is not None:
            prof.reindex(value, controller, position)
        arr[position] = value

    def copy_doc(self, device: str, profile_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.doc(device, profile_id))
