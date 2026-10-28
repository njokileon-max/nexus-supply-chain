# Copyright (c) 2026, Nexus and contributors
# For license information, please see license.txt

"""
Nexus App Settings (Single).

Every server-side reader goes through `get_nexus_settings()` so the defaults
live in ONE place and a site that has never opened the settings page still
behaves correctly. The dict is cached in Redis and cleared whenever the
settings are saved.

On save, FastAPI is told (SETTINGS_UPDATED, with the new thresholds) so the
Live / On Stand-by / Offline rules change without a restart. Desk pages pick
up map changes on their next reload (boot.py).
"""

import ipaddress
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

CACHE_KEY = "nexus_app_settings_v1"

DEFAULTS = {
	"dropoff_on_site_threshold_m": 200.0,
	"completed_history_days": 7,
	"standby_threshold_seconds": 180,
	"offline_threshold_minutes": 360,
	"default_dispatch_map": "Google",
	"hosted_tile_url": "",
	"google_maps_web_api_key": "",
	"google_maps_map_id": "",
}

LOCAL_HOST_SUFFIXES = (".local", ".lan", ".test", ".internal", ".localhost")


def _is_local_host(host):
	"""localhost, private/loopback IPs and local-only domain suffixes."""
	host = (host or "").strip().lower()
	if not host:
		return False
	if host == "localhost" or host.endswith(LOCAL_HOST_SUFFIXES):
		return True
	try:
		ip = ipaddress.ip_address(host)
		return ip.is_private or ip.is_loopback or ip.is_link_local
	except ValueError:
		return False


class NexusAppSettings(Document):
	def validate(self):
		if flt(self.dropoff_on_site_threshold_m) <= 0:
			frappe.throw(_("On-Site Threshold must be greater than 0 m."))
		if cint(self.completed_history_days) < 1:
			frappe.throw(_("Completed History must be at least 1 day."))
		if cint(self.standby_threshold_seconds) < 30:
			frappe.throw(_("On Stand-by must be at least 30 seconds."))
		if cint(self.offline_threshold_minutes) * 60 <= cint(self.standby_threshold_seconds):
			frappe.throw(_("Offline must be longer than On Stand-by."))

		self.hosted_tile_url = (self.hosted_tile_url or "").strip()
		self.google_maps_web_api_key = (self.google_maps_web_api_key or "").strip()
		self.google_maps_map_id = (self.google_maps_map_id or "").strip()

		if self.default_dispatch_map == "Hosted" and not self.hosted_tile_url:
			frappe.throw(_("Set the Hosted Map Style URL before making the hosted map the default."))
		if self.hosted_tile_url:
			self._validate_hosted_url()

	def _validate_hosted_url(self):
		parsed = urlparse(self.hosted_tile_url)
		if parsed.scheme not in ("http", "https") or not parsed.hostname:
			frappe.throw(_("Hosted Map Style URL must be a full web address, e.g. https://tiles.example.com/styles/basic/style.json"))
		if parsed.scheme == "http" and not _is_local_host(parsed.hostname):
			frappe.throw(_(
				"Hosted Map Style URL must use https:// for a public address. "
				"http:// is only allowed for localhost, private network addresses and local test domains."
			))

	def on_update(self):
		frappe.cache().delete_value(CACHE_KEY)
		values = get_nexus_settings()
		frappe.enqueue(
			"nexus_supply_chain.api.execute_fastapi_webhook",
			queue="short",
			enqueue_after_commit=True,
			affected_emails=[],
			doctype="Nexus App Settings",
			docname="Nexus App Settings",
			command="SETTINGS_UPDATED",
			extra={
				"standby_threshold_seconds": cint(values["standby_threshold_seconds"]),
				"offline_threshold_minutes": cint(values["offline_threshold_minutes"]),
				"dropoff_on_site_threshold_m": flt(values["dropoff_on_site_threshold_m"]),
			},
		)


def get_nexus_settings():
	"""Returns a plain dict of the driver/dispatch settings, defaults applied."""
	cached = frappe.cache().get_value(CACHE_KEY)
	if cached:
		return cached

	values = dict(DEFAULTS)
	stored = frappe.db.get_singles_dict("Nexus App Settings") or {}
	for key, default in DEFAULTS.items():
		raw = stored.get(key)
		if raw in (None, ""):
			continue
		if isinstance(default, float):
			values[key] = flt(raw) or default
		elif isinstance(default, int):
			values[key] = cint(raw) or default
		else:
			values[key] = str(raw).strip()

	frappe.cache().set_value(CACHE_KEY, values, expires_in_sec=3600)
	return values
