import frappe
from frappe.utils import flt

from nexus_supply_chain.api import NEXUS_ON_SITE_THRESHOLD_METERS, parse_combined_coords

CHUNK_SIZE = 1000


def execute():
	table = "tabNexus Sales Visit"

	# 1. visit_type for historical rows
	frappe.db.sql(f"""
		UPDATE `{table}`
		SET visit_type = 'Customer'
		WHERE IFNULL(visit_type, '') = ''
	""")

	# 2. party_name for historical customer rows
	frappe.db.sql(f"""
		UPDATE `{table}` v
		JOIN `tabCustomer` c ON c.name = v.customer
		SET v.party_name = c.customer_name
		WHERE v.visit_type = 'Customer' AND IFNULL(v.party_name, '') = ''
	""")

	# 3. is_on_site for historical rows (no target_coordinates recorded)
	rows = frappe.db.sql(f"""
		SELECT v.name, v.distance_from_target_meters,
		       c.custom_combined_coordinates, c.custom_latitude, c.custom_longitude
		FROM `{table}` v
		LEFT JOIN `tabCustomer` c ON c.name = v.customer
		WHERE v.visit_type = 'Customer'
		AND IFNULL(v.target_coordinates, '') = ''
	""", as_dict=True)

	on_site, off_site = [], []
	for r in rows:
		has_target = parse_combined_coords(
			r.custom_combined_coordinates, r.custom_latitude, r.custom_longitude
		) is not None
		dist = r.distance_from_target_meters
		if has_target and dist is not None and flt(dist) <= NEXUS_ON_SITE_THRESHOLD_METERS:
			on_site.append(r.name)
		else:
			off_site.append(r.name)

	_set_flag(table, on_site, 1)
	_set_flag(table, off_site, 0)

	print(
		f"[Nexus Patch] Nexus Sales Visit backfill: {len(rows)} historical rows, "
		f"{len(on_site)} on-site, {len(off_site)} off-site."
	)


def _set_flag(table, names, value):
	for i in range(0, len(names), CHUNK_SIZE):
		chunk = tuple(names[i:i + CHUNK_SIZE])
		frappe.db.sql(
			f"UPDATE `{table}` SET is_on_site = %s WHERE name IN %s",
			(value, chunk),
		)

