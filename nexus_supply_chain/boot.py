# apps/nexus_supply_chain/nexus_supply_chain/boot.py
"""
Desk boot data for the dispatch maps (Live Dispatch, Sales Dispatch and the
manifest form). Read from Nexus App Settings once per desk load, so a change
there reaches every page on the next reload without a code deploy.

Only desk (System User) accounts that can read manifests or sales visits get
it. The web key is referrer-restricted in Google Cloud, so it only works
from the ERPNext addresses listed on the key.

asset_version: a stamp computed from the map/dispatch script files on disk.
Production nginx caches /assets for a year; the pages load those scripts as
"...js?v=<asset_version>", so every deploy reaches every browser at once
without anyone clearing a cache.
"""

import os

import frappe
from frappe.utils import cint, flt

_ASSET_FILES = (
	"js/nexus_map_adapter.js",
	"js/nexus_gmaps_loader.js",
	"js/nexus_dispatch_socket.js",
)


def _asset_version():
	"""Latest modification time of the shared scripts, as a short hex stamp."""
	public = frappe.get_app_path("nexus_supply_chain", "public")
	latest = 0
	for rel in _ASSET_FILES:
		try:
			latest = max(latest, int(os.stat(os.path.join(public, rel)).st_mtime))
		except OSError:
			continue
	return format(latest, "x") if latest else "1"


def boot_session(bootinfo):
	try:
		user = frappe.session.user
		if user == "Guest":
			return
		if frappe.get_cached_value("User", user, "user_type") != "System User":
			return
		if not (frappe.has_permission("Vehicle Delivery Manifest", "read")
		        or frappe.has_permission("Nexus Sales Visit", "read")):
			return

		from nexus_supply_chain.nexus_supply_chain.doctype.nexus_app_settings.nexus_app_settings import (
			get_nexus_settings,
		)

		s = get_nexus_settings()
		bootinfo.nexus_maps = {
			"default_engine": "hosted" if s.get("default_dispatch_map") == "Hosted" else "google",
			"hosted_tile_url": s.get("hosted_tile_url") or "",
			"google_maps_web_api_key": s.get("google_maps_web_api_key") or "",
			"google_maps_map_id": s.get("google_maps_map_id") or "",
			"standby_threshold_seconds": cint(s.get("standby_threshold_seconds")),
			"offline_threshold_minutes": cint(s.get("offline_threshold_minutes")),
			"dropoff_on_site_threshold_m": flt(s.get("dropoff_on_site_threshold_m")),
			"asset_version": _asset_version(),
		}
	except Exception:
		# The desk must always load, even if this fails.
		frappe.log_error(title="Nexus Boot Session Failed", message=frappe.get_traceback())
