"""Home Assistant: find the entities and services to put on keys."""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any

from ..core import homeassistant as ha
from ..core.paths import Paths
from ..core.profile import ProfileError
from ..core.secrets import redact

# Domains whose entities are on/off, so the two-state ("custom icons") action fits them.
ON_OFF = {"light", "switch", "fan", "input_boolean", "cover", "lock", "siren", "climate", "media_player"}
SKIP_ATTRIBUTES = {"friendly_name", "entity_picture", "icon", "supported_features", "supported_color_modes"}


def _row(e: ha.Entity) -> str:
    return f"- {e.entity_id} · {e.name} · {e.state}" + (f" · {e.area}" if e.area else "")


def ha_search(paths: Paths, query: str | None, domain: str | None, area: str | None, limit: int) -> str:
    entities = ha.client(paths).entities()
    if not (query or domain or area):
        areas = Counter(e.area for e in entities if e.area)
        domains = Counter(e.domain for e in entities)
        return (f"Home Assistant has {len(entities)} entities.\n"
                f"Areas: {', '.join(f'{a} ({n})' for a, n in sorted(areas.items())) or 'none set'}\n"
                f"Domains: {', '.join(f'{d} ({n})' for d, n in domains.most_common())}\n"
                "Search by words, domain and/or area.")
    hits = ha.search(entities, query, domain, area)
    if not hits:
        return "No matching entities. Try fewer words, or list areas and domains by calling with no filters."
    limit = max(1, min(limit, 100))
    more = f"\n({len(hits) - limit} more; narrow it down)" if len(hits) > limit else ""
    return "\n".join(_row(e) for e in hits[:limit]) + more + "\nDetails and key settings: ha_describe_entity."


def ha_describe_entity(paths: Paths, entity_id: str) -> str:
    client = ha.client(paths)
    e = client.entity(entity_id.strip())
    attributes = {k: v for k, v in e.attributes.items() if k not in SKIP_ATTRIBUTES}
    lines = [f"{e.name} [{e.entity_id}]", f"state: {e.state}" + (f" · area: {e.area}" if e.area else "")]
    if attributes:
        text = json.dumps(redact(attributes), ensure_ascii=False, default=str)
        lines.append("attributes: " + (text if len(text) < 1500 else text[:1500] + "…"))
    services = client.services().get(e.domain, {})
    if services:
        lines.append(f"{e.domain} services:")
        for name, info in sorted(services.items()):
            fields = ", ".join(sorted((info.get("fields") or {}).keys()))
            lines.append(f"  {e.domain}.{name}" + (f" ({fields})" if fields else ""))
    service = ha.DEFAULT_SERVICE.get(e.domain, f"{e.domain}.turn_on" if "turn_on" in services else None)
    action = ha.ACTIONS[1] if e.domain in ON_OFF else ha.ACTIONS[0]
    settings = ha.key_settings(e.entity_id, service)
    if e.domain in ON_OFF:
        settings["display"]["useStateImagesForOnOffStates"] = True
    lines += ["", "A key for it (Home Assistant plugin):",
              f"  set_key action={action}",
              f"  settings={json.dumps(settings, ensure_ascii=False)}",
              f"Short press calls {service or 'nothing (display only)'}. Change button.serviceShortPress / "
              "serviceLongPress (serviceId, entityId, serviceData as a JSON string) for other services; "
              "describe_action explains every field."]
    return "\n".join(lines)


def ha_call_service(paths: Paths, service: str, entity_id: str | None, data: Any, confirm: bool) -> str:
    if isinstance(data, str):
        try:
            data = json.loads(data) if data.strip() else {}
        except ValueError as exc:
            raise ProfileError("data must be a JSON object") from exc
    payload = dict(data or {})
    if entity_id:
        payload["entity_id"] = entity_id
    if not confirm:
        return (f"Would call {service} with {json.dumps(redact(payload), ensure_ascii=False)} on "
                f"{ha.client(paths).url}. This acts on the real home; call again with confirm=true once "
                "the user agrees.")
    client = ha.client(paths)
    before = client.entity(entity_id).state if entity_id else None
    changed = client.call_service(service, payload)
    if entity_id and not changed:
        # Many devices (Zigbee, Wi-Fi bulbs) report their new state a moment after the call returns.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            time.sleep(0.5)
            client.forget()
            if client.entity(entity_id).state != before:
                break
        return f"Called {service}: {entity_id} {before} → {client.entity(entity_id).state}."
    states = ", ".join(f"{s.get('entity_id')} → {s.get('state')}" for s in changed[:10]) or "no state changes"
    return f"Called {service}: {states}."
