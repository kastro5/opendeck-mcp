"""Key icons made to order: text, an emoji, a brand logo or a generic symbol on a coloured square.

Sources, fetched on demand through jsDelivr and cached (all free to use):
- Material Design Icons (Apache 2.0): ~7,400 generic symbols, `mdi:lightbulb-on`;
- Simple Icons (CC0): ~3,400 brand logos with their brand colour, `brand:github`;
- Twemoji (CC-BY 4.0, by Twitter/X and contributors): emoji, `emoji:🔥`.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image, ImageColor, ImageDraw

from . import fonts
from .paths import Paths
from .profile import ProfileError

SIZE = 288
CDN = "https://cdn.jsdelivr.net/"
MDI = "npm/@mdi/svg@7.4.47/"
SIMPLE = "npm/simple-icons@16.33.0/"
TWEMOJI = "gh/jdecked/twemoji@17.0.3/assets/svg/"
DARK = "#1E1E24"


@dataclass
class IconSpec:
    kind: str  # text | emoji | brand | mdi
    value: str


def parse(spec: str) -> IconSpec:
    kind, sep, value = spec.partition(":")
    if not sep or kind not in ("text", "emoji", "brand", "mdi") or not value:
        raise ProfileError("icon must look like text:Hi, emoji:🔥, brand:github or mdi:lightbulb-on "
                           "(find names with find_icons)")
    return IconSpec(kind, value if kind in ("text", "emoji") else value.strip().lower())


# -- fetching ------------------------------------------------------------------------ #

def _fetch(paths: Paths, rel: str) -> bytes:
    cache = paths.data_dir / "cache" / "icons" / hashlib.sha1(rel.encode()).hexdigest()[:16]
    if cache.is_file():
        return cache.read_bytes()
    try:
        response = httpx.get(CDN + rel, timeout=20, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ProfileError(f"couldn't download the icon ({exc.__class__.__name__}); are you online?") from None
    if response.status_code == 404:
        raise ProfileError("no such icon")
    if response.status_code >= 400:
        raise ProfileError(f"icon download failed ({response.status_code})")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(response.content)
    return response.content


def brands(paths: Paths) -> list[dict]:
    return json.loads(_fetch(paths, SIMPLE + "data/simple-icons.json"))


def brand_slug(title: str) -> str:
    """Simple Icons' own title -> slug rule, for looking a brand up by name."""
    swaps = {"+": "plus", ".": "dot", "&": "and", "đ": "d", "ħ": "h", "ı": "i", "ĸ": "k", "ŀ": "l", "ł": "l",
             "ß": "ss", "ŧ": "t"}
    import unicodedata

    text = "".join(swaps.get(ch, ch) for ch in title.lower())
    text = unicodedata.normalize("NFD", text)
    return re.sub(r"[^a-z0-9]", "", text)


def mdi_names(paths: Paths) -> list[dict]:
    return json.loads(_fetch(paths, MDI + "meta.json"))


# -- drawing ------------------------------------------------------------------------- #

def _svg(svg: str, colour: str | None, size: int) -> Image.Image:
    from resvg_py import svg_to_bytes

    if colour:
        svg = re.sub(r"<svg\b", f'<svg fill="{colour}"', svg, count=1)
    return Image.open(io.BytesIO(bytes(svg_to_bytes(svg_string=svg, width=size, height=size)))).convert("RGBA")


def _contrast(hex_colour: str) -> str:
    r, g, b = ImageColor.getrgb(hex_colour)[:3]
    return "#000000" if 0.299 * r + 0.587 * g + 0.114 * b > 160 else "#FFFFFF"


def _emoji_file(value: str) -> str:
    # Twemoji names files by code points joined with "-", without the U+FE0F variation selector
    # (except where it's part of a keycap or a sequence's base).
    points = [f"{ord(ch):x}" for ch in value.strip()]
    if "200d" not in points:
        points = [p for p in points if p != "fe0f"]
    return "-".join(points) + ".svg"


def _fit_text(paths: Paths, draw: ImageDraw.ImageDraw, text: str, family: str, style: str, box: int):
    lines = text.split("\n")
    size = box
    while size > 12:
        font = fonts.font(paths, family, style, size)
        widths = [draw.textlength(line, font=font) for line in lines]
        if max(widths) <= box and size * 1.15 * len(lines) <= box:
            return font, size
        size -= 4
    return fonts.font(paths, family, style, 12), 12


def render(paths: Paths, spec: IconSpec, background: str | None, colour: str | None, scale: float,
           family: str = "Liberation Sans", style: str = "Bold") -> Image.Image:
    scale = min(max(scale, 0.2), 1.0)
    inner = round(SIZE * scale)
    if spec.kind == "brand":
        info = next((b for b in brands(paths) if b.get("slug", brand_slug(b["title"])) == spec.value), None)
        if info is None:
            raise ProfileError(f"no brand icon {spec.value!r}; find the slug with find_icons(set='brand')")
        brand = f"#{info['hex']}"
        background = background or brand
        colour = colour or _contrast(background)
        if colour.lower() == background.lower():
            colour = _contrast(background)
        art = _svg(_fetch(paths, f"{SIMPLE}icons/{spec.value}.svg").decode(), colour, inner)
    elif spec.kind == "mdi":
        background = background or DARK
        colour = colour or _contrast(background)
        try:
            raw = _fetch(paths, f"{MDI}svg/{spec.value}.svg").decode()
        except ProfileError as exc:
            raise ProfileError(f"no mdi icon {spec.value!r}; find names with find_icons(set='mdi')") from exc
        art = _svg(raw, colour, inner)
    elif spec.kind == "emoji":
        background = background or DARK
        try:
            raw = _fetch(paths, TWEMOJI + _emoji_file(spec.value)).decode()
        except ProfileError as exc:
            raise ProfileError(f"no Twemoji image for {spec.value!r}") from exc
        art = _svg(raw, None, inner)
    else:
        background = background or DARK
        colour = colour or _contrast(background)
        art = None
    try:
        img = Image.new("RGBA", (SIZE, SIZE), ImageColor.getrgb(background))
    except ValueError as exc:
        raise ProfileError(f"background must be a colour like #FF8800, not {background!r}") from exc
    if art is not None:
        img.alpha_composite(art, ((SIZE - art.width) // 2, (SIZE - art.height) // 2))
    else:
        draw = ImageDraw.Draw(img)
        text = spec.value.replace("\\n", "\n")
        font, size = _fit_text(paths, draw, text, family, style, inner)
        lines = text.split("\n")
        top = SIZE / 2 - size * 1.15 * len(lines) / 2
        for i, line in enumerate(lines):
            draw.text((SIZE / 2, top + size * 1.15 * (i + 0.5)), line, font=font, fill=colour, anchor="mm")
    return img.convert("RGB")


def save(paths: Paths, img: Image.Image, spec: IconSpec, background: str | None, colour: str | None,
         scale: float) -> Path:
    key = json.dumps([spec.kind, spec.value, background, colour, scale], ensure_ascii=False)
    value = _emoji_file(spec.value).removesuffix(".svg") if spec.kind == "emoji" else spec.value
    name = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:30] or spec.kind
    out = paths.data_dir / "icons" / f"{spec.kind}-{name}-{hashlib.sha1(key.encode()).hexdigest()[:8]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)
    return out


# -- searching ----------------------------------------------------------------------- #

def search(paths: Paths, query: str, icon_set: str, limit: int) -> list[str]:
    words = query.lower().split()
    rows: list[tuple[int, str]] = []
    if icon_set in ("mdi", "all"):
        for icon in mdi_names(paths):
            if icon.get("deprecated"):
                continue
            hay = " ".join([icon["name"], *icon.get("aliases", []), *icon.get("tags", [])]).lower()
            if all(w in hay for w in words):
                exact = sum(w in icon["name"] for w in words)
                rows.append((-exact * 10 + len(icon["name"]) // 4, f"mdi:{icon['name']}"))
    if icon_set in ("brand", "all"):
        for b in brands(paths):
            slug = b.get("slug") or brand_slug(b["title"])
            hay = f"{b['title']} {slug} {' '.join((b.get('aliases') or {}).get('aka', []))}".lower()
            if all(w in hay for w in words):
                rows.append((-20 + len(slug) // 4, f"brand:{slug} ({b['title']}, #{b['hex']})"))
    rows.sort()
    return [r for _, r in rows[:limit]]
