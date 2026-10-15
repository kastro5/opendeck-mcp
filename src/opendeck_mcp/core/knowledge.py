"""What the settings of well-known actions mean (OpenDeck built-ins and the Starter Pack).

Each entry describes the instance `settings` object the action's own settings
page would write. Unknown plugins fall back to hints scraped from their
property inspector (pi_scrape) and examples from existing keys.
"""

from __future__ import annotations

ROTATE_NOTE = "On a dial, 'clockwise'/'anticlockwise' fire per tick; 'down'/'up' on press/release."

_HA_CALL = ("{entityId, serviceId: 'domain.service' (e.g. light.toggle), serviceData: a JSON *string* "
            "of extra data, may use {{ }} templates; empty strings when unused}")
HA_ENTITY = {
    "summary": "Shows a Home Assistant entity's state and calls a Home Assistant service on press. Find "
               "entities with ha_search; ha_describe_entity prints ready-made settings for one.",
    "settings": {
        "version": "8 (always)",
        "controllerType": "'Keypad' or 'Encoder'",
        "display.entityId": "entity shown on the key",
        "display.useCustomTitle / buttonTitle": "bool / title template, e.g. '{{friendly_name}}' or "
                                                "'{{state}} {{unit_of_measurement}}' (entity attributes)",
        "display.useCustomButtonLabels / buttonLabels": "bool / label templates, one per line",
        "display.iconSettings": "'PREFER_PLUGIN', 'PREFER_HA' (the entity's mdi icon) or 'HIDE'",
        "display.iconLayout": "'STANDARD', 'BOTTOM' or 'FULL'",
        "display.labelFontSize": "integer, default 48",
        "display.backgroundColor / backgroundColorEnd": "'' for none, one colour for solid, both for a gradient",
        "display.enableServiceIndicator": "bool: flash the key when a service is called",
        "display.useStateImagesForOnOffStates": "bool: show the action's state 1 image while the entity is on",
        "button.serviceShortPress": _HA_CALL,
        "button.serviceLongPress": "same shape; called on a long press",
        "button.serviceTap / serviceRotation": "same shape; touch strip tap / dial turn (serviceData may use "
                                               "{{ ticks }})",
        "rotationTickMultiplier / rotationTickBucketSizeMs": "dial tuning, default 1 / 300",
    },
    "notes": "Server URL and token are the plugin's global settings, set once in OpenDeck. serviceId "
             "'streamdeck.open_url' with serviceData '{\"url\": ...}' opens a URL instead.",
}

KNOWN: dict[str, dict] = {
    "opendeck.multiaction": {
        "summary": "Runs its child actions in order on one press. Add children with add_child.",
        "settings": {
            "delays": "list of integers (ms): delays[i] is the wait after child i; default 100. "
                      "Set with set_multi_delays.",
        },
        "notes": "Children must support multi-actions; Multi/Toggle actions can't be children.",
    },
    "opendeck.toggleaction": {
        "summary": "Cycles through its children: each press runs the next child. Add children with add_child.",
        "settings": {},
        "notes": "Has one state (icon/title) per child; state i shows while child i is next.",
    },
    "com.amansprojects.starterpack.runcommand": {
        "summary": "Runs a shell command (cmd /C on Windows, sh -c elsewhere) from the home directory.",
        "settings": {
            "down": "command run on key press",
            "up": "command run on key release",
            "rotate": "command run on dial turn; %d is replaced with the tick count (negative = anticlockwise)",
            "file": "optional path: write the command's output to this file",
            "show": "boolean: show the command's output as the key title",
        },
        "example": {"down": "notepad.exe"},
        "danger": "Runs arbitrary commands on your computer when pressed.",
    },
    "com.amansprojects.starterpack.openurl": {
        "summary": "Opens a URL (or any URI) with the system's default handler.",
        "settings": {
            "down": "URL opened on press", "up": "URL opened on release",
            "clockwise": "URL per clockwise dial tick", "anticlockwise": "URL per anticlockwise dial tick",
        },
        "example": {"down": "https://github.com", "up": "", "clockwise": "", "anticlockwise": ""},
        "notes": ROTATE_NOTE,
    },
    "com.amansprojects.starterpack.inputsimulation": {
        "summary": "Types text / presses keys / moves the mouse, using an enigo token list (RON syntax).",
        "settings": {
            "down": "token list run on press", "up": "token list run on release",
            "clockwise": "token list per clockwise tick", "anticlockwise": "token list per anticlockwise tick",
        },
        "syntax": "A RON list of enigo tokens, e.g. [t(\"hello\"),k(ctrl,p),k(uni('c')),k(ctrl,r)] types "
                  "'hello' then presses Ctrl+C. Tokens: t(\"text\") types text; k(KEY, DIR) a key, where DIR "
                  "is p (press), r (release) or c (click, the default, so k(Return) works); b(BUTTON, DIR) a "
                  "mouse button: l, r, m; m(x, y, a|r) moves the mouse absolute/relative (default a); "
                  "s(n, v|h) scrolls (default v). KEY names are case-sensitive PascalCase: Control (alias "
                  "ctrl), Shift, Alt, Meta, Command (alias cmd), Return, Tab, Escape, Space, Backspace, "
                  "Delete, Home, End, PageUp, PageDown, UpArrow, DownArrow, LeftArrow, RightArrow, F1..F24, "
                  "VolumeUp, VolumeDown, VolumeMute, MicMute, MediaPlayPause, MediaNextTrack, "
                  "MediaPrevTrack, MediaStop, PrintScr; uni('a') (alias char) for any character. "
                  "Lowercase 'shift' or 'return' do NOT parse.",
        "example": {"down": "[k(ctrl,p),k(Shift,p),k(uni('m')),k(Shift,r),k(ctrl,r)]", "up": "",
                    "clockwise": "", "anticlockwise": ""},
        "danger": "Sends keystrokes to whatever window has focus.",
    },
    "com.amansprojects.starterpack.switchprofile": {
        "summary": "Switches a device to another profile.",
        "settings": {
            "device": "device id (default: the key's own device)",
            "profile": "profile id to switch to, e.g. 'Gaming' or 'Folder/Name' (default 'Default')",
            "clockwise": "profile for clockwise dial turns", "anticlockwise": "profile for anticlockwise turns",
        },
        "example": {"device": "", "profile": "Gaming"},
    },
    "com.amansprojects.starterpack.devicebrightness": {
        "summary": "Sets, raises or lowers the deck's brightness.",
        "settings": {
            "action": "'set', 'increase' or 'decrease' (default 'set')",
            "value": "integer 0-100 (default 50): the level to set, or the step to change by",
        },
        "example": {"action": "set", "value": 35},
        "notes": "On a dial, turning raises/lowers by the tick count.",
    },
    "de.perdoctus.streamdeck.homeassistant.entity": HA_ENTITY,
    "de.perdoctus.streamdeck.homeassistant.dual-state-entity": {
        **HA_ENTITY,
        "summary": "Shows a Home Assistant entity with your own off/on images (state 0 = off, 1 = on) and "
                   "calls services on press. Set display.useStateImagesForOnOffStates to true.",
    },
}


def danger_note(uuid: str, settings: dict | None) -> str | None:
    """A warning for keys that run commands or send input, shown when staging and previewing."""
    settings = settings or {}
    if uuid == "com.amansprojects.starterpack.runcommand":
        cmds = [f"{k}: {settings[k]}" for k in ("down", "up", "rotate") if settings.get(k)]
        return "runs command(s) on your computer: " + "; ".join(cmds) if cmds else None
    if uuid == "com.amansprojects.starterpack.inputsimulation":
        seqs = [f"{k}: {settings[k]}" for k in ("down", "up", "clockwise", "anticlockwise") if settings.get(k)]
        return "simulates keyboard/mouse input: " + "; ".join(seqs) if seqs else None
    if uuid == "com.amansprojects.starterpack.openurl":
        odd = [str(v) for k, v in settings.items()
               if isinstance(v, str) and v and not v.lower().startswith(("http://", "https://"))]
        return "opens non-web URI(s): " + ", ".join(odd) if odd else None
    return None
