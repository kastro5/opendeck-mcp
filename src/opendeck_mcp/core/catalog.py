"""Every action that can be placed: OpenDeck's built-ins plus each installed plugin's."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .jsonio import read_json
from .manifest import PluginInfo, SnapshotBuilder, builtin_actions
from .paths import Paths
from .profile import ProfileError

BUILTIN_PLUGIN = "opendeck"


@dataclass
class ActionEntry:
    snapshot: dict[str, Any]
    plugin: PluginInfo | None  # None for OpenDeck built-ins

    @property
    def uuid(self) -> str:
        return self.snapshot["uuid"]

    @property
    def name(self) -> str:
        if self.plugin:
            return self.plugin.display_name(self.uuid, self.snapshot["name"])
        return self.snapshot["name"]

    @property
    def plugin_name(self) -> str:
        return self.plugin.name if self.plugin else "OpenDeck"

    @property
    def label(self) -> str:
        return f"{self.plugin_name} > {self.name}"


def app_language(paths: Paths) -> str:
    try:
        return str(read_json(paths.app_settings_file).get("language") or "en")
    except (OSError, ValueError, AttributeError):
        return "en"


class Catalog:
    def __init__(self, paths: Paths, extra_plugin_dirs: list[Path] | None = None):
        self.paths = paths
        builder = SnapshotBuilder(paths)
        language = app_language(paths)
        self.plugins: dict[str, PluginInfo] = {}
        self.pending: set[str] = set()  # staged for install, not on disk yet
        dirs = sorted(p for p in paths.plugins_dir.iterdir() if p.is_dir()) if paths.plugins_dir.is_dir() else []
        for plugin_dir in dirs:
            self.plugins[plugin_dir.name] = builder.plugin(plugin_dir, language)
        for plugin_dir in extra_plugin_dirs or []:
            # Built from the staged copy, but snapshots must point at the final location.
            info = builder.plugin(plugin_dir, language, final_dir=paths.plugins_dir / plugin_dir.name)
            self.plugins[plugin_dir.name] = info
            self.pending.add(plugin_dir.name)
        self.actions: dict[str, ActionEntry] = {a["uuid"]: ActionEntry(a, None) for a in builtin_actions()}
        for info in self.plugins.values():
            for snapshot in info.actions:
                self.actions[snapshot["uuid"]] = ActionEntry(snapshot, info)

    def plugin_exists(self, folder: str) -> bool:
        return folder == BUILTIN_PLUGIN or folder in self.plugins

    def entry_for_instance(self, instance: dict[str, Any]) -> ActionEntry | None:
        return self.actions.get(instance["action"]["uuid"])

    def display_name(self, instance: dict[str, Any]) -> str:
        entry = self.entry_for_instance(instance)
        return entry.name if entry else instance["action"].get("name", instance["action"]["uuid"])

    def find(self, query: str, controller: str | None = None) -> ActionEntry:
        """Resolve an action by uuid, name, or 'Plugin > Action'; errors list the closest matches."""
        q = query.strip()
        if q in self.actions:
            return self._check_controller(self.actions[q], controller)
        lowered = q.lower()
        exact = [e for e in self.actions.values()
                 if lowered in (e.name.lower(), e.label.lower(), e.snapshot["name"].lower())]
        candidates = exact or [e for e in self.actions.values()
                               if lowered in e.label.lower() or lowered in e.uuid.lower()]
        if controller:
            usable = [e for e in candidates if controller in e.snapshot["controllers"]]
            candidates = usable or candidates
        if len(candidates) == 1:
            return self._check_controller(candidates[0], controller)
        if not candidates:
            raise ProfileError(f"no action matches {query!r}; use list_actions to see what's installed")
        options = "; ".join(f"{e.label} [{e.uuid}]" for e in candidates[:6])
        raise ProfileError(f"{query!r} is ambiguous: {options}. Pass the uuid.")

    @staticmethod
    def _check_controller(entry: ActionEntry, controller: str | None) -> ActionEntry:
        if controller and controller not in entry.snapshot["controllers"]:
            kinds = ", ".join(entry.snapshot["controllers"])
            raise ProfileError(f"{entry.label} can't go on a {controller} slot (supports: {kinds})")
        return entry
