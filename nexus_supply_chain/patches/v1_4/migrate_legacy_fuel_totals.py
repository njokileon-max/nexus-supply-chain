# apps/nexus_supply_chain/nexus_supply_chain/patches/v1_4/migrate_legacy_fuel_totals.py
"""
Driver App 1.1.0 — fuel totals move to Fuel Log rows.

From 1.1.0 the trip's fuel total is always the SUM of its Fuel Log rows
(driver_api.recalculate_manifest_totals). Fuel logged before 1.1.0 was only
added to cumulative_additional_fuel_cost, with no rows. So that history isn't
lost on the next recalculation, each such manifest gets ONE row carrying its
old total, clearly labelled.

Safe to re-run: only manifests with a total and no Fuel Log rows are touched,
and each row has a fixed request ID.
"""

import frappe
from frappe.utils import flt

MANIFEST = "Vehicle Delivery Manifest"
FUEL = "Manifest Fuel Log"


def execute():
	if not frappe.db.table_exists(FUEL) or not frappe.db.has_column(MANIFEST, "cumulative_additional_fuel_cost"):
		return

	rows = frappe.db.sql("""
		SELECT m.name, m.cumulative_additional_fuel_cost AS total, m.modified, m.driver, m.docstatus
		FROM `tabVehicle Delivery Manifest` m
		WHERE IFNULL(m.cumulative_additional_fuel_cost, 0) > 0
		  AND m.docstatus < 2
		  AND NOT EXISTS (
		      SELECT 1 FROM `tabManifest Fuel Log` f
		      WHERE f.parent = m.name AND f.parenttype = %s
		  )
	""", (MANIFEST,), as_dict=True)

	for r in rows:
		frappe.get_doc({
			"doctype": FUEL,
			"parent": r.name,
			"parenttype": MANIFEST,
			"parentfield": "fuel_logs",
			"idx": 1,
			"docstatus": r.docstatus,
			"fuelled_at": r.modified,
			"amount": flt(r.total),
			"notes": "Total fuel logged before driver app 1.1.0 (kept as one entry).",
			"recorded_by": r.driver or "Administrator",
			"client_request_id": f"legacy-fuel-{r.name}",
		}).db_insert()

	frappe.db.commit()
