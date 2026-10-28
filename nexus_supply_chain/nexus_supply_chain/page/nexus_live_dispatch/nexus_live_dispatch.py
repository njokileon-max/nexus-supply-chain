# apps/nexus_supply_chain/nexus_supply_chain/nexus_supply_chain/page/nexus_live_dispatch/nexus_live_dispatch.py
"""
Server side of the Live Dispatch page.

  get_dispatch_board()           vehicles + their active trips (with stop
                                 progress) in ONE call (replaces the page's two
                                 frappe.client.get_list calls)
  get_manifest_map(manifest)     one trip for the map: stops with pins, status,
                                 drop-off proof, planned route
  get_eta_route(manifest, lat, lng)
                                 the office's own ETA view: road route from the
                                 truck's live position through the remaining
                                 stops, in manifest order, from YOUR routing
                                 server (FastAPI /api/v2/fleet/route -> ORS).
                                 Replaces opening Google Maps in a new tab.
  get_trip_track(manifest)       the path actually driven (GPS): live while
                                 the trip is on the road, final once it is
                                 completed (route_recompute.office_track)

Batch 10: planned distance (recalculated when a stop's pin changes) and the
distance actually driven are on the board, the trip map and the panel; the
ETA route takes up to 150 stops (FastAPI splits long routes).

Live positions are NOT here: the page receives them over FastAPI's dispatch
socket (/api/v2/fleet/ws) with a ticket from driver_api.get_dispatch_ws_ticket.

Every call requires read access to Vehicle Delivery Manifest.
"""

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt

from nexus_supply_chain.fastapi_client import fastapi_url, internal_headers
from nexus_supply_chain.route_recompute import office_track, yard_from_route

MANIFEST = "Vehicle Delivery Manifest"
STOP = "In Transit-Delivery Stop"
VEHICLE = "Vehicle In Transit"
ACTIVE_TRIP_STATES = ("Ready", "Dispatched", "Returning")
MAX_ROUTE_WAYPOINTS = 150  # FastAPI /api/v2/fleet/route limit for ERPNext (split into 25-point requests)
DISTANCE_FIELDS = (
	"original_planned_distance_km", "approximate_drive_time_min", "stops_without_location", "route_recomputed_at",
	"actual_distance_km", "distance_variance_km", "track_quality", "track_status",
)
ROUTE_TIMEOUT_SEC = 60  # long routes are several ORS requests


def _require_read():
	if not frappe.has_permission(MANIFEST, "read"):
		frappe.throw(_("You don't have access to Live Dispatch."), frappe.PermissionError)


def _point(stop):
	"""(lat, lng) from the stop's stored pin text, else its lat/lng columns."""
	text = (stop.get("combined_coordinates") or "").strip()
	if "," in text:
		try:
			lat, lng = (float(v) for v in text.split(",")[:2])
			if -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0):
				return lat, lng
		except ValueError:
			pass
	try:
		lat, lng = float(stop.get("latitude")), float(stop.get("longitude"))
	except (TypeError, ValueError):
		return None
	if -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0):
		return lat, lng
	return None


def _distance_columns(alias=""):
	"""SQL select fragment for the distance fields that exist on this site."""
	prefix = f"{alias}." if alias else ""
	cols = [f for f in DISTANCE_FIELDS if frappe.db.has_column(MANIFEST, f)]
	return "".join(f", {prefix}`{c}`" for c in cols)


def _distance_payload(row):
	final = row.get("track_status") == "Final"
	return {
		"planned_km": flt(row.get("approximate_total_distance_km")) or None,
		"original_planned_km": flt(row.get("original_planned_distance_km")) or None,
		"planned_drive_min": flt(row.get("approximate_drive_time_min")) or None,
		"stops_without_location": cint(row.get("stops_without_location")) if row.get("route_recomputed_at") else None,
		"route_recomputed_at": str(row.get("route_recomputed_at")) if row.get("route_recomputed_at") else None,
		"actual_km": flt(row.get("actual_distance_km")) if final else None,
		"variance_km": flt(row.get("distance_variance_km")) if final else None,
		"track_quality": row.get("track_quality") or None,
		"track_status": row.get("track_status") or None,
	}


def _float_or_none(value):
	try:
		f = float(value)
	except (TypeError, ValueError):
		return None
	return f if f == f else None


@frappe.whitelist()
def get_dispatch_board():
	"""
	{
	  vehicles:  [{name, plate, driver, driver_name, status}],
	  manifests: [{name, vehicle, driver, trip_status, trip_started_at,
	               stops_total, stops_pending, stops_delivered, stops_partial,
	               stops_failed, has_route}]
	}
	Only trips that are Ready / Dispatched / Returning are listed.
	"""
	_require_read()

	vehicles = frappe.db.sql(f"""
		SELECT v.name, v.vehicle_plate AS plate, v.current_driver AS driver,
		       v.current_status AS status, u.full_name AS driver_name
		FROM `tab{VEHICLE}` v
		LEFT JOIN `tabUser` u ON u.name = v.current_driver
		ORDER BY v.name
	""", as_dict=True)

	manifests = frappe.db.sql(f"""
		SELECT m.name, m.vehicle, m.driver, m.trip_status, m.trip_started_at,
		       m.approximate_total_distance_km {_distance_columns("m")},
		       (m.route_geojson IS NOT NULL AND m.route_geojson != '') AS has_route,
		       COUNT(s.name) AS stops_total,
		       SUM(CASE WHEN IFNULL(s.delivery_status, 'Pending') = 'Pending' THEN 1 ELSE 0 END) AS stops_pending,
		       SUM(CASE WHEN s.delivery_status = 'Delivered' THEN 1 ELSE 0 END) AS stops_delivered,
		       SUM(CASE WHEN s.delivery_status = 'Partially Delivered' THEN 1 ELSE 0 END) AS stops_partial,
		       SUM(CASE WHEN s.delivery_status IN ('Failed', 'Returned') THEN 1 ELSE 0 END) AS stops_failed
		FROM `tab{MANIFEST}` m
		LEFT JOIN `tab{STOP}` s ON s.parent = m.name AND s.parenttype = %(dt)s
		WHERE m.docstatus = 1 AND m.trip_status IN %(states)s
		GROUP BY m.name
		ORDER BY m.trip_started_at DESC, m.name DESC
	""", {"dt": MANIFEST, "states": ACTIVE_TRIP_STATES}, as_dict=True)

	for m in manifests:
		for key in ("stops_total", "stops_pending", "stops_delivered", "stops_partial", "stops_failed"):
			m[key] = cint(m.get(key))
		m["has_route"] = bool(cint(m.get("has_route")))
		m["trip_started_at"] = str(m.trip_started_at) if m.get("trip_started_at") else None
		m.update(_distance_payload(m))
		for key in DISTANCE_FIELDS + ("approximate_total_distance_km",):
			m.pop(key, None)

	return {"vehicles": vehicles, "manifests": manifests, "server_time": frappe.utils.now()}


@frappe.whitelist()
def get_manifest_map(manifest):
	"""One trip for the map (stops in manifest order)."""
	_require_read()
	m = frappe.db.get_value(
		MANIFEST, manifest,
		["name", "vehicle", "driver", "trip_status", "route_geojson", "docstatus", "trip_started_at",
		 "trip_completed_at", "approximate_total_distance_km"]
		+ [f for f in DISTANCE_FIELDS if frappe.db.has_column(MANIFEST, f)],
		as_dict=True,
	)
	if not m or m.docstatus == 2:
		frappe.throw(_("Trip {0} was not found.").format(manifest), frappe.DoesNotExistError)

	rows = frappe.db.sql(f"""
		SELECT name, idx, sales_order, customer, customer_id, grand_total, delivery_status,
		       combined_coordinates, latitude, longitude,
		       dropoff_latitude, dropoff_longitude, dropoff_gps_status,
		       dropoff_distance_from_target_m, is_on_site_dropoff, completion_time
		FROM `tab{STOP}`
		WHERE parent = %s AND parenttype = %s
		ORDER BY idx
	""", (m.name, MANIFEST), as_dict=True)

	stops = []
	for r in rows:
		pin = _point(r)
		stops.append({
			"name": r.name,
			"idx": r.idx,
			"sales_order": r.sales_order,
			"customer": r.customer or r.customer_id,
			"grand_total": flt(r.grand_total),
			"status": r.delivery_status or "Pending",
			"lat": pin[0] if pin else None,
			"lng": pin[1] if pin else None,
			"dropoff_lat": _float_or_none(r.dropoff_latitude),
			"dropoff_lng": _float_or_none(r.dropoff_longitude),
			"dropoff_gps_status": r.dropoff_gps_status,
			"dropoff_distance_m": _float_or_none(r.dropoff_distance_from_target_m),
			"on_site": cint(r.is_on_site_dropoff),
			"completion_time": str(r.completion_time) if r.completion_time else None,
		})

	return {
		"name": m.name,
		"vehicle": m.vehicle,
		"driver": m.driver,
		"trip_status": m.trip_status,
		"trip_started_at": str(m.trip_started_at) if m.trip_started_at else None,
		"trip_completed_at": str(m.trip_completed_at) if m.trip_completed_at else None,
		"route_geojson": m.route_geojson or None,
		"stops": stops,
		**_distance_payload(m),
	}


def _yard_from_route(route_geojson):
	"""Kept for any caller of the old name; the shared version lives in route_recompute."""
	return yard_from_route(route_geojson)


@frappe.whitelist()
def get_eta_route(manifest, latitude, longitude, include_yard=1):
	"""
	Road route from the truck (latitude, longitude = its live pin) through the
	remaining stops in manifest order, optionally back to the yard (the start
	of the planned route). The stops come from the database, not the browser.

	Returns {status:"success", polyline, distance_m, duration_s,
	         legs:[{distance_m, duration_s}], waypoints:[{kind, name, label, lat, lng}],
	         skipped_without_pin:[labels], truncated:bool, cached:bool}
	or {status:"error", code, message}.
	"""
	_require_read()
	origin_lat, origin_lng = _float_or_none(latitude), _float_or_none(longitude)
	if origin_lat is None or origin_lng is None or (origin_lat == 0 and origin_lng == 0):
		return {"status": "error", "code": "NO_POSITION", "message": _("The truck has no live position yet.")}

	data = get_manifest_map(manifest)
	pending = [s for s in data["stops"] if s["status"] == "Pending"]
	with_pin = [s for s in pending if s["lat"] is not None]
	skipped = [f"{s['idx']}. {s['customer'] or s['sales_order']}" for s in pending if s["lat"] is None]

	waypoints = [{
		"kind": "stop", "name": s["name"], "label": f"{s['idx']}. {s['customer'] or s['sales_order']}",
		"lat": s["lat"], "lng": s["lng"],
	} for s in with_pin]

	yard = _yard_from_route(data["route_geojson"]) if cint(include_yard) else None
	room = MAX_ROUTE_WAYPOINTS - (1 if yard else 0)
	truncated = len(waypoints) > room
	waypoints = waypoints[:room]
	if yard:
		waypoints.append({"kind": "yard", "name": None, "label": _("Back to the yard"), "lat": yard[0], "lng": yard[1]})

	if not waypoints:
		return {"status": "error", "code": "NOTHING_TO_ROUTE",
		        "message": _("No remaining stop has a saved location."), "skipped_without_pin": skipped}

	try:
		resp = requests.post(
			fastapi_url("/api/v2/fleet/route"),
			json={"origin": [origin_lat, origin_lng], "waypoints": [[w["lat"], w["lng"]] for w in waypoints]},
			headers=internal_headers(),
			timeout=ROUTE_TIMEOUT_SEC,
		)
		body = resp.json() if resp.content else {}
	except requests.RequestException as e:
		frappe.log_error(title="Nexus ETA route failed", message=str(e))
		return {"status": "error", "code": "ROUTING_UNREACHABLE", "message": _("The routing server can't be reached right now.")}
	except ValueError:
		body = {}

	if isinstance(body.get("detail"), dict):
		body = body["detail"]
	if resp.status_code != 200 or body.get("status") != "success" or not body.get("polyline"):
		return {
			"status": "error",
			"code": body.get("code") or f"HTTP_{resp.status_code}",
			"message": body.get("message") or _("The route couldn't be calculated."),
		}

	return {
		"status": "success",
		"polyline": body["polyline"],
		"distance_m": flt(body.get("distance_m")),
		"duration_s": flt(body.get("duration_s")),
		"legs": body.get("legs") or [],
		"waypoints": waypoints,
		"skipped_without_pin": skipped,
		"truncated": truncated,
		"cached": bool(body.get("cached")),
	}


@frappe.whitelist()
def get_trip_track(manifest):
	"""
	The path actually driven: live (estimate) while the trip is on the road,
	final once it is completed. {status, final, polyline, distance_km,
	quality, gap_minutes, points_used, [live]} or {status:"error", code, message}.
	"""
	_require_read()
	if not frappe.db.exists(MANIFEST, manifest):
		frappe.throw(_("Trip {0} was not found.").format(manifest), frappe.DoesNotExistError)
	return office_track(manifest)
