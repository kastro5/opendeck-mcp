"""The title fonts OpenDeck bundles, for drawing previews.

OpenDeck embeds its fonts in its executable, so they're fetched once from the
Google Fonts repository (all OFL) into the server's cache. Liberation Sans and
Liberation Serif are Arimo and Tinos under another name. Without a network the
preview falls back to a system font with the same metrics, or Pillow's own.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import httpx
from PIL import ImageFont

from .paths import Paths

SOURCE = "https://cdn.jsdelivr.net/gh/google/fonts@94301472b4d809299b4775bc6502592b83dd6934/ofl/"

# family -> (regular, bold, italic, bold italic); None falls back to the regular face (OpenDeck's
# canvas fakes the style then). A "variable:" prefix marks a variable font with a weight axis.
FILES: dict[str, tuple[str | None, ...]] = {
    "Liberation Sans": ("variable:arimo/Arimo%5Bwght%5D.ttf", "variable:arimo/Arimo%5Bwght%5D.ttf",
                        "variable:arimo/Arimo-Italic%5Bwght%5D.ttf", "variable:arimo/Arimo-Italic%5Bwght%5D.ttf"),
    "Liberation Serif": ("tinos/Tinos-Regular.ttf", "tinos/Tinos-Bold.ttf", "tinos/Tinos-Italic.ttf",
                         "tinos/Tinos-BoldItalic.ttf"),
    "Tinos": ("tinos/Tinos-Regular.ttf", "tinos/Tinos-Bold.ttf", "tinos/Tinos-Italic.ttf",
              "tinos/Tinos-BoldItalic.ttf"),
    "Anton": ("anton/Anton-Regular.ttf", None, None, None),
    "Archivo Black": ("archivoblack/ArchivoBlack-Regular.ttf", None, None, None),
    "Comic Neue": ("comicneue/ComicNeue-Regular.ttf", "comicneue/ComicNeue-Bold.ttf",
                   "comicneue/ComicNeue-Italic.ttf", "comicneue/ComicNeue-BoldItalic.ttf"),
    "Courier Prime": ("courierprime/CourierPrime-Regular.ttf", "courierprime/CourierPrime-Bold.ttf",
                      "courierprime/CourierPrime-Italic.ttf", "courierprime/CourierPrime-BoldItalic.ttf"),
    "Open Sans": ("variable:opensans/OpenSans%5Bwdth,wght%5D.ttf", "variable:opensans/OpenSans%5Bwdth,wght%5D.ttf",
                  "variable:opensans/OpenSans-Italic%5Bwdth,wght%5D.ttf",
                  "variable:opensans/OpenSans-Italic%5Bwdth,wght%5D.ttf"),
    "Fira Sans": ("firasans/FiraSans-Regular.ttf", "firasans/FiraSans-Bold.ttf", "firasans/FiraSans-Italic.ttf",
                  "firasans/FiraSans-BoldItalic.ttf"),
}
# Same-metric system fonts, by platform, for when the download isn't possible.
SYSTEM = {
    "sans": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "serif": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "mono": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
}
LINUX_FALLBACK = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf")


def _style_index(style: str) -> int:
    return ("Bold" in style) + 2 * ("Italic" in style)


def _download(paths: Paths, rel: str) -> Path | None:
    cache = paths.data_dir / "cache" / "fonts"
    target = cache / rel.replace("/", "_").replace("%5B", "[").replace("%5D", "]")
    if target.is_file():
        return target
    if (cache / ".offline").exists():
        return None
    try:
        response = httpx.get(SOURCE + rel, timeout=15, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / ".offline").touch()  # don't retry on every render; deleting the cache retries
        return None
    cache.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    return target


@lru_cache(maxsize=64)
def _cached_font(path: str, size: int, index: int, variable: bool) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path, size)
    if variable:
        name = ("Regular", "Bold", "Italic", "Bold Italic")[index]
        try:
            font.set_variation_by_name(name)
        except (OSError, ValueError):
            pass
    return font


def font(paths: Paths, family: str, style: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    index = _style_index(style or "Regular")
    files = FILES.get(family) or FILES["Liberation Sans"]
    rel = files[index] or files[0]
    variable = rel.startswith("variable:")
    rel = rel.removeprefix("variable:")
    local = _download(paths, rel)
    if local is not None:
        try:
            return _cached_font(str(local), size, index, variable)
        except OSError:
            pass
    kind = "mono" if "Courier" in family else "serif" if family in ("Tinos", "Liberation Serif") else "sans"
    for name in (SYSTEM[kind][index], LINUX_FALLBACK[index], "Helvetica.ttc"):
        try:
            return _cached_font(name, size, 0, False)
        except OSError:
            continue
    return ImageFont.load_default(size)
