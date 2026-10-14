"""Check a profile document before it is written.

OpenDeck replaces a profile it can't deserialize with an EMPTY one, so every
write is checked twice: the document, then the serialized bytes re-parsed.
Errors are strings meant for humans and agents.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .profile import ARRAY_FOR, PARENT_UUIDS

_CTX = re.compile(r"^(Keypad|Encoder|Infobar)\.(\d{1,3})\.(\d{1,5})$")
_STR_FIELDS = ("image", "name", "text", "colour", "stroke_colour", "alignment", "family", "style",
               "background_colour")


def _state_errors(state: Any, where: str) -> list[str]:
    if not isinstance(state, dict):
        return [f"{where}: state is not an object"]
    errors = []
    for key in _STR_FIELDS:
        if key in state and not isinstance(state[key], str):
            errors.append(f"{where}: {key} must be a string")
    for key in ("show", "underline"):
        if key in state and not isinstance(state[key], bool):
            errors.append(f"{where}: {key} must be true/false")
    if "image_scale" in state and not (isinstance(state["image_scale"], int) and 0 <= state["image_scale"] <= 255):
        errors.append(f"{where}: image_scale must be an integer 0-255")
    for key in ("size", "stroke_size"):
        value = state.get(key, 0)
        ok = (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 65535) or (
            isinstance(value, str) and value.strip().replace(".", "", 1).isdigit())
        if not ok:
            errors.append(f"{where}: {key} must be a number 0-65535")
    if "alignment" in state and state["alignment"] not in ("top", "middle", "bottom"):
        errors.append(f"{where}: alignment must be top, middle or bottom")
    return errors


def _instance_errors(inst: Any, controller: str, position: int, index: int, where: str,
                     plugin_exists: Callable[[str], bool] | None, child: bool = False) -> list[str]:
    if not isinstance(inst, dict):
        return [f"{where}: not an object"]
    errors = []
    for key in ("action", "context", "states", "current_state", "settings"):
        if key not in inst:
            errors.append(f"{where}: missing {key}")
    if errors:
        return errors
    action = inst["action"]
    if not isinstance(action, dict) or not isinstance(action.get("name"), str) or not isinstance(
            action.get("uuid"), str) or not isinstance(action.get("states"), list) or not action["states"]:
        return [f"{where}: action needs name, uuid and non-empty states"]
    m = _CTX.match(str(inst["context"]))
    if not m:
        errors.append(f"{where}: bad context {inst['context']!r}")
    elif (m.group(1), int(m.group(2))) != (controller, position) or (
            int(m.group(3)) != 0 if not child else int(m.group(3)) < 1):
        # OpenDeck leaves gaps in child numbering after deletions, so children only need index >= 1.
        errors.append(f"{where}: context {inst['context']} doesn't match its slot {controller}.{position}")
    controllers = action.get("controllers", ["Keypad"])
    if not isinstance(controllers, list) or controller not in controllers:
        errors.append(f"{where}: {action['uuid']} doesn't support the {controller} controller")
    states = inst["states"]
    if not isinstance(states, list) or not states:
        errors.append(f"{where}: states must be a non-empty list")
    else:
        for i, state in enumerate(states):
            errors += _state_errors(state, f"{where} state {i}")
        cs = inst["current_state"]
        if not isinstance(cs, int) or isinstance(cs, bool) or not 0 <= cs < len(states):
            errors.append(f"{where}: current_state {cs!r} out of range")
    for i, state in enumerate(action["states"]):
        errors += _state_errors(state, f"{where} action state {i}")
    if plugin_exists is not None and not plugin_exists(action.get("plugin", "")):
        errors.append(f"{where}: plugin {action.get('plugin')!r} is not installed; OpenDeck would delete this key")
    children = inst.get("children")
    uuid = action["uuid"]
    if uuid in PARENT_UUIDS and not child:
        if not isinstance(children, list):
            errors.append(f"{where}: {uuid} needs a children list")
        else:
            contexts = [k.get("context") for k in children if isinstance(k, dict)]
            if len(contexts) != len(set(contexts)):
                errors.append(f"{where}: children have duplicate contexts")
            for n, kid in enumerate(children, start=1):
                kid_where = f"{where}/child{n}"
                errors += _instance_errors(kid, controller, position, n, kid_where, plugin_exists, child=True)
                if isinstance(kid, dict) and isinstance(kid.get("action"), dict):
                    kid_uuid = kid["action"].get("uuid")
                    if kid_uuid in PARENT_UUIDS:
                        errors.append(f"{kid_where}: multi/toggle actions can't be nested")
                    if uuid == "opendeck.multiaction" and kid["action"].get("supported_in_multi_actions") is False:
                        errors.append(f"{kid_where}: {kid_uuid} doesn't support multi-actions")
            if uuid == "opendeck.toggleaction" and isinstance(states, list) and len(states) != max(1, len(children)):
                errors.append(f"{where}: a toggle action needs one state per child")
    elif children is not None:
        errors.append(f"{where}: only multi/toggle actions can have children")
    return errors


def profile_errors(doc: Any, plugin_exists: Callable[[str], bool] | None = None,
                   expect_lengths: dict[str, int] | None = None) -> list[str]:
    if not isinstance(doc, dict):
        return ["profile is not an object"]
    errors = []
    for controller, array in ARRAY_FOR.items():
        values = doc.get(array)
        if values is None:
            if array != "infobars":
                errors.append(f"missing {array}")
            continue
        if not isinstance(values, list):
            errors.append(f"{array} must be a list")
            continue
        if expect_lengths and array in expect_lengths and len(values) != expect_lengths[array]:
            errors.append(f"{array} has {len(values)} slots, the device has {expect_lengths[array]}")
        for position, inst in enumerate(values):
            if inst is not None:
                errors += _instance_errors(inst, controller, position, 0, f"{controller}.{position}", plugin_exists)
    return errors
