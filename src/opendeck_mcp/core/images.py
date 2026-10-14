"""Key images.

New images are stored in the profile as data: URLs. OpenDeck loads those as-is
and, the next time it saves the profile, writes them out to
images/<device>/<profile>/<context>/<state>.<ext> itself. So this package
never has to lay out image files, only remove folders that went stale.
"""

from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from .paths import Paths
from .profile import ProfileError

MAX_SIDE = 288  # OpenDeck renders keys at 144 (192 on the XL); 2x is plenty
MAX_BYTES = 8 * 1024 * 1024


def image_dir(paths: Paths, device: str, profile: str, context: str) -> Path:
    return paths.images_dir / device / profile / context


def _to_png_data_url(data: bytes) -> str:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGBA")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=True)
    except Exception as exc:
        raise ProfileError(f"not a readable image: {exc}") from exc
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def load_image(source: str) -> str:
    """A file path, http(s) URL or data: URL -> a PNG data: URL (SVG passes through as SVG)."""
    source = source.strip()
    if source.startswith("data:"):
        if ";base64," in source:
            header, payload = source.split(";base64,", 1)
            if "svg" in header:
                return source
            return _to_png_data_url(base64.b64decode(payload))
        return source
    if source.startswith(("http://", "https://")):
        try:
            response = httpx.get(source, follow_redirects=True, timeout=20)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProfileError(f"couldn't download image: {exc}") from exc
        data = response.content
        if len(data) > MAX_BYTES:
            raise ProfileError("image is larger than 8 MB")
        if "svg" in response.headers.get("content-type", "") or source.lower().endswith(".svg"):
            return "data:image/svg+xml;base64," + base64.b64encode(data).decode("ascii")
        return _to_png_data_url(data)
    path = Path(source).expanduser()
    if not path.is_file():
        raise ProfileError(f"image file not found: {source}")
    if path.stat().st_size > MAX_BYTES:
        raise ProfileError("image is larger than 8 MB")
    if path.suffix.lower() == ".svg":
        return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    return _to_png_data_url(path.read_bytes())


def _file_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def inline_images(instance: dict[str, Any], paths: Paths, device: str, profile: str) -> list[Path]:
    """Turn an instance's custom images (bare filenames like "0.png", which are relative to its
    own image folder) into data: URLs, so it can move to another slot or profile intact.
    Returns the folders that become stale."""
    stale = []
    for item in [instance] + list(instance.get("children") or []):
        folder = image_dir(paths, device, profile, item["context"])
        for state in item.get("states") or []:
            image = state.get("image", "")
            if image and image[0].isdigit() and "/" not in image:
                file = folder / image
                if file.is_file():
                    state["image"] = _file_to_data_url(file)
                    stale.append(folder)
    return sorted(set(stale))
