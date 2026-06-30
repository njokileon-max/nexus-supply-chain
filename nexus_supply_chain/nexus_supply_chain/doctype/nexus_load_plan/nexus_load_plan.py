# apps/nexus_supply_chain/nexus_supply_chain/doctype/nexus_load_plan/nexus_load_plan.py

import frappe
from frappe.model.document import Document

class NexusLoadPlan(Document):
    def on_submit(self):
        # Automatically move to loading phase when confirmed
        self.db_set("dispatch_status", "Production/Loading")

@frappe.whitelist()
def make_delivery_manifest(source_name):
    """Creates a Draft Vehicle Delivery Manifest securely and prevents duplicates"""
    
    # 1. Uniqueness Check: Does a manifest already exist for this Load Plan?
    existing_manifest = frappe.db.get_value("Vehicle Delivery Manifest", 
        {"load_plan": source_name, "docstatus": ["<", 2]}, "name")
        
    if existing_manifest:
        return {
            "status": "exists",
            "manifest_id": existing_manifest,
            "message": "A Delivery Manifest already exists for this Load Plan."
        }
        
    # 2. Map and Create the Document
    source = frappe.get_doc("Nexus Load Plan", source_name)
    target = frappe.new_doc("Vehicle Delivery Manifest")
    
    target.load_plan = source.name
    target.trip_status = "Manifested"
    # Note: target.vehicle mapping removed to allow manual assignment of the specific license plate
    
    # 🚨 FINANCIAL & LOGISTICAL HANDOFF: Map Load Plan intelligence directly to the Manifest
    target.profit_loss = source.profit_loss
    target.gross_margin = source.margin_percentage  # Maps Load Plan's 'margin_percentage' to Manifest's 'gross_margin'
    target.net_margin = source.net_margin
    target.approximate_total_distance_km = source.approximate_total_distance_km
    target.approximate_fuel_consumption_ltrs = source.approximate_fuel_consumption_ltrs
    target.approximate_fuel_cost = source.approximate_fuel_cost
    target.actual_fuel_fuelled = source.actual_fuel_fuelled
    target.profitability_status = source.profitability_status
    
    for so_row in source.sales_orders:
        so_doc = frappe.get_doc("Sales Order", so_row.sales_order)
        target.append("stops", {
            "sales_order": so_doc.name,
            "customer": so_doc.customer_name or so_doc.customer,
            "payment_terms": so_row.payment_terms, # Pass Payment Terms to the Driver
            "grand_total": so_doc.grand_total,
            "currency": so_doc.currency,
            "weight_kg": so_row.total_weight,
            "delivery_status": "Pending"
        })

    # Save as Draft
    target.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success", 
        "manifest_id": target.name,
        "message": "Vehicle Delivery Manifest drafted successfully."
    }