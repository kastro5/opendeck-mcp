"""Applying the staged plan to OpenDeck's files, and restoring backups.

OpenDeck caches profiles in memory and overwrites external edits, so writes
only happen with it stopped: flush (make it save what plugins changed), stop
the whole process tree, replay the plan onto the fresh files, back up, write,
verify, and start it again detached.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core import profile as prof
from ..core.devices import layouts
from ..core.jsonio import atomic_write, dumps, dumps_compact, read_json
from ..core.paths import Paths
from ..core.profile import ProfileError
from ..core.validate import profile_errors
from ..staging import diff, plan
from ..staging.workspace import DELETED, Workspace
from ..tools.edit_tools import describe
from ..tools.view import build_workspace
from . import backups, process

TESTED = ("2.14.",)


def version_ok(paths: Paths) -> tuple[bool, str]:
    version = process.version(paths) or "unknown"
    return version.startswith(TESTED), version


def _touched_rels(paths: Paths, ws: Workspace) -> set[str]:
    rels = set()
    for folder in ws.dirty_settings:
        rels.add(f"settings/{folder}.json")
    for folder in set(ws.plugin_installs) | ws.plugin_removals:
        rels.add(f"plugins/{folder}")
        rels.add(f"settings/{folder}.json")
    for stale in ws.stale_dirs:
        try:
            rels.add(paths.rel(stale))
        except ValueError:
            pass
    return rels


def _clean_strays(paths: Paths, devices: set[str]) -> None:
    for device in devices:
        for stray in prof.stray_files(paths, device):
            stray.unlink(missing_ok=True)


def write(paths: Paths, ws: Workspace) -> list[Path]:
    """Turn the workspace into file changes. Returns the JSON files written (for verification)."""
    written: list[Path] = []
    for folder in sorted(ws.plugin_removals):
        shutil.rmtree(paths.plugins_dir / folder, ignore_errors=True)
    for folder, info in sorted(ws.plugin_installs.items()):
        final = paths.plugins_dir / folder
        staging_dir = paths.plugins_dir / (folder + ".mcp-new")
        shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.copytree(info["source"], staging_dir)
        if final.exists():
            shutil.rmtree(final)
        staging_dir.rename(final)
    for stale in sorted(ws.stale_dirs, key=lambda p: len(p.parts), reverse=True):
        shutil.rmtree(stale, ignore_errors=True)
    devices = set()
    for (device, profile_id) in sorted(ws.dirty_docs):
        devices.add(device)
        path = prof.profile_path(paths, device, profile_id)
        doc = ws._docs.get((device, profile_id))
        if doc is DELETED:
            path.unlink(missing_ok=True)
            if path.parent != paths.profiles_dir / device and path.parent.is_dir() and not any(
                    path.parent.iterdir()):
                path.parent.rmdir()
            continue
        atomic_write(path, dumps(doc))
        written.append(path)
    for device in sorted(ws.dirty_selected):
        path = prof.device_file(paths, device)
        atomic_write(path, dumps({"selected_profile": ws.selected(device)}))
        written.append(path)
    if ws.dirty_apps:
        atomic_write(paths.applications_file, dumps(ws.apps()))
        written.append(paths.applications_file)
    for folder in sorted(ws.dirty_settings):
        path = paths.settings_dir / f"{folder}.json"
        value = ws.plugin_settings_state(folder)
        if value is DELETED or value is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, dumps_compact(value))
            written.append(path)
    _clean_strays(paths, devices)
    return written


def _validate(ws: Workspace) -> list[str]:
    errors = []
    lengths = {}
    for device, layout in layouts(ws.paths).items():
        lengths[device] = {"keys": layout.keys, "sliders": layout.encoders, "infobars": layout.infobars}
    for key in ws.dirty_docs:
        doc = ws._docs.get(key)
        if doc is None or doc is DELETED:
            continue
        expect = lengths.get(key[0]) if any(lengths.get(key[0], {}).values()) else None
        errors += [f"{key[0]}/{key[1]}: {e}" for e in profile_errors(doc, ws.catalog.plugin_exists, expect)]
    return errors


def _verify(paths: Paths, ws: Workspace, written: list[Path]) -> list[str]:
    problems = []
    for path in written:
        try:
            read_json(path)
        except (OSError, ValueError) as exc:
            problems.append(f"{paths.rel(path)} unreadable after writing: {exc}")
    for (device, profile_id) in ws.dirty_docs:
        doc = ws._docs.get((device, profile_id))
        if doc is None or doc is DELETED:
            continue
        path = prof.profile_path(paths, device, profile_id)
        if path.read_bytes() != dumps(doc):
            problems.append(f"{paths.rel(path)} doesn't match what was written")
    return problems


def _log_size(paths: Paths) -> int:
    log = paths.log_dir / "OpenDeck.log"
    return log.stat().st_size if log.exists() else 0


def _log_errors(paths: Paths, since: int) -> list[str]:
    log = paths.log_dir / "OpenDeck.log"
    if not log.exists():
        return []
    with open(log, "rb") as handle:
        handle.seek(since if since <= log.stat().st_size else 0)
        text = handle.read().decode("utf-8", errors="replace")
    return [line for line in text.splitlines() if "[ERROR]" in line or "[WARN]" in line][-10:]


def _stop(paths: Paths) -> tuple[bool, list[str]]:
    """Flush and stop OpenDeck if it's running. Returns (was_running, notes)."""
    if not process.is_running(paths):
        return False, []
    notes = []
    selections = {}
    known = layouts(paths)
    bridge_reporting = any(layout.source == "bridge" for layout in known.values())
    for device in prof.list_devices(paths):
        # With the bridge reporting, it knows exactly which decks are connected; only those can have
        # unsaved changes (asking OpenDeck about an absent one just logs an error).
        if bridge_reporting and not (device in known and known[device].connected):
            continue
        if prof.profile_path(paths, device, prof.selected_profile(paths, device)).exists():
            selections[device] = prof.selected_profile(paths, device)
    try:
        flushed = process.flush(paths, selections)
        unsaved = [d for d, ok in flushed.items() if not ok]
        if unsaved:
            notes.append("couldn't confirm OpenDeck saved " + ", ".join(unsaved)
                         + " before stopping (fine if those devices are unplugged)")
    except Exception as exc:
        notes.append(f"flush failed ({exc}); recent plugin-driven changes may be lost")
    process.stop(paths)
    return True, notes


def apply(paths: Paths, confirm: bool, show_window: bool, on_conflict: str, allow_untested_version: bool,
          start_if_stopped: bool = False) -> str:
    with plan.locked(paths):
        records = plan.load(paths)["ops"]
        if not records:
            return "Nothing is staged."
        ok, version = version_ok(paths)
        if not ok and not allow_untested_version:
            raise ProfileError(f"OpenDeck {version} hasn't been tested with opendeck-mcp (tested: 2.14.x). "
                               "Re-run with allow_untested_version=true to apply anyway; a backup is taken first.")
        dry, warnings, conflicts = build_workspace(paths, records)
        preview = diff.summary(dry, warnings, conflicts, len(records))
        if conflicts and on_conflict != "skip":
            return preview + "\n\nNot applied: resolve the conflicts (discard those ops or restage), or pass " \
                             "on_conflict='skip' to apply the rest."
        if not confirm:
            return (preview + "\n\nNot applied yet. Show this to the user; once they agree, call apply_changes "
                              "again with confirm=true.")

        was_running, notes = _stop(paths)
        start_size = _log_size(paths)
        try:
            ws, warnings, conflicts = build_workspace(paths, records)
            skipped: list[str] = []
            if conflicts:
                if on_conflict != "skip":
                    raise ProfileError("the config changed while stopping OpenDeck: " + "; ".join(conflicts))
                bad = {c.split(" ")[0] for c in conflicts}
                skipped = [describe(r) for r in records if r["id"] in bad]
                ws, warnings, _ = build_workspace(paths, [r for r in records if r["id"] not in bad])
            errors = _validate(ws)
            if errors:
                raise ProfileError("refusing to write invalid profiles: " + "; ".join(errors[:8]))
            changes = [describe(r) for r in records if describe(r) not in skipped]
            backup = backups.create(paths, _touched_rels(paths, ws), "apply", changes, records, version)
            try:
                written = write(paths, ws)
                problems = _verify(paths, ws, written)
                if problems:
                    raise ProfileError("; ".join(problems))
            except Exception:
                backups.restore_files(paths, backup)
                raise
            plan.clear(paths)
        finally:
            started = None
            if was_running or start_if_stopped:
                started = process.start(paths, hidden=not show_window)
        lines = [f"Applied {len(changes)} change(s). Backup: {backup.id} (undo with restore_backup)."]
        lines += ["  " + change for change in changes]
        if skipped:
            lines.append("Skipped (conflicts): " + "; ".join(skipped))
        lines += [f"Note: {n}" for n in notes]
        if was_running or start_if_stopped:
            lines.append("OpenDeck restarted" + (" (hidden; open it from the tray)." if not show_window else ".")
                         if started else "⚠ OpenDeck didn't come back up; start it manually.")
            errors_after = _log_errors(paths, start_size)
            if errors_after:
                lines.append("OpenDeck log since restart:\n" + "\n".join("  " + e for e in errors_after))
        else:
            lines.append("OpenDeck wasn't running; changes take effect when it starts.")
        return "\n".join(lines)


def restore(paths: Paths, backup_id: str, confirm: bool, show_window: bool) -> str:
    with plan.locked(paths):
        backup = backups.get(paths, backup_id)
        listing = "\n".join(f"  {'restore' if e['existed'] else 'remove'} {e['rel']}" for e in backup.meta["entries"])
        if not confirm:
            return (f"Restoring {backup.summary}\nputs these back exactly as they were before that change "
                    f"(later edits to them are undone too; a backup of the current state is taken first):\n"
                    f"{listing}\n\nCall restore_backup again with confirm=true once the user agrees.")
        was_running, notes = _stop(paths)
        try:
            rels = {e["rel"] for e in backup.meta["entries"]}
            current = backups.create(paths, rels, "pre-restore", [f"state before restoring {backup.id}"],
                                     version=process.version(paths))
            backups.restore_files(paths, backup)
            _clean_strays(paths, set(prof.list_devices(paths)))
        finally:
            started = process.start(paths, hidden=not show_window) if was_running else None
        out = [f"Restored {backup.id}. The state just before this restore is saved as {current.id}."]
        out += [f"Note: {n}" for n in notes]
        if was_running:
            out.append("OpenDeck restarted." if started else "⚠ OpenDeck didn't come back up; start it manually.")
        return "\n".join(out)


def list_backups(paths: Paths, limit: int) -> str:
    items = backups.list_all(paths)[: max(1, limit)]
    if not items:
        return "No backups yet (one is made automatically on every apply)."
    return "Backups, newest first (restore_backup takes the id):\n" + "\n".join("- " + b.summary for b in items)
