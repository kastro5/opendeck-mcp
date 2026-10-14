"""Finding, installing, updating and removing plugins."""

from __future__ import annotations

from ..core.devices import BRIDGE_ID
from ..core.paths import Paths
from ..core.profile import ProfileError
from ..plugins import catalogue, installer
from .edit_tools import stage
from .view import View

BUILTIN = {"com.amansprojects.starterpack.sdPlugin"}


def find_plugins(paths: Paths, query: str | None, source: str, limit: int) -> str:
    view = View(paths)
    installed = {folder.removesuffix(".sdPlugin"): info for folder, info in view.catalog.plugins.items()}
    if source == "installed":
        rows = [f"- {info.name} {info.version} [{folder}]" + (" (built in)" if folder in BUILTIN else "")
                for folder, info in sorted(view.catalog.plugins.items()) if folder != BRIDGE_ID
                and (not query or query.lower() in f"{folder} {info.name}".lower())]
        return "\n".join(rows) or "No matching installed plugins."
    listings = catalogue.archive(paths) if source == "archive" else catalogue.openaction(paths)
    hits = catalogue.search(listings, query)
    if not hits:
        return f"No {source} plugins match {query!r}."
    rows = []
    for item in hits[: max(1, min(limit, 50))]:
        mark = f" · installed {installed[item.id].version}" if item.id in installed else ""
        flag = " · ⚠ AI-assisted" if item.ai_assisted else ""
        version = f" {item.version}" if item.version else ""
        desc = f" — {item.description}" if item.description else ""
        rows.append(f"- {item.name}{version} by {item.author} [{item.id}]{mark}{flag}{desc}"
                    + (f"\n    {item.repository}" if item.repository else ""))
    more = f"\n({len(hits) - limit} more; refine the query)" if len(hits) > limit else ""
    note = ("\nArchive plugins are Elgato Stream Deck plugins; most are Windows/macOS-only."
            if source == "archive" else "")
    return "\n".join(rows) + more + note + "\nInstall with stage_plugin (confirm=false first to inspect)."


def _listing(paths: Paths, plugin_id: str, source: str) -> catalogue.Listing:
    listings = catalogue.archive(paths) if source == "archive" else catalogue.openaction(paths)
    bare = plugin_id.removesuffix(".sdPlugin")
    for item in listings:
        if item.id == bare:
            return item
    raise ProfileError(f"{plugin_id!r} isn't in the {source}; search with find_plugins")


def stage_plugin(paths: Paths, operation: str, plugin_id: str, source: str, url: str | None,
                 confirm: bool) -> str:
    folder_id = plugin_id if plugin_id.endswith(".sdPlugin") else plugin_id + ".sdPlugin"
    if folder_id in BUILTIN or folder_id == BRIDGE_ID:
        raise ProfileError(f"{folder_id} is managed by OpenDeck / opendeck-mcp and can't be changed here")
    view = View(paths)
    if operation == "remove":
        if folder_id not in view.catalog.plugins:
            raise ProfileError(f"{folder_id} isn't installed")
        if not confirm:
            return (f"Removing {view.catalog.plugins[folder_id].name} deletes the plugin, its global settings, "
                    "and every key that uses it. Call again with confirm=true once the user agrees.")
        return stage(paths, "remove_plugin", {"folder": folder_id})

    if source == "url":
        if not url:
            raise ProfileError("source='url' needs url")
        listing = catalogue.Listing(plugin_id.removesuffix(".sdPlugin"), plugin_id, "?", "", None, None, url,
                                    False, "url")
    else:
        listing = _listing(paths, plugin_id, source)
    if operation == "update" and folder_id not in view.catalog.plugins:
        raise ProfileError(f"{folder_id} isn't installed; use operation='install'")
    if operation == "install" and folder_id in view.catalog.plugins:
        raise ProfileError(f"{folder_id} is already installed; use operation='update'")
    download_url, version = catalogue.resolve_download(listing)
    archive_path = catalogue.download(paths, download_url)
    unpacked = installer.unpack(paths, archive_path, fallback_id=listing.id)
    manifest = unpacked.manifest
    supported, platforms = installer.os_support(manifest)
    actions = [str(a.get("Name") or a.get("UUID")) for a in manifest.get("Actions") or []]
    report = [
        f"{manifest.get('Name', listing.name)} {manifest.get('Version', version or '?')} "
        f"by {manifest.get('Author', listing.author)} [{unpacked.folder}]",
        f"source: {listing.source} · {download_url}",
        f"repository: {listing.repository or 'unknown'}",
        f"sha256: {unpacked.sha256} · {unpacked.size / 1024:.0f} KiB",
        f"runs as: {installer.runtime_of(manifest)}",
        f"platforms: {platforms}",
        f"actions ({len(actions)}): {', '.join(actions[:20])}" + (" …" if len(actions) > 20 else ""),
    ]
    if listing.ai_assisted:
        report.append("⚠ The catalogue marks this plugin as AI-assisted (largely AI-written).")
    if unpacked.folder != folder_id:
        report.append(f"note: the archive's plugin folder is {unpacked.folder}")
    if not supported:
        report.append("✗ This plugin doesn't support this operating system; OpenDeck won't run it.")
        return "\n".join(report)
    if manifest.get("DeviceNamespace"):
        report.append(f"note: it's a device driver (namespace {manifest['DeviceNamespace']!r}).")
    if not confirm:
        report.append("\nThis installs third-party code that runs on the user's computer. Show this to the user; "
                      "if they agree, call stage_plugin again with confirm=true.")
        return "\n".join(report)
    staged = stage(paths, "install_plugin", {"folder": unpacked.folder, "source": str(unpacked.path),
                                             "version": str(manifest.get("Version", version or "")),
                                             "sha256": unpacked.sha256, "url": download_url,
                                             "ai_assisted": listing.ai_assisted})
    return "\n".join(report) + "\n\n" + staged + "\nIts actions can be placed now (list_actions shows them)."
