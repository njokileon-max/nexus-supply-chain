# apps/nexus_supply_chain/nexus_supply_chain/route_recompute.py
"""
PLANNED distance (recalculated when a pin changes) and ACTUAL distance
(from the vehicle's GPS track) for Vehicle Delivery Manifests.

PLANNED — recalculated when a customer's pin changes
  Every pin change (Driver app, Sales app, desk, geocoder) goes through
  driver_api.refresh_customer_stop_pins, which calls request_recompute()
  for the open trips whose stops moved. That:
    1. ticks route_recompute_pending on the trip (same transaction as the
       pin change, so a rolled-back change recalculates nothing), and
    2. queues ONE job per trip after the commit (job id per trip; repeated
       requests while it waits are merged into it).
  The job routes yard -> every stop with a pin, in the trip's EXISTING
  order -> yard on your ORS server (FastAPI /api/v2/fleet/route, internal
  secret; long trips are split there). Stops are never re-ordered: the
  order is the plan the driver follows. It then writes
    approximate_total_distance_km   the new figure
    original_planned_distance_km    the figure before the first recalculation
                                    (kept once, for audit)
    approximate_drive_time_min, stops_without_location, route_recomputed_at
    route_geojson                   the new planned line (app + Live Dispatch)
    approximate_fuel_consumption_ltrs / approximate_fuel_cost
                                    scaled with the distance (same litres per
                                    km and price per litre as the plan)
  Profit stays as it is (plan profit minus logged fuel, recalculate_manifest_totals).
  If ORS is down the tick stays on and the 5-minute job retries.

ACTUAL — the distance driven and the path taken
  FastAPI keeps every GPS point of each vehicle (nexus_fleet.trip_track).
  When a trip is Completed, set_trip_state calls queue_track_finalize() and
  the 5-minute job asks FastAPI for the result of the trip's time window
  10 minutes later (so the phone's last buffered points have arrived), and
  once more 3 hours later when the first result had gaps (late uploads
  after a dead zone). It writes actual_distance_km, distance_variance_km,
  actual_duration_min, actual_route_polyline, track_points,
  track_gap_minutes, track_quality, track_status, and on each confirmed
  stop odometer_km / distance_from_previous_km.

Everything checks its columns first, so the code is safe to deploy before
`bench migrate` (it simply does nothing until the fields exist).
"""

import json
import math
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, get_system_timezone, now_datetime

from nexus_supply_chain.fastapi_client import fastapi_url, internal_headers

MANIFEST = "Vehicle Delivery Manifest"
STOP = "In Transit-Delivery Stop"
OPEN_STATES = ("Manifested", "Ready", "Dispatched", "Returning")
CONFIRMED_STOP_STATES = ("Delivered", "Partially Delivered", "Failed")

ROUTE_TIMEOUT_SEC = 60
FINALIZE_TIMEOUT_SEC = 90
LOCK_TTL_SEC = 180
MAX_ROUTE_WAYPOINTS = 150          # FastAPI internal limit
GEOJSON_MAX_POINTS = 2000
TRACK_SETTLE_MINUTES = 10          # wait for the phone's last buffered points
TRACK_LATE_PASS_HOURS = 3          # one more pass when the first had gaps
TRACK_MAX_ATTEMPTS = 12            # ~1 hour of retries when FastAPI is down
TRACK_MAX_AGE_DAYS = 7             # FastAPI keeps points 9 days (NEXUS_TRACK_RETENTION_DAYS)
MAINTENANCE_BATCH = 50
ERROR_LOG_EVERY_SEC = 3600


# ============================================================================
# Small helpers
# ============================================================================
def _has(field, doctype=MANIFEST):
	return frappe.db.has_column(doctype, field)


def _ready():
	return _has("route_recompute_pending")


def _enqueue(method, job_id, after_commit=True, **kwargs):
	"""One queued job per id (Frappe v15 dedupes by job_id). Falls back to a
	plain enqueue on a Frappe without job_id/deduplicate."""
	try:
		frappe.enqueue(method, queue="short", timeout=300, enqueue_after_commit=after_commit,
		               job_id=job_id, deduplicate=True, **kwargs)
	except TypeError:
		frappe.enqueue(method, queue="short", timeout=300, enqueue_after_commit=after_commit, **kwargs)


def _log_once(key, title, message):
	"""Error Log at most once an hour per key (ORS / FastAPI outages retry every 5 min)."""
	cache = frappe.cache()
	if cache.get_value(f"nexus_route_err:{key}"):
		return
	cache.set_value(f"nexus_route_err:{key}", 1, expires_in_sec=ERROR_LOG_EVERY_SEC)
	frappe.log_error(title=title, message=message)


def _lock(name):
	cache = frappe.cache()
	return cache.set(cache.make_key(name), 1, ex=LOCK_TTL_SEC, nx=True)


def _unlock(name):
	frappe.cache().delete_value(name)


def _epoch_ms(value):
	"""A naive datetime in the system timezone -> epoch milliseconds."""
	if not value:
		return None
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return int(dt.timestamp() * 1000)


def _parse_point(text):
	from nexus_supply_chain.api import parse_combined_coords
	return parse_combined_coords(text)


def _point_text(point):
	return f"{point[0]:.6f},{point[1]:.6f}"


def _decode_polyline(encoded, precision=5):
	factor = float(10 ** precision)
	points, index, lat, lng, length = [], 0, 0, 0, len(encoded or "")
	while index < length:
		deltas = []
		for _i in range(2):
			shift = result = 0
			while True:
				if index >= length:
					return points
				b = ord(encoded[index]) - 63
				index += 1
				result |= (b & 0x1F) << shift
				shift += 5
				if b < 0x20:
					break
			deltas.append(~(result >> 1) if result & 1 else (result >> 1))
		lat += deltas[0]
		lng += deltas[1]
		points.append((lat / factor, lng / factor))
	return points


def _simplify(points, max_points):
	"""Douglas-Peucker (iterative) with a growing tolerance until it fits."""
	if len(points) <= max_points:
		return points
	cos_lat = math.cos(math.radians(sum(p[0] for p in points) / len(points)))
	k = math.radians(1) * 6371000.0

	def dist(p, a, b):
		ax, ay, bx, by = a[1] * k * cos_lat, a[0] * k, b[1] * k * cos_lat, b[0] * k
		px, py = p[1] * k * cos_lat, p[0] * k
		dx, dy = bx - ax, by - ay
		if dx == 0 and dy == 0:
			return math.hypot(px - ax, py - ay)
		t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
		return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

	tolerance, out = 5.0, points
	while len(out) > max_points and tolerance < 2000:
		keep = [False] * len(points)
		keep[0] = keep[-1] = True
		stack = [(0, len(points) - 1)]
		while stack:
			s, e = stack.pop()
			best, best_d = -1, tolerance
			for i in range(s + 1, e):
				d = dist(points[i], points[s], points[e])
				if d > best_d:
					best, best_d = i, d
			if best != -1:
				keep[best] = True
				stack += [(s, best), (best, e)]
		out = [p for p, kept in zip(points, keep) if kept]
		tolerance *= 1.6
	return out


def yard_from_route(route_geojson):
	"""The planned route starts at the yard: its first coordinate, or None."""
	if not route_geojson:
		return None
	try:
		data = frappe.parse_json(route_geojson) if isinstance(route_geojson, str) else route_geojson
	except Exception:
		return None

	def first_line(node, depth=0):
		if not isinstance(node, dict) or depth > 6:
			return None
		t = node.get("type")
		if t == "FeatureCollection":
			for f in node.get("features") or []:
				found = first_line(f, depth + 1)
				if found:
					return found
		elif t == "Feature":
			return first_line(node.get("geometry"), depth + 1)
		elif t == "GeometryCollection":
			for g in node.get("geometries") or []:
				found = first_line(g, depth + 1)
				if found:
					return found
		elif t == "LineString":
			return node.get("coordinates")
		elif t == "MultiLineString":
			lines = node.get("coordinates") or []
			return lines[0] if lines else None
		return None

	coords = first_line(data)
	if not coords:
		return None
	try:
		lng, lat = float(coords[0][0]), float(coords[0][1])
	except (TypeError, ValueError, IndexError):
		return None
	if -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0):
		return lat, lng
	return None


def _company_yard():
	"""The default company's coordinates (Company.custom_latitude / longitude), if set."""
	if not (_has("custom_latitude", "Company") and _has("custom_longitude", "Company")):
		return None
	from nexus_supply_chain.api import parse_combined_coords
	company = frappe.defaults.get_global_default("company")
	rows = []
	if company:
		rows = frappe.get_all("Company", filters={"name": company}, fields=["custom_latitude", "custom_longitude"])
	if not rows:
		rows = frappe.get_all("Company", fields=["custom_latitude", "custom_longitude"], limit=2)
		rows = [r for r in rows if parse_combined_coords(None, r.custom_latitude, r.custom_longitude)]
		if len(rows) != 1:
			return None
	r = rows[0]
	return parse_combined_coords(None, r.custom_latitude, r.custom_longitude)


def manifest_yard(m):
	"""Yard for a manifest row: stored yard_coordinates, else the start of
	its planned route, else the default company's coordinates."""
	stored = _parse_point(m.get("yard_coordinates")) if m.get("yard_coordinates") else None
	return stored or yard_from_route(m.get("route_geojson")) or _company_yard()


def _stop_rows(manifest):
	return frappe.db.sql(f"""
		SELECT name, idx, customer, customer_id, sales_order, delivery_status, completion_time,
		       combined_coordinates, latitude, longitude
		FROM `tab{STOP}`
		WHERE parent = %s AND parenttype = %s
		ORDER BY idx
	""", (manifest, MANIFEST), as_dict=True)


def _stop_point(s):
	from nexus_supply_chain.api import parse_combined_coords
	return parse_combined_coords(s.get("combined_coordinates"), s.get("latitude"), s.get("longitude"))


def _comment(manifest, html):
	frappe.get_doc({
		"doctype": "Comment",
		"comment_type": "Info",
		"reference_doctype": MANIFEST,
		"reference_name": manifest,
		"content": html,
	}).insert(ignore_permissions=True)


# ============================================================================
# PLANNED distance
# ============================================================================
def request_recompute(manifest_names, reason=None):
	"""Marks open trips for recalculation and queues one job per trip after
	the commit. Called inside the transaction that changed the pins."""
	names = sorted({n for n in (manifest_names or []) if n})
	if not names or not _ready():
		return []
	open_trips = frappe.get_all(
		MANIFEST,
		filters={"name": ["in", names], "docstatus": ["<", 2], "trip_status": ["in", OPEN_STATES]},
		pluck="name",
	)
	for name in open_trips:
		frappe.db.set_value(MANIFEST, name, "route_recompute_pending", 1, update_modified=False)
		_enqueue("nexus_supply_chain.route_recompute.recompute_manifest_route",
		         f"nexus_route_recompute::{name}", manifest=name, reason=reason)
	return open_trips


def recompute_manifest_route(manifest, reason=None):
	"""Background job. One run per trip at a time; a change that arrives
	while it runs ticks the flag again and triggers one more run."""
	if not _ready():
		return
	lock = f"nexus_route_recompute_lock:{manifest}"
	if not _lock(lock):
		return  # another run is busy; the flag stays ticked for the next one

	outcome = None
	try:
		frappe.db.set_value(MANIFEST, manifest, "route_recompute_pending", 0, update_modified=False)
		frappe.db.commit()
		outcome = _recompute(manifest, reason)
		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		if frappe.db.exists(MANIFEST, manifest):
			frappe.db.set_value(MANIFEST, manifest, "route_recompute_pending", 1, update_modified=False)
			frappe.db.commit()
		_log_once(f"recompute:{manifest}", "Nexus route recalculation failed",
		          f"{manifest} ({reason}): {e}\n\n{frappe.get_traceback()}")
	finally:
		_unlock(lock)

	if outcome is not None and cint(frappe.db.get_value(MANIFEST, manifest, "route_recompute_pending")):
		_enqueue("nexus_supply_chain.route_recompute.recompute_manifest_route",
		         f"nexus_route_recompute::{manifest}::again", after_commit=False,
		         manifest=manifest, reason="changed_while_recalculating")


def _route_call(origin, waypoints):
	resp = requests.post(
		fastapi_url("/api/v2/fleet/route"),
		json={"origin": list(origin), "waypoints": [list(p) for p in waypoints]},
		headers=internal_headers(),
		timeout=ROUTE_TIMEOUT_SEC,
	)
	try:
		body = resp.json() if resp.content else {}
	except ValueError:
		body = {}
	if isinstance(body.get("detail"), dict):
		body = body["detail"]
	if resp.status_code != 200 or body.get("status") != "success" or not body.get("polyline"):
		raise RuntimeError(f"Routing failed: HTTP {resp.status_code} {body.get('code') or ''} {body.get('message') or ''}".strip())
	return body


def _recompute(manifest, reason=None):
	"""Returns an outcome dict; raises on routing failure (the caller retries)."""
	from nexus_supply_chain.driver_api import push_manifest_refresh

	cols = ["name", "docstatus", "trip_status", "route_geojson", "approximate_total_distance_km"]
	cols += [c for c in ("original_planned_distance_km", "yard_coordinates", "approximate_fuel_consumption_ltrs",
	                     "approximate_fuel_cost", "approximate_drive_time_min") if _has(c)]
	m = frappe.db.get_value(MANIFEST, manifest, cols, as_dict=True)
	if not m or m.docstatus == 2 or (m.trip_status or "Manifested") not in OPEN_STATES:
		return {"status": "skipped"}

	stops = _stop_rows(manifest)
	located = [(s, _stop_point(s)) for s in stops]
	missing = [s for s, p in located if not p]
	points = [p for _s, p in located if p]
	yard = manifest_yard(m)

	if yard:
		origin, waypoints = yard, points + [yard]
	elif len(points) >= 2:
		origin, waypoints = points[0], points[1:]
	else:
		origin, waypoints = None, []

	base_update = {"stops_without_location": len(missing), "route_recomputed_at": now_datetime()}
	if not points or not waypoints:
		frappe.db.set_value(MANIFEST, manifest, {k: v for k, v in base_update.items() if _has(k)}, update_modified=False)
		return {"status": "nothing_to_route", "missing": len(missing)}
	if len(waypoints) > MAX_ROUTE_WAYPOINTS:
		raise RuntimeError(f"{len(waypoints)} waypoints is more than the {MAX_ROUTE_WAYPOINTS} the route server accepts.")

	body = _route_call(origin, waypoints)
	new_km = round(flt(body.get("distance_m")) / 1000.0, 2)
	old_km = flt(m.approximate_total_distance_km)

	line = _simplify(_decode_polyline(body["polyline"], 5), GEOJSON_MAX_POINTS)
	geojson = {
		"type": "FeatureCollection",
		"features": [{
			"type": "Feature",
			"properties": {
				"summary": {"distance": flt(body.get("distance_m")), "duration": flt(body.get("duration_s"))},
				"segments": body.get("legs") or [],
				"source": "nexus_route_recompute",
				"stops_routed": len(points),
				"stops_without_location": len(missing),
				"includes_yard": bool(yard),
				"computed_at": str(now_datetime()),
			},
			"geometry": {"type": "LineString", "coordinates": [[round(lng, 6), round(lat, 6)] for lat, lng in line]},
		}],
	}

	update = dict(base_update)
	update.update({
		"approximate_total_distance_km": new_km,
		"approximate_drive_time_min": round(flt(body.get("duration_s")) / 60.0, 1),
		"route_geojson": json.dumps(geojson, separators=(",", ":")),
	})
	if _has("original_planned_distance_km") and not flt(m.get("original_planned_distance_km")):
		update["original_planned_distance_km"] = old_km or new_km
	if yard and not m.get("yard_coordinates"):
		update["yard_coordinates"] = _point_text(yard)
	if old_km > 0 and new_km > 0:
		ratio = new_km / old_km
		if flt(m.get("approximate_fuel_consumption_ltrs")) > 0:
			update["approximate_fuel_consumption_ltrs"] = round(flt(m.approximate_fuel_consumption_ltrs) * ratio, 2)
		if flt(m.get("approximate_fuel_cost")) > 0:
			update["approximate_fuel_cost"] = round(flt(m.approximate_fuel_cost) * ratio, 2)

	frappe.db.set_value(MANIFEST, manifest, {k: v for k, v in update.items() if _has(k)}, update_modified=False)

	changed = abs(new_km - old_km) >= 0.05
	if changed or not flt(m.get("original_planned_distance_km")):
		lines = [f"🗺️ Planned route recalculated: <b>{old_km:,.1f} km</b> → <b>{new_km:,.1f} km</b>"
		         + (f" ({frappe.utils.escape_html(reason.replace('_', ' '))})" if reason else "")]
		lines.append(f"{len(points)} stop(s) routed in trip order" + (", from and back to the yard." if yard else " (yard unknown: from the first stop)."))
		if missing:
			names = ", ".join(frappe.utils.escape_html(s.customer or s.sales_order or s.name) for s in missing[:8])
			lines.append(f"Without a location (not in the distance): {names}{' …' if len(missing) > 8 else ''}")
		_comment(manifest, "<br>".join(lines))

	push_manifest_refresh([manifest], reason="route_recomputed")
	return {"status": "updated", "old_km": old_km, "new_km": new_km, "missing": len(missing)}


@frappe.whitelist(methods=["POST"])
def recompute_route_now(manifest):
	"""Office button / console: recalculate one trip's planned distance now."""
	if not frappe.has_permission(MANIFEST, "write", manifest):
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	queued = request_recompute([manifest], reason="requested by the office")
	return {"status": "queued" if queued else "skipped",
	        "message": _("Recalculating…") if queued else _("Only open trips are recalculated.")}


# ============================================================================
# ACTUAL distance
# ============================================================================
def queue_track_finalize(manifest):
	"""Called by set_trip_state when a trip is Completed (same transaction).
	The 5-minute job finalizes it TRACK_SETTLE_MINUTES later."""
	if not _has("track_status"):
		return
	update = {"track_status": "Pending"}
	if _has("track_attempts"):
		update["track_attempts"] = 0
	frappe.db.set_value(MANIFEST, manifest, update, update_modified=False)


def finalize_trip_track(manifest, force=False):
	"""Background job: asks FastAPI for the trip's GPS result and stores it."""
	if not _has("track_status"):
		return
	lock = f"nexus_track_finalize_lock:{manifest}"
	if not _lock(lock):
		return
	try:
		_finalize(manifest, force)
		frappe.db.commit()
	except Exception as e:
		frappe.db.rollback()
		_log_once(f"track:{manifest}", "Nexus trip distance failed", f"{manifest}: {e}\n\n{frappe.get_traceback()}")
	finally:
		_unlock(lock)


def _finalize(manifest, force=False):
	from nexus_supply_chain.driver_api import push_manifest_refresh

	cols = ["name", "docstatus", "trip_status", "vehicle", "trip_started_at", "trip_completed_at",
	        "route_geojson", "approximate_total_distance_km", "track_status"]
	cols += [c for c in ("track_attempts", "yard_coordinates") if _has(c)]
	m = frappe.db.get_value(MANIFEST, manifest, cols, as_dict=True)
	if not m or m.docstatus != 1 or m.trip_status != "Completed":
		return
	if not force and m.track_status in ("Final", "No Data", "Not Tracked", "Failed"):
		return
	if not (m.vehicle and m.trip_started_at and m.trip_completed_at):
		frappe.db.set_value(MANIFEST, manifest, {"track_status": "No Data"}, update_modified=False)
		return

	stops = [s for s in _stop_rows(manifest) if s.delivery_status in CONFIRMED_STOP_STATES and s.completion_time]
	yard = manifest_yard(m)
	payload = {
		"manifest": manifest,
		"vehicle": m.vehicle,
		"started_at": _epoch_ms(m.trip_started_at),
		"completed_at": _epoch_ms(m.trip_completed_at),
		"stops": [{"name": s.name, "at": _epoch_ms(s.completion_time)} for s in stops],
		"yard": list(yard) if yard else None,
	}

	attempts = cint(m.get("track_attempts")) + 1
	try:
		resp = requests.post(fastapi_url("/api/v2/trips/finalize"), json=payload,
		                     headers=internal_headers(), timeout=FINALIZE_TIMEOUT_SEC)
		data = resp.json() if resp.content else {}
		if resp.status_code != 200 or data.get("status") != "success":
			raise RuntimeError(f"HTTP {resp.status_code}: {data.get('code') or ''} {data.get('message') or data.get('detail') or ''}")
	except Exception as e:
		failed = attempts >= TRACK_MAX_ATTEMPTS
		update = {"track_status": "Failed" if failed else "Pending"}
		if _has("track_attempts"):
			update["track_attempts"] = attempts
		frappe.db.set_value(MANIFEST, manifest, update, update_modified=False)
		_log_once(f"track:{manifest}", "Nexus trip distance unavailable",
		          f"{manifest} attempt {attempts}/{TRACK_MAX_ATTEMPTS}: {e}")
		return

	quality = data.get("quality") or "No Data"
	status = "No Data" if quality == "No Data" else "Final"
	actual_km = flt(data.get("distance_km"))
	planned_km = flt(m.approximate_total_distance_km)
	update = {
		"track_status": status,
		"track_quality": quality,
		"actual_distance_km": actual_km if status == "Final" else 0,
		"distance_variance_km": round(actual_km - planned_km, 2) if (status == "Final" and planned_km) else 0,
		"actual_duration_min": flt(data.get("duration_min")),
		"actual_route_polyline": data.get("polyline") or None,
		"track_points": cint(data.get("points_used")),
		"track_gap_minutes": flt(data.get("gap_minutes")),
		"track_finalized_at": now_datetime(),
		"track_attempts": attempts,
	}
	frappe.db.set_value(MANIFEST, manifest, {k: v for k, v in update.items() if _has(k)}, update_modified=False)

	if _has("odometer_km", STOP) and _has("distance_from_previous_km", STOP):
		for row in data.get("stops") or []:
			if row.get("name"):
				frappe.db.set_value(STOP, {"name": row["name"], "parent": manifest, "parenttype": MANIFEST}, {
					"odometer_km": flt(row.get("odometer_km")),
					"distance_from_previous_km": flt(row.get("distance_from_previous_km")),
				}, update_modified=False)

	if status == "Final":
		variance = f" ({actual_km - planned_km:+,.1f} km vs planned {planned_km:,.1f} km)" if planned_km else ""
		html = (f"🛣️ Distance driven (GPS): <b>{actual_km:,.1f} km</b>{variance}<br>"
		        f"Track quality: <b>{frappe.utils.escape_html(quality)}</b> · {cint(data.get('points_used'))} GPS points"
		        f" · gaps {flt(data.get('gap_minutes')):,.0f} min"
		        + (f" (bridged on the road map)" if cint(data.get("road_bridged")) else ""))
	else:
		html = "🛣️ No usable GPS points were received for this trip, so the distance driven is unknown."
	if force:
		html += "<br><i>Recalculated with late GPS uploads.</i>"
	_comment(manifest, html)
	push_manifest_refresh([manifest], reason="trip_distance_recorded")


@frappe.whitelist(methods=["POST"])
def refresh_trip_track(manifest):
	"""Office: recalculate a completed trip's GPS distance now (e.g. after a
	phone that was offline uploaded its points)."""
	if not frappe.has_permission(MANIFEST, "write", manifest):
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	if frappe.db.get_value(MANIFEST, manifest, "trip_status") != "Completed":
		return {"status": "skipped", "message": _("Only completed trips have a final GPS distance.")}
	_enqueue("nexus_supply_chain.route_recompute.finalize_trip_track", f"nexus_track_finalize::{manifest}::manual",
	         manifest=manifest, force=True)
	return {"status": "queued", "message": _("Recalculating the distance driven…")}


def office_track(manifest):
	"""Path driven so far (or final) for the office pages. Read-only."""
	cols = ["name", "vehicle", "trip_status", "trip_started_at", "trip_completed_at", "docstatus"]
	cols += [c for c in ("track_status", "track_quality", "actual_distance_km", "actual_route_polyline",
	                     "track_gap_minutes", "track_points") if _has(c)]
	m = frappe.db.get_value(MANIFEST, manifest, cols, as_dict=True)
	if not m or m.docstatus == 2:
		return {"status": "error", "code": "NOT_FOUND", "message": _("Trip not found.")}

	if m.trip_status == "Completed" and m.get("track_status") == "Final" and m.get("actual_route_polyline"):
		return {"status": "success", "final": True, "polyline": m.actual_route_polyline,
		        "distance_km": flt(m.actual_distance_km), "quality": m.get("track_quality"),
		        "gap_minutes": flt(m.get("track_gap_minutes")), "points_used": cint(m.get("track_points"))}
	if not (m.vehicle and m.trip_started_at):
		return {"status": "error", "code": "NOT_STARTED", "message": _("The trip hasn't started yet.")}

	until = _epoch_ms(m.trip_completed_at or now_datetime())
	try:
		resp = requests.get(fastapi_url("/api/v2/trips/track"), params={
			"vehicle": m.vehicle, "since": _epoch_ms(m.trip_started_at), "until": until, "manifest": manifest,
		}, headers=internal_headers(), timeout=30)
		data = resp.json() if resp.content else {}
	except (requests.RequestException, ValueError) as e:
		return {"status": "error", "code": "UNREACHABLE", "message": _("The tracking server can't be reached: {0}").format(str(e)[:120])}
	if resp.status_code != 200 or data.get("status") != "success":
		return {"status": "error", "code": data.get("code") or f"HTTP_{resp.status_code}",
		        "message": data.get("message") or _("The driven path couldn't be loaded.")}
	return {"status": "success", "final": False, "polyline": data.get("polyline"),
	        "distance_km": flt(data.get("distance_km")), "quality": data.get("quality"),
	        "gap_minutes": flt(data.get("gap_minutes")), "points_used": cint(data.get("points_used")),
	        "live": data.get("live")}


# ============================================================================
# Scheduler (every 5 minutes)
# ============================================================================
def run_maintenance():
	if not _ready():
		return
	try:
		for name in frappe.get_all(
			MANIFEST,
			filters={"route_recompute_pending": 1, "docstatus": ["<", 2], "trip_status": ["in", OPEN_STATES]},
			pluck="name", limit=MAINTENANCE_BATCH,
		):
			_enqueue("nexus_supply_chain.route_recompute.recompute_manifest_route",
			         f"nexus_route_recompute::{name}", after_commit=False, manifest=name, reason="retry")
	except Exception:
		frappe.log_error(title="Nexus route maintenance failed", message=frappe.get_traceback())

	if not _has("track_status"):
		return
	try:
		now = now_datetime()
		settled = add_to_date(now, minutes=-TRACK_SETTLE_MINUTES)
		oldest = add_to_date(now, days=-TRACK_MAX_AGE_DAYS)
		for name in frappe.db.sql_list(f"""
			SELECT name FROM `tab{MANIFEST}`
			WHERE docstatus = 1 AND trip_status = 'Completed'
			  AND IFNULL(track_status, '') IN ('', 'Pending')
			  AND trip_completed_at BETWEEN %s AND %s
			ORDER BY trip_completed_at
			LIMIT {MAINTENANCE_BATCH}
		""", (oldest, settled)):
			_enqueue("nexus_supply_chain.route_recompute.finalize_trip_track",
			         f"nexus_track_finalize::{name}", after_commit=False, manifest=name)

		# One late pass for trips whose first result had gaps (phones that were
		# offline upload their buffered points when they get signal back).
		late_from = add_to_date(now, hours=-24)
		late_to = add_to_date(now, hours=-TRACK_LATE_PASS_HOURS)
		cache = frappe.cache()
		for name in frappe.db.sql_list(f"""
			SELECT name FROM `tab{MANIFEST}`
			WHERE docstatus = 1 AND trip_status = 'Completed'
			  AND track_status IN ('Final', 'No Data') AND IFNULL(track_quality, '') != 'Good'
			  AND trip_completed_at BETWEEN %s AND %s
			LIMIT {MAINTENANCE_BATCH}
		""", (late_from, late_to)):
			flag = f"nexus_track_late_pass:{name}"
			if cache.get_value(flag):
				continue
			cache.set_value(flag, 1, expires_in_sec=7 * 86400)
			_enqueue("nexus_supply_chain.route_recompute.finalize_trip_track",
			         f"nexus_track_finalize::{name}::late", after_commit=False, manifest=name, force=True)
	except Exception:
		frappe.log_error(title="Nexus trip distance maintenance failed", message=frappe.get_traceback())
