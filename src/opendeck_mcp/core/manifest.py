"""Plugin manifests -> the Action snapshots OpenDeck stores inside every instance.

This mirrors OpenDeck 2.14's manifest loading (plugins/mod.rs initialise_plugin,
shared.rs Action/ActionState serde defaults, simplified_profile.rs path
normalisation) closely enough that a snapshot built here is byte-identical to
one OpenDeck writes when you drag the same action onto a key.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .jsonio import merge_patch, read_json
from .paths import Paths, rust_os

# Font families OpenDeck swaps for bundled ones. It matches on the lowercased
# name, so its arms for "Courier New", "Times New Roman" and "Verdana" never
# fire; that bug is reproduced on purpose.
FONT_REMAP = {
    "arial": "Liberation Sans",
    "arial black": "Archivo Black",
    "comic sans ms": "Comic Neue",
    "courier": "Courier Prime",
    "georgia": "Tinos",
    "impact": "Anton",
    "microsoft sans serif": "Liberation Serif",
    "tahoma": "Open Sans",
    "trebuchet ms": "Fira Sans",
}

FONTS = ("Liberation Sans", "Archivo Black", "Comic Neue", "Courier Prime", "Tinos", "Anton",
         "Liberation Serif", "Open Sans", "Fira Sans")
FONT_STYLES = ("Regular", "Bold", "Italic", "Bold Italic")

STATE_DEFAULTS: dict[str, Any] = {
    "image": "actionDefaultImage",
    "image_scale": 100,
    "background_colour": "#000000",
    "name": "",
    "text": "",
    "show": True,
    "colour": "#FFFFFF",
    "stroke_colour": "#000000",
    "alignment": "middle",
    "family": "Liberation Sans",
    "style": "Regular",
    "size": 16,
    "stroke_size": 3,
    "underline": False,
}
# snake_case field -> manifest (PascalCase) alias
STATE_ALIASES = {
    "image": "Image", "image_scale": "ImageScale", "background_colour": "BackgroundColour", "name": "Name",
    "text": "Title", "show": "ShowTitle", "colour": "TitleColor", "stroke_colour": "TitleStroke",
    "alignment": "TitleAlignment", "family": "FontFamily", "style": "FontStyle", "size": "FontSize",
    "stroke_size": "StrokeSize", "underline": "FontUnderline",
}


class ManifestError(ValueError):
    pass


def _get(obj: dict, snake: str, pascal: str, default: Any = None) -> Any:
    if snake in obj:
        return obj[snake]
    return obj.get(pascal, default)


def _font_size(value: Any, default: int) -> int:
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return default
    return int(value) if isinstance(value, (int, float)) else default


def make_state(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """An ActionState with every field present, as serde fills them."""
    raw = raw or {}
    state = {}
    for key, default in STATE_DEFAULTS.items():
        value = _get(raw, key, STATE_ALIASES[key], default)
        if key in ("size", "stroke_size"):
            value = _font_size(value, default)
        state[key] = value
    return state


def convert_icon(base: str) -> str:
    """OpenDeck's icon resolution: <base>.svg, else <base>@2x.png, else <base>.png."""
    if os.path.exists(base + ".svg"):
        return base + ".svg"
    if os.path.exists(base + "@2x.png"):
        return base + "@2x.png"
    return base + ".png"


@dataclass
class PluginInfo:
    folder: str  # also the plugin "uuid" OpenDeck uses everywhere
    path: Path
    manifest: dict[str, Any]
    actions: list[dict[str, Any]] = field(default_factory=list)  # Action snapshots
    localized: dict[str, dict[str, str]] = field(default_factory=dict)
    error: str | None = None

    @property
    def name(self) -> str:
        return str(self.localized.get("__plugin__", {}).get("Name") or self.manifest.get("Name") or self.folder)

    @property
    def version(self) -> str:
        return str(self.manifest.get("Version", "?"))

    @property
    def category(self) -> str:
        return str(self.manifest.get("Category") or "Custom")

    @property
    def device_namespace(self) -> str | None:
        return self.manifest.get("DeviceNamespace")

    def display_name(self, uuid: str, fallback: str) -> str:
        return self.localized.get(uuid, {}).get("Name") or fallback


def read_manifest(plugin_dir: Path) -> dict[str, Any]:
    manifest = read_json(plugin_dir / "manifest.json")
    override = plugin_dir / f"manifest.{rust_os()}.json"
    if override.exists():
        manifest = merge_patch(manifest, read_json(override))
    if not isinstance(manifest, dict):
        raise ManifestError("manifest is not an object")
    return manifest


class SnapshotBuilder:
    """Turns manifest actions into on-disk Action snapshots for one config dir."""

    def __init__(self, paths: Paths):
        self.paths = paths
        self.config = str(paths.config_dir)

    def normalise(self, value: str) -> str:
        """simplified_profile.rs normalise_path: config-relative with '/' when inside config."""
        if not value:
            return value
        try:
            rel = Path(value).relative_to(self.paths.config_dir)
        except ValueError:
            return value.replace("\\", "/")
        return rel.as_posix()

    def action(self, plugin_dir: Path, manifest: dict[str, Any], raw: dict[str, Any],
               final_dir: Path | None = None) -> dict[str, Any]:
        """final_dir: where the plugin will live, when building from a staged (cached) copy."""
        folder = plugin_dir.name
        src = str(plugin_dir)
        base = str(final_dir or plugin_dir)

        def resolve(rel: str) -> str:
            # Probe the files where they are now, but name them where OpenDeck will see them.
            return base + convert_icon(os.path.join(src, rel))[len(src):]
        name = _get(raw, "name", "Name")
        uuid = _get(raw, "uuid", "UUID")
        states_raw = _get(raw, "states", "States")
        if not isinstance(name, str) or not isinstance(uuid, str) or not isinstance(states_raw, list):
            raise ManifestError(f"action {uuid or name!r} lacks Name/UUID/States")

        icon = resolve(_get(raw, "icon", "Icon", ""))

        pi = _get(raw, "property_inspector", "PropertyInspectorPath", "")
        if pi:
            pi = os.path.join(base, pi)
        elif manifest.get("PropertyInspectorPath"):
            pi = os.path.join(base, manifest["PropertyInspectorPath"])

        encoder = None
        enc_raw = _get(raw, "encoder", "Encoder")
        if isinstance(enc_raw, dict):
            trig = _get(enc_raw, "trigger_description", "TriggerDescription", {}) or {}
            enc_icon = _get(enc_raw, "icon", "Icon", "")
            background = enc_raw.get("background", "")  # lowercase only in OpenDeck
            encoder = {
                "icon": resolve(enc_icon) if enc_icon else "",
                "stack_color": _get(enc_raw, "stack_color", "StackColor", ""),
                "trigger_description": {
                    "long_touch": _get(trig, "long_touch", "LongTouch", ""),
                    "push": _get(trig, "push", "Push", ""),
                    "rotate": _get(trig, "rotate", "Rotate", ""),
                    "touch": _get(trig, "touch", "Touch", ""),
                },
                "background": resolve(background) if background else "",
                "layout": enc_raw.get("layout", "$X1"),  # "Layout" is ignored by OpenDeck
            }

        states = []
        for state_raw in states_raw:
            state = make_state(state_raw if isinstance(state_raw, dict) else {})
            if state["image"] == "actionDefaultImage":
                state["image"] = icon
            else:
                state["image"] = resolve(state["image"])
            remapped = FONT_REMAP.get(state["family"].lower().strip())
            if remapped:
                state["family"] = remapped
            state["image"] = self.normalise(state["image"])
            states.append(state)

        return {
            "name": name,
            "uuid": uuid,
            "plugin": folder,
            "tooltip": _get(raw, "tooltip", "Tooltip", ""),
            "icon": self.normalise(icon),
            "disable_automatic_states": bool(_get(raw, "disable_automatic_states", "DisableAutomaticStates", False)),
            "visible_in_action_list": bool(_get(raw, "visible_in_action_list", "VisibleInActionsList", True)),
            "supported_in_multi_actions": bool(
                _get(raw, "supported_in_multi_actions", "SupportedInMultiActions", True)),
            "property_inspector": self.normalise(pi),
            "controllers": list(_get(raw, "controllers", "Controllers", ["Keypad"])),
            "encoder": encoder,
            "states": states,
        }

    def plugin(self, plugin_dir: Path, language: str = "en", final_dir: Path | None = None) -> PluginInfo:
        try:
            manifest = read_manifest(plugin_dir)
        except Exception as exc:  # unreadable plugin: listed, but with its error
            return PluginInfo(plugin_dir.name, plugin_dir, {}, error=f"manifest: {exc}")
        info = PluginInfo(plugin_dir.name, plugin_dir, manifest)
        missing = [k for k in ("Name", "Author", "Version", "Icon", "Actions", "OS") if k not in manifest]
        if missing:
            info.error = f"manifest lacks {', '.join(missing)}; OpenDeck will not load it"
        for raw in manifest.get("Actions") or []:
            try:
                info.actions.append(self.action(plugin_dir, manifest, raw, final_dir))
            except ManifestError as exc:
                info.error = str(exc)
        loc = plugin_dir / f"{language}.json"
        if loc.exists():
            try:
                data = read_json(loc)
                info.localized = {k: v for k, v in data.items() if isinstance(v, dict)}
                if isinstance(data.get("Name"), str):
                    info.localized["__plugin__"] = {"Name": data["Name"]}
            except Exception:
                pass
        return info


def builtin_actions() -> list[dict[str, Any]]:
    """OpenDeck's own Multi Action and Toggle Action, as stored in profiles."""
    out = []
    for uuid, name, tooltip, image in (
            ("opendeck.multiaction", "Multi Action", "Execute multiple actions", "opendeck/multi-action.png"),
            ("opendeck.toggleaction", "Toggle Action", "Cycle through multiple actions",
             "opendeck/toggle-action.png")):
        state = make_state({"image": image})
        out.append({
            "name": name, "uuid": uuid, "plugin": "opendeck", "tooltip": tooltip, "icon": image,
            "disable_automatic_states": False, "visible_in_action_list": True,
            "supported_in_multi_actions": False, "property_inspector": "", "controllers": ["Keypad"],
            "encoder": None, "states": [state],
        })
    return out


def new_instance(action: dict[str, Any], controller: str, position: int, index: int = 0) -> dict[str, Any]:
    """A fresh instance, exactly as create_instance builds one."""
    return {
        "action": copy.deepcopy(action),
        "context": f"{controller}.{position}.{index}",
        "states": copy.deepcopy(action["states"]),
        "current_state": 0,
        "settings": {},
        "children": [] if action["uuid"] in ("opendeck.multiaction", "opendeck.toggleaction") and index == 0
        else None,
    }
