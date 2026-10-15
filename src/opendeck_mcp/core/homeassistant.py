"""Read-mostly access to the user's Home Assistant, through the Home Assistant plugin's own login.

The plugin keeps `serverUrl` and a long-lived `accessToken` in its global
settings. The token is only ever put in a request header: it is never
returned, logged, or allowed into an error message.
"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .jsonio import read_json
from .paths import Paths
from .profile import ProfileError
from .secrets import redact_text

PLUGIN = "de.perdoctus.streamdeck.homeassistant.sdPlugin"
ACTIONS = ("de.perdoctus.streamdeck.homeassistant.entity", "de.perdoctus.streamdeck.homeassistant.dual-state-entity")
CACHE_SECONDS = 30.0
# Templates are rendered by HA itself; area_name() also follows an entity's device to its area.
AREAS_TEMPLATE = ("[{% for s in states %}[{{ s.entity_id|tojson }},{{ area_name(s.entity_id)|tojson }}]"
                  "{% if not loop.last %},{% endif %}{% endfor %}]")


class HAError(ProfileError):
    pass


@dataclass
class Entity:
    entity_id: str
    name: str
    state: str
    area: str | None
    attributes: dict[str, Any]

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]


def credentials(paths: Paths) -> tuple[str, str]:
    try:
        data = read_json(paths.settings_dir / f"{PLUGIN}.json")
    except (OSError, ValueError) as exc:
        raise HAError("the Home Assistant plugin isn't set up in OpenDeck (no saved server and token)") from exc
    url, token = str(data.get("serverUrl") or "").rstrip("/"), str(data.get("accessToken") or "")
    if not url or not token:
        raise HAError("the Home Assistant plugin has no server URL or access token saved; set it up in "
                      "OpenDeck first (any Home Assistant key's settings)")
    return url, token


def _pinned(url: str) -> tuple[str, dict[str, str]]:
    """For plain-http mDNS hosts, resolve IPv4 once: Windows waits seconds on the IPv6 lookup of
    *.local names. https keeps the name, since the certificate is checked against it."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "http" or not host.endswith(".local"):
        return url, {}
    try:
        ip = socket.getaddrinfo(host, parts.port or 80, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except OSError:
        return url, {}
    netloc = ip + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", "")), {"Host": parts.netloc}


class Client:
    def __init__(self, paths: Paths):
        url, token = credentials(paths)
        self.url = url
        base, extra = _pinned(url)
        verify = os.environ.get("OPENDECK_MCP_HA_VERIFY_TLS", "1") not in ("0", "false", "no")
        self._http = httpx.Client(base_url=base, timeout=15, verify=verify,
                                  headers={"Authorization": f"Bearer {token}", **extra})
        self._token = token
        self._cache: dict[str, tuple[float, Any]] = {}

    def _clean(self, text: str) -> str:
        return redact_text(text.replace(self._token, "«redacted»"))

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise HAError(f"couldn't reach Home Assistant at {self.url}: {self._clean(str(exc))}") from None
        if response.status_code == 401:
            raise HAError("Home Assistant rejected the plugin's access token (401); renew it in the "
                          "Home Assistant plugin's settings")
        if response.status_code >= 400:
            raise HAError(f"Home Assistant answered {response.status_code} for {path}: "
                          f"{self._clean(response.text[:300])}")
        return response

    def _cached(self, key: str, fetch) -> Any:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = fetch()
        self._cache[key] = (time.monotonic(), value)
        return value

    # -- reads ---------------------------------------------------------------- #

    def entities(self) -> list[Entity]:
        def fetch():
            states = self._request("GET", "/api/states").json()
            try:
                areas = dict(json.loads(self._request("POST", "/api/template",
                                                      json={"template": AREAS_TEMPLATE}).text))
            except (HAError, ValueError, TypeError):
                areas = {}  # templates can be disabled; areas are a nicety
            return [Entity(s["entity_id"], str(s.get("attributes", {}).get("friendly_name") or s["entity_id"]),
                           str(s.get("state")), areas.get(s["entity_id"]), s.get("attributes") or {})
                    for s in states]
        return self._cached("entities", fetch)

    def services(self) -> dict[str, dict[str, Any]]:
        """domain -> service -> description (name, fields, target)."""
        def fetch():
            return {d["domain"]: d.get("services") or {} for d in self._request("GET", "/api/services").json()}
        return self._cached("services", fetch)

    def entity(self, entity_id: str) -> Entity:
        for e in self.entities():
            if e.entity_id == entity_id:
                return e
        raise HAError(f"Home Assistant has no entity {entity_id!r}; find one with ha_search")

    def forget(self) -> None:
        self._cache.pop("entities", None)

    # -- writes --------------------------------------------------------------- #

    def call_service(self, service: str, data: dict[str, Any]) -> list[dict]:
        domain, _, name = service.partition(".")
        if not domain or not name:
            raise HAError("service must look like domain.service, e.g. light.toggle")
        self._cache.pop("entities", None)
        return self._request("POST", f"/api/services/{domain}/{name}", json=data).json()


_clients: dict[str, Client] = {}


def client(paths: Paths) -> Client:
    """One client per server, so the connection (and the resolved address) is reused."""
    url, token = credentials(paths)
    key = f"{url}|{hash(token)}"
    if key not in _clients:
        _clients.clear()
        _clients[key] = Client(paths)
    return _clients[key]


def search(entities: list[Entity], query: str | None, domain: str | None, area: str | None) -> list[Entity]:
    words = (query or "").lower().split()
    out = []
    for e in entities:
        if domain and e.domain != domain.lower().strip():
            continue
        if area and (e.area or "").lower() != area.lower().strip():
            continue
        hay = f"{e.entity_id} {e.name} {e.area or ''}".lower()
        if all(w in hay for w in words):
            out.append(e)
    # Things people put on keys first; long sensor lists last.
    rank = {"light": 0, "switch": 0, "scene": 0, "script": 0, "cover": 1, "fan": 1, "climate": 1,
            "media_player": 1, "lock": 1, "input_boolean": 1, "button": 1, "automation": 2}
    return sorted(out, key=lambda e: (rank.get(e.domain, 3), e.area or "~", e.name.lower()))


def key_settings(entity_id: str, service: str | None, service_data: dict | None = None,
                 long_press: str | None = None, title_template: str | None = None) -> dict[str, Any]:
    """The settings object the Home Assistant plugin's own settings page writes (version 8)."""
    empty = {"entityId": "", "serviceId": "", "serviceData": ""}

    def call(svc: str | None) -> dict[str, str]:
        if not svc:
            return dict(empty)
        return {"entityId": entity_id, "serviceId": svc,
                "serviceData": json.dumps(service_data) if service_data else ""}

    return {
        "version": 8,
        "controllerType": "Keypad",
        "display": {
            "entityId": entity_id,
            "useCustomTitle": bool(title_template),
            "buttonTitle": title_template or "{{friendly_name}}",
            "enableServiceIndicator": True,
            "iconSettings": "PREFER_PLUGIN",
            "iconLayout": "STANDARD",
            "labelFontSize": 48,
            "backgroundColor": "",
            "backgroundColorEnd": "",
            "useCustomButtonLabels": False,
            "buttonLabels": "",
            "useStateImagesForOnOffStates": False,
        },
        "button": {"serviceShortPress": call(service), "serviceLongPress": call(long_press),
                   "serviceTap": dict(empty), "serviceRotation": dict(empty)},
        "rotationTickMultiplier": 1,
        "rotationTickBucketSizeMs": 300,
    }


# The service a key for each domain usually calls.
DEFAULT_SERVICE = {
    "light": "light.toggle", "switch": "switch.toggle", "fan": "fan.toggle", "input_boolean": "input_boolean.toggle",
    "cover": "cover.toggle", "media_player": "media_player.media_play_pause", "scene": "scene.turn_on",
    "script": "script.turn_on", "automation": "automation.trigger", "button": "button.press",
    "input_button": "input_button.press", "lock": "lock.lock", "climate": "climate.toggle",
    "siren": "siren.toggle", "vacuum": "vacuum.start", "camera": None, "sensor": None, "binary_sensor": None,
}
