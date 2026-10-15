"""render_profile: a picture of a deck, with or without the staged changes."""

from __future__ import annotations

from ..core import preview
from ..core.paths import Paths
from ..core.profile import ProfileError
from ..staging.workspace import Workspace
from .view import View


def render_profile(paths: Paths, device: str, profile: str | None, staged: bool, compare: bool) -> tuple[bytes, str]:
    view = View(paths)
    layout = view.ws.layout(device)
    ws = view.ws if staged or compare else Workspace(paths)
    profile_id = profile or ws.selected(layout.id)
    if not ws.has_profile(layout.id, profile_id):
        raise ProfileError(f"profile {profile_id!r} doesn't exist on {layout.id}; "
                           f"existing: {', '.join(ws.profiles(layout.id))}")
    changed = {label.split("/")[0] for label in view.changed_slots(layout.id, profile_id)} if staged or compare \
        else set()
    after = preview.render(paths, layout, profile_id, ws.doc(layout.id, profile_id), changed,
                           heading=f"{layout.name} · {profile_id}" + (" · after apply" if compare else ""))
    image = after
    if compare:
        disk = Workspace(paths)
        if disk.has_profile(layout.id, profile_id):
            before = preview.render(paths, layout, profile_id, disk.doc(layout.id, profile_id), changed,
                                    heading=f"{layout.name} · {profile_id} · now")
            image = preview.side_by_side(before, after)
        else:
            compare = False
    notes = [f"{layout.name} [{layout.id}] · profile {profile_id!r}"
             + (" (with staged changes)" if staged and changed else "")]
    if changed:
        notes.append(f"Outlined in orange: {', '.join(sorted(changed))} (staged, not applied yet).")
    if compare:
        notes.append("Left: as it is now. Right: after apply_changes.")
    notes.append("Images that plugins draw while running (clocks, live states) show as last saved.")
    return preview.png(image), "\n".join(notes)
