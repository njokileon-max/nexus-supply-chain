"""
Ensures the Lead doctype has the four GeoLocation custom fields the Leads
feature relies on (check-in distance, Navigate, Correct Location):
  custom_google_maps_link, custom_latitude, custom_longitude,
  custom_combined_coordinates

Why a patch: on sites where these were added through Customize Form they
exist only in that site's database, not in the app repo. This makes the
release self-contained — one `bench migrate` guarantees them everywhere.

Behaviour:
  - Idempotent: any field that already exists on Lead is left untouched.
  - Field definitions are copied from the Customer's custom fields of the
    same name, so Lead and Customer store coordinates identically.
    Fallback definitions are used only if Customer lacks the field.
  - New fields are grouped in a collapsible "GeoLocation" section.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

GEO_FIELDS = (
	"custom_google_maps_link",
	"custom_latitude",
	"custom_longitude",
	"custom_combined_coordinates",
)

FALLBACK_DEFINITIONS = {
	"custom_google_maps_link": {"fieldtype": "Small Text", "label": "Google Maps Link"},
	"custom_latitude": {"fieldtype": "Float", "label": "Latitude", "precision": "9"},
	"custom_longitude": {"fieldtype": "Float", "label": "Longitude", "precision": "9"},
	"custom_combined_coordinates": {"fieldtype": "Data", "label": "Combined Coordinates"},
}

COPYABLE_PROPERTIES = ("fieldtype", "label", "precision", "options", "length")
SECTION_FIELDNAME = "custom_geolocation_section"


def execute():
	lead_meta = frappe.get_meta("Lead")
	if all(lead_meta.has_field(f) for f in GEO_FIELDS):
		print("[Nexus Patch] Lead GeoLocation fields already present, nothing to do.")
		return

	anchor = next((f for f in ("territory", "source", "status") if lead_meta.has_field(f)), None)

	if not lead_meta.has_field(SECTION_FIELDNAME):
		create_custom_field(
			"Lead",
			{
				"fieldname": SECTION_FIELDNAME,
				"fieldtype": "Section Break",
				"label": "GeoLocation",
				"collapsible": 1,
				"insert_after": anchor,
			},
			ignore_validate=True,
		)

	previous = SECTION_FIELDNAME
	created = []
	for fieldname in GEO_FIELDS:
		if frappe.get_meta("Lead").has_field(fieldname):
			previous = fieldname
			continue
		df = _definition_from_customer(fieldname)
		df.update({"fieldname": fieldname, "insert_after": previous})
		create_custom_field("Lead", df, ignore_validate=True)
		created.append(fieldname)
		previous = fieldname

	frappe.clear_cache(doctype="Lead")
	print(f"[Nexus Patch] Lead GeoLocation fields created: {created or 'none (already present)'}")


def _definition_from_customer(fieldname):
	df = dict(FALLBACK_DEFINITIONS[fieldname])
	source = frappe.db.get_value(
		"Custom Field",
		{"dt": "Customer", "fieldname": fieldname},
		list(COPYABLE_PROPERTIES),
		as_dict=True,
	)
	if source:
		for prop in COPYABLE_PROPERTIES:
			if source.get(prop) not in (None, "", 0):
				df[prop] = source[prop]
	return df

