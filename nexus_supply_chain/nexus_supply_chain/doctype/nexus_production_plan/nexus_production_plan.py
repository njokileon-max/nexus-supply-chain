import frappe
from frappe.model.document import Document
from frappe.utils import flt, nowdate

class NexusProductionPlan(Document):
    pass


@frappe.whitelist()
def create_from_explosion(load_plan_name, api_result):
    """
    Called from nexus_inventory_sync.js after successful API explosion
    Creates Nexus Production Plan document
    """
    if not load_plan_name or not frappe.db.exists("Nexus Load Plan", load_plan_name):
        frappe.throw(f"Load Plan {load_plan_name or '—'} does not exist")

    doc = frappe.new_doc("Nexus Production Plan")
    doc.load_plan = load_plan_name
    doc.planned_date = nowdate()
    doc.plan_status = "Planned"

    # Requirement ledger
    for item in api_result.get("exploded_items", []):
        doc.append("requirement_ledger", {
            "item_code": item.get("item_code"),
            "level": item.get("level", 0),
            "required_qty": flt(item.get("required_qty", 0)),
            "stock_qty": flt(item.get("stock_available", 0)),
            "shortfall_qty": flt(item.get("shortfall", 0))
        })

    # Aggregated shortfalls (if your DocType has a JSON or Table field)
    if "aggregated_shortfalls" in api_result:
        doc.aggregated_shortfalls = api_result["aggregated_shortfalls"]  # assuming JSON field

    doc.total_shortfall = sum(flt(i.get("shortfall", 0)) for i in api_result.get("exploded_items", []))
    doc.total_required = sum(flt(i.get("required_qty", 0)) for i in api_result.get("exploded_items", []))

    try:
        doc.insert(ignore_permissions=True)
        return {"name": doc.name, "status": "success"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Production Plan Creation Failed")
        frappe.throw(f"Failed to create Production Plan: {str(e)}")