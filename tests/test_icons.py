import io
import json

import pytest
from PIL import Image

from opendeck_mcp.core import fonts, icons
from opendeck_mcp.core.profile import ProfileError
from opendeck_mcp.staging import plan
from opendeck_mcp.tools import edit_tools as E
from opendeck_mcp.tools import icon_tools as I
from opendeck_mcp.tools.view import View

DEV = "ulanzi-d200h"
SQUARE = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M0 0H24V24H0Z"/></svg>'
FILES = {
    icons.SIMPLE + "data/simple-icons.json": json.dumps([{"title": "GitHub", "slug": "github", "hex": "181717"},
                                                         {"title": "Spotify", "slug": "spotify", "hex": "1ED760"}]),
    icons.SIMPLE + "icons/github.svg": SQUARE,
    icons.MDI + "meta.json": json.dumps([{"name": "microphone-off", "aliases": ["mic-off"], "tags": ["Audio"]},
                                         {"name": "lightbulb-on", "aliases": [], "tags": []}]),
    icons.MDI + "svg/lightbulb-on.svg": SQUARE,
    icons.TWEMOJI + "1f525.svg": SQUARE.replace(b"<path", b'<path fill="#FF8800"'),
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def fetch(paths, rel):
        if rel not in FILES:
            raise ProfileError("no such icon")
        value = FILES[rel]
        return value.encode() if isinstance(value, str) else value

    monkeypatch.setattr(icons, "_fetch", fetch)
    monkeypatch.setattr(fonts, "_download", lambda paths, rel: None)


def pixel(data: bytes, xy=(72, 72)):
    return Image.open(io.BytesIO(data)).convert("RGB").getpixel(xy)


def test_symbols_take_the_colours_asked_for(paths):
    data, text = I.make_icon(paths, "mdi:lightbulb-on", "#000000", "#FFD000", 0.62, None, None)
    assert pixel(data) == (255, 208, 0) and pixel(data, (2, 2)) == (0, 0, 0)
    path = text.split("Saved ", 1)[1].splitlines()[0]
    assert Image.open(path).size == (icons.SIZE, icons.SIZE)


def test_brands_default_to_their_colour_with_a_readable_logo(paths):
    data, _ = I.make_icon(paths, "brand:github", None, None, None, None, None)
    assert pixel(data, (2, 2)) == (0x18, 0x17, 0x17) and pixel(data) == (255, 255, 255)


def test_emoji_keep_their_own_colours(paths):
    data, text = I.make_icon(paths, "emoji:🔥", None, None, None, None, None)
    assert pixel(data) == (255, 136, 0) and "1f525" in text and "Twemoji" in text


def test_text_icons_need_no_download(paths):
    data, _ = I.make_icon(paths, "text:Ext", "#2D6CDF", None, None, None, None)
    assert pixel(data, (2, 2)) == (45, 108, 223)


def test_bad_specs_and_unknown_names_explain_themselves(paths):
    with pytest.raises(ProfileError, match="icon must look like"):
        icons.parse("lightbulb")
    with pytest.raises(ProfileError, match="find_icons"):
        I.make_icon(paths, "mdi:nope", None, None, None, None, None)
    with pytest.raises(ProfileError, match="find_icons"):
        I.make_icon(paths, "brand:nope", None, None, None, None, None)


def test_find_icons_searches_names_aliases_and_brands(paths):
    assert I.find_icons(paths, "mic off", "mdi", 5).startswith("mdi:microphone-off")
    assert "brand:spotify (Spotify, #1ED760)" in I.find_icons(paths, "spot", "all", 5)
    assert I.find_icons(paths, "zzz", "all", 5).startswith("No all icons")


def test_brand_slug_rule():
    assert icons.brand_slug("Node.js") == "nodedotjs" and icons.brand_slug("C++") == "cplusplus"


def test_make_folder_stages_profile_and_both_keys(paths):
    out = E.make_folder(paths, DEV, "r2c1", "Lights", None, "r1c1", None, "default")
    assert "Default/Lights" in out
    view = View(paths)
    opener = view.profile(DEV, "Default")["keys"][5]
    back = view.profile(DEV, "Default/Lights")["keys"][0]
    assert opener["settings"] == {"device": DEV, "profile": "Default/Lights"}
    assert opener["states"][0]["text"] == "Lights"
    assert back["settings"] == {"device": DEV, "profile": "Default"} and back["states"][0]["text"] == "Back"
    assert [r["op"] for r in plan.load(paths)["ops"]][-3:] == ["create_profile", "set_key", "set_key"]


def test_make_folder_refuses_a_used_key(paths):
    with pytest.raises(ProfileError, match="isn't empty"):
        E.make_folder(paths, DEV, "r1c1", "Lights", None, "r1c1", None, "default")


def test_folders_stay_one_level_deep():
    assert E.folder_profile_id("Default", "Lights") == "Default/Lights"
    assert E.folder_profile_id("Work/Main", "Lights") == "Work/Lights"
