import base64
import io

import pytest
from PIL import Image

from opendeck_mcp.core import fonts, preview
from opendeck_mcp.tools import edit_tools as E
from opendeck_mcp.tools import preview_tools as P

DEV = "ulanzi-d200h"


@pytest.fixture(autouse=True)
def offline_fonts(monkeypatch):
    monkeypatch.setattr(fonts, "_download", lambda paths, rel: None)


def data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def instance(image: str, **state) -> dict:
    return {"context": "Keypad.0.0", "current_state": 0, "settings": {},
            "action": {"icon": "", "states": [{"image": ""}]},
            "states": [{"image": image, "show": False, "text": "", "background_colour": "#000000",
                        "image_scale": 100, **state}]}


def test_key_draws_image_background_and_scale(paths):
    red = data_url(Image.new("RGB", (10, 10), (255, 0, 0)))
    key = preview.draw_key(paths, DEV, "Default", instance(red))
    assert key.getpixel((72, 72)) == (255, 0, 0)
    half = preview.draw_key(paths, DEV, "Default", instance(red, image_scale=50, background_colour="#0000FF"))
    assert half.getpixel((72, 72)) == (255, 0, 0) and half.getpixel((5, 5)) == (0, 0, 255)


def test_title_is_drawn_where_its_alignment_says(paths):
    black = data_url(Image.new("RGB", (10, 10), (0, 0, 0)))
    key = preview.draw_key(paths, DEV, "Default", instance(black, show=True, text="HI", colour="#FFFFFF",
                                                            size=16, alignment="top", stroke_size=0))
    top = key.crop((0, 0, 144, 40)).getextrema()
    bottom = key.crop((0, 100, 144, 144)).getextrema()
    assert max(c[1] for c in top) > 200 and max(c[1] for c in bottom) == 0


def test_sixteen_bit_greyscale_isnt_white(paths, tmp_path):
    img = Image.new("I;16", (10, 10), 3000)
    path = tmp_path / "grey.png"
    img.save(path)
    key = preview.draw_key(paths, DEV, "Default", instance(str(path)))
    assert key.getpixel((72, 72))[0] < 30


def test_svg_and_missing_images(paths):
    svg = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='4' height='4'%3E%3Crect " \
          "width='4' height='4' fill='%2300ff00'/%3E%3C/svg%3E"
    assert preview.draw_key(paths, DEV, "Default", instance(svg)).getpixel((72, 72)) == (0, 255, 0)
    alert = preview.draw_key(paths, DEV, "Default", instance("plugins/nope/missing.png"))
    assert alert.getpixel((72, 40))[0] > 200  # the red warning triangle


def test_render_profile_outlines_staged_keys(paths):
    E.set_key(paths, DEV, "r1c2", "Open URL", None, "Hi", {"down": "https://x.y"}, None, None)
    data, caption = P.render_profile(paths, DEV, None, True, False)
    img = Image.open(io.BytesIO(data))
    assert img.format == "PNG" and img.width > 700
    assert "r1c2" in caption and "staged" in caption
    both, caption = P.render_profile(paths, DEV, None, True, True)
    assert Image.open(io.BytesIO(both)).width > 2 * img.width - 10 and "Left: as it is now" in caption


@pytest.mark.anyio
async def test_render_profile_tool_returns_an_image(paths):
    from mcp.client import Client

    from opendeck_mcp.server import mcp

    async with Client(mcp) as client:
        result = await client.call_tool("render_profile", {"device": DEV})
    kinds = [c.type for c in result.content]
    assert kinds == ["image", "text"] and result.content[0].mime_type == "image/png"
