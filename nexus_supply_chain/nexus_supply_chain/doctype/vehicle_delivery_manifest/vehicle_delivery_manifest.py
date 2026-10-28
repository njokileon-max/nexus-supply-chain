# apps/nexus_supply_chain/nexus_supply_chain/doctype/vehicle_delivery_manifest/vehicle_delivery_manifest.py
"""
Vehicle Delivery Manifest controller.

Trip status is never set here directly: every change goes through
nexus_supply_chain.driver_api.set_trip_state() (submit -> Ready,
cancel -> Cancelled, and every driver/desk action). A manual edit of
trip_status is silently reverted.

Removed in 1.1.0:
  * the dead 127.0.0.1:8001/calculate-route call on submit. The route
    GeoJSON comes from the Truck Plan / Load Plan (built by the Dispatch flow).
  * automatic invoicing on drop-off (process_financials_in_background).
    It returns as a settings-controlled feature in the final batch.

The old whitelisted functions at the bottom are kept as thin wrappers so
any old caller still works, through the same checked code path.
"""

import frappe
from frappe import _
from frappe.desk.form.assign_to import add as add_assignment
from frappe.model.document import Document

TRUCK_PLAN = "Crystal Truck Plan"
LOAD_PLAN = "Nexus Load Plan"
VEHICLE = "Vehicle In Transit"


class VehicleDeliveryManifest(Document):
	# ------------------------------------------------------------------ draft
	def validate(self):
		self._guard_trip_status()
		if not self.trip_status:
			self.trip_status = "Manifested"
		self._copy_load_plan_route()
		self._hydrate_stops()
		self._check_vehicle_available()

	def before_submit(self):
		if not self.vehicle:
			frappe.throw(_("Select the vehicle before submitting."), title=_("Vehicle Required"))
		driver = frappe.db.get_value(VEHICLE, self.vehicle, "current_driver")
		if not driver:
			frappe.throw(
				_("Vehicle {0} has no current driver. Assign a driver on the vehicle first.").format(self.vehicle),
				title=_("Driver Required"),
			)
		self.driver = driver
		if not self.stops:
			frappe.throw(_("Add at least one stop before submitting."), title=_("No Stops"))

		missing = [s.customer or s.sales_order for s in self.stops if not (s.combined_coordinates or "").strip()]
		if missing:
			frappe.msgprint(
				_("These stops have no location yet; the driver can set it on site: {0}").format(", ".join(missing[:10])),
				indicator="orange",
				title=_("Stops Without a Location"),
			)

	def on_update(self):
		self.auto_assign_driver()

	# --------------------------------------------------------------- submit
	def on_submit(self):
		from nexus_supply_chain.driver_api import recalculate_manifest_totals, set_trip_state

		self._copy_plan_financials()
		recalculate_manifest_totals(self.name)
		set_trip_state(self.name, "Ready", force=True)
		self.trip_status = "Ready"
		self.notify_driver("New Dispatch Ready", f"Manifest {self.name} is ready for loading.")

	def before_update_after_submit(self):
		self._guard_trip_status()
		if self.has_value_changed("vehicle"):
			if not self.vehicle:
				frappe.throw(_("A submitted manifest must keep a vehicle."))
			trip_status = frappe.db.get_value(self.doctype, self.name, "trip_status")
			if trip_status not in ("Ready",):
				frappe.throw(_("The vehicle can only be changed before the trip starts."))
			if not frappe.db.get_value(VEHICLE, self.vehicle, "current_driver"):
				frappe.throw(_("Vehicle {0} has no current driver.").format(self.vehicle))

	def on_update_after_submit(self):
		from nexus_supply_chain.driver_api import push_manifest_refresh

		if self.has_value_changed("vehicle"):
			new_driver = frappe.db.get_value(VEHICLE, self.vehicle, "current_driver")
			if new_driver and new_driver != self.driver:
				self.db_set("driver", new_driver)
				self.driver = new_driver
				self.auto_assign_driver()
				self.notify_driver("Route Reassigned", f"Manifest {self.name} has been assigned to you.")
		push_manifest_refresh([self.name], reason="manifest_edited")

	def on_cancel(self):
		from nexus_supply_chain.driver_api import set_trip_state

		set_trip_state(self.name, "Cancelled", force=True)
		self.trip_status = "Cancelled"

	# --------------------------------------------------------------- helpers
	def _guard_trip_status(self):
		"""trip_status is server-owned: an edit from the form or REST is undone."""
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before and (self.trip_status or "") != (before.trip_status or ""):
			self.trip_status = before.trip_status

	def _copy_load_plan_route(self):
		if not self.load_plan:
			return
		if not self.route_geojson:
			self.route_geojson = frappe.db.get_value(LOAD_PLAN, self.load_plan, "route_geojson")

		from nexus_supply_chain.api import build_coordinate_fields

		lp_rows = frappe.get_all(
			"Nexus Load Plan Sales Order",
			filters={"parent": self.load_plan},
			fields=["sales_order", "latitude", "longitude"],
		)
		pins = {}
		for r in lp_rows:
			fields, point = build_coordinate_fields(r.latitude, r.longitude)
			if fields:
				pins[r.sales_order] = (fields["custom_combined_coordinates"], point)

		for stop in self.stops or []:
			if (stop.combined_coordinates or "").strip():
				continue
			pin = pins.get(stop.sales_order)
			if pin:
				stop.combined_coordinates, (stop.latitude, stop.longitude) = pin[0], pin[1]

	def _hydrate_stops(self):
		from nexus_supply_chain.driver_api import hydrate_stop_row

		for stop in self.stops or []:
			if not stop.customer_id or not (stop.combined_coordinates or "").strip() or not stop.customer_phone:
				hydrate_stop_row(stop)
			if not stop.delivery_status:
				stop.delivery_status = "Pending"

	def _check_vehicle_available(self):
		if not self.vehicle or self.docstatus != 0:
			return
		status = frappe.db.get_value(VEHICLE, self.vehicle, "current_status")
		if status == "Idle":
			frappe.db.set_value(VEHICLE, self.vehicle, "current_status", "Loading", update_modified=False)
		elif status not in ("Idle", "Loading"):
			frappe.throw(
				_("Vehicle {0} is currently '{1}' and can't be put on a new manifest.").format(self.vehicle, status)
			)

	def _copy_plan_financials(self):
		if self.load_plan:
			plan = frappe.get_doc(LOAD_PLAN, self.load_plan)
			values = {
				"profit_loss": plan.get("profit_loss"),
				"gross_margin": plan.get("margin_percentage"),
				"net_margin": plan.get("net_margin"),
				"approximate_total_distance_km": plan.get("approximate_total_distance_km"),
				"approximate_fuel_consumption_ltrs": plan.get("approximate_fuel_consumption_ltrs"),
				"approximate_fuel_cost": plan.get("approximate_fuel_cost"),
				"actual_fuel_fuelled": plan.get("actual_fuel_fuelled"),
				"profitability_status": plan.get("profitability_status"),
			}
		elif self.truck_plan:
			plan = frappe.get_doc(TRUCK_PLAN, self.truck_plan)
			values = {
				"profit_loss": plan.get("narrowed_gross_profit"),
				"gross_margin": plan.get("narrowed_gross_margin_percentage"),
				"approximate_total_distance_km": plan.get("approximate_total_distance_km"),
				"approximate_fuel_consumption_ltrs": plan.get("approximate_fuel_consumption_ltrs"),
				"approximate_fuel_cost": plan.get("approximate_fuel_cost"),
				"actual_fuel_fuelled": plan.get("actual_fuel_fuelled"),
				"profitability_status": plan.get("profitability_status"),
			}
			if not self.route_geojson and plan.get("route_geojson"):
				values["route_geojson"] = plan.get("route_geojson")
		else:
			return
		self.db_set({k: v for k, v in values.items() if v is not None}, update_modified=False)

	def auto_assign_driver(self):
		if not self.driver:
			return
		already = frappe.db.exists("ToDo", {
			"reference_type": self.doctype,
			"reference_name": self.name,
			"allocated_to": self.driver,
			"status": "Open",
		})
		if not already:
			add_assignment({
				"assign_to": [self.driver],
				"doctype": self.doctype,
				"name": self.name,
				"description": f"New Delivery Assignment: {self.name}",
			})

	def notify_driver(self, title, message):
		if not self.driver:
			return
		devices = frappe.get_all("Nexus FCM Device", filters={"user": self.driver}, fields=["fcm_token"])
		if not devices:
			frappe.log_error("FCM Push Skipped", f"No registered devices found for driver: {self.driver}")
			return
		for device in devices:
			if device.fcm_token:
				frappe.enqueue(
					"nexus_supply_chain.nexus_supply_chain.doctype.vehicle_delivery_manifest.vehicle_delivery_manifest.send_fcm_push",
					queue="short",
					enqueue_after_commit=True,
					token=device.fcm_token,
					title=title,
					body=message,
					data_payload={"type": "new_manifest", "command": "REFRESH_MANIFESTS", "manifest_id": self.name},
				)


def send_fcm_push(token, title, body, data_payload):
	"""HTTP v1 push to one device, using the service-account key in the
	site's private folder. Google libraries are imported here so the
	controller still loads on a site without them."""
	import google.auth.transport.requests
	import requests
	from google.oauth2 import service_account

	try:
		key_path = f"{frappe.get_site_path()}/private/nexus_firebase_key.json"
		try:
			credentials = service_account.Credentials.from_service_account_file(
				key_path, scopes=["https://www.googleapis.com/auth/firebase.messaging"]
			)
		except FileNotFoundError:
			frappe.log_error("FCM Push Aborted", f"Missing secure key file at: {key_path}")
			return

		credentials.refresh(google.auth.transport.requests.Request())
		response = requests.post(
			f"https://fcm.googleapis.com/v1/projects/{credentials.project_id}/messages:send",
			headers={"Authorization": f"Bearer {credentials.token}", "Content-Type": "application/json; UTF-8"},
			json={"message": {
				"token": token,
				"notification": {"title": title, "body": body},
				"data": {k: str(v) for k, v in (data_payload or {}).items()},
			}},
			timeout=10,
		)
		if response.status_code != 200:
			frappe.log_error(title="FCM Push Error", message=f"Status: {response.status_code}\nResponse: {response.text}")
	except Exception as e:
		frappe.log_error(title="FCM Push System Failure", message=str(e))


# ============================================================================
# LEGACY WHITELISTED ENTRY POINTS — kept so old callers keep working.
# Each one now runs through driver_api (authorisation, locking, validation,
# no invoicing). New code calls driver_api directly.
# ============================================================================
@frappe.whitelist()
def start_trip_telemetry(manifest_name):
	from nexus_supply_chain.driver_api import start_trip

	return start_trip(manifest_name)


@frappe.whitelist()
def get_sales_order_items(sales_order):
	if not frappe.has_permission("Sales Order", "read", sales_order):
		frappe.throw(_("Not permitted."), frappe.PermissionError)
	return frappe.get_all(
		"Sales Order Item", filters={"parent": sales_order}, fields=["item_code", "item_name", "qty"], order_by="idx"
	)


@frappe.whitelist()
def confirm_stop_delivery(manifest_name, row_id, delivery_status, return_reason=None, returned_items_json=None, notes=None):
	from nexus_supply_chain.driver_api import confirm_stop_from_desk

	return confirm_stop_from_desk(manifest_name, row_id, delivery_status, returned_items_json, return_reason, notes)


@frappe.whitelist()
def confirm_arrival_at_yard(manifest_name):
	from nexus_supply_chain.driver_api import complete_trip

	return complete_trip(manifest_name)
