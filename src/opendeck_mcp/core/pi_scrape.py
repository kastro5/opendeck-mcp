"""Best-effort hints about an unknown action's settings, from its property inspector HTML.

Stream Deck property inspectors are web pages; many (sdpi-components, or the
classic sdpi.css forms) name their inputs after the settings keys they save.
This pulls out those names, their labels and select options. It is a hint
for the agent, not a schema.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

MAX_HINTS = 40


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fields: dict[str, dict] = {}
        self.labels: dict[str, str] = {}
        self._label_for: str | None = None
        self._label_text: list[str] = []
        self._select: str | None = None
        self.scripts: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script":
            self._in_script = True
        if tag == "label":
            self._label_for, self._label_text = a.get("for"), []
        key = a.get("setting") or a.get("data-setting") or a.get("name") or a.get("id")
        if tag in ("input", "select", "textarea") or tag.startswith("sdpi-"):
            if tag.startswith("sdpi-") and not a.get("setting"):
                return
            if not key or key in ("", "submit"):
                return
            entry = self.fields.setdefault(key, {"type": a.get("type") or tag})
            if a.get("placeholder"):
                entry["placeholder"] = a["placeholder"]
            if a.get("label"):
                entry["label"] = a["label"]
            if a.get("value") and tag == "input" and a.get("type") not in ("checkbox", "radio"):
                entry["default"] = a["value"]
            if tag in ("select", "sdpi-select", "sdpi-radio"):
                self._select = key
        if tag == "option" and self._select and "value" in a:
            self.fields[self._select].setdefault("options", []).append(a["value"])

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
        if tag in ("select", "sdpi-select", "sdpi-radio"):
            self._select = None
        if tag == "label" and self._label_for:
            self.labels[self._label_for] = " ".join("".join(self._label_text).split())
            self._label_for = None

    def handle_data(self, data):
        if self._in_script:
            self.scripts.append(data)
        elif self._label_for is not None:
            self._label_text.append(data)


_SETTINGS_KEY = re.compile(r"settings\??\.([A-Za-z_]\w*)")
_PAYLOAD_KEY = re.compile(r"setSettings[\s\S]{0,200}?\{([^{}]{0,400})\}")


def scrape(pi_path: Path) -> dict:
    """{'fields': {key: {...}}, 'script_keys': [...]} or {} if nothing usable."""
    if not pi_path.is_file():
        return {}
    try:
        html = pi_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    collector = _Collector()
    try:
        collector.feed(html)
    except Exception:
        pass
    for key, label in collector.labels.items():
        if key in collector.fields and "label" not in collector.fields[key]:
            collector.fields[key]["label"] = label
    script = "\n".join(collector.scripts)
    for js in pi_path.parent.glob("*.js"):
        if js.stat().st_size < 400_000:
            script += js.read_text(encoding="utf-8", errors="replace")
    keys = set(_SETTINGS_KEY.findall(script))
    for body in _PAYLOAD_KEY.findall(script):
        keys.update(re.findall(r"([A-Za-z_]\w*)\s*:", body))
    keys -= {"settings", "payload", "event", "context", "action"}
    fields = dict(list(collector.fields.items())[:MAX_HINTS])
    out: dict = {}
    if fields:
        out["fields"] = fields
    extra = sorted(keys - set(fields))[:MAX_HINTS]
    if extra:
        out["script_keys"] = extra
    return out
