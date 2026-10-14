import json

import pytest

from opendeck_mcp.core import profile
from opendeck_mcp.core.profile import ProfileError
from opendeck_mcp.runtime import applier, backups
from opendeck_mcp.staging import plan
from opendeck_mcp.tools import edit_tools as E
from opendeck_mcp.tools.view import View

DEV = "ulanzi-d200h"


def doc(paths, profile_id="Default"):
    return View(paths).profile(DEV, profile_id)


def test_set_key_builds_an_instance_like_opendeck(paths):
    E.set_key(paths, DEV, "r1c2", "Open URL", None, "Hi", {"down": "https://x.y"}, None,
              {"colour": "#FF8800", "alignment": "bottom"})
    inst = doc(paths)["keys"][1]
    assert inst["context"] == "Keypad.1.0" and inst["settings"] == {"down": "https://x.y"}
    assert inst["states"][0]["text"] == "Hi" and inst["states"][0]["colour"] == "#FF8800"
    assert inst["children"] is None and inst["current_state"] == 0
    # The bridge plugin is staged alongside the first change.
    assert plan.load(paths)["ops"][0]["op"] == "install_bridge"


def test_toggle_children_keep_one_state_each(paths):
    E.set_key(paths, DEV, "r1c2", "Toggle Action", None, None, None, None, None)
    for url in ("a", "b", "c"):
        E.add_child(paths, DEV, "r1c2", "Open URL", None, {"down": url}, None, None, None, None, None)
    inst = doc(paths)["keys"][1]
    assert [c["context"] for c in inst["children"]] == ["Keypad.1.1", "Keypad.1.2", "Keypad.1.3"]
    assert len(inst["states"]) == 3
    E.remove_child(paths, DEV, "r1c2", 2, None)
    inst = doc(paths)["keys"][1]
    assert [c["settings"]["down"] for c in inst["children"]] == ["a", "c"]
    assert [c["context"] for c in inst["children"]] == ["Keypad.1.1", "Keypad.1.2"]
    assert len(inst["states"]) == 2


def test_multi_delays_follow_children(paths):
    # r1c1 in the fixture is a Multi Action with two children and no delays yet.
    E.add_child(paths, DEV, "r1c1", "Device Brightness", None, None, None, None, None, 1, 250)
    inst = doc(paths)["keys"][0]
    assert inst["settings"]["delays"] == [250, 100, 100]
    E.remove_child(paths, DEV, "r1c1", 1, None)
    assert doc(paths)["keys"][0]["settings"]["delays"] == [100, 100]
    with pytest.raises(ProfileError, match="Multi/Toggle actions|can't be nested"):
        E.add_child(paths, DEV, "r1c1", "Toggle Action", None, None, None, None, None, None, None)


def test_move_carries_custom_images_as_data_urls(paths):
    # r3c3 (key 12) has a custom image stored as "0.png" in its image folder.
    E.move_key(paths, DEV, "r3c3", "r1c2", "move", None, None, None)
    d = doc(paths)
    assert d["keys"][12] is None
    assert d["keys"][1]["context"] == "Keypad.1.0"
    assert d["keys"][1]["states"][0]["image"].startswith("data:image/png;base64,")
    with pytest.raises(ProfileError, match="occupied"):
        E.move_key(paths, DEV, "r1c1", "r1c2", "move", None, None, None)


def test_rename_profile_updates_references(paths):
    E.manage_profile(paths, DEV, "create", "Work", None, None)
    E.set_key(paths, DEV, "r1c5", "Switch Profile", None, None, {"profile": "Work"}, None, None)
    E.set_app_rule(paths, "Code", DEV, "Work")
    E.manage_profile(paths, DEV, "rename", "Work", "Office", None)
    view = View(paths)
    assert "Office" in view.profiles(DEV) and "Work" not in view.profiles(DEV)
    assert view.profile(DEV, "Default")["keys"][4]["settings"]["profile"] == "Office"
    assert view.app_rules()["Code"][DEV] == "Office"


def test_conflicts_are_detected_but_state_changes_are_not(paths):
    E.update_key(paths, DEV, "r3c1", None, None, "Bright", None, None, None, None, None, None)
    # A plugin flipping state / settings in OpenDeck isn't a conflict...
    path = profile.profile_path(paths, DEV, "Default")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["keys"][10]["settings"]["value"] = 90
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert View(paths).conflicts == []
    # ...but the key being replaced is.
    raw["keys"][10] = None
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert len(View(paths).conflicts) == 1


def test_apply_and_restore_round_trip(paths):
    before = {p: p.read_bytes() for p in paths.profiles_dir.rglob("*.json")}
    E.set_key(paths, DEV, "r2c1", "Open URL", None, "New", {"down": "https://x.y"}, None, None)
    E.manage_profile(paths, DEV, "create", "Folder/Extra", None, None)
    preview = applier.apply(paths, confirm=False, show_window=False, on_conflict="abort",
                            allow_untested_version=False)
    assert "Not applied yet" in preview
    result = applier.apply(paths, confirm=True, show_window=False, on_conflict="abort",
                           allow_untested_version=False)
    assert "Applied" in result and "wasn't running" in result
    written = json.loads(profile.profile_path(paths, DEV, "Default").read_text(encoding="utf-8"))
    assert written["keys"][5]["states"][0]["text"] == "New"
    assert profile.profile_path(paths, DEV, "Folder/Extra").exists()
    assert (paths.plugins_dir / "dev.kastro5.opendeck-mcp-bridge.sdPlugin" / "manifest.json").exists()
    assert not list(paths.config_dir.rglob("*.mcptmp"))
    assert plan.load(paths)["ops"] == []

    backup_id = backups.list_all(paths)[0].id
    applier.restore(paths, backup_id, confirm=True, show_window=False)
    after = {p: p.read_bytes() for p in paths.profiles_dir.rglob("*.json")}
    assert after == before
    assert not (paths.plugins_dir / "dev.kastro5.opendeck-mcp-bridge.sdPlugin").exists()


def test_invalid_change_is_refused(paths):
    with pytest.raises(ProfileError):
        E.set_key(paths, DEV, "r1c2", "Wide screen", None, None, None, None, {"alignment": "sideways"})
    with pytest.raises(ProfileError, match="only profile"):
        E.manage_profile(paths, DEV, "delete", "Default", None, None)
    assert plan.load(paths)["ops"] == []


def test_discard(paths):
    E.clear_key(paths, DEV, "r1c1", None)
    E.clear_key(paths, DEV, "r1c3", None)
    assert "Discarded" in E.discard_changes(paths, "last")
    assert len(plan.load(paths)["ops"]) == 2  # bridge + first clear
    E.discard_changes(paths, "all")
    assert plan.load(paths)["ops"] == []
