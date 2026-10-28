# apps/nexus_supply_chain/nexus_supply_chain/patches/v1_5/trip_distance_tracking.py
"""
Planned vs actual distance (Batch 10).

  * loads the new Vehicle Delivery Manifest / stop fields
  * original_planned_distance_km = the current planned distance on every
    manifest that has one (the audit baseline before any recalculation)
  * trips completed BEFORE this release are marked "Not Tracked": FastAPI
    only started keeping GPS tracks now, so there is nothing to calculate
  * trips on the road right now are left empty: their distance is
    calculated from the points stored from now on when they complete
    (it will show gap time for the part driven before the upgrade)
Safe to run more than once.
"""

import frappe

MANIFEST = "Vehicle Delivery Manifest"


def execute():
	frappe.reload_doc("nexus_supply_chain", "doctype", "in_transit_delivery_stop")
	frappe.reload_doc("nexus_supply_chain", "doctype", "vehicle_delivery_manifest")

	if not frappe.db.has_column(MANIFEST, "original_planned_distance_km"):
		return

	frappe.db.sql(f"""
		UPDATE `tab{MANIFEST}`
		SET original_planned_distance_km = approximate_total_distance_km
		WHERE IFNULL(original_planned_distance_km, 0) = 0
		  AND IFNULL(approximate_total_distance_km, 0) > 0
	""")
	frappe.db.sql(f"""
		UPDATE `tab{MANIFEST}`
		SET track_status = 'Not Tracked'
		WHERE trip_status IN ('Completed', 'Cancelled')
		  AND IFNULL(track_status, '') = ''
	""")
	frappe.db.sql(f"""
		UPDATE `tab{MANIFEST}`
		SET route_recompute_pending = 0
		WHERE route_recompute_pending IS NULL
	""")
	frappe.db.commit()
