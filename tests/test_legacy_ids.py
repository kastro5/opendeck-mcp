"""Installs from before 0.5.0 use the dev.kastro5.* IDs; they keep working after the rename."""

import asyncio
import json

import pytest

from opendeck_mcp import plugin as P
from opendeck_mcp.core.devices import bridge_status, layouts, plugin_installed
from opendeck_mcp.core.profile import ProfileError
from opendeck_mcp.runtime import applier
from opendeck_mcp.staging import plan
from opendeck_mcp.tools import edit_tools as E

DEV = "ulanzi-d200h"
OLD_BRIDGE = "dev.kastro5.opendeck-mcp-bridge.sdPlugin"
OLD_PLUGIN = "dev.kastro5.opendeck-mcp.sdPlugin"


def _install(paths, folder):
    (paths.plugins_dir / folder).mkdir(parents=True)
    (paths.plugins_dir / folder / "manifest.json").write_text(
        json.dumps({"Name": folder, "Version": "0.4.1", "Actions": []}), encoding="utf-8")


def test_old_bridge_records_are_still_read(paths):
    # The fixture's geometry was recorded by a bridge under the old ID.
    assert (paths.settings_dir / f"{OLD_BRIDGE}.json").exists()
    assert layouts(paths)[DEV].columns == 5


def test_an_old_plugin_counts_as_installed(paths):
    _install(paths, OLD_PLUGIN)
    assert plugin_installed(paths)
    assert bridge_status(paths).startswith("not needed")


def test_a_new_bridge_replaces_the_old_one(paths):
    _install(paths, OLD_BRIDGE)
    assert bridge_status(paths).startswith("installed")
    reply = E.set_key(paths, DEV, "r2c1", "Open URL", None, "New", {"down": "https://x.y"}, None, None)
    ops = {(r["op"], r["args"].get("folder")) for r in plan.load(paths)["ops"]}
    assert ("install_bridge", "io.github.kastro5.opendeck-mcp-bridge.sdPlugin") in ops
    assert ("remove_plugin", OLD_BRIDGE) in ops
    assert "remove plugin " + OLD_BRIDGE in reply

    applier.apply(paths, confirm=True, show_window=False, on_conflict="abort", allow_untested_version=False)
    assert not (paths.plugins_dir / OLD_BRIDGE).exists()
    assert (paths.plugins_dir / "io.github.kastro5.opendeck-mcp-bridge.sdPlugin").is_dir()


def test_the_plugin_carries_over_its_old_settings(paths):
    old = {"server": {"host": "127.0.0.1", "port": 8900, "allowed_hosts": []}, "devices": {}}
    (paths.settings_dir / f"{OLD_PLUGIN}.json").write_text(json.dumps(old), encoding="utf-8")
    plugin = P.Plugin(paths, "io.github.kastro5.opendeck-mcp.sdPlugin", {"devices": []})
    sent = []

    class Socket:
        async def send(self, body):
            sent.append(json.loads(body))

    plugin.ws = Socket()
    asyncio.run(plugin.loaded({}))
    assert plugin.server_config()["port"] == 8900
    assert sent[-1]["payload"]["server"]["port"] == 8900  # stored under the new ID from now on


def test_only_the_running_plugin_is_refused_as_an_apply_target(paths, monkeypatch):
    # As the plugin, the helper runs from the plugin's own folder; an old copy is just another plugin.
    exe = paths.plugins_dir / "io.github.kastro5.opendeck-mcp.sdPlugin" / "bin" / "windows" / "p.exe"
    monkeypatch.setattr(applier.sys, "executable", str(exe))
    assert applier.own_folder(paths) == "io.github.kastro5.opendeck-mcp.sdPlugin"
    _install(paths, OLD_PLUGIN)
    E.set_key(paths, DEV, "r2c1", "Open URL", None, "New", {"down": "https://x.y"}, None, None)
    current = plan.load(paths)
    current["ops"].append({"id": f"op{current['next_id']}", "op": "remove_plugin", "pre": {},
                           "args": {"folder": OLD_PLUGIN}})
    current["next_id"] += 1
    plan.save(paths, current)
    assert applier.apply(paths, confirm=True, show_window=False, on_conflict="abort",
                         allow_untested_version=False, detach=lambda: "detached") == "detached"

    monkeypatch.setattr(applier.sys, "executable", str(paths.plugins_dir / OLD_PLUGIN / "p.exe"))
    with pytest.raises(ProfileError, match="can't replace or remove itself"):
        applier.apply(paths, confirm=True, show_window=False, on_conflict="abort",
                      allow_untested_version=False, detach=lambda: "detached")
