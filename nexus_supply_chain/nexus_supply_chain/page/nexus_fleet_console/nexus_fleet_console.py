# apps/nexus_supply_chain/nexus_supply_chain/nexus_supply_chain/page/nexus_fleet_console/nexus_fleet_console.py
"""
Nexus Fleet Console: the office's view of the moving parts.

  get_phones()                    every phone registered with FastAPI (driver
                                  app 1.1.0+), with the signed-in driver's name
  revoke_phone(device_id, ...)    a lost / replaced phone: its key stops working
                                  at once; logged in the Activity Log
  get_health()                    FastAPI /health in full (Redis, ERPNext link,
                                  routing server, sweep, fleet counts, legacy
                                  switches) + ERPNext-side checks
  get_legacy_usage(days)          calls to the old (pre-1.1.0) endpoints per day,
                                  so they are switched off only once unused

All calls go server-to-server with the internal secret; the browser never
sees it. Access: whoever may edit Nexus App Settings (System Manager by
default).
"""

import time

import frappe
import requests
from frappe import _
from frappe.utils import cint

from nexus_supply_chain.fastapi_client import fastapi_url, internal_headers, internal_secret, pushes_enabled

TIMEOUT_SEC = 12


def _require_admin():
	if not frappe.has_permission("Nexus App Settings", "write"):
		frappe.throw(_("Only users who can edit Nexus App Settings can use the Fleet Console."), frappe.PermissionError)


def _fastapi(method, path, *, params=None, json_body=None):
	"""(http_status, body_dict, error_text). http_status 0 = unreachable."""
	if not internal_secret():
		return 0, {}, _("crystal_api_internal_secret is not set in this site's site_config.json.")
	try:
		resp = requests.request(
			method, fastapi_url(path), params=params, json=json_body,
			headers=internal_headers(), timeout=TIMEOUT_SEC,
		)
	except requests.RequestException as e:
		return 0, {}, str(e)[:300]
	try:
		body = resp.json()
	except ValueError:
		body = {}
	if isinstance(body.get("detail"), dict):
		body = body["detail"]
	error = None if resp.ok else (body.get("message") or body.get("detail") or f"HTTP {resp.status_code}")
	return resp.status_code, body, error


@frappe.whitelist()
def get_phones():
	_require_admin()
	status, body, error = _fastapi("GET", "/api/v2/telemetry/devices")
	if error:
		return {"status": "error", "message": error, "http": status}

	devices = body.get("devices") or []
	emails = {(d.get("user") or "").lower() for d in devices} | {(d.get("registered_by") or "").lower() for d in devices}
	emails.discard("")
	names = {}
	if emails:
		for u in frappe.get_all("User", filters={"name": ["in", list(emails)]}, fields=["name", "full_name"]):
			names[u.name.lower()] = u.full_name

	def ts(value):
		try:
			return float(value) if value not in (None, "") else None
		except (TypeError, ValueError):
			return None

	rows = []
	for d in devices:
		user = (d.get("user") or "").lower()
		reg_by = (d.get("registered_by") or "").lower()
		rows.append({
			"device_id": d.get("device_id"),
			"model": d.get("model") or "",
			"platform": d.get("platform") or "",
			"app_version": d.get("app_version") or "",
			"user": user,
			"user_name": names.get(user) or "",
			"registered_by": reg_by,
			"registered_by_name": names.get(reg_by) or "",
			"registered_at": ts(d.get("registered_at")),
			"bound_at": ts(d.get("bound_at")),
			"last_vehicle": d.get("last_vehicle") or "",
			"revoked": str(d.get("revoked") or "0") == "1",
		})
	return {"status": "success", "phones": rows, "fetched_at": time.time()}


@frappe.whitelist(methods=["POST"])
def revoke_phone(device_id, reason=None, remove_pin=0):
	"""Stops a phone's key at once (lost, stolen, replaced). The driver can sign
	in again on that phone later, which issues a fresh key."""
	_require_admin()
	device_id = (device_id or "").strip()
	reason = (reason or "").strip()
	if not device_id:
		frappe.throw(_("Choose the phone to revoke."))
	if len(reason) < 3:
		frappe.throw(_("Write why the phone is being revoked (for the audit log)."))
	if not pushes_enabled():
		# A local/staging copy pointed at the live FastAPI must not switch off
		# a real driver's phone.
		frappe.throw(_("This site has pushes to FastAPI switched off (nexus_fastapi_push_enabled = 0), "
			"so it can't revoke phones. Revoke from the live ERPNext."))

	status, body, error = _fastapi(
		"POST", "/api/v2/telemetry/device/revoke",
		json_body={"device_id": device_id, "remove": bool(cint(remove_pin))},
	)
	if error:
		return {"status": "error", "message": error, "http": status}

	frappe.get_doc({
		"doctype": "Activity Log",
		"subject": _("Nexus phone {0} revoked: {1}").format(device_id, reason)[:140],
		"content": _("Revoked by {0}. Reason: {1}. Pin removed: {2}.").format(
			frappe.session.user, frappe.utils.escape_html(reason), _("yes") if cint(remove_pin) else _("no")),
		"status": "Success",
		"user": frappe.session.user,
		"full_name": frappe.utils.get_fullname(frappe.session.user),
	}).insert(ignore_permissions=True)
	return {"status": "success", "device_id": device_id}


@frappe.whitelist()
def get_health():
	_require_admin()
	started = time.perf_counter()
	status, body, error = _fastapi("GET", "/health")
	round_trip_ms = round((time.perf_counter() - started) * 1000)

	from frappe.utils.scheduler import is_scheduler_disabled

	erpnext = {
		"fastapi_url": fastapi_url(),
		"secret_configured": bool(internal_secret()),
		"pushes_enabled": pushes_enabled(),
		"scheduler_enabled": not is_scheduler_disabled(),
		"round_trip_ms": round_trip_ms,
	}
	if error:
		return {"status": "error", "message": error, "http": status, "erpnext": erpnext}
	if "redis" not in body:
		return {
			"status": "error",
			"message": _("FastAPI answered without details: the internal secret here doesn't match FastAPI's INTERNAL_API_SECRET."),
			"erpnext": erpnext,
			"fastapi": body,
		}
	return {"status": "success", "erpnext": erpnext, "fastapi": body}


@frappe.whitelist()
def get_legacy_usage(days=14):
	_require_admin()
	days = max(1, min(35, cint(days) or 14))
	status, body, error = _fastapi("GET", "/api/v2/ops/legacy-usage", params={"days": days})
	if error:
		hint = _("Deploy the Batch 9 FastAPI files to see legacy traffic.") if status == 404 else None
		return {"status": "error", "message": error, "hint": hint, "http": status}
	return body
