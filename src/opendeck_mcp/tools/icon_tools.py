"""make_icon / find_icons: key images made to order."""

from __future__ import annotations

import io

from ..core import icons
from ..core.paths import Paths


def make_icon(paths: Paths, icon: str, background: str | None, colour: str | None, scale: float | None,
              font: str | None, style: str | None) -> tuple[bytes, str]:
    spec = icons.parse(icon)
    default_scale = 0.62 if spec.kind in ("brand", "mdi") else 0.72 if spec.kind == "emoji" else 0.8
    scale = scale or default_scale
    img = icons.render(paths, spec, background, colour, scale, font or "Liberation Sans", style or "Bold")
    path = icons.save(paths, img, spec, background, colour, scale)
    preview = io.BytesIO()
    img.resize((144, 144)).save(preview, format="PNG")
    credit = " Emoji art: Twemoji (CC-BY 4.0)." if spec.kind == "emoji" else ""
    return preview.getvalue(), (f"Saved {path}\nUse it with set_key / update_key / add_child: image={str(path)!r}. "
                                "If the key also gets a title, put it under the art: title_style "
                                "{'alignment': 'bottom', 'size': 11}." + credit)


def find_icons(paths: Paths, query: str, icon_set: str, limit: int) -> str:
    rows = icons.search(paths, query, icon_set, max(1, min(limit, 60)))
    if not rows:
        return f"No {icon_set} icons match {query!r}; try a broader word (e.g. 'light' rather than 'lamp')."
    return "\n".join(rows) + "\nMake one with make_icon(icon=...), using the part before any parentheses."
