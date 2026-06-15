import frappe
from frappe.model.document import Document

class NexusLoadPlan(Document):
    def on_submit(self):
        self.db_set("dispatch_status", "Production/Loading")

@frappe.whitelist()
def make_delivery_manifest(source_name):
    """Creates a Draft Vehicle Delivery Manifest securely and prevents duplicates"""
    
    existing_manifest = frappe.db.get_value("Vehicle Delivery Manifest", 
        {"load_plan": source_name, "docstatus": ["<", 2]}, "name")
        
    if existing_manifest:
        return {
            "status": "exists",
            "manifest_id": existing_manifest,
            "message": "A Delivery Manifest already exists for this Load Plan."
        }
        
    source = frappe.get_doc("Nexus Load Plan", source_name)
    target = frappe.new_doc("Vehicle Delivery Manifest")
    
    target.load_plan = source.name
    target.trip_status = "Manifested"
    
    for so_row in source.sales_orders:
        so_doc = frappe.get_doc("Sales Order", so_row.sales_order)
        target.append("stops", {
            "sales_order": so_doc.name,
            "customer": so_doc.customer_name or so_doc.customer,
            "payment_terms": so_row.payment_terms,
            "grand_total": so_doc.grand_total,
            "currency": so_doc.currency,
            "weight_kg": so_row.total_weight,
            "delivery_status": "Pending"
        })

    target.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success", 
        "manifest_id": target.name,
        "message": "Vehicle Delivery Manifest drafted successfully."
    }
