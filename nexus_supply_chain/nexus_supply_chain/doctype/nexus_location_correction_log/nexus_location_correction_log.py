# Copyright (c) 2026, Nexus and contributors
# For license information, please see license.txt

"""
Nexus Location Correction Log — append-only pin history for Customer and Lead.

Rows are created by the server only (update_customer_coordinates,
update_lead_coordinates and the driver stop-location endpoint in Batch 2),
always through `record_location_correction()` below so every caller fills
the row the same way. Users cannot create, edit or delete rows from the desk:

* `in_create` hides the New button;
* no role has write/create/delete permission;
* `validate` refuses any update after insert, and `on_trash` refuses deletes
  unless the site is migrating or an Administrator is cleaning up.

Coordinates are stored as exact "lat,lng" text (same rule as Customer/Lead);
the moved distance is always computed here, never taken from the client.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime

PARTY_TYPES = ("Customer", "Lead")
ROLES = ("Sales", "Driver", "Office")
METHODS = ("GPS Snap", "Maps Link")


def _helpers():
	# Imported lazily: api.py is large and imports this module's doctype
	# indirectly through frappe.get_doc, so a top-level import could cycle.
	from nexus_supply_chain.api import haversine_meters, parse_combined_coords

	return parse_combined_coords, haversine_meters


def _normalise(value):
	"""Returns the exact "lat,lng" text if valid, else ""."""
	parse_combined_coords, _h = _helpers()
	if not value:
		return ""
	text = ",".join(p.strip() for p in str(value).split(",")[:2])
	return text if parse_combined_coords(text) else ""


class NexusLocationCorrectionLog(Document):
	def validate(self):
		if not self.is_new() and not _maintenance_allowed():
			frappe.throw(_("Location correction history cannot be changed."), frappe.PermissionError)

		if self.party_type not in PARTY_TYPES:
			frappe.throw(_("Party Type must be Customer or Lead."))
		if self.changed_by_role not in ROLES:
			frappe.throw(_("Changed By Role must be Sales, Driver or Office."))
		if self.method not in METHODS:
			frappe.throw(_("Method must be GPS Snap or Maps Link."))

		self.new_coordinates = _normalise(self.new_coordinates)
		if not self.new_coordinates:
			frappe.throw(_("New Coordinates must be a valid \"lat,lng\" pair."))
		self.old_coordinates = _normalise(self.old_coordinates)

		self.changed_at = self.changed_at or now_datetime()
		self.changed_by = self.changed_by or frappe.session.user
		self.is_mock_location = cint(self.is_mock_location)
		if self.gps_accuracy_m not in (None, ""):
			self.gps_accuracy_m = max(flt(self.gps_accuracy_m), 0.0)

		if not self.party_name:
			name_field = "customer_name" if self.party_type == "Customer" else "lead_name"
			self.party_name = frappe.db.get_value(self.party_type, self.party, name_field) or self.party

		self.moved_distance_m = None
		if self.old_coordinates:
			parse_combined_coords, haversine_meters = _helpers()
			old = parse_combined_coords(self.old_coordinates)
			new = parse_combined_coords(self.new_coordinates)
			self.moved_distance_m = round(haversine_meters(old[0], old[1], new[0], new[1]), 2)

	def on_trash(self):
		if not _maintenance_allowed():
			frappe.throw(_("Location correction history cannot be deleted."), frappe.PermissionError)


def _maintenance_allowed():
	return bool(
		frappe.flags.in_patch
		or frappe.flags.in_migrate
		or frappe.flags.in_install
		or frappe.session.user == "Administrator"
	)


def record_location_correction(
	party_type,
	party,
	new_coordinates,
	old_coordinates=None,
	method="GPS Snap",
	changed_by_role="Sales",
	gps_accuracy_m=None,
	is_mock_location=0,
	google_maps_link=None,
	sales_visit=None,
	manifest=None,
	stop_row=None,
	sales_order=None,
	device_id=None,
	app_version=None,
	client_request_id=None,
):
	"""
	The single way to write a correction row. Idempotent on
	client_request_id: a retry returns the existing row instead of a duplicate.
	Callers own the transaction (no commit here). Returns the log name.
	"""
	if client_request_id:
		existing = frappe.db.get_value(
			"Nexus Location Correction Log", {"client_request_id": client_request_id}, "name"
		)
		if existing:
			return existing

	doc = frappe.get_doc(
		{
			"doctype": "Nexus Location Correction Log",
			"party_type": party_type,
			"party": party,
			"old_coordinates": old_coordinates,
			"new_coordinates": new_coordinates,
			"method": method if method in METHODS else "GPS Snap",
			"changed_by_role": changed_by_role,
			"changed_by": frappe.session.user,
			"changed_at": now_datetime(),
			"gps_accuracy_m": gps_accuracy_m,
			"is_mock_location": cint(is_mock_location),
			"google_maps_link": google_maps_link,
			"sales_visit": sales_visit,
			"manifest": manifest,
			"stop_row": stop_row,
			"sales_order": sales_order,
			"device_id": device_id,
			"app_version": app_version,
			"client_request_id": client_request_id or None,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name
