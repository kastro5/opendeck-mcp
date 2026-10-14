"""The config as the agent sees it: what's on disk plus every staged operation replayed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.catalog import Catalog
from ..core.devices import DeviceLayout
from ..core.paths import Paths
from ..staging import ops, plan
from ..staging.workspace import Workspace


def pending_plugin_dirs(paths: Paths, records: list[dict]) -> list[Path]:
    dirs = []
    for record in records:
        if record["op"] in ("install_plugin", "install_bridge"):
            source = Path(record["args"]["source"])
            if source.is_dir():
                dirs.append(source)
    return dirs


def build_workspace(paths: Paths, records: list[dict], check: bool = True) -> tuple[Workspace, list[str],
                                                                                    list[str]]:
    """Replay records onto a fresh workspace. Returns (workspace, warnings, conflicts)."""
    ws = Workspace(paths, Catalog(paths, pending_plugin_dirs(paths, records)))
    warnings: list[str] = []
    conflicts: list[str] = []
    for record in records:
        try:
            warnings += ops.run(ws, record, check=check)
        except ops.Conflict as exc:
            conflicts.append(f"{record['id']} ({record['op']}): {exc}")
        except Exception as exc:  # an op that can no longer apply at all
            conflicts.append(f"{record['id']} ({record['op']}): {exc}")
    return ws, warnings, conflicts


class View:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.plan = plan.load(paths)
        self.ws, self.warnings, self.conflicts = build_workspace(paths, self.plan["ops"])

    @property
    def catalog(self) -> Catalog:
        return self.ws.catalog

    @property
    def plan_size(self) -> int:
        return len(self.plan["ops"])

    def layouts(self) -> dict[str, DeviceLayout]:
        return self.ws.layouts()

    def layout(self, device: str) -> DeviceLayout:
        return self.ws.layout(device)

    def selected(self, device: str) -> str:
        return self.ws.selected(device)

    def profiles(self, device: str) -> list[str]:
        return self.ws.profiles(device)

    def profile(self, device: str, profile_id: str) -> dict[str, Any]:
        return self.ws.doc(device, profile_id)

    def changed_slots(self, device: str, profile_id: str) -> set[str]:
        return self.ws.changed.get((device, profile_id), set())

    def app_rules(self) -> dict[str, dict[str, str]]:
        return self.ws.apps()

    def plugin_settings(self, folder: str) -> Any:
        return self.ws.plugin_settings(folder)
