import json

import pytest

from opendeck_mcp.core import jsonio, profile, secrets, validate
from opendeck_mcp.core.catalog import Catalog
from opendeck_mcp.core.devices import get_layout, parse_slot
from opendeck_mcp.core.profile import ProfileError


def all_instances(doc):
    for array in ("keys", "sliders", "infobars"):
        for inst in doc.get(array) or []:
            if inst:
                yield inst
                yield from inst.get("children") or []


def test_round_trip_is_byte_identical(paths):
    for path in paths.config_dir.rglob("*.json"):
        if "plugins" in path.parts or "settings" in path.parts:
            continue
        raw = path.read_bytes()
        assert jsonio.dumps(jsonio.loads(raw)) == raw, path


def test_plugin_settings_use_compact_format(paths):
    path = paths.settings_dir / "dev.kastro5.opendeck-mcp-bridge.sdPlugin.json"
    raw = path.read_bytes()
    assert jsonio.dumps_compact(jsonio.loads(raw)) == raw


def test_snapshots_match_what_opendeck_stored(paths):
    catalog = Catalog(paths)
    for device in profile.list_devices(paths):
        for profile_id in profile.list_profiles(paths, device):
            for inst in all_instances(profile.load_profile(paths, device, profile_id)):
                built = catalog.actions[inst["action"]["uuid"]].snapshot
                assert jsonio.dumps(built) == jsonio.dumps(inst["action"]), inst["context"]


def test_snapshot_rules(paths):
    plugin = paths.plugins_dir / "com.example.demo.sdPlugin"
    (plugin / "img").mkdir(parents=True)
    for name in ("act.svg", "act.png", "state@2x.png", "state.png"):
        (plugin / "img" / name).write_bytes(b"")
    (plugin / "manifest.json").write_text(json.dumps({
        "Name": "Demo", "Author": "t", "Version": "1", "Icon": "img/act", "OS": [{"Platform": "windows"}],
        "CodePath": "x.html", "PropertyInspectorPath": "pi.html",
        "Actions": [{"Name": "Act", "UUID": "com.example.demo.act", "Icon": "img/act",
                     "Controllers": ["Keypad", "Encoder"],
                     "Encoder": {"Layout": "$B1", "layout": "$A0", "StackColor": "#fff"},
                     "States": [{"Image": "actionDefaultImage", "FontFamily": "Arial", "FontSize": "12"},
                                {"Image": "img/state", "FontFamily": "Verdana", "Title": "On"}]}],
    }), encoding="utf-8")
    snap = Catalog(paths).actions["com.example.demo.act"].snapshot
    assert snap["icon"] == "plugins/com.example.demo.sdPlugin/img/act.svg"  # .svg wins over .png
    assert snap["states"][0]["image"] == snap["icon"]  # actionDefaultImage
    assert snap["states"][1]["image"] == "plugins/com.example.demo.sdPlugin/img/state@2x.png"
    assert snap["states"][0]["family"] == "Liberation Sans"  # Arial remapped
    assert snap["states"][1]["family"] == "Verdana"  # OpenDeck's case bug: never remapped
    assert snap["states"][0]["size"] == 12 and snap["states"][1]["text"] == "On"
    assert snap["property_inspector"] == "plugins/com.example.demo.sdPlugin/pi.html"  # plugin-level fallback
    assert snap["encoder"]["layout"] == "$A0" and snap["encoder"]["stack_color"] == "#fff"


def test_validator_accepts_fixtures_and_catches_breakage(paths):
    catalog = Catalog(paths)
    doc = profile.load_profile(paths, "ulanzi-d200h", "Default")
    assert validate.profile_errors(doc, catalog.plugin_exists) == []
    doc["keys"][6]["current_state"] = 5
    doc["keys"][10]["context"] = "Keypad.11.0"
    doc["keys"][8]["action"]["plugin"] = "gone.sdPlugin"
    errors = validate.profile_errors(doc, catalog.plugin_exists)
    assert any("current_state" in e for e in errors)
    assert any("doesn't match its slot" in e for e in errors)
    assert any("not installed" in e for e in errors)


def test_layouts_and_slots(paths):
    d200h = get_layout(paths, "ulanzi-d200h")
    assert (d200h.rows, d200h.columns, d200h.touchpoints, d200h.encoders) == (3, 5, 0, 0)
    d200x = get_layout(paths, "Ulanzi D200X")
    assert (d200x.touchpoints, d200x.encoders) == (2, 3)
    assert parse_slot("r2c3", d200h).position == 7
    assert parse_slot("touch2", d200x).position == 16
    assert parse_slot("dial3", d200x).controller == "Encoder"
    assert parse_slot("r1c1/child2", d200h).child == 2
    with pytest.raises(ProfileError):
        parse_slot("r4c1", d200h)
    with pytest.raises(ProfileError):
        parse_slot("dial1", d200h)


def test_catalog_lookup(paths):
    catalog = Catalog(paths)
    assert catalog.find("open url").uuid == "com.amansprojects.starterpack.openurl"
    assert catalog.find("Multi Action").uuid == "opendeck.multiaction"
    with pytest.raises(ProfileError, match="ambiguous"):
        catalog.find("entity")
    with pytest.raises(ProfileError, match="can't go on"):
        catalog.find("Multi Action", "Encoder")


def test_secrets():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.c2lnbmF0dXJlLWZpeHR1cmU"
    out = secrets.redact({"accessToken": "x", "serverUrl": "http://ha.local:8123", "author": "me",
                          "url": "https://example.com/a/very-long-path-segment-that-is-just-words/x",
                          "header": f"Bearer {token}", "nested": {"note": token}})
    assert out["accessToken"] == secrets.REDACTED
    assert out["serverUrl"] == "http://ha.local:8123" and out["author"] == "me"
    assert out["url"].startswith("https://example.com/a/very-long")
    assert out["header"] == secrets.REDACTED and out["nested"]["note"] == secrets.REDACTED
    assert secrets.restore_placeholders({"accessToken": secrets.REDACTED, "x": 1},
                                        {"accessToken": "real"}) == {"accessToken": "real", "x": 1}


def test_profile_ids():
    assert profile.check_profile_id("Folder/Name") == "Folder/Name"
    for bad in ("a/b/c", "con", "x.json", "bad.name", " lead", ""):
        with pytest.raises(ProfileError):
            profile.check_profile_id(bad)


@pytest.mark.live
def test_live_snapshots_match():
    """Against the real install: every stored action equals a freshly built snapshot."""
    from opendeck_mcp.core.paths import discover

    real = discover()
    if not real.config_dir.is_dir():
        pytest.skip("no OpenDeck install")
    catalog = Catalog(real)
    for device in profile.list_devices(real):
        for profile_id in profile.list_profiles(real, device):
            for inst in all_instances(profile.load_profile(real, device, profile_id)):
                entry = catalog.actions.get(inst["action"]["uuid"])
                if entry:
                    assert jsonio.dumps(entry.snapshot) == jsonio.dumps(inst["action"])
