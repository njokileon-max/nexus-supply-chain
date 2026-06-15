import frappe
from frappe.utils import flt, now_datetime

def sync_load_plan_status(load_plan_name):
    """
    Analyzes all reservations for a Load Plan and sets the definitive status.
    Triggers the Insight Engine to update shortfall notes.
    """
    if not load_plan_name: return
    
    _update_shortfall_notes(load_plan_name)
    
    statuses = frappe.db.sql("""
        SELECT reservation_status, docstatus FROM `tabNexus Inventory Reservation`
        WHERE nexus_load_plan = %s AND docstatus < 2
    """, (load_plan_name,), as_dict=True)
    
    if not statuses:
        frappe.db.set_value("Nexus Load Plan", load_plan_name, "reservation_status", "Released")
        return
        
    submitted_statuses = {r.reservation_status for r in statuses if r.docstatus == 1}
    
    if not submitted_statuses:
        frappe.db.set_value("Nexus Load Plan", load_plan_name, "reservation_status", "Soft")
        return
        
    status_set = submitted_statuses
    
    if "Consumed" in status_set and len(status_set) == 1:
        new_status = "Consumed"
    elif "Partially Consumed" in status_set or ("Consumed" in status_set and len(status_set) > 1):
        new_status = "Partially Consumed"
    elif "Waiting for Stock" in status_set:
        new_status = "Partially Reserved"
    elif "Active" in status_set:
        new_status = "Reserved"
    else:
        if "Expired" in status_set: new_status = "Expired"
        elif "Released" in status_set: new_status = "Released"
        else: new_status = "Soft"
        
    frappe.db.set_value("Nexus Load Plan", load_plan_name, "reservation_status", new_status)


def _update_shortfall_notes(load_plan_name):
    """
    Actionable Shortfall Intelligence: Identifies missing stock and 
    finds the 'Active' Last-in-Line or reports True Stockouts.
    """
    items = frappe.db.sql("""
        SELECT ri.item_code, SUM(ri.required_qty) as qty 
        FROM `tabNexus Inventory Reservation Item` ri
        JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
        WHERE r.nexus_load_plan = %s AND r.docstatus < 2 
        GROUP BY ri.item_code
    """, (load_plan_name,), as_dict=True)
    
    report = []
    for item in items:
        actual = flt(frappe.db.get_value("Bin", {"item_code": item.item_code, "warehouse": "Finished Goods - CAL"}, "actual_qty"))
        
        if actual < item.qty:
            shortfall = item.qty - actual
            base_msg = f"{item.item_code}: Missing {shortfall} units."
            
            last_active = frappe.db.sql("""
                SELECT r.name, ri.sales_order 
                FROM `tabNexus Inventory Reservation Item` ri
                JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
                WHERE ri.item_code = %s AND r.reservation_status = 'Active' AND r.docstatus = 1
                ORDER BY r.creation DESC LIMIT 1
            """, (item.item_code,), as_dict=True)
            
            if last_active:
                msg = f"{base_msg} [Action: Reallocate from {last_active[0].name} ({last_active[0].sales_order})]"
            else:
                others_waiting = frappe.db.sql("""
                    SELECT count(ri.name) 
                    FROM `tabNexus Inventory Reservation Item` ri
                    JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
                    WHERE ri.item_code = %s AND r.nexus_load_plan != %s AND r.docstatus = 1
                """, (item.item_code, load_plan_name))[0][0]
                
                if others_waiting > 0:
                    msg = f"{base_msg} [Status: Congested - {others_waiting} others also waiting]"
                else:
                    msg = f"{base_msg} [Status: OUT OF STOCK - No reservations holding this item]"
            
            report.append(msg)
            
    frappe.db.set_value("Nexus Load Plan", load_plan_name, "custom_inventory_shortfall_notes", "\n".join(report))


def _eval_consumed_state(reservation_name):
    """Strict state machine for Delivery Note satisfaction."""
    so_list = frappe.db.sql("SELECT DISTINCT sales_order FROM `tabNexus Inventory Reservation Item` WHERE parent = %s", (reservation_name,))
    if not so_list: return
    
    delivered_stats = [flt(frappe.db.get_value("Sales Order", so[0], "per_delivered")) for so in so_list]
    
    all_done = all(p >= 100.0 for p in delivered_stats)
    any_done = any(p > 0.0 for p in delivered_stats)
    
    current = frappe.db.get_value("Nexus Inventory Reservation", reservation_name, "reservation_status")
    new_status = "Consumed" if all_done else ("Partially Consumed" if any_done else "Active")
        
    if current != new_status:
        frappe.db.set_value("Nexus Inventory Reservation", reservation_name, "reservation_status", new_status)


def _eval_dispatch_status(load_plan_name):
    """Calculates if all orders in a Load Plan have submitted Delivery Notes"""
    plan = frappe.get_doc("Nexus Load Plan", load_plan_name)
    if plan.docstatus != 1: return
    
    total_orders = len(plan.sales_orders)
    dispatched_orders = 0
    
    for row in plan.sales_orders:
        dn_exists = frappe.db.exists("Delivery Note Item", {
            "against_sales_order": row.sales_order,
            "docstatus": 1
        })
        if dn_exists:
            dispatched_orders += 1
            
    new_status = "Production/Loading"
    if dispatched_orders == total_orders and total_orders > 0:
        new_status = "Fully Dispatched"
    elif dispatched_orders > 0:
        new_status = "Partially Dispatched"
        
    if plan.dispatch_status != new_status:
        frappe.db.set_value("Nexus Load Plan", plan.name, "dispatch_status", new_status)


def prepare_reservation_submission(doc, method):
    """
    PRE-FLIGHT HOOK: Runs the exact moment the user clicks 'Submit'.
    Prepares the document to be mathematically evaluated by the Waterfall Engine.
    """
    if doc.reservation_status in ["Draft", "", None]:
        doc.reservation_status = "Waiting for Stock"
        
    for item in doc.items:
        item.reserved_qty = 0


def validate_reservation_cancel(doc, method):
    """Enforce mandatory Release Reason on manual cancellation."""
    if not doc.release_reason and doc.reservation_status != "Expired":
        frappe.throw("<b>Release Reason</b> is mandatory before cancelling a reservation.")


def _reallocate_fifo_stock(item_code):
    """Waterfall Engine: Updates Active/Waiting statuses based on 'Finished Goods - CAL' levels."""
    actual_qty = frappe.db.sql("""
        SELECT SUM(actual_qty) FROM `tabBin` 
        WHERE item_code = %s AND warehouse = 'Finished Goods - CAL'
    """, (item_code,))[0][0] or 0

    queue = frappe.db.sql("""
        SELECT ri.name as item_name, r.name as parent_name, ri.required_qty, r.reservation_status, r.nexus_load_plan 
        FROM `tabNexus Inventory Reservation Item` ri
        INNER JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
        WHERE ri.item_code = %s AND r.docstatus = 1 AND r.reservation_status IN ('Active', 'Waiting for Stock')
        ORDER BY r.creation ASC
    """, (item_code,), as_dict=True)

    available = flt(actual_qty)
    touched_lps = set()

    for entry in queue:
        needed = flt(entry.required_qty)
        if available >= needed:
            if entry.reservation_status == 'Waiting for Stock':
                frappe.db.set_value("Nexus Inventory Reservation", entry.parent_name, "reservation_status", "Active")
                frappe.db.set_value("Nexus Inventory Reservation Item", entry.item_name, "reserved_qty", needed)
                touched_lps.add(entry.nexus_load_plan)
            elif entry.reservation_status == 'Active':
                frappe.db.set_value("Nexus Inventory Reservation Item", entry.item_name, "reserved_qty", needed)
            available -= needed
        else:
            if entry.reservation_status == 'Active':
                frappe.db.set_value("Nexus Inventory Reservation", entry.parent_name, "reservation_status", "Waiting for Stock")
                touched_lps.add(entry.nexus_load_plan)
            frappe.db.set_value("Nexus Inventory Reservation Item", entry.item_name, "reserved_qty", max(0, available))
            available = 0

    for lp in touched_lps: sync_load_plan_status(lp)


def validate_delivery_note_submission(doc, method):
    """
    Active-Pool Logic: Blocks delivery ONLY if an older reservation 
    is 'Waiting for Stock' (FIFO Starvation).
    """
    for item in doc.items:
        if not getattr(item, 'against_sales_order', None): continue
            
        res_info = frappe.db.sql("""
            SELECT r.creation FROM `tabNexus Inventory Reservation Item` ri
            INNER JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
            WHERE ri.sales_order = %s AND ri.item_code = %s AND r.docstatus = 1 LIMIT 1
        """, (item.against_sales_order, item.item_code), as_dict=True)
        
        if not res_info: continue 
            
        older_starving = frappe.db.sql("""
            SELECT ri.sales_order, r.name FROM `tabNexus Inventory Reservation Item` ri
            INNER JOIN `tabNexus Inventory Reservation` r ON ri.parent = r.name
            WHERE ri.item_code = %s 
            AND r.creation < %s
            AND r.docstatus = 1 
            AND r.reservation_status = 'Waiting for Stock'
            AND ri.sales_order != %s
            LIMIT 1
        """, (item.item_code, res_info[0].creation, item.against_sales_order), as_dict=True)

        if older_starving:
            frappe.throw(f"<b>FIFO Violation:</b> Order {item.against_sales_order} cannot bypass "
                         f"older Order <b>{older_starving[0].sales_order}</b> which is still Waiting for Stock.")


def process_delivery_note(doc, method):
    res_names = {r[0] for i in doc.items if getattr(i, 'against_sales_order', None) 
                 for r in frappe.db.sql("SELECT parent FROM `tabNexus Inventory Reservation Item` WHERE sales_order=%s AND item_code=%s", (i.against_sales_order, i.item_code))}
    
    load_plans_to_check = set()
    for name in res_names:
        _eval_consumed_state(name)
        lp_name = frappe.db.get_value("Nexus Inventory Reservation", name, "nexus_load_plan")
        if lp_name:
            sync_load_plan_status(lp_name)
            load_plans_to_check.add(lp_name)
            
    for lp_name in load_plans_to_check:
        _eval_dispatch_status(lp_name)


def process_delivery_note_cancel(doc, method):
    process_delivery_note(doc, method)


def process_sales_order_update(doc, method):
    """If SO is cancelled, release its linked active reservations."""
    if doc.docstatus == 2:
        reservations = frappe.get_all("Nexus Inventory Reservation Item", filters={"sales_order": doc.name}, fields=["parent", "item_code"])
        for r in reservations:
            status = frappe.db.get_value("Nexus Inventory Reservation", r.parent, "reservation_status")
            if status not in ["Consumed", "Released", "Expired"]:
                frappe.db.set_value("Nexus Inventory Reservation", r.parent, "reservation_status", "Released")
                frappe.db.set_value("Nexus Inventory Reservation", r.parent, "release_reason", "Sales Order Cancelled")
                sync_load_plan_status(frappe.db.get_value("Nexus Inventory Reservation", r.parent, "nexus_load_plan"))
                _reallocate_fifo_stock(r.item_code)


def process_reservation_update(doc, method):
    """Triggered on save or submission of a reservation."""
    sync_load_plan_status(doc.nexus_load_plan)
    
    if doc.docstatus == 1 and doc.reservation_status in ["Active", "Waiting for Stock"]:
        for i in doc.items: _reallocate_fifo_stock(i.item_code)


def process_reservation_cancel(doc, method):
    """Cleanup and Waterfall trigger upon manual cancellation."""
    if doc.reservation_status != "Expired":
        doc.db_set("reservation_status", "Released")
    
    sync_load_plan_status(doc.nexus_load_plan)
    for i in doc.items: _reallocate_fifo_stock(i.item_code)


def process_stock_movement(doc, method):
    """Triggered by Purchase Receipts or Stock Entries to the CAL warehouse."""
    items = {i.item_code for i in doc.items if getattr(i, 'item_code', None)}
    for code in items: _reallocate_fifo_stock(code)


def process_stock_movement_cancel(doc, method):
    process_stock_movement(doc, method)


def check_and_expire_reservations():
    """Daily job to clear timed-out reservations and re-distribute stock."""
    expired = frappe.get_all("Nexus Inventory Reservation", 
        filters={
            "expiry_date": ["<", now_datetime()], 
            "reservation_status": ["in", ["Active", "Waiting for Stock"]], 
            "docstatus": 1
        }, fields=["name", "nexus_load_plan"])
        
    for ed in expired:
        frappe.db.set_value("Nexus Inventory Reservation", ed.name, {
            "reservation_status": "Expired",
            "release_reason": "Reservation Expired"
        })
        
        sync_load_plan_status(ed.nexus_load_plan)
        res_doc = frappe.get_doc("Nexus Inventory Reservation", ed.name)
        for item in res_doc.items: 
            _reallocate_fifo_stock(item.item_code)
