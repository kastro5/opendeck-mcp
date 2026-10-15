import json

import httpx
import pytest

from opendeck_mcp.core import homeassistant as ha
from opendeck_mcp.core.jsonio import read_json
from opendeck_mcp.tools import ha_tools as H

STATES = [
    {"entity_id": "light.abajur", "state": "on", "attributes": {"friendly_name": "Abajur", "brightness": 129}},
    {"entity_id": "scene.baixo", "state": "2026-10-15", "attributes": {"friendly_name": "Baixo"}},
    {"entity_id": "sensor.temp", "state": "21", "attributes": {"friendly_name": "Temperature"}},
    {"entity_id": "camera.printer", "state": "streaming",
     "attributes": {"friendly_name": "Printer", "access_token": "a" * 40}},
]
SERVICES = [{"domain": "light", "services": {"toggle": {"fields": {"brightness_pct": {}}}, "turn_on": {}}}]
AREAS = [["light.abajur", "Quarto"], ["scene.baixo", None], ["sensor.temp", "Sala"], ["camera.printer", None]]


@pytest.fixture
def home(paths, monkeypatch):
    """A fake Home Assistant: records requests, answers like the real REST API."""
    calls = []
    token = read_json(paths.settings_dir / f"{ha.PLUGIN}.json")["accessToken"]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.headers.get("authorization") != f"Bearer {token}":
            return httpx.Response(401)
        path = request.url.path
        if path == "/api/states":
            return httpx.Response(200, json=STATES)
        if path == "/api/services":
            return httpx.Response(200, json=SERVICES)
        if path == "/api/template":
            return httpx.Response(200, text=json.dumps(AREAS))
        if path.startswith("/api/services/"):
            return httpx.Response(200, json=[{"entity_id": "light.abajur", "state": "off"}])
        return httpx.Response(404, text=f"no {path} for token {token}")

    real = httpx.Client
    monkeypatch.setattr(ha.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(ha, "_pinned", lambda url: (url, {}))
    ha._clients.clear()
    return {"calls": calls, "token": token}


def test_search_filters_and_ranks_key_friendly_domains_first(paths, home):
    out = H.ha_search(paths, None, None, None, 10)
    assert "4 entities" in out and "Quarto (1)" in out
    rows = H.ha_search(paths, "a", None, None, 10).splitlines()
    assert rows[0].startswith("- light.abajur")  # lights before sensors
    assert H.ha_search(paths, None, None, "sala", 10).startswith("- sensor.temp")


def test_describe_gives_ready_made_key_settings_and_hides_secrets(paths, home):
    out = H.ha_describe_entity(paths, "light.abajur")
    settings = json.loads(out.split("settings=", 1)[1].splitlines()[0])
    assert settings["button"]["serviceShortPress"] == {"entityId": "light.abajur", "serviceId": "light.toggle",
                                                       "serviceData": ""}
    assert settings["display"]["useStateImagesForOnOffStates"] is True
    assert "dual-state-entity" in out and "light.toggle (brightness_pct)" in out
    camera = H.ha_describe_entity(paths, "camera.printer")
    assert "a" * 40 not in camera and "«redacted»" in camera
    assert "display only" in camera


def test_call_service_needs_confirm(paths, home):
    out = H.ha_call_service(paths, "light.toggle", "light.abajur", None, False)
    assert out.startswith("Would call") and not any("/api/services/" in str(c.url) for c in home["calls"])
    out = H.ha_call_service(paths, "light.toggle", "light.abajur", {"transition": 1}, True)
    assert out == "Called light.toggle: light.abajur → off."
    sent = [c for c in home["calls"] if c.url.path == "/api/services/light/toggle"]
    assert json.loads(sent[0].content) == {"transition": 1, "entity_id": "light.abajur"}


def test_the_token_never_leaks_into_errors(paths, home):
    client = ha.client(paths)
    with pytest.raises(ha.HAError) as exc:
        client._request("GET", "/api/nope")
    assert home["token"] not in str(exc.value)


def test_missing_login_is_explained(paths):
    (paths.settings_dir / f"{ha.PLUGIN}.json").write_text('{"serverUrl": ""}', encoding="utf-8")
    with pytest.raises(ha.HAError, match="no server URL or access token"):
        ha.credentials(paths)


def test_mdns_http_hosts_are_pinned_to_ipv4(monkeypatch):
    monkeypatch.setattr(ha.socket, "getaddrinfo", lambda *a: [(0, 0, 0, "", ("192.168.1.5", 8123))])
    assert ha._pinned("http://homeassistant.local:8123") == ("http://192.168.1.5:8123",
                                                             {"Host": "homeassistant.local:8123"})
    assert ha._pinned("https://homeassistant.local:8123") == ("https://homeassistant.local:8123", {})
