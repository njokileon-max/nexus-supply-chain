# apps/nexus_supply_chain/nexus_supply_chain/doctype/vehicle_delivery_manifest/vehicle_delivery_manifest.py

import frappe
import json
import requests
import google.auth.transport.requests
from google.oauth2 import service_account
from frappe.model.document import Document
from frappe.utils import now_datetime, flt
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe.desk.form.assign_to import add as add_assignment

API_URL = "http://nexus-brain:8001"  

class VehicleDeliveryManifest(Document):
    
    def validate(self):
        if self.load_plan and not self.route_geojson:
            self.route_geojson = frappe.db.get_value("Nexus Load Plan", self.load_plan, "route_geojson")

        if self.load_plan and hasattr(self, 'stops') and self.stops:
            lp_stops = frappe.get_all(
                "Nexus Load Plan Sales Order", 
                filters={"parent": self.load_plan}, 
                fields=["sales_order", "latitude", "longitude"]
            )
            
            coord_map = {d.sales_order: {"lat": flt(d.latitude), "lng": flt(d.longitude)} for d in lp_stops}

            for stop in self.stops:
                if not stop.latitude or not stop.longitude:
                    mapped_coords = coord_map.get(stop.sales_order)
                    if mapped_coords:
                        stop.latitude = mapped_coords["lat"]
                        stop.longitude = mapped_coords["lng"]

        if self.vehicle and self.docstatus == 0:
            current_v_status = frappe.db.get_value("Vehicle In Transit", self.vehicle, "current_status")
            
            if current_v_status == "Idle":
                frappe.db.set_value("Vehicle In Transit", self.vehicle, "current_status", "Loading")
            elif current_v_status not in ["Idle", "Loading"]:
                frappe.throw(f"The selected vehicle ({self.vehicle}) is currently '{current_v_status}' and cannot be assigned to a new Manifest.")

    def on_update(self):
        self.auto_assign_driver()
        
        # 🚨 FCM PUSH TRIGGER: Re-assignments
        if self.docstatus == 1 and self.has_value_changed("driver") and self.trip_status in ["Ready", "Dispatched"]:
            self.notify_driver("Route Reassigned", f"Manifest {self.name} has been assigned to you.")

    def on_submit(self):
        try:
            company_name = None
            if self.load_plan:
                company_name = frappe.db.get_value("Nexus Load Plan", self.load_plan, "company")
            if not company_name:
                company_name = frappe.defaults.get_user_default("Company")

            factory_lat = flt(frappe.db.get_value("Company", company_name, "custom_latitude"))
            factory_lng = flt(frappe.db.get_value("Company", company_name, "custom_longitude"))

            if not factory_lat or not factory_lng:
                frappe.throw(
                    title="Routing Configuration Error",
                    msg=f"Missing GPS Coordinates for Company '{company_name}'.\n"
                         "Please set 'custom_latitude' and 'custom_longitude' in the Company settings."
                )

            FACTORY_COORDS = [factory_lng, factory_lat]
            coords = [FACTORY_COORDS] 
            
            for stop in self.stops:
                if stop.longitude and stop.latitude:
                    coords.append([flt(stop.longitude), flt(stop.latitude)])
            
            coords.append(FACTORY_COORDS) 
            
            if len(coords) >= 3: 
                response = requests.post(
                    f"{API_URL}/calculate-route",
                    json={"coordinates": coords},
                    timeout=10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if "features" in data:
                        self.db_set("route_geojson", json.dumps(data))
        except Exception as e:
            frappe.log_error(title="Submit Route Generation Failed", message=str(e))

        self.db_set("trip_status", "Ready")
        
        if self.load_plan:
            frappe.db.set_value("Nexus Load Plan", self.load_plan, "dispatch_status", "Manifested")

        # 🚨 FCM PUSH TRIGGER: Initial Dispatch Ready
        self.notify_driver("New Dispatch Ready", f"Manifest {self.name} is ready for departure.")

    def on_cancel(self):
        self.db_set("trip_status", "Cancelled")
        
        if self.vehicle:
            frappe.db.set_value("Vehicle In Transit", self.vehicle, "current_status", "Idle")

    def auto_assign_driver(self):
        if self.driver:
            already_assigned = frappe.db.exists("ToDo", {
                "reference_type": self.doctype,
                "reference_name": self.name,
                "allocated_to": self.driver,
                "status": "Open"
            })
            
            if not already_assigned:
                add_assignment({
                    "assign_to": [self.driver],
                    "doctype": self.doctype,
                    "name": self.name,
                    "description": f"New Delivery Assignment: {self.name}"
                })

    def notify_driver(self, title, message):
        """Fetches the driver's FCM tokens from the custom table and enqueues the push notifications"""
        if self.driver:
            # Supports multi-device by fetching all tokens registered to this user
            devices = frappe.get_all(
                "Nexus FCM Device", 
                filters={"user": self.driver}, 
                fields=["fcm_token"]
            )
            
            if not devices:
                frappe.log_error("FCM Push Skipped", f"No registered devices found for driver: {self.driver}")
                return

            for device in devices:
                if device.fcm_token:
                    frappe.enqueue(
                        "nexus_supply_chain.nexus_supply_chain.doctype.vehicle_delivery_manifest.vehicle_delivery_manifest.send_fcm_push",
                        token=device.fcm_token,
                        title=title,
                        body=message,
                        data_payload={"type": "new_manifest", "manifest_id": self.name},
                        queue="short"
                    )

# =========================================================================
# BACKGROUND WORKERS & UTILITIES
# =========================================================================

def send_fcm_push(token, title, body, data_payload):
    """
    Executes the HTTP v1 request to Google Firebase to wake up the driver's phone.
    Uses OAuth2 Service Account credentials securely stored in the site's private folder.
    """
    try:
        site_path = frappe.get_site_path()
        key_path = f"{site_path}/private/nexus_firebase_key.json"
        
        try:
            credentials = service_account.Credentials.from_service_account_file(
                key_path,
                scopes=['https://www.googleapis.com/auth/firebase.messaging']
            )
        except FileNotFoundError:
            frappe.log_error("FCM Push Aborted", f"Missing secure key file at: {key_path}")
            return
            
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        access_token = credentials.token
        
        project_id = credentials.project_id
        url = f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; UTF-8"
        }
        
        payload = {
            "message": {
                "token": token,
                "notification": {
                    "title": title,
                    "body": body
                },
                "data": data_payload
            }
        }
        
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code != 200:
            frappe.log_error(title="FCM Push Error", message=f"Status: {response.status_code}\nResponse: {response.text}")
            
    except Exception as e:
        frappe.log_error(title="FCM Push System Failure", message=str(e))

def process_financials_in_background(sales_order):
    try:
        if frappe.db.exists("Sales Invoice Item", {"sales_order": sales_order, "docstatus": 1}):
            return
            
        si_doc = make_sales_invoice(sales_order)
        si_doc.set_posting_time = 1
        si_doc.posting_date = frappe.utils.today()
        si_doc.insert(ignore_permissions=True)
        si_doc.submit()
        
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        pe_doc = get_payment_entry("Sales Invoice", si_doc.name)
        pe_doc.reference_no = f"AUTO-{sales_order}"
        pe_doc.reference_date = frappe.utils.today()
        pe_doc.insert(ignore_permissions=True)

        frappe.db.commit()

    except Exception as e:
        frappe.log_error(message=str(e), title=f"Auto-Invoice Failed for {sales_order}")

# =========================================================================
# WHITELISTED API METHODS
# =========================================================================

@frappe.whitelist()
def start_trip_telemetry(manifest_name):
    doc = frappe.get_doc("Vehicle Delivery Manifest", manifest_name)
    doc.db_set("trip_status", "Dispatched")
    
    if doc.vehicle:
        frappe.db.set_value("Vehicle In Transit", doc.vehicle, "current_status", "En Route")
    
    if doc.load_plan:
        frappe.db.set_value("Nexus Load Plan", doc.load_plan, "dispatch_status", "Dispatched")
        
    return {"status": "success", "message": "Departure recorded. Telemetry active."}


@frappe.whitelist()
def get_sales_order_items(sales_order):
    items = frappe.get_all("Sales Order Item", filters={"parent": sales_order}, 
                           fields=["item_code", "item_name", "qty"])
    return items


@frappe.whitelist()
def confirm_stop_delivery(manifest_name, row_id, delivery_status, return_reason=None, returned_items_json=None, notes=None):
    readable_returns = ""
    if returned_items_json:
        try:
            parsed = json.loads(returned_items_json)
            readable_returns = " | ".join([f"Item: {item['item_code']} (Returned: {item['qty_returned']}/{item.get('ordered_qty', '?')})" for item in parsed])
        except Exception:
            readable_returns = returned_items_json

    frappe.db.set_value("In Transit-Delivery Stop", row_id, {
        "delivery_status": delivery_status,
        "return_reason": return_reason,
        "returned_items_json": readable_returns,
        "completion_time": now_datetime(),
        "delivery_notes": notes
    })
    
    manifest = frappe.get_doc("Vehicle Delivery Manifest", manifest_name)
    
    target_sales_order = None
    for row in manifest.stops:
        if row.name == row_id:
            target_sales_order = row.sales_order
            break
            
    if delivery_status == "Partially Delivered" and returned_items_json and target_sales_order:
        try:
            parsed_items = json.loads(returned_items_json)
            for item in parsed_items:
                child = frappe.new_doc("Manifest Returned Item")
                child.parent = manifest.name
                child.parenttype = "Vehicle Delivery Manifest"
                child.parentfield = "returned_items" 
                
                child.sales_order = target_sales_order
                child.item_code = item.get("item_code")
                child.ordered_qty = flt(item.get("ordered_qty"))
                child.returned_qty = flt(item.get("qty_returned"))
                child.reason = return_reason
                
                child.docstatus = 1 
                child.db_insert()
                
        except Exception as e:
            frappe.log_error(title="Failed to Parse Returns", message=str(e))
            
    pending_stops = [s for s in manifest.stops if s.delivery_status == "Pending"]
    message_tail = ""
    
    if len(pending_stops) == 0:
        manifest.db_set("trip_status", "Completed")
        
        if manifest.vehicle:
            frappe.db.set_value("Vehicle In Transit", manifest.vehicle, "current_status", "Returning")
            
        if manifest.load_plan:
            frappe.db.set_value("Nexus Load Plan", manifest.load_plan, "dispatch_status", "Fully Dispatched")
            
        message_tail = " All deliveries completed! Your status is now 'Returning to Yard'."
    
    if delivery_status in ["Delivered", "Partially Delivered"] and target_sales_order:
        frappe.enqueue(
            "nexus_supply_chain.nexus_supply_chain.doctype.vehicle_delivery_manifest.vehicle_delivery_manifest.process_financials_in_background",
            sales_order=target_sales_order,
            queue="short",
            job_name=f"Auto-Invoice SO: {target_sales_order}"
        )
    
    return {"status": "success", "message": f"Stop marked as {delivery_status}.{message_tail}"}


@frappe.whitelist()
def confirm_arrival_at_yard(manifest_name):
    manifest = frappe.get_doc("Vehicle Delivery Manifest", manifest_name)
    
    if manifest.vehicle:
        frappe.db.set_value("Vehicle In Transit", manifest.vehicle, "current_status", "Idle")
    
    todos = frappe.get_all("ToDo", filters={
        "reference_type": "Vehicle Delivery Manifest", 
        "reference_name": manifest.name, 
        "status": "Open"
    })
    for todo in todos:
        frappe.db.set_value("ToDo", todo.name, "status", "Closed")
        
    return {"status": "success", "message": "Vehicle is now Idle and ready for reassignment."}