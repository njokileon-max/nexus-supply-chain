# apps/nexus_supply_chain/nexus_supply_chain/patches/v1_4/driver_app_v1_1_backfill.py
"""
Driver App 1.1.0 — data back-fill for the Batch 1 data model.

Runs in [post_model_sync], so the new columns already exist. Safe to re-run:
every step only fills values that are still empty and checks that the
columns it touches exist first.

1. Stops: customer_id from the Sales Order; customer_phone from the Customer.
2. Stops: combined_coordinates / latitude / longitude re-derived with the
   shared parser (parse_combined_coords) instead of flt():
     * a stop that already has a valid numeric pin keeps it, written as text;
     * a Pending stop with no valid pin takes the customer's current pin.
   Finished stops with no pin are left empty — the pin at the time of the
   delivery is unknown and is not guessed.
3. Stops: any stored driver_notes text is copied into delivery_notes
   (only if a driver_notes column exists — the old app sent that key but the
   field never existed, so on most sites there is nothing to copy).
4. Returned items: stop_row linked to the matching stop; ordered_qty filled
   from the Sales Order where it is 0.
5. Manifests: trip_completed_at for Completed trips, from the latest stop
   completion time (or the last modified time when no stop has one).
   cumulative_additional_fuel_cost is left untouched.
6. Single settings: defaults written where empty; Sales minimum version
   copied from the legacy minimum_version. The Driver minimum version is
   deliberately left empty — set it to 1.1.0 once every phone has the APK.
"""

import frappe
from frappe.utils import cint, flt

STOP = "In Transit-Delivery Stop"
RETURN = "Manifest Returned Item"
MANIFEST = "Vehicle Delivery Manifest"
FINAL_STOP_STATES = ("Delivered", "Partially Delivered", "Failed", "Returned")
BATCH = 500


def execute():
	from nexus_supply_chain.api import parse_combined_coords

	if not frappe.db.table_exists(STOP):
		return

	frappe.reload_doc("nexus_supply_chain", "doctype", "in_transit_delivery_stop")
	frappe.reload_doc("nexus_supply_chain", "doctype", "manifest_returned_item")
	frappe.reload_doc("nexus_supply_chain", "doctype", "manifest_fuel_log")
	frappe.reload_doc("nexus_supply_chain", "doctype", "vehicle_delivery_manifest")
	frappe.reload_doc("nexus_supply_chain", "doctype", "nexus_location_correction_log")
	frappe.reload_doc("nexus_supply_chain", "doctype", "nexus_app_settings")
	frappe.reload_doc("nexus_supply_chain", "doctype", "nexus_app_version")

	_backfill_customer_id()
	_backfill_customer_phone()
	_backfill_stop_coordinates(parse_combined_coords)
	_copy_driver_notes()
	_backfill_returned_items()
	_backfill_trip_completed_at()
	_seed_settings()
	_seed_versions()


def _has(doctype, *columns):
	return all(frappe.db.has_column(doctype, c) for c in columns)


# ---------------------------------------------------------------- 1. customer

def _backfill_customer_id():
	if not _has(STOP, "customer_id", "sales_order"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabIn Transit-Delivery Stop` s
		JOIN `tabSales Order` so ON so.name = s.sales_order
		SET s.customer_id = so.customer
		WHERE IFNULL(s.customer_id, '') = ''
		  AND IFNULL(so.customer, '') != ''
		"""
	)


def _backfill_customer_phone():
	if not _has(STOP, "customer_phone", "customer_id"):
		return
	phone_cols = [c for c in ("mobile_no", "phone") if frappe.db.has_column("Customer", c)]
	if not phone_cols:
		return
	phone_expr = "COALESCE({})".format(", ".join(f"NULLIF(c.`{col}`, '')" for col in phone_cols))
	frappe.db.sql(
		f"""
		UPDATE `tabIn Transit-Delivery Stop` s
		JOIN `tabCustomer` c ON c.name = s.customer_id
		SET s.customer_phone = {phone_expr}
		WHERE IFNULL(s.customer_phone, '') = ''
		  AND {phone_expr} IS NOT NULL
		"""
	)


# ------------------------------------------------------------- 2. coordinates

def _customer_pin(customer, parse_combined_coords, cache):
	if customer in cache:
		return cache[customer]
	cols = [c for c in ("custom_combined_coordinates", "custom_latitude", "custom_longitude")
	        if frappe.db.has_column("Customer", c)]
	row = frappe.db.get_value("Customer", customer, cols, as_dict=True) if cols else None
	text = None
	if row:
		combined = (row.get("custom_combined_coordinates") or "").strip()
		point = parse_combined_coords(combined, row.get("custom_latitude"), row.get("custom_longitude"))
		if point:
			# Keep the customer's exact digits when the combined text is the valid source.
			if parse_combined_coords(combined):
				text = ",".join(p.strip() for p in combined.split(",")[:2])
			else:
				text = f"{str(row.get('custom_latitude')).strip()},{str(row.get('custom_longitude')).strip()}"
	cache[customer] = text
	return text


def _backfill_stop_coordinates(parse_combined_coords):
	if not _has(STOP, "combined_coordinates", "latitude", "longitude", "customer_id", "delivery_status"):
		return

	cache = {}
	last = ""
	while True:
		rows = frappe.db.sql(
			"""
			SELECT name, latitude, longitude, customer_id, delivery_status
			FROM `tabIn Transit-Delivery Stop`
			WHERE IFNULL(combined_coordinates, '') = '' AND name > %s
			ORDER BY name
			LIMIT %s
			""",
			(last, BATCH),
			as_dict=True,
		)
		if not rows:
			break

		for r in rows:
			last = r.name
			text = None
			point = parse_combined_coords(None, r.latitude, r.longitude)
			if point:
				text = f"{repr(float(point[0]))},{repr(float(point[1]))}"
			elif r.customer_id and (r.delivery_status or "Pending") not in FINAL_STOP_STATES:
				text = _customer_pin(r.customer_id, parse_combined_coords, cache)

			if not text:
				continue
			lat, lng = parse_combined_coords(text)
			frappe.db.set_value(
				STOP,
				r.name,
				{"combined_coordinates": text, "latitude": lat, "longitude": lng},
				update_modified=False,
			)
		frappe.db.commit()


# --------------------------------------------------------------- 3. notes

def _copy_driver_notes():
	if not _has(STOP, "driver_notes", "delivery_notes"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabIn Transit-Delivery Stop`
		SET delivery_notes = driver_notes
		WHERE IFNULL(driver_notes, '') != ''
		  AND IFNULL(delivery_notes, '') = ''
		"""
	)


# ------------------------------------------------------- 4. returned items

def _backfill_returned_items():
	if not frappe.db.table_exists(RETURN):
		return

	if _has(RETURN, "stop_row", "sales_order", "parent"):
		# Link each return to the first stop for the same Sales Order on the
		# same manifest. Where a Sales Order appears once (the normal case)
		# this is exact.
		frappe.db.sql(
			"""
			UPDATE `tabManifest Returned Item` r
			JOIN (
				SELECT parent, sales_order, MIN(name) AS stop_name
				FROM `tabIn Transit-Delivery Stop`
				WHERE parenttype = 'Vehicle Delivery Manifest'
				GROUP BY parent, sales_order
			) s ON s.parent = r.parent AND s.sales_order = r.sales_order
			SET r.stop_row = s.stop_name
			WHERE IFNULL(r.stop_row, '') = ''
			  AND r.parenttype = 'Vehicle Delivery Manifest'
			"""
		)

	if _has(RETURN, "ordered_qty", "sales_order", "item_code"):
		frappe.db.sql(
			"""
			UPDATE `tabManifest Returned Item` r
			JOIN (
				SELECT parent AS so, item_code, SUM(qty) AS qty
				FROM `tabSales Order Item`
				GROUP BY parent, item_code
			) soi ON soi.so = r.sales_order AND soi.item_code = r.item_code
			SET r.ordered_qty = soi.qty
			WHERE IFNULL(r.ordered_qty, 0) = 0
			"""
		)


# ------------------------------------------------------------ 5. manifests

def _backfill_trip_completed_at():
	if not _has(MANIFEST, "trip_completed_at", "trip_status"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabVehicle Delivery Manifest` m
		LEFT JOIN (
			SELECT parent, MAX(completion_time) AS done
			FROM `tabIn Transit-Delivery Stop`
			WHERE parenttype = 'Vehicle Delivery Manifest'
			GROUP BY parent
		) s ON s.parent = m.name
		SET m.trip_completed_at = COALESCE(s.done, m.modified)
		WHERE m.trip_status = 'Completed'
		  AND m.trip_completed_at IS NULL
		"""
	)
	# Any manifest left with a blank status (very old rows) starts as Manifested.
	frappe.db.sql(
		"""
		UPDATE `tabVehicle Delivery Manifest`
		SET trip_status = 'Manifested'
		WHERE IFNULL(trip_status, '') = '' AND docstatus < 2
		"""
	)


# ------------------------------------------------------------- 6. singles

def _seed_settings():
	from nexus_supply_chain.nexus_supply_chain.doctype.nexus_app_settings.nexus_app_settings import (
		CACHE_KEY,
		DEFAULTS,
	)

	stored = frappe.db.get_singles_dict("Nexus App Settings") or {}
	for key in (
		"dropoff_on_site_threshold_m",
		"completed_history_days",
		"standby_threshold_seconds",
		"offline_threshold_minutes",
		"default_dispatch_map",
	):
		if stored.get(key) in (None, "", 0, "0", 0.0):
			frappe.db.set_single_value("Nexus App Settings", key, DEFAULTS[key])
	frappe.cache().delete_value(CACHE_KEY)


def _seed_versions():
	stored = frappe.db.get_singles_dict("Nexus App Version") or {}
	legacy = (stored.get("minimum_version") or "").strip()
	if legacy and not (stored.get("sales_minimum_version") or "").strip():
		frappe.db.set_single_value("Nexus App Version", "sales_minimum_version", legacy)
