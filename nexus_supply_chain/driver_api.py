# apps/nexus_supply_chain/nexus_supply_chain/driver_api.py
"""
NEXUS DRIVER API — every driver-side read and write goes through here.

Callers:
  * the new driver app (1.1.0+)             -> the *_from_app endpoints
  * the old driver app and old desk code    -> thin wrappers in api.py and in
                                               vehicle_delivery_manifest.py
  * the Vehicle Delivery Manifest desk form -> the *_from_desk endpoints
  * FastAPI (shared secret)                 -> get_fleet_context,
                                               get_driver_device_context,
                                               get_tracking_settings

Rules every write follows:
  1. Authorisation: the caller must be the manifest's driver (or the vehicle's
     current driver), or an office user with Submit permission on Vehicle
     Delivery Manifest.
  2. The manifest row is locked (SELECT ... FOR UPDATE) for the whole write, so
     two phones / a phone and the desk can never interleave.
  3. Retries are safe: client_request_id results are replayed from Redis for
     10 minutes, and the same ID stored on the row makes them safe forever.
  4. Trip status changes ONLY through set_trip_state().
  5. Distances are computed here from the customer's pin, never trusted from
     the client. Same haversine_meters as the sales app.
  6. One commit per request; pushes to FastAPI and the desk are queued after
     that commit, so a rolled-back write never notifies anyone.

Errors are returned as {"status": "error", "code": ..., "message": ...}
(same shape as the lead endpoints), never as a 500.

The app never creates returns, credit notes, invoices, stock entries or
payments. Returned quantities (Manifest Returned Item) and goods collected
against earlier invoices (Manifest Return Collection, invoice number only)
are RECORDS for the back office, which creates any documents itself.
"""

import json
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, get_system_timezone, now_datetime

from nexus_supply_chain.api import (
	NEXUS_COORD_FIELDS,
	NEXUS_GPS_SNAP_LINK_LABEL,
	NEXUS_LOCATION_SOURCES,
	_coord_text,
	build_coordinate_fields,
	haversine_meters,
	parse_combined_coords,
)
from nexus_supply_chain.fastapi_client import (
	DISPATCH_TICKET_TTL_SEC,
	fastapi_url,
	fastapi_ws_url,
	is_internal_request,
	make_dispatch_ticket,
)
from nexus_supply_chain.nexus_supply_chain.doctype.nexus_app_settings.nexus_app_settings import (
	get_nexus_settings,
)

MANIFEST = "Vehicle Delivery Manifest"
STOP = "In Transit-Delivery Stop"
RETURN = "Manifest Returned Item"
RETURN_COLLECTION = "Manifest Return Collection"
FUEL = "Manifest Fuel Log"
VEHICLE = "Vehicle In Transit"

ACTIVE_TRIP_STATES = ("Ready", "Dispatched", "Returning")
ON_ROAD_STATES = ("Dispatched", "Returning")
CONFIRMABLE_STOP_STATES = ("Delivered", "Partially Delivered", "Failed")
PENDING = "Pending"

# Goods from earlier invoices collected at one stop (invoice numbers only).
MAX_RETURN_INVOICES = 10
MAX_INVOICE_NO_LEN = 60

# Where a Sales Person's phone number may live (first existing, non-empty
# column wins; the linked Employee's numbers are the fallback). Add your own
# custom field name here if it is different.
SALES_PERSON_PHONE_FIELDS = (
	"mobile_no", "custom_mobile_no", "custom_phone_number", "custom_phone",
	"phone", "phone_no", "contact_no", "custom_contact_no",
)
EMPLOYEE_PHONE_FIELDS = ("cell_number", "custom_phone_number", "company_phone")

# The one table of legal trip moves. Anything else is refused unless a
# controller forces it (submit -> Ready, cancel -> Cancelled).
TRIP_TRANSITIONS = {
	"Manifested": ("Ready", "Cancelled"),
	"Ready": ("Dispatched", "Cancelled"),
	"Dispatched": ("Returning", "Completed"),
	"Returning": ("Completed",),
	"Completed": (),
	"Cancelled": (),
}
VEHICLE_STATUS_FOR_TRIP = {
	"Ready": "Loading",
	"Dispatched": "En Route",
	"Returning": "Returning",
	"Completed": "Idle",
	"Cancelled": "Idle",
}
LOAD_PLAN_STATUS_FOR_TRIP = {
	"Ready": "Manifested",
	"Dispatched": "Dispatched",
	"Returning": "Fully Dispatched",
}

RESULT_TTL_SEC = 10 * 60
LOCK_TTL_SEC = 30
MAX_FUEL_AMOUNT = 1_000_000
FUTURE_CLOCK_TOLERANCE = timedelta(minutes=5)


# ============================================================================
# Errors and request plumbing
# ============================================================================
class DriverAPIError(Exception):
	def __init__(self, code, message, **extra):
		super().__init__(message)
		self.code = code
		self.message = message
		self.extra = extra


def _err(code, message, **extra):
	raise DriverAPIError(code, message, **extra)


def _run(fn, title="Nexus Driver API"):
	"""Runs one endpoint. Any failure rolls the whole request back."""
	try:
		return fn()
	except DriverAPIError as e:
		frappe.db.rollback()
		result = {"status": "error", "code": e.code, "message": e.message}
		result.update(e.extra)
		return result
	except frappe.PermissionError as e:
		frappe.db.rollback()
		return {"status": "error", "code": "NOT_PERMITTED", "message": str(e) or "Not permitted."}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(title=title, message=frappe.get_traceback())
		return {"status": "error", "code": "SERVER_ERROR", "message": f"Nothing was saved. {e}"}


def _idempotent(scope, request_id, fn):
	"""
	Replays a finished request for 10 minutes and refuses a duplicate that
	is still running. fn(request_id) must commit before returning success.
	"""
	rid = str(request_id or "").strip()[:64] or None
	if not rid:
		return fn(None)

	cache = frappe.cache()
	result_key = f"nexus_driver:{scope}:{frappe.session.user.lower()}:{rid}"
	cached = cache.get_value(result_key)
	if cached:
		replay = dict(cached)
		replay["replayed"] = True
		return replay

	lock_key = f"{result_key}:lock"
	if not cache.set(cache.make_key(lock_key), 1, ex=LOCK_TTL_SEC, nx=True):
		_err("REQUEST_IN_PROGRESS", "This request is already being processed. Please wait a moment.")
	try:
		result = fn(rid)
		if isinstance(result, dict) and result.get("status") == "success":
			cache.set_value(result_key, result, expires_in_sec=RESULT_TTL_SEC)
		return result
	finally:
		cache.delete_value(lock_key)


def _parse_json(value, default=None):
	if value in (None, ""):
		return default
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value)
	except Exception:
		return default


def _s(value):
	"""Datetime/date -> string for a stable, JSON-safe payload."""
	return str(value) if value not in (None, "") else None


def _client_datetime(value):
	"""
	A timestamp sent by the phone -> naive datetime in the system timezone.
	Accepts epoch milliseconds/seconds or ISO text (with or without offset).
	A clock running in the future is clamped to now. Unreadable -> None.
	"""
	if value in (None, "", 0, "0"):
		return None
	try:
		if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
			number = float(value)
			if number > 1e11:
				number /= 1000.0
			dt = datetime.fromtimestamp(number, tz=timezone.utc)
		else:
			text = str(value).strip().replace("Z", "+00:00")
			try:
				dt = datetime.fromisoformat(text)
			except ValueError:
				dt = get_datetime(text)
		if dt.tzinfo is not None:
			dt = dt.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	except Exception:
		return None

	now = now_datetime()
	return now if dt > now + FUTURE_CLOCK_TOLERANCE else dt


# ============================================================================
# Authorisation
# ============================================================================
def _is_office_user(user=None):
	"""Dispatch/office staff: anyone who may submit manifests. Configured in
	Role Permission Manager, so no role names are hardcoded here."""
	return bool(frappe.has_permission(MANIFEST, "submit", user=user or frappe.session.user))


def _driver_users_for(row):
	users = set()
	if row.get("driver"):
		users.add(row.driver.strip().lower())
	if row.get("vehicle"):
		current = frappe.db.get_value(VEHICLE, row.vehicle, "current_driver")
		if current:
			users.add(current.strip().lower())
	return users


def _manifest_for_actor(manifest_name, lock=False):
	"""Loads (and optionally locks) a submitted manifest the caller may act on."""
	if not manifest_name:
		_err("MANIFEST_REQUIRED", "No manifest was given.")
	row = frappe.db.get_value(
		MANIFEST,
		manifest_name,
		["name", "docstatus", "trip_status", "driver", "vehicle", "load_plan", "truck_plan",
		 "trip_started_at", "trip_completed_at"],
		as_dict=True,
		for_update=lock,
	)
	if not row:
		_err("MANIFEST_NOT_FOUND", "This manifest no longer exists.")
	if row.docstatus != 1:
		_err("MANIFEST_NOT_RELEASED", "This manifest hasn't been released to the driver yet (it isn't submitted).")

	user = (frappe.session.user or "").strip().lower()
	is_driver = user in _driver_users_for(row)
	if not is_driver and not _is_office_user():
		_err("NOT_YOUR_MANIFEST", "This manifest is assigned to another driver.")

	row.trip_status = row.trip_status or "Manifested"
	row.actor_role = "Driver" if is_driver else "Office"
	return row


def _require_office():
	if not _is_office_user():
		_err("NOT_PERMITTED", "Only dispatch staff can do this.")


def _require_internal_or_office():
	if is_internal_request():
		return
	if frappe.session.user != "Guest" and _is_office_user():
		return
	frappe.throw(_("Not permitted."), frappe.PermissionError)


# ============================================================================
# Pins, items and reasons
# ============================================================================
def _pin_from_party_row(row):
	"""(exact "lat,lng" text, (lat, lng)) from a Customer/Lead row, or (None, None)."""
	fields, point = build_coordinate_fields(
		(row or {}).get("custom_latitude"),
		(row or {}).get("custom_longitude"),
		(row or {}).get("custom_combined_coordinates"),
	)
	return (fields["custom_combined_coordinates"], point) if fields else (None, None)


def _pin_from_stop(stop):
	fields, point = build_coordinate_fields(
		(stop or {}).get("latitude"),
		(stop or {}).get("longitude"),
		(stop or {}).get("combined_coordinates"),
	)
	return (fields["custom_combined_coordinates"], point) if fields else (None, None)


def _customer_columns(extra=()):
	wanted = ("customer_name", "mobile_no", "custom_phone_number") + NEXUS_COORD_FIELDS + tuple(extra)
	return [f for f in wanted if frappe.db.has_column("Customer", f)]


def _customer_pin(customer):
	if not customer:
		return None, None
	cols = [f for f in NEXUS_COORD_FIELDS if frappe.db.has_column("Customer", f)]
	if not cols:
		return None, None
	return _pin_from_party_row(frappe.db.get_value("Customer", customer, cols, as_dict=True) or {})


def _customer_phone(row):
	return ((row or {}).get("mobile_no") or (row or {}).get("custom_phone_number") or "").strip() or None


def _sales_order_items(sales_order):
	"""item_code -> {item_code, item_name, qty, uom}, in order-line order."""
	items = OrderedDict()
	if not sales_order:
		return items
	for r in frappe.db.sql("""
		SELECT item_code, MAX(item_name) AS item_name, SUM(qty) AS qty, MAX(uom) AS uom, MIN(idx) AS first_idx
		FROM `tabSales Order Item`
		WHERE parent = %s
		GROUP BY item_code
		ORDER BY first_idx
	""", (sales_order,), as_dict=True):
		items[r.item_code] = {"item_code": r.item_code, "item_name": r.item_name, "qty": flt(r.qty), "uom": r.uom}
	return items


def _return_reasons():
	field = frappe.get_meta(RETURN).get_field("reason")
	return [o.strip() for o in (field.options or "").split("\n") if o.strip()] if field else []


def _parse_returned_items(raw):
	"""Accepts the new app shape {item_code, returned_qty}, the old app
	{item_code, qty_returned} and plain {item_code, qty}. Merged by item."""
	data = _parse_json(raw, [])
	if isinstance(data, dict):
		data = [data]
	merged = OrderedDict()
	for r in data or []:
		if not isinstance(r, dict):
			continue
		code = (r.get("item_code") or "").strip()
		qty = flt(r.get("returned_qty", r.get("qty_returned", r.get("qty"))))
		if code and qty > 0:
			merged[code] = merged.get(code, 0.0) + qty
	return merged


def _parse_return_invoices(raw):
	"""
	Earlier-invoice numbers the driver typed for goods the customer handed
	back. Accepts a JSON list, a list, or one comma/newline separated string.
	Trimmed, de-duplicated (case-insensitive), at most MAX_RETURN_INVOICES.
	"""
	data = _parse_json(raw, raw)
	if data in (None, ""):
		return []
	if isinstance(data, str):
		data = [p for chunk in data.splitlines() for p in chunk.split(",")]
	if not isinstance(data, (list, tuple)):
		data = [data]
	seen, out = set(), []
	for value in data:
		text = " ".join(str(value or "").split())
		if not text:
			continue
		if len(text) > MAX_INVOICE_NO_LEN:
			_err("INVOICE_NO_TOO_LONG", f"Invoice number '{text[:20]}…' is too long.", field="return_invoices")
		key = text.upper()
		if key not in seen:
			seen.add(key)
			out.append(text)
	if len(out) > MAX_RETURN_INVOICES:
		_err("TOO_MANY_INVOICES", f"At most {MAX_RETURN_INVOICES} invoice numbers per stop.", field="return_invoices")
	return out


def _match_invoices(invoice_numbers, customer):
	"""{typed number -> (match, matched_invoice)}; never rejects a number."""
	result = {n: ("Not Found", None) for n in invoice_numbers}
	if not invoice_numbers:
		return result
	rows = frappe.db.sql(
		"SELECT name, customer FROM `tabSales Invoice` WHERE docstatus = 1 AND name IN %(names)s",
		{"names": tuple(invoice_numbers)}, as_dict=True,
	)
	found = {r.name.upper(): r for r in rows}
	for n in invoice_numbers:
		r = found.get(n.upper())
		if r:
			result[n] = ("Matched" if (customer and r.customer == customer) else "Other Customer", r.name)
	return result


def _columns(doctype, candidates):
	return [f for f in candidates if frappe.db.has_column(doctype, f)]


def _sales_person_contacts(sales_orders):
	"""
	{sales_order: {"name", "phone"}} for the main sales person of each order
	(Sales Team row with the highest allocation, then the first row).
	Phone: the Sales Person's own phone field, else the linked Employee's.
	Bulk: three queries regardless of the number of orders.
	"""
	if not sales_orders:
		return {}
	team = frappe.db.sql("""
		SELECT parent, sales_person, IFNULL(allocated_percentage, 0) AS pct, idx
		FROM `tabSales Team`
		WHERE parenttype = 'Sales Order' AND parent IN %(sos)s AND IFNULL(sales_person, '') != ''
		ORDER BY parent, pct DESC, idx ASC
	""", {"sos": tuple(sales_orders)}, as_dict=True)
	main = {}
	for r in team:
		main.setdefault(r.parent, r.sales_person)
	if not main:
		return {}

	people = tuple(set(main.values()))
	sp_cols = _columns("Sales Person", SALES_PERSON_PHONE_FIELDS)
	has_employee = frappe.db.has_column("Sales Person", "employee")
	select = ["name", "sales_person_name"] + sp_cols + (["employee"] if has_employee else [])
	persons = {
		p.name: p for p in frappe.db.sql(
			f"SELECT {', '.join(f'`{c}`' for c in select)} FROM `tabSales Person` WHERE name IN %(names)s",
			{"names": people}, as_dict=True,
		)
	}

	employee_phone = {}
	employees = tuple({p.employee for p in persons.values() if has_employee and p.get("employee")})
	emp_cols = _columns("Employee", EMPLOYEE_PHONE_FIELDS)
	if employees and emp_cols:
		for e in frappe.db.sql(
			f"SELECT name, {', '.join(f'`{c}`' for c in emp_cols)} FROM `tabEmployee` WHERE name IN %(names)s",
			{"names": employees}, as_dict=True,
		):
			employee_phone[e.name] = next((str(e.get(c)).strip() for c in emp_cols if (e.get(c) or "").strip()), None)

	out = {}
	for so, person_name in main.items():
		p = persons.get(person_name) or {}
		phone = next((str(p.get(c)).strip() for c in sp_cols if (p.get(c) or "").strip()), None)
		if not phone and p.get("employee"):
			phone = employee_phone.get(p.get("employee"))
		out[so] = {"name": p.get("sales_person_name") or person_name, "phone": phone or None}
	return out


# ============================================================================
# Stop hydration (used by Truck Plan -> Manifest and the manifest controller)
# ============================================================================
def build_stop_fields(sales_order):
	"""Everything a stop row should carry, read live from the Sales Order and
	its Customer. Coordinates are exact text through the shared builder."""
	so = frappe.db.get_value(
		"Sales Order", sales_order,
		["name", "customer", "customer_name", "payment_terms_template", "grand_total"],
		as_dict=True,
	)
	if not so:
		return {}

	fields = {
		"sales_order": so.name,
		"customer_id": so.customer,
		"customer": so.customer_name or so.customer,
		"payment_terms": so.payment_terms_template or "",
		"grand_total": flt(so.grand_total),
	}
	if so.customer:
		cust = frappe.db.get_value("Customer", so.customer, _customer_columns(), as_dict=True) or {}
		fields["customer_phone"] = _customer_phone(cust)
		text, point = _pin_from_party_row(cust)
		if text:
			fields.update({"combined_coordinates": text, "latitude": point[0], "longitude": point[1]})
	return fields


def hydrate_stop_row(stop):
	"""Fills only what is missing on a stop child Document (never overwrites
	a valid pin or a typed value)."""
	if not stop.get("sales_order"):
		return
	fresh = build_stop_fields(stop.sales_order)
	if not fresh:
		return
	for key in ("customer_id", "customer", "customer_phone", "payment_terms"):
		if not stop.get(key) and fresh.get(key):
			stop.set(key, fresh[key])
	if not stop.get("grand_total") and fresh.get("grand_total"):
		stop.set("grand_total", fresh["grand_total"])

	text, point = _pin_from_stop(stop)
	if text:
		stop.combined_coordinates, stop.latitude, stop.longitude = text, point[0], point[1]
	elif fresh.get("combined_coordinates"):
		stop.combined_coordinates = fresh["combined_coordinates"]
		stop.latitude, stop.longitude = fresh["latitude"], fresh["longitude"]


def refresh_customer_stop_pins(customer, pin_text, point):
	"""
	A customer's pin changed: every PENDING stop for that customer on a
	manifest that is still open (draft, Ready, Dispatched, Returning) gets
	the new pin. Confirmed stops are history and are never touched.
	Returns the set of affected manifest names. Does not commit or push.
	"""
	if not customer or not pin_text or not point:
		return set()
	rows = frappe.db.sql("""
		SELECT s.name, s.parent
		FROM `tabIn Transit-Delivery Stop` s
		JOIN `tabVehicle Delivery Manifest` m ON m.name = s.parent
		WHERE s.parenttype = 'Vehicle Delivery Manifest'
		  AND s.customer_id = %(customer)s
		  AND IFNULL(s.delivery_status, 'Pending') = 'Pending'
		  AND m.docstatus < 2
		  AND IFNULL(m.trip_status, 'Manifested') IN ('Manifested', 'Ready', 'Dispatched', 'Returning')
		  AND IFNULL(s.combined_coordinates, '') != %(pin)s
	""", {"customer": customer, "pin": pin_text}, as_dict=True)

	for r in rows:
		frappe.db.set_value(
			STOP, r.name,
			{"combined_coordinates": pin_text, "latitude": point[0], "longitude": point[1]},
			update_modified=False,
		)
	return {r.parent for r in rows}


# ============================================================================
# Pushes (queued after commit)
# ============================================================================
def push_manifest_refresh(manifest_names, reason=None):
	"""Tells the desk (realtime) and FastAPI (driver apps + dispatch pages)
	that these manifests changed. Both are sent only after the commit."""
	names = sorted({n for n in (manifest_names or []) if n})
	if not names:
		return

	rows = frappe.get_all(MANIFEST, filters={"name": ["in", names]}, fields=["name", "driver", "vehicle", "trip_status"])
	emails = set()
	for r in rows:
		emails |= _driver_users_for(r)
	emails.discard("")

	message = {"manifests": names, "reason": reason, "trip_statuses": {r.name: r.trip_status for r in rows}}
	frappe.publish_realtime("nexus_manifest_update", message=message, after_commit=True)
	frappe.enqueue(
		"nexus_supply_chain.api.execute_fastapi_webhook",
		queue="short",
		enqueue_after_commit=True,
		affected_emails=sorted(emails),
		doctype=MANIFEST,
		docname=names[0],
		command="REFRESH_MANIFESTS",
		extra=message,
	)


def _add_manifest_comment(manifest_name, html):
	frappe.get_doc({
		"doctype": "Comment",
		"comment_type": "Info",
		"reference_doctype": MANIFEST,
		"reference_name": manifest_name,
		"content": html,
	}).insert(ignore_permissions=True)


# ============================================================================
# Trip state — THE single place trip_status changes
# ============================================================================
def _vehicle_has_other_active_trip(vehicle, manifest_name, states=ACTIVE_TRIP_STATES):
	return bool(frappe.db.sql("""
		SELECT name FROM `tabVehicle Delivery Manifest`
		WHERE vehicle = %s AND name != %s AND docstatus = 1 AND trip_status IN %s
		LIMIT 1
	""", (vehicle, manifest_name, tuple(states))))


def set_trip_state(manifest_name, new_status, force=False, note=None):
	"""
	Moves a manifest to new_status and applies every side effect:
	trip timestamps, vehicle status, load plan status, ToDo closing,
	leftover stops on completion, timeline comment, pushes.
	force=True skips the transition table (used by submit and cancel only).
	Does NOT commit.
	"""
	if new_status not in TRIP_TRANSITIONS:
		_err("INVALID_STATUS", f"\"{new_status}\" is not a trip status.")

	row = frappe.db.get_value(
		MANIFEST, manifest_name,
		["name", "docstatus", "trip_status", "vehicle", "load_plan", "trip_started_at"],
		as_dict=True, for_update=True,
	)
	if not row:
		_err("MANIFEST_NOT_FOUND", "This manifest no longer exists.")

	old = row.trip_status or "Manifested"
	if old == new_status:
		return {"changed": False, "from": old, "to": new_status}
	if not force and new_status not in TRIP_TRANSITIONS.get(old, ()):
		_err("INVALID_TRANSITION", f"A trip can't move from {old} to {new_status}.", trip_status=old)

	now = now_datetime()
	update = {"trip_status": new_status}
	if new_status == "Dispatched" and not row.trip_started_at:
		update["trip_started_at"] = now
	if new_status == "Completed":
		update["trip_completed_at"] = now

	closed_stops = 0
	if new_status == "Completed":
		# Stops never reached go back to the yard with the truck.
		for stop in frappe.get_all(STOP, filters={"parent": manifest_name, "parenttype": MANIFEST,
		                                           "delivery_status": PENDING}, fields=["name", "delivery_notes"]):
			notes = ((stop.delivery_notes or "") + "\nNot delivered: trip closed with this stop still pending.").strip()
			frappe.db.set_value(STOP, stop.name, {"delivery_status": "Returned", "delivery_notes": notes,
			                                      "completion_time": now}, update_modified=False)
			closed_stops += 1

	frappe.db.set_value(MANIFEST, manifest_name, update)

	if row.vehicle and new_status in VEHICLE_STATUS_FOR_TRIP:
		releasing = new_status in ("Completed", "Cancelled")
		if not (releasing and _vehicle_has_other_active_trip(row.vehicle, manifest_name)):
			frappe.db.set_value(VEHICLE, row.vehicle, "current_status", VEHICLE_STATUS_FOR_TRIP[new_status],
			                    update_modified=False)

	if row.load_plan and new_status in LOAD_PLAN_STATUS_FOR_TRIP and frappe.db.has_column("Nexus Load Plan", "dispatch_status"):
		frappe.db.set_value("Nexus Load Plan", row.load_plan, "dispatch_status",
		                    LOAD_PLAN_STATUS_FOR_TRIP[new_status], update_modified=False)

	if new_status in ("Completed", "Cancelled"):
		frappe.db.sql("""
			UPDATE `tabToDo` SET status = 'Closed'
			WHERE reference_type = %s AND reference_name = %s AND status = 'Open'
		""", (MANIFEST, manifest_name))

	lines = [f"🚚 Trip status: <b>{old}</b> → <b>{new_status}</b> by <b>{frappe.utils.escape_html(frappe.session.user)}</b>"]
	if closed_stops:
		lines.append(f"{closed_stops} stop(s) still pending were marked Returned.")
	if note:
		lines.append(frappe.utils.escape_html(note))
	_add_manifest_comment(manifest_name, "<br>".join(lines))

	push_manifest_refresh([manifest_name], reason=f"trip_{new_status.lower()}")
	return {"changed": True, "from": old, "to": new_status, "closed_stops": closed_stops}


def _trip_action(manifest, target, allowed_from, request_id, scope):
	def work(rid):
		m = _manifest_for_actor(manifest, lock=True)
		if m.trip_status == target:
			return {"status": "success", "message": f"Trip is already {target}.", "trip_status": target, "already": True}
		if m.trip_status not in allowed_from:
			_err("INVALID_TRANSITION", f"A trip can't move from {m.trip_status} to {target}.", trip_status=m.trip_status)
		if target == "Dispatched" and m.vehicle and _vehicle_has_other_active_trip(m.vehicle, m.name, ON_ROAD_STATES):
			_err("TRIP_IN_PROGRESS", "This vehicle already has a trip on the road. Complete it first.")
		result = set_trip_state(m.name, target)
		frappe.db.commit()
		msg = {
			"Dispatched": "Trip started. Drive safely.",
			"Returning": "Heading back to the yard.",
			"Completed": "Trip completed. The vehicle is now Idle.",
		}.get(target, f"Trip is now {target}.")
		if result.get("closed_stops"):
			msg += f" {result['closed_stops']} undelivered stop(s) were marked Returned."
		return {"status": "success", "message": msg, "trip_status": target}

	return _run(lambda: _idempotent(scope, request_id, work), title=f"Nexus Trip {target} Failed")


# ============================================================================
# Driver payload (vault)
# ============================================================================
def _vehicle_for_driver(user):
	rows = frappe.get_all(
		VEHICLE, filters={"current_driver": user},
		fields=["name", "vehicle_plate", "current_status"], order_by="modified desc", limit=1,
	)
	return rows[0] if rows else None


def get_driver_payload(user=None):
	"""
	Everything the driver app shows, in one deterministic payload (FastAPI
	hashes it, so nothing volatile like 'now' or 'modified' is included).
	Active trips + Completed trips within Completed History days.
	Stop pins: the customer's live pin first, the stop's own copy second.
	Bulk queries only: one per child table, regardless of trip count.
	"""
	user = user or frappe.session.user
	settings = get_nexus_settings()
	history_days = cint(settings.get("completed_history_days")) or 7
	vehicle = _vehicle_for_driver(user)
	vehicle_name = vehicle.name if vehicle else ""

	rows = frappe.db.sql("""
		SELECT name, vehicle, driver, trip_status, trip_started_at, trip_completed_at, route_geojson,
		       cumulative_additional_fuel_cost, approximate_total_distance_km, truck_plan, load_plan, modified
		FROM `tabVehicle Delivery Manifest`
		WHERE docstatus = 1
		  AND (driver = %(user)s OR (%(vehicle)s != '' AND vehicle = %(vehicle)s))
		  AND (trip_status IN ('Ready', 'Dispatched', 'Returning')
		       OR (trip_status = 'Completed' AND COALESCE(trip_completed_at, modified) >= %(since)s))
	""", {"user": user, "vehicle": vehicle_name, "since": add_days(now_datetime(), -history_days)}, as_dict=True)

	rank = {"Dispatched": 0, "Returning": 1, "Ready": 2}
	active = sorted([r for r in rows if r.trip_status in rank], key=lambda r: (rank[r.trip_status], r.name))
	completed = sorted(
		[r for r in rows if r.trip_status == "Completed"],
		key=lambda r: get_datetime(r.trip_completed_at or r.modified), reverse=True,
	)
	ordered = active + completed

	current = next((r for r in active if r.trip_status in ON_ROAD_STATES), None) or (active[0] if active else None)
	context = {
		"driver": user,
		"driver_name": frappe.db.get_value("User", user, "full_name") or user,
		"vehicle": vehicle_name or "Idle",
		"vehicle_plate": vehicle.vehicle_plate if vehicle else None,
		"vehicle_status": vehicle.current_status if vehicle else None,
		"active_manifest_id": current.name if current else "No_Active_Manifest",
		"active_trip_status": current.trip_status if current else None,
		"settings": {
			"dropoff_on_site_threshold_m": flt(settings.get("dropoff_on_site_threshold_m")),
			"completed_history_days": history_days,
		},
		"return_reasons": _return_reasons(),
	}
	if not ordered:
		return {"manifests": [], "context": context}

	names = tuple(r.name for r in ordered)
	stops = frappe.db.sql("""
		SELECT name, parent, idx, sales_order, customer_id, customer, customer_phone, payment_terms,
		       grand_total, weight_kg, delivery_status, arrival_time, completion_time, delivery_notes,
		       combined_coordinates, latitude, longitude, location_updated_during_trip,
		       dropoff_latitude, dropoff_longitude, dropoff_accuracy_m, dropoff_captured_at,
		       dropoff_gps_status, target_coordinates, dropoff_distance_from_target_m,
		       original_dropoff_distance_m, is_on_site_dropoff, confirmed_by
		FROM `tabIn Transit-Delivery Stop`
		WHERE parenttype = %(dt)s AND parent IN %(names)s
		ORDER BY parent, idx
	""", {"dt": MANIFEST, "names": names}, as_dict=True)

	returns = frappe.db.sql("""
		SELECT name, parent, idx, stop_row, sales_order, item_code, ordered_qty, returned_qty, reason
		FROM `tabManifest Returned Item`
		WHERE parenttype = %(dt)s AND parent IN %(names)s
		ORDER BY parent, idx
	""", {"dt": MANIFEST, "names": names}, as_dict=True)

	collections_by_stop = {}
	if frappe.db.table_exists(RETURN_COLLECTION):
		for c in frappe.db.sql("""
			SELECT name, stop_row, invoice_no, invoice_match, back_office_status, recorded_at
			FROM `tabManifest Return Collection`
			WHERE parenttype = %(dt)s AND parent IN %(names)s
			ORDER BY parent, idx
		""", {"dt": MANIFEST, "names": names}, as_dict=True):
			collections_by_stop.setdefault(c.stop_row, []).append({
				"name": c.name, "invoice_no": c.invoice_no, "invoice_match": c.invoice_match,
				"back_office_status": c.back_office_status or "Pending", "recorded_at": _s(c.recorded_at),
			})

	fuel = frappe.db.sql("""
		SELECT name, parent, idx, fuelled_at, amount, litres, odometer_km, station, combined_coordinates, notes
		FROM `tabManifest Fuel Log`
		WHERE parenttype = %(dt)s AND parent IN %(names)s
		ORDER BY parent, idx
	""", {"dt": MANIFEST, "names": names}, as_dict=True)

	customer_ids = tuple({s.customer_id for s in stops if s.customer_id})
	customers = {}
	if customer_ids:
		cols = ", ".join(f"`{c}`" for c in ["name"] + _customer_columns())
		for c in frappe.db.sql(f"SELECT {cols} FROM `tabCustomer` WHERE name IN %(ids)s",
		                       {"ids": customer_ids}, as_dict=True):
			customers[c.name] = c

	so_names = tuple({s.sales_order for s in stops if s.sales_order})
	sales_people = _sales_person_contacts(so_names)
	so_items = {}
	if so_names:
		for it in frappe.db.sql("""
			SELECT parent, item_code, MAX(item_name) AS item_name, SUM(qty) AS qty, MAX(uom) AS uom, MIN(idx) AS first_idx
			FROM `tabSales Order Item`
			WHERE parent IN %(sos)s
			GROUP BY parent, item_code
			ORDER BY parent, first_idx
		""", {"sos": so_names}, as_dict=True):
			so_items.setdefault(it.parent, []).append({
				"item_code": it.item_code, "item_name": it.item_name,
				"qty": flt(it.qty), "max_qty": flt(it.qty), "uom": it.uom,
			})

	returns_by_stop, returns_by_parent = {}, {}
	for r in returns:
		item = {
			"name": r.name, "stop_row": r.stop_row, "sales_order": r.sales_order, "item_code": r.item_code,
			"ordered_qty": flt(r.ordered_qty), "returned_qty": flt(r.returned_qty), "reason": r.reason,
		}
		returns_by_parent.setdefault(r.parent, []).append(item)
		if r.stop_row:
			returns_by_stop.setdefault(r.stop_row, []).append(item)

	stops_by_parent = {}
	for s in stops:
		cust = customers.get(s.customer_id) or {}
		pin_text, point = _pin_from_party_row(cust)
		if not pin_text:
			pin_text, point = _pin_from_stop(s)

		stops_by_parent.setdefault(s.parent, []).append({
			"name": s.name,
			"idx": s.idx,
			"sales_order": s.sales_order,
			"customer_id": s.customer_id,
			"customer": s.customer or cust.get("customer_name") or s.customer_id,
			"customer_name": s.customer or cust.get("customer_name") or s.customer_id,
			"customer_phone": s.customer_phone or _customer_phone(cust),
			"payment_terms": s.payment_terms,
			"grand_total": flt(s.grand_total),
			"weight_kg": flt(s.weight_kg),
			"delivery_status": s.delivery_status or PENDING,
			"arrival_time": _s(s.arrival_time),
			"completion_time": _s(s.completion_time),
			"delivery_notes": s.delivery_notes,
			"pin": pin_text,
			"has_location": bool(point),
			# Numeric copies for map SDKs and for app builds before 1.1.0.
			"custom_latitude": point[0] if point else None,
			"custom_longitude": point[1] if point else None,
			"latitude": point[0] if point else None,
			"longitude": point[1] if point else None,
			"location_updated_during_trip": cint(s.location_updated_during_trip),
			"dropoff": {
				"latitude": s.dropoff_latitude,
				"longitude": s.dropoff_longitude,
				"accuracy_m": flt(s.dropoff_accuracy_m) if s.dropoff_accuracy_m is not None else None,
				"captured_at": _s(s.dropoff_captured_at),
				"gps_status": s.dropoff_gps_status or None,
				"target_coordinates": s.target_coordinates,
				"distance_from_target_m": flt(s.dropoff_distance_from_target_m) if s.dropoff_distance_from_target_m is not None else None,
				"original_distance_m": flt(s.original_dropoff_distance_m) if s.original_dropoff_distance_m is not None else None,
				"is_on_site": cint(s.is_on_site_dropoff),
			},
			"confirmed_by": s.confirmed_by,
			"items": so_items.get(s.sales_order, []),
			"returned_items": returns_by_stop.get(s.name, []),
			"sales_person": sales_people.get(s.sales_order),
			"return_collections": collections_by_stop.get(s.name, []),
		})

	fuel_by_parent = {}
	for f in fuel:
		fuel_by_parent.setdefault(f.parent, []).append({
			"name": f.name, "fuelled_at": _s(f.fuelled_at), "amount": flt(f.amount),
			"litres": flt(f.litres) if f.litres is not None else None,
			"odometer_km": flt(f.odometer_km) if f.odometer_km is not None else None,
			"station": f.station, "location": f.combined_coordinates, "notes": f.notes,
		})

	manifests = []
	for r in ordered:
		manifest_stops = stops_by_parent.get(r.name, [])
		manifests.append({
			"name": r.name,
			"vehicle": r.vehicle,
			"driver": r.driver,
			"trip_status": r.trip_status,
			"docstatus": 1,
			"truck_plan": r.truck_plan,
			"load_plan": r.load_plan,
			"trip_started_at": _s(r.trip_started_at),
			"trip_completed_at": _s(r.trip_completed_at),
			"route_geojson": r.route_geojson,
			"approximate_total_distance_km": flt(r.approximate_total_distance_km),
			"cumulative_additional_fuel_cost": flt(r.cumulative_additional_fuel_cost),
			"stop_count": len(manifest_stops),
			"pending_stop_count": sum(1 for s in manifest_stops if s["delivery_status"] == PENDING),
			"has_map_data": bool(r.route_geojson) or any(s["has_location"] for s in manifest_stops),
			"stops": manifest_stops,
			"returned_items": returns_by_parent.get(r.name, []),
			"fuel_logs": fuel_by_parent.get(r.name, []),
		})

	return {"manifests": manifests, "context": context}


@frappe.whitelist()
def get_driver_manifests():
	"""New-app vault source (via FastAPI). Same payload as api.get_my_active_manifests_and_context."""
	return _run(lambda: {"status": "success", "message": get_driver_payload()}, title="Nexus Driver Payload Failed")


@frappe.whitelist()
def get_stop_items(manifest, stop_row):
	"""Order lines of one stop, permission-checked (desk drop-off dialog)."""
	def work():
		m = _manifest_for_actor(manifest)
		so = frappe.db.get_value(STOP, {"name": stop_row, "parent": m.name, "parenttype": MANIFEST}, "sales_order")
		if not so:
			_err("STOP_NOT_FOUND", "This stop isn't on the manifest.")
		return {"status": "success", "items": list(_sales_order_items(so).values()), "reasons": _return_reasons()}
	return _run(work)


# ============================================================================
# Confirm a stop (Delivered / Partially Delivered / Failed)
# ============================================================================
def _confirm_stop(manifest, stop_row, delivery_status, returned_items=None, return_reason=None, notes=None,
                  dropoff=None, arrival_time=None, request_id=None, legacy=False, return_invoices=None):
	m = _manifest_for_actor(manifest, lock=True)

	stop = frappe.db.get_value(
		STOP, {"name": stop_row, "parent": m.name, "parenttype": MANIFEST},
		["name", "sales_order", "customer_id", "customer", "delivery_status", "arrival_time",
		 "delivery_notes", "client_request_id", "combined_coordinates", "latitude", "longitude"],
		as_dict=True, for_update=True,
	)
	if not stop:
		_err("STOP_NOT_FOUND", "This stop isn't on the manifest.")

	current = stop.delivery_status or PENDING
	if current != PENDING:
		same_request = request_id and stop.client_request_id == request_id
		same_legacy_retry = legacy and current == delivery_status
		if same_request or same_legacy_retry:
			return _stop_result(m.name, stop.name, replayed=True)
		_err("STOP_ALREADY_CONFIRMED", f"This stop was already marked {current}.", delivery_status=current)

	if m.trip_status not in ON_ROAD_STATES:
		_err("TRIP_NOT_ON_ROAD", f"Stops can only be confirmed while the trip is on the road (it is {m.trip_status}).",
		     trip_status=m.trip_status)
	if delivery_status not in CONFIRMABLE_STOP_STATES:
		_err("INVALID_STATUS", "Choose Delivered, Partially Delivered or Failed.")

	# --- Returns ---------------------------------------------------------
	reason = (return_reason or "").strip()
	order_items = _sales_order_items(stop.sales_order)
	return_rows = []

	if delivery_status in ("Partially Delivered", "Failed"):
		reasons = _return_reasons()
		if reason not in reasons:
			_err("REASON_REQUIRED", "Choose the reason for the return.", field="return_reason", reasons=reasons)

	if delivery_status == "Partially Delivered":
		requested = _parse_returned_items(returned_items)
		if not requested:
			_err("RETURNS_REQUIRED", "Select at least one returned item.", field="returned_items")
		for code, qty in requested.items():
			line = order_items.get(code)
			if not line:
				_err("ITEM_NOT_ON_ORDER", f"{code} isn't on this order.", field="returned_items")
			if qty > line["qty"]:
				_err("QTY_TOO_HIGH", f"{code}: you can't return more than the {line['qty']:g} ordered.", field="returned_items")
			return_rows.append((code, line["qty"], qty))
		if order_items and all(requested.get(code, 0) >= line["qty"] for code, line in order_items.items()):
			_err("ALL_ITEMS_RETURNED", "Every item is being returned. Mark this stop as Failed instead.")
	elif delivery_status == "Failed":
		return_rows = [(code, line["qty"], line["qty"]) for code, line in order_items.items()]

	# --- Goods from EARLIER invoices (number only; any delivery status) ----
	collected_invoices = _parse_return_invoices(return_invoices)

	# --- Drop-off GPS ----------------------------------------------------
	d = _parse_json(dropoff, {}) or {}
	gps_point = parse_combined_coords(None, d.get("latitude"), d.get("longitude"))
	is_mock = cint(d.get("is_mock"))
	gps_status = ("Mock Detected" if is_mock else "Captured") if gps_point else "Unavailable"
	now = now_datetime()
	captured_at = _client_datetime(d.get("captured_at")) if gps_point else None

	customer = stop.customer_id or frappe.db.get_value("Sales Order", stop.sales_order, "customer")
	target_text, target_point = _customer_pin(customer)
	if not target_text:
		target_text, target_point = _pin_from_stop(stop)

	distance = None
	if gps_point and target_point:
		distance = round(haversine_meters(gps_point[0], gps_point[1], target_point[0], target_point[1]), 2)
	threshold = flt(get_nexus_settings().get("dropoff_on_site_threshold_m")) or 200.0
	on_site = 1 if (gps_status == "Captured" and distance is not None and distance <= threshold) else 0

	clean_notes = (notes or "").strip()
	update = {
		"delivery_status": delivery_status,
		"completion_time": now,
		"arrival_time": stop.arrival_time or _client_datetime(arrival_time) or captured_at or now,
		"confirmed_by": frappe.session.user,
		"client_request_id": request_id,
		"dropoff_gps_status": gps_status,
		"dropoff_latitude": _coord_text(d.get("latitude")) if gps_point else None,
		"dropoff_longitude": _coord_text(d.get("longitude")) if gps_point else None,
		"dropoff_accuracy_m": flt(d.get("accuracy_m")) if gps_point and d.get("accuracy_m") not in (None, "") else None,
		"dropoff_captured_at": captured_at,
		"target_coordinates": target_text,
		"dropoff_distance_from_target_m": distance,
		"original_dropoff_distance_m": distance,
		"is_on_site_dropoff": on_site,
	}
	if clean_notes:
		update["delivery_notes"] = clean_notes
	if customer and not stop.customer_id:
		update["customer_id"] = customer
	frappe.db.set_value(STOP, stop.name, update)

	if return_rows:
		next_idx = cint(frappe.db.sql(
			"SELECT MAX(idx) FROM `tabManifest Returned Item` WHERE parent = %s AND parenttype = %s",
			(m.name, MANIFEST),
		)[0][0])
		for code, ordered_qty, returned_qty in return_rows:
			next_idx += 1
			frappe.get_doc({
				"doctype": RETURN,
				"parent": m.name,
				"parenttype": MANIFEST,
				"parentfield": "returned_items",
				"idx": next_idx,
				"docstatus": 1,
				"stop_row": stop.name,
				"sales_order": stop.sales_order,
				"item_code": code,
				"ordered_qty": ordered_qty,
				"returned_qty": returned_qty,
				"reason": reason,
				"client_request_id": request_id,
			}).db_insert()

	invoice_checks = {}
	if collected_invoices:
		invoice_checks = _match_invoices(collected_invoices, customer)
		next_idx = cint(frappe.db.sql(
			"SELECT MAX(idx) FROM `tabManifest Return Collection` WHERE parent = %s AND parenttype = %s",
			(m.name, MANIFEST),
		)[0][0])
		for number in collected_invoices:
			next_idx += 1
			match, matched = invoice_checks[number]
			frappe.get_doc({
				"doctype": RETURN_COLLECTION,
				"parent": m.name,
				"parenttype": MANIFEST,
				"parentfield": "return_collections",
				"idx": next_idx,
				"docstatus": 1,
				"stop_row": stop.name,
				"sales_order": stop.sales_order,
				"customer": customer,
				"invoice_no": number,
				"invoice_match": match,
				"matched_invoice": matched,
				"back_office_status": "Pending",
				"recorded_by": frappe.session.user,
				"recorded_at": now,
				"client_request_id": request_id,
			}).db_insert()

	# --- Audit -----------------------------------------------------------
	where = (f"{distance:,.0f} m from the customer's pin ({'on site' if on_site else 'off site'})"
	         if distance is not None else
	         ("no GPS point" if gps_status == "Unavailable" else "customer has no pin"))
	lines = [
		f"📦 <b>{frappe.utils.escape_html(stop.customer or stop.sales_order or stop.name)}</b> "
		f"({frappe.utils.escape_html(stop.sales_order or '')}) marked <b>{delivery_status}</b> "
		f"by <b>{frappe.utils.escape_html(frappe.session.user)}</b> ({m.actor_role})",
		f"Drop-off: {where}" + (" — <b>mock location detected</b>" if gps_status == "Mock Detected" else ""),
	]
	if return_rows:
		lines.append(f"Returned: {', '.join(f'{c} × {q:g}' for c, _o, q in return_rows)} — {frappe.utils.escape_html(reason)}")
	if collected_invoices:
		lines.append("Collected returns from earlier invoices (back office to process): " + ", ".join(
			f"{frappe.utils.escape_html(n)} ({invoice_checks[n][0]})" for n in collected_invoices
		))
	if clean_notes:
		lines.append(f"Notes: {frappe.utils.escape_html(clean_notes)}")
	_add_manifest_comment(m.name, "<br>".join(lines))

	# --- Trip moves on when nothing is left ------------------------------
	remaining = frappe.db.count(STOP, {"parent": m.name, "parenttype": MANIFEST, "delivery_status": PENDING})
	if remaining == 0 and m.trip_status == "Dispatched":
		set_trip_state(m.name, "Returning", note="All stops confirmed.")
	else:
		push_manifest_refresh([m.name], reason="stop_confirmed")

	frappe.db.commit()
	return _stop_result(m.name, stop.name)


def _stop_result(manifest_name, stop_name, replayed=False):
	s = frappe.db.get_value(
		STOP, stop_name,
		["name", "delivery_status", "completion_time", "dropoff_gps_status",
		 "dropoff_distance_from_target_m", "is_on_site_dropoff"],
		as_dict=True,
	)
	trip_status = frappe.db.get_value(MANIFEST, manifest_name, "trip_status")
	distance = s.dropoff_distance_from_target_m
	if distance is not None:
		message = f"{s.delivery_status}. {flt(distance):,.0f} m from the customer's pin ({'on site' if cint(s.is_on_site_dropoff) else 'off site'})."
	else:
		message = f"{s.delivery_status}. Distance not recorded ({'no GPS' if s.dropoff_gps_status == 'Unavailable' else 'customer has no pin'})."
	if trip_status == "Returning":
		message += " All stops are done — head back to the yard."
	return {
		"status": "success",
		"message": message,
		"replayed": replayed,
		"trip_status": trip_status,
		"stop": {
			"name": s.name,
			"delivery_status": s.delivery_status,
			"completion_time": _s(s.completion_time),
			"gps_status": s.dropoff_gps_status,
			"distance_from_target_m": flt(distance) if distance is not None else None,
			"is_on_site": cint(s.is_on_site_dropoff),
		},
	}


@frappe.whitelist(methods=["POST"])
def confirm_stop_from_app(manifest, stop_row, delivery_status, returned_items=None, return_reason=None,
                          notes=None, dropoff=None, arrival_time=None, client_request_id=None, return_invoices=None):
	"""
	Driver app drop-off. dropoff = {latitude, longitude, accuracy_m,
	captured_at (epoch ms or ISO), is_mock}. Omit it (or send no point) when
	the phone had no GPS; the stop is still confirmed as "Unavailable".
	return_invoices = ["INV-…", …]: earlier invoices whose goods the customer
	handed back (recorded only; the back office processes them).
	"""
	return _run(lambda: _idempotent("confirm_stop", client_request_id, lambda rid: _confirm_stop(
		manifest, stop_row, delivery_status, returned_items, return_reason, notes, dropoff, arrival_time, rid,
		return_invoices=return_invoices,
	)), title="Nexus Stop Confirmation Failed")


@frappe.whitelist(methods=["POST"])
def confirm_stop_from_desk(manifest, stop_row, delivery_status, returned_items=None, return_reason=None,
                           notes=None, client_request_id=None, return_invoices=None):
	"""Office drop-off from the manifest form. No GPS, so 'Unavailable'."""
	return _run(lambda: _idempotent("confirm_stop", client_request_id, lambda rid: _confirm_stop(
		manifest, stop_row, delivery_status, returned_items, return_reason, notes, None, None, rid,
		return_invoices=return_invoices,
	)), title="Nexus Stop Confirmation Failed")


# ============================================================================
# Update a customer's location from a stop
# ============================================================================
def _update_stop_location(manifest, stop_row, latitude=None, longitude=None, combined=None, accuracy_m=None,
                          is_mock=0, method=None, google_maps_link=None, app_version=None, device_id=None,
                          request_id=None):
	from nexus_supply_chain.api import _add_party_location_comment, _write_party_coordinates
	from nexus_supply_chain.nexus_supply_chain.doctype.nexus_location_correction_log.nexus_location_correction_log import (
		record_location_correction,
	)

	m = _manifest_for_actor(manifest, lock=True)
	if m.trip_status not in ON_ROAD_STATES:
		_err("TRIP_NOT_ON_ROAD", "Locations can only be updated while the trip is on the road.", trip_status=m.trip_status)

	stop = frappe.db.get_value(
		STOP, {"name": stop_row, "parent": m.name, "parenttype": MANIFEST},
		["name", "sales_order", "customer_id", "customer", "delivery_status",
		 "dropoff_latitude", "dropoff_longitude", "dropoff_gps_status",
		 "dropoff_distance_from_target_m", "original_dropoff_distance_m"],
		as_dict=True, for_update=True,
	)
	if not stop:
		_err("STOP_NOT_FOUND", "This stop isn't on the manifest.")
	customer = stop.customer_id or frappe.db.get_value("Sales Order", stop.sales_order, "customer")
	if not customer:
		_err("NO_CUSTOMER", "This stop has no customer to update.")

	link = (google_maps_link or "").strip()
	if method not in NEXUS_LOCATION_SOURCES:
		method = "Maps Link" if (link and link != NEXUS_GPS_SNAP_LINK_LABEL) else "GPS Snap"
	if method == "GPS Snap" and cint(is_mock):
		_err("MOCK_LOCATION", "A mock-location app is active on this phone. Turn it off and try again.")
	if method == "GPS Snap" and not link:
		link = NEXUS_GPS_SNAP_LINK_LABEL

	written = _write_party_coordinates("Customer", customer, latitude, longitude, combined, link)
	if written.get("error"):
		_err("INVALID_COORDINATES", written["error"])
	new_text = written["coord_fields"]["custom_combined_coordinates"]
	new_point = written["point"]

	log_name = record_location_correction(
		party_type="Customer",
		party=customer,
		new_coordinates=new_text,
		old_coordinates=written.get("previous_text"),
		method=method,
		changed_by_role=m.actor_role,
		gps_accuracy_m=accuracy_m,
		is_mock_location=is_mock,
		google_maps_link=link if method == "Maps Link" else None,
		manifest=m.name,
		stop_row=stop.name,
		sales_order=stop.sales_order,
		device_id=device_id,
		app_version=app_version,
		client_request_id=request_id,
	)

	update = {
		"combined_coordinates": new_text,
		"latitude": new_point[0],
		"longitude": new_point[1],
		"location_updated_during_trip": 1,
		"location_correction_log": log_name,
	}
	recomputed = None
	dropoff_point = parse_combined_coords(None, stop.dropoff_latitude, stop.dropoff_longitude)
	if (stop.delivery_status or PENDING) != PENDING and dropoff_point:
		recomputed = round(haversine_meters(dropoff_point[0], dropoff_point[1], new_point[0], new_point[1]), 2)
		threshold = flt(get_nexus_settings().get("dropoff_on_site_threshold_m")) or 200.0
		update.update({
			"target_coordinates": new_text,
			"dropoff_distance_from_target_m": recomputed,
			"is_on_site_dropoff": 1 if (stop.dropoff_gps_status == "Captured" and recomputed <= threshold) else 0,
		})
		if stop.original_dropoff_distance_m is None and stop.dropoff_distance_from_target_m is not None:
			update["original_dropoff_distance_m"] = stop.dropoff_distance_from_target_m
	frappe.db.set_value(STOP, stop.name, update)

	extra_lines = [f"Trip: <b>{m.name}</b> · Stop: {frappe.utils.escape_html(stop.customer or stop.name)}"]
	if recomputed is not None:
		extra_lines.append(f"Drop-off distance recomputed: <b>{recomputed:,.0f} m</b>")
	_add_party_location_comment(
		"Customer", customer, frappe.session.user, "Nexus Driver App",
		written.get("previous_coords"), written["coord_fields"], method,
		link if method == "Maps Link" else None, extra_lines,
	)

	push_manifest_refresh([m.name], reason="location_updated")
	frappe.enqueue(
		"nexus_supply_chain.api._notify_party_location_changed",
		queue="short", enqueue_after_commit=True, party_type="Customer", name=customer,
	)
	frappe.db.commit()

	return {
		"status": "success",
		"message": "Customer location updated." + (f" Drop-off distance is now {recomputed:,.0f} m." if recomputed is not None else ""),
		"customer": customer,
		"pin": new_text,
		"lat": new_point[0],
		"lng": new_point[1],
		"previous_pin": written.get("previous_text"),
		"moved_m": frappe.db.get_value("Nexus Location Correction Log", log_name, "moved_distance_m"),
		"dropoff_distance_m": recomputed,
		"correction_log": log_name,
	}


@frappe.whitelist(methods=["POST"])
def update_stop_location_from_app(manifest, stop_row, latitude=None, longitude=None, custom_combined_coordinates=None,
                                  accuracy_m=None, is_mock=0, location_source=None, google_maps_link=None,
                                  app_version=None, device_id=None, client_request_id=None):
	"""'Update Location Database' on a stop. Writes the Customer's pin directly
	(text, shared builder), logs the change and refreshes every open stop."""
	return _run(lambda: _idempotent("stop_location", client_request_id, lambda rid: _update_stop_location(
		manifest, stop_row, latitude, longitude, custom_combined_coordinates, accuracy_m, is_mock,
		location_source, google_maps_link, app_version, device_id, rid,
	)), title="Nexus Stop Location Update Failed")


# ============================================================================
# Fuel
# ============================================================================
def _plan_baseline(manifest_row):
	"""(profit before road fuel, order value) from the source plan, or (None, None)."""
	if manifest_row.get("truck_plan"):
		cols = [c for c in ("narrowed_gross_profit", "total_order_value", "total_value")
		        if frappe.db.has_column("Crystal Truck Plan", c)]
		plan = frappe.db.get_value("Crystal Truck Plan", manifest_row.truck_plan, cols, as_dict=True) if cols else None
		if plan:
			return flt(plan.get("narrowed_gross_profit")), flt(plan.get("total_order_value") or plan.get("total_value"))
	if manifest_row.get("load_plan"):
		cols = [c for c in ("profit_loss", "total_amount") if frappe.db.has_column("Nexus Load Plan", c)]
		plan = frappe.db.get_value("Nexus Load Plan", manifest_row.load_plan, cols, as_dict=True) if cols else None
		if plan:
			return flt(plan.get("profit_loss")), flt(plan.get("total_amount"))
	return None, None


def recalculate_manifest_totals(manifest_name):
	"""Fuel total = sum of Fuel Logs. Net profit = plan profit - that total.
	Always recomputed from scratch, so it can never drift. No commit."""
	row = frappe.db.get_value(MANIFEST, manifest_name, ["name", "truck_plan", "load_plan"], as_dict=True)
	total = flt(frappe.db.sql(
		"SELECT SUM(amount) FROM `tabManifest Fuel Log` WHERE parent = %s AND parenttype = %s",
		(manifest_name, MANIFEST),
	)[0][0])
	update = {"cumulative_additional_fuel_cost": total}
	baseline, order_value = _plan_baseline(row)
	if baseline is not None:
		profit = baseline - total
		update.update({
			"profit_loss": profit,
			"net_margin": (profit / order_value * 100.0) if order_value else 0.0,
			"profitability_status": "Profitable" if profit >= 0 else "Loss",
		})
	frappe.db.set_value(MANIFEST, manifest_name, update, update_modified=False)
	return update


def _log_fuel(manifest, amount, litres=None, odometer_km=None, station=None, latitude=None, longitude=None,
              accuracy_m=None, notes=None, receipt=None, fuelled_at=None, request_id=None):
	m = _manifest_for_actor(manifest, lock=True)
	if m.trip_status not in ACTIVE_TRIP_STATES:
		_err("TRIP_NOT_ACTIVE", f"Fuel can only be logged on an active trip (this one is {m.trip_status}).")

	value = flt(amount)
	if value <= 0:
		_err("INVALID_AMOUNT", "Enter an amount greater than zero.", field="amount")
	if value > MAX_FUEL_AMOUNT:
		_err("INVALID_AMOUNT", "That amount is too large. Check it and try again.", field="amount")

	if request_id:
		existing = frappe.db.get_value(FUEL, {"parent": m.name, "parenttype": MANIFEST, "client_request_id": request_id}, "name")
		if existing:
			total = frappe.db.get_value(MANIFEST, m.name, "cumulative_additional_fuel_cost")
			return {"status": "success", "message": "Fuel already logged.", "fuel_log": existing,
			        "new_cumulative_total": flt(total), "replayed": True}

	receipt = (receipt or "").strip() or None
	if receipt and not receipt.startswith(("/files/", "/private/files/")):
		_err("INVALID_RECEIPT", "The receipt must be a file uploaded to ERPNext.")

	fields, point = build_coordinate_fields(latitude, longitude)
	next_idx = cint(frappe.db.sql(
		"SELECT MAX(idx) FROM `tabManifest Fuel Log` WHERE parent = %s AND parenttype = %s", (m.name, MANIFEST),
	)[0][0]) + 1
	log = frappe.get_doc({
		"doctype": FUEL,
		"parent": m.name,
		"parenttype": MANIFEST,
		"parentfield": "fuel_logs",
		"idx": next_idx,
		"docstatus": 1,
		"fuelled_at": _client_datetime(fuelled_at) or now_datetime(),
		"amount": value,
		"litres": flt(litres) if litres not in (None, "") else None,
		"odometer_km": flt(odometer_km) if odometer_km not in (None, "") else None,
		"station": (station or "").strip() or None,
		"combined_coordinates": fields["custom_combined_coordinates"] if fields else None,
		"latitude": fields["custom_latitude"] if fields else None,
		"longitude": fields["custom_longitude"] if fields else None,
		"gps_accuracy_m": flt(accuracy_m) if (fields and accuracy_m not in (None, "")) else None,
		"receipt": receipt,
		"notes": (notes or "").strip() or None,
		"recorded_by": frappe.session.user,
		"client_request_id": request_id,
	})
	log.db_insert()

	totals = recalculate_manifest_totals(m.name)
	_add_manifest_comment(
		m.name,
		f"⛽ Fuel logged: <b>{value:,.2f}</b> by <b>{frappe.utils.escape_html(frappe.session.user)}</b>. "
		f"Trip fuel total: <b>{flt(totals['cumulative_additional_fuel_cost']):,.2f}</b>",
	)
	push_manifest_refresh([m.name], reason="fuel_logged")
	frappe.db.commit()

	return {
		"status": "success",
		"message": "Fuel logged.",
		"fuel_log": log.name,
		"new_cumulative_total": flt(totals["cumulative_additional_fuel_cost"]),
	}


@frappe.whitelist(methods=["POST"])
def log_fuel_from_app(manifest, amount, litres=None, odometer_km=None, station=None, latitude=None,
                      longitude=None, accuracy_m=None, notes=None, receipt=None, fuelled_at=None,
                      client_request_id=None):
	return _run(lambda: _idempotent("fuel", client_request_id, lambda rid: _log_fuel(
		manifest, amount, litres, odometer_km, station, latitude, longitude, accuracy_m, notes, receipt,
		fuelled_at, rid,
	)), title="Nexus Fuel Logging Failed")


# ============================================================================
# Trip actions
# ============================================================================
@frappe.whitelist(methods=["POST"])
def start_trip(manifest, client_request_id=None):
	return _trip_action(manifest, "Dispatched", ("Ready",), client_request_id, "trip_start")


@frappe.whitelist(methods=["POST"])
def start_return(manifest, client_request_id=None):
	return _trip_action(manifest, "Returning", ("Dispatched",), client_request_id, "trip_return")


@frappe.whitelist(methods=["POST"])
def complete_trip(manifest, client_request_id=None):
	return _trip_action(manifest, "Completed", ON_ROAD_STATES, client_request_id, "trip_complete")


@frappe.whitelist(methods=["POST"])
def set_trip_status_from_desk(manifest, status, client_request_id=None):
	"""Office override buttons on the manifest form."""
	routes = {"Dispatched": start_trip, "Returning": start_return, "Completed": complete_trip}
	if status not in routes:
		return {"status": "error", "code": "INVALID_STATUS",
		        "message": "Use Cancel on the manifest to cancel a trip."}
	check = _run(lambda: _require_office() or {"status": "success"})
	if check.get("status") != "success":
		return check
	return routes[status](manifest, client_request_id)


# ============================================================================
# FastAPI-facing reads (shared secret, or office users for debugging)
# ============================================================================
def _fleet_rows(driver=None):
	params = {"driver": driver or ""}
	return frappe.db.sql("""
		SELECT v.name AS vehicle, v.vehicle_plate, v.current_status AS vehicle_status,
		       v.current_driver AS driver, u.full_name AS driver_name,
		       (SELECT m.name FROM `tabVehicle Delivery Manifest` m
		        WHERE m.vehicle = v.name AND m.docstatus = 1
		          AND m.trip_status IN ('Ready', 'Dispatched', 'Returning')
		        ORDER BY FIELD(m.trip_status, 'Dispatched', 'Returning', 'Ready'), m.modified DESC
		        LIMIT 1) AS manifest
		FROM `tabVehicle In Transit` v
		LEFT JOIN `tabUser` u ON u.name = v.current_driver
		WHERE IFNULL(v.current_driver, '') != ''
		  AND (%(driver)s = '' OR v.current_driver = %(driver)s)
		ORDER BY v.name
	""", params, as_dict=True)


def _with_trip_status(rows):
	names = tuple({r.manifest for r in rows if r.manifest})
	statuses = {}
	if names:
		statuses = dict(frappe.db.sql(
			"SELECT name, trip_status FROM `tabVehicle Delivery Manifest` WHERE name IN %s", (names,)
		))
	return [{
		"vehicle": r.vehicle,
		"vehicle_plate": r.vehicle_plate,
		"vehicle_status": r.vehicle_status,
		"driver": (r.driver or "").lower() or None,
		"driver_name": r.driver_name or r.driver,
		"active_manifest_id": r.manifest or "No_Active_Manifest",
		"trip_status": statuses.get(r.manifest),
	} for r in rows]


@frappe.whitelist(allow_guest=True)
def get_fleet_context():
	"""Every vehicle with a driver and its current trip. FastAPI binds each
	device's pings to this (device -> driver -> vehicle -> manifest)."""
	_require_internal_or_office()
	return {"status": "success", "fleet": _with_trip_status(_fleet_rows())}


@frappe.whitelist(allow_guest=True)
def get_driver_device_context(driver):
	"""Same as get_fleet_context, for one driver (when a device signs in)."""
	_require_internal_or_office()
	rows = _with_trip_status(_fleet_rows(driver))
	if rows:
		return {"status": "success", **rows[0]}
	return {"status": "success", "driver": (driver or "").lower(), "vehicle": "Idle",
	        "active_manifest_id": "No_Active_Manifest", "trip_status": None}


@frappe.whitelist(allow_guest=True)
def get_tracking_settings():
	"""Thresholds FastAPI uses for Live / On Stand-by / Offline."""
	_require_internal_or_office()
	s = get_nexus_settings()
	return {
		"status": "success",
		"standby_threshold_seconds": cint(s.get("standby_threshold_seconds")),
		"offline_threshold_minutes": cint(s.get("offline_threshold_minutes")),
		"dropoff_on_site_threshold_m": flt(s.get("dropoff_on_site_threshold_m")),
	}


# ============================================================================
# Dispatch WebSocket ticket (desk pages -> FastAPI /api/v2/fleet/ws)
# ============================================================================
@frappe.whitelist()
def get_dispatch_ws_ticket():
	"""
	A 10-minute ticket for the live dispatch socket. Scopes follow the
	user's own permissions, so no role names are hardcoded:
	  fleet  read Vehicle Delivery Manifest  (Live Dispatch)
	  sales  read Nexus Sales Visit          (Sales Dispatch)
	The page asks for a new ticket each time it (re)connects.
	"""
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)

	scopes = []
	if frappe.has_permission(MANIFEST, "read"):
		scopes.append("fleet")
	if frappe.has_permission("Nexus Sales Visit", "read"):
		scopes.append("sales")
	if not scopes:
		frappe.throw(_("You don't have access to the dispatch maps."), frappe.PermissionError)

	ticket = make_dispatch_ticket(user, scopes)
	if not ticket:
		frappe.throw(_("crystal_api_internal_secret is not set in site_config.json."))

	return {
		"status": "success",
		"ticket": ticket,
		"scopes": scopes,
		"ws_url": fastapi_ws_url("/api/v2/fleet/ws"),
		"api_url": fastapi_url(),
		"expires_in": DISPATCH_TICKET_TTL_SEC,
	}


# ============================================================================
# Driver app error reports (app 1.1.0+ -> Error Log)
# ============================================================================
APP_ERROR_MAX_BATCH = 20          # reports per call
APP_ERROR_RATE_PER_HOUR = 60      # reports per driver per hour (beyond: dropped, counted)
APP_ERROR_DEDUPE_SEC = 600        # the same error from the same driver: once per 10 min
APP_ERROR_TEXT = {"name": 80, "message": 500, "stack": 8000, "component_stack": 4000, "screen": 80,
                  "app_version": 32, "device_model": 64, "device_id": 64, "js_engine": 32, "source": 32}


def _clean_text(value, limit):
	text = "" if value is None else str(value)
	text = text.replace("\x00", "")
	return text[:limit]


@frappe.whitelist(methods=["POST"])
def report_app_errors(errors=None):
	"""
	The driver app sends the errors it caught (JS crashes, unhandled promise
	rejections, render errors) in small batches. Each becomes one Error Log
	titled "Driver app: <name>: <message>" with the driver, app version,
	device, Android version and screen, so problems on drivers' phones are
	visible in the desk without anyone collecting a phone.

	errors = [{name, message, stack, component_stack, screen, fatal, count,
	           first_at, last_at, app_version, version_code, device_model,
	           sdk_int, device_id, js_engine, source, context}]
	Plain text only: nothing in a report is ever executed or rendered as HTML.
	Returns {status, accepted, duplicates, dropped}.
	"""
	def work():
		import hashlib

		user = frappe.session.user
		items = _parse_json(errors, [])
		if isinstance(items, dict):
			items = [items]
		if not isinstance(items, list):
			_err("INVALID", "errors must be a list.")
		items = [i for i in items if isinstance(i, dict)][:APP_ERROR_MAX_BATCH]

		cache = frappe.cache()
		rate_key = f"nexus:apperr:rate:{user}"
		used = cint(cache.get_value(rate_key))
		room = max(0, APP_ERROR_RATE_PER_HOUR - used)

		accepted = duplicates = dropped = 0
		for item in items:
			clean = {k: _clean_text(item.get(k), n) for k, n in APP_ERROR_TEXT.items()}
			name = clean["name"] or "Error"
			message = clean["message"] or "(no message)"
			first_frame = next((ln.strip() for ln in clean["stack"].splitlines() if ln.strip() and ln.strip() != message), "")
			fingerprint = hashlib.sha1(f"{name}|{message[:200]}|{first_frame[:200]}".encode()).hexdigest()[:16]
			seen_key = f"nexus:apperr:seen:{user}:{fingerprint}"
			if cache.get_value(seen_key):
				duplicates += 1
				continue
			if room <= 0:
				dropped += 1
				continue
			room -= 1
			cache.set_value(seen_key, 1, expires_in_sec=APP_ERROR_DEDUPE_SEC)

			context = item.get("context")
			try:
				context_text = json.dumps(context, default=str)[:2000] if context else ""
			except Exception:
				context_text = ""
			lines = [
				f"Driver: {user}",
				f"App: {clean['app_version'] or '?'} (code {cint(item.get('version_code')) or '?'}) · {clean['js_engine'] or 'JS'}",
				f"Device: {clean['device_model'] or '?'} · Android SDK {cint(item.get('sdk_int')) or '?'} · id {clean['device_id'] or '?'}",
				f"Screen: {clean['screen'] or '?'} · Source: {clean['source'] or '?'} · Fatal: {'yes' if item.get('fatal') else 'no'}",
				f"Seen {max(1, cint(item.get('count')))} time(s) on the phone · first {_clean_text(item.get('first_at'), 40)} · last {_clean_text(item.get('last_at'), 40)}",
				f"Fingerprint: {fingerprint}",
				"",
				f"{name}: {message}",
				"",
				clean["stack"] or "(no stack)",
			]
			if clean["component_stack"]:
				lines += ["", "Component stack:", clean["component_stack"]]
			if context_text:
				lines += ["", "Context:", context_text]
			frappe.log_error(title=f"Driver app: {name}: {message}"[:140], message="\n".join(lines))
			accepted += 1

		cache.set_value(rate_key, used + accepted, expires_in_sec=3600)
		return {"status": "success", "accepted": accepted, "duplicates": duplicates, "dropped": dropped}

	return _run(work, title="Nexus Driver App Error Intake Failed")


# ============================================================================
# Doc hooks
# ============================================================================
def on_customer_update(doc, method=None):
	"""Customer on_update: a pin typed in the desk reaches open stops and
	the drivers' apps. (App and geocoder writes call the same helper directly,
	since they use db.set_value and fire no hooks.)"""
	try:
		if not any(doc.meta.has_field(f) and doc.has_value_changed(f) for f in NEXUS_COORD_FIELDS):
			return
		text, point = _pin_from_party_row(doc)
		if not text:
			return
		affected = refresh_customer_stop_pins(doc.name, text, point)
		if affected:
			push_manifest_refresh(affected, reason="customer_pin_changed")
	except Exception:
		frappe.log_error(title="Nexus Stop Pin Refresh Failed", message=frappe.get_traceback())
