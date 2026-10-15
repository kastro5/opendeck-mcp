"""A picture of a profile: each key drawn the way OpenDeck draws it.

The key drawing mirrors OpenDeck's renderImage (src/lib/rendererHelper.ts): a
144 px canvas, the background colour unless it is black, the image scaled by
image_scale, then the title at size * 2 px with its stroke, alignment and
underline. Images resolve the way OpenDeck loads them from disk.

What it can't show: images a plugin sets while running (a live clock, a Home
Assistant state icon) unless OpenDeck has saved them, and the "ok"/"alert"
overlays.
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from PIL import Image, ImageColor, ImageDraw

from . import fonts
from .devices import DeviceLayout
from .paths import Paths

KEY = 144
GAP = 18
PAD = 24
LABEL = 18
BG = (24, 24, 27)
EMPTY = (40, 40, 46)
LABEL_COLOUR = (150, 150, 160)
CHANGED = (255, 159, 28)
DIAL = 112


# -- images -------------------------------------------------------------------------- #

def _svg_to_png(svg: str) -> bytes:
    import resvg_py

    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=KEY * 2, height=KEY * 2))


def _open(data: bytes, svg: bool) -> Image.Image:
    if svg:
        data = _svg_to_png(data.decode("utf-8", "replace"))
    img = Image.open(io.BytesIO(data))
    img.seek(0)
    if img.mode.startswith("I"):  # 16-bit greyscale: a plain convert clips every pixel to white
        img = img.convert("I").point(lambda v: v / 256).convert("L")
    return img.convert("RGBA")


def _builtin(name: str) -> Image.Image:
    """OpenDeck's own Multi/Toggle Action icons live inside its executable; draw stand-ins."""
    img = Image.new("RGBA", (KEY, KEY), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (235, 235, 240, 255)
    if "toggle" in name:
        d.rounded_rectangle((32, 52, 112, 92), radius=20, outline=white, width=6)
        d.ellipse((72, 56, 104, 88), fill=white)
    elif "multi" in name:
        for i in range(3):
            o = 14 * i
            d.rounded_rectangle((34 + o, 34 + o, 82 + o, 82 + o), radius=8, outline=white, width=5,
                                fill=(24, 24, 27, 255))
    else:
        d.rectangle((44, 44, 100, 100), outline=white, width=5)
    return img


_DATA = re.compile(r"^data:image/([\w.+-]+)(;base64)?,(.*)$", re.S)


def load_image(paths: Paths, device: str, profile: str, context: str, value: str | None) -> Image.Image | None:
    if not value:
        return None
    try:
        if value.startswith("opendeck/"):
            return _builtin(value)
        match = _DATA.match(value)
        if match:
            kind, b64, body = match.groups()
            raw = base64.b64decode(body) if b64 else unquote(body).encode("utf-8")
            return _open(raw, kind.startswith("svg"))
        if value[0].isdigit():  # a file OpenDeck extracted from a data: URL
            path = paths.images_dir / device / profile / context / value
        else:
            path = Path(value) if Path(value).is_absolute() else paths.config_dir / value
        return _open(path.read_bytes(), path.suffix.lower() == ".svg")
    except Exception:  # an unreadable image shows as OpenDeck's alert, not an error
        return None


# -- keys ---------------------------------------------------------------------------- #

def _colour(value: Any, default: tuple) -> Any:
    return value if isinstance(value, str) and value.startswith("#") else default


def _alert() -> Image.Image:
    img = Image.new("RGBA", (KEY, KEY), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    d.polygon([(72, 34), (114, 108), (30, 108)], outline=(230, 60, 60, 255), width=6)
    d.line((72, 60, 72, 86), fill=(230, 60, 60, 255), width=6)
    d.ellipse((68, 94, 76, 102), fill=(230, 60, 60, 255))
    return img


def draw_key(paths: Paths, device: str, profile: str, inst: dict, size: int = KEY) -> Image.Image:
    states = inst.get("states") or [{}]
    current = min(max(int(inst.get("current_state") or 0), 0), len(states) - 1)
    state = states[current]
    action_states = (inst.get("action") or {}).get("states") or []
    fallback = (action_states[min(current, len(action_states) - 1)].get("image") if action_states else None) \
        or (inst.get("action") or {}).get("icon")
    canvas = Image.new("RGBA", (KEY, KEY), (0, 0, 0, 255))
    image = load_image(paths, device, profile, inst["context"], state.get("image")) \
        or load_image(paths, device, profile, inst["context"], fallback)
    if image is None:
        canvas = _alert()
    else:
        background = str(state.get("background_colour") or "#000000")
        if not background.startswith("#000000"):
            try:
                canvas.paste(ImageColor.getrgb(background[:7]) + (255,), (0, 0, KEY, KEY))
            except ValueError:
                pass
        scale = max(10, int(state.get("image_scale") or 100)) / 100
        side = round(KEY * scale)
        image = image.resize((side, side), Image.LANCZOS)
        offset = (KEY - side) // 2
        canvas.alpha_composite(image, (offset, offset)) if offset >= 0 else \
            canvas.alpha_composite(image.crop((-offset, -offset, -offset + KEY, -offset + KEY)))
    if state.get("show", True) and state.get("text"):
        _draw_title(paths, canvas, state)
    return canvas.convert("RGB").resize((size, size), Image.LANCZOS) if size != KEY else canvas.convert("RGB")


def _draw_title(paths: Paths, canvas: Image.Image, state: dict) -> None:
    size = int(state.get("size") or 16) * 2
    font = fonts.font(paths, str(state.get("family") or "Liberation Sans"), str(state.get("style") or "Regular"),
                      size)
    d = ImageDraw.Draw(canvas)
    stroke = int(state.get("stroke_size") or 0)
    lines = str(state["text"]).split("\n")
    x = KEY / 2
    y = KEY / 2 - size * len(lines) * 0.5
    if state.get("alignment") == "top":
        y = stroke
    elif state.get("alignment") == "bottom":
        y = KEY - size * len(lines) - stroke
    colour = _colour(state.get("colour"), "#FFFFFF")
    for i, line in enumerate(lines):
        top = y + size * i
        # Canvas strokes are centred on the outline, so half the line width shows outside the glyph.
        d.text((x, top), line, font=font, fill=colour, anchor="ma",
               stroke_width=max(0, round(stroke / 2)), stroke_fill=_colour(state.get("stroke_colour"), "#000000"))
        if state.get("underline"):
            width = d.textlength(line, font=font)
            d.rectangle((x - width / 2 - 3, top + size, x + width / 2 + 3, top + size + 9), fill="black")
            d.rectangle((x - width / 2, top + size + 4, x + width / 2, top + size + 7), fill=colour)


# -- the deck ------------------------------------------------------------------------ #

def _rounded(img: Image.Image, radius: int = 14) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def render(paths: Paths, layout: DeviceLayout, profile: str, doc: dict, changed: set[str] | None = None,
           heading: str | None = None) -> Image.Image:
    """The whole deck: keys in their grid, then touchpoints, dials and info bars."""
    changed = changed or set()
    keys = doc.get("keys") or []
    columns = layout.columns or 5
    grid = layout.grid if layout.grid is not None else len(keys)
    rows = -(-grid // columns) if grid else 0
    touch = list(range(grid, len(keys)))
    dials = doc.get("sliders") or []
    bars = doc.get("infobars") or []
    cell = KEY + GAP
    width = PAD * 2 + columns * cell - GAP
    top = PAD + (LABEL + 8 if heading else 0)
    height = top + rows * (cell + LABEL) + (cell + LABEL if touch else 0) \
        + ((DIAL + GAP + LABEL * 2) if dials else 0) + ((KEY // 2 + GAP + LABEL) if bars else 0) + PAD - GAP
    sheet = Image.new("RGB", (width, max(height, top + KEY)), BG)
    d = ImageDraw.Draw(sheet)
    small = fonts.font(paths, "Liberation Sans", "Regular", 14)
    if heading:
        d.text((PAD, PAD), heading, font=fonts.font(paths, "Liberation Sans", "Bold", 16), fill=(230, 230, 235))

    def slot(controller: str, position: int, x: int, y: int, w: int, h: int, label: str) -> None:
        inst = (keys if controller == "Keypad" else dials if controller == "Encoder" else bars)[position]
        if inst is None:
            d.rounded_rectangle((x, y, x + w, y + h), radius=14, fill=EMPTY)
        else:
            face = draw_key(paths, layout.id, profile, inst, min(w, h))
            if w > h:  # a wide cell: the key's image centred, as the device shows it
                d.rounded_rectangle((x, y, x + w, y + h), radius=14, fill=(0, 0, 0))
                sheet.paste(_rounded(face), (x + (w - h) // 2, y), _rounded(face))
            else:
                sheet.paste(_rounded(face), (x, y), _rounded(face))
        if label in changed:
            d.rounded_rectangle((x - 4, y - 4, x + w + 4, y + h + 4), radius=16, outline=CHANGED, width=4)
        d.text((x + w / 2, y + h + 4), label, font=small, fill=CHANGED if label in changed else LABEL_COLOUR,
               anchor="ma")

    y = top
    for r in range(rows):
        for c in range(columns):
            pos = r * columns + c
            if pos >= grid or pos >= len(keys):
                continue
            quirk = layout.quirks.get(pos, "")
            if "unused" in quirk:
                continue
            w = KEY + cell if "double width" in quirk else KEY
            slot("Keypad", pos, PAD + c * cell, y, w, KEY, layout.label("Keypad", pos))
        y += cell + LABEL
    if touch:
        for i, pos in enumerate(touch):
            slot("Keypad", pos, PAD + i * cell, y, KEY, KEY, layout.label("Keypad", pos))
        y += cell + LABEL
    if dials:
        step = (width - PAD * 2) // max(len(dials), 1)
        for i, inst in enumerate(dials):
            x = PAD + i * step + (step - DIAL) // 2
            label = layout.label("Encoder", i)
            d.ellipse((x - 6, y - 6, x + DIAL + 6, y + DIAL + 6), fill=(60, 60, 68))
            if inst is not None:
                face = draw_key(paths, layout.id, profile, inst, DIAL)
                mask = Image.new("L", (DIAL, DIAL), 0)
                ImageDraw.Draw(mask).ellipse((0, 0, DIAL - 1, DIAL - 1), fill=255)
                sheet.paste(face, (x, y), mask)
            else:
                d.ellipse((x, y, x + DIAL, y + DIAL), fill=EMPTY)
            if label in changed:
                d.ellipse((x - 8, y - 8, x + DIAL + 8, y + DIAL + 8), outline=CHANGED, width=4)
            d.text((x + DIAL / 2, y + DIAL + 10), label, font=small,
                   fill=CHANGED if label in changed else LABEL_COLOUR, anchor="ma")
        y += DIAL + GAP + LABEL * 2
    if bars:
        w = (width - PAD * 2 - GAP * (len(bars) - 1)) // len(bars)
        for i in range(len(bars)):
            slot("Infobar", i, PAD + i * (w + GAP), y, w, KEY // 2, layout.label("Infobar", i))
    return sheet


def side_by_side(left: Image.Image, right: Image.Image) -> Image.Image:
    out = Image.new("RGB", (left.width + right.width + GAP, max(left.height, right.height)), (60, 60, 68))
    out.paste(left, (0, 0))
    out.paste(right, (left.width + GAP, 0))
    return out


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
