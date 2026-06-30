# apps/nexus_supply_chain/nexus_supply_chain/nexus_supply_chain/page/dispatch_status/dispatch_status.py

import frappe
import json
from datetime import datetime
from frappe.utils import flt

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_submitted_dnote_so_set(so_names):
    """
    Returns a set of Sales Order names that have at least one submitted
    Delivery Note (docstatus = 1) covering them.
    """
    if not so_names:
        return set()
    rows = frappe.db.sql("""
        SELECT DISTINCT dni.against_sales_order
        FROM `tabDelivery Note Item` dni
        INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE dni.against_sales_order IN %s
          AND dn.docstatus = 1
    """, (tuple(so_names),), as_dict=True)
    return {r['against_sales_order'] for r in rows}


def _get_delivery_status(so_status, qty_per_delivered):
    """
    Evaluates delivery status purely on exact physical quantity execution.
    """
    if so_status in ('Closed', 'Completed'):
        return 'Closed'
    if qty_per_delivered >= 99.99: # 100% accounting for float precision
        return 'Fully Delivered'
    if qty_per_delivered > 0:
        return 'Partially Delivered'
    if so_status == 'On Hold':
        return 'On Hold'
    return 'Pending'

# ─────────────────────────────────────────────────────────────────────────────
# API 1 – Sidebar Load Plans (Physical Quantity RAM Map Query)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_dispatch_board_load_plans():
    """
    Fetches active Load Plans and splits them into Pending vs Dispatched using a
    high-speed RAM Map to bypass ERPNext's native 'delivered_qty' lag.
    """
    # 1. Map total ordered quantities per Load Plan
    load_plans = frappe.db.sql("""
        SELECT
            lp.name, lp.docstatus, lp.vehicle_type, lp.transport_mode,
            lp.reservation_status, lp.creation,
            SUM(soi.stock_qty) as total_ordered,
            COUNT(DISTINCT so.name) as so_count
        FROM `tabNexus Load Plan` lp
        LEFT JOIN `tabNexus Load Plan Sales Order` lp_so ON lp_so.parent = lp.name
        LEFT JOIN `tabSales Order` so ON so.name = lp_so.sales_order
        LEFT JOIN `tabSales Order Item` soi ON soi.parent = so.name
        WHERE lp.docstatus < 2
        GROUP BY lp.name
        ORDER BY lp.creation DESC
    """, as_dict=True)

    # 2. Map absolute truth of delivered quantities direct from submitted D-Notes
    delivered_map_raw = frappe.db.sql("""
        SELECT 
            lp_so.parent as load_plan,
            SUM(dni.stock_qty) as total_delivered
        FROM `tabNexus Load Plan Sales Order` lp_so
        INNER JOIN `tabDelivery Note Item` dni ON dni.against_sales_order = lp_so.sales_order
        INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE dn.docstatus = 1
        GROUP BY lp_so.parent
    """, as_dict=True)
    
    del_map = {r.load_plan: flt(r.total_delivered) for r in delivered_map_raw}

    pending_lps    = []
    dispatched_lps = []

    # 3. RAM Merge & Status Classification
    for lp in load_plans:
        tot_ordered = flt(lp.total_ordered)
        tot_delivered = del_map.get(lp.name, 0.0)

        if lp.so_count == 0 or tot_ordered == 0:
            st = 'Pending'
            lp['delivered_percentage'] = 0
        else:
            pct_delivered = (tot_delivered / tot_ordered) * 100
            lp['delivered_percentage'] = pct_delivered
            
            if pct_delivered >= 99.99:
                st = 'Fully Dispatched'
            elif pct_delivered > 0:
                st = 'Partially Dispatched'
            else:
                st = 'Pending'
                
        lp['dispatch_status'] = st
        
        if st in ['Fully Dispatched', 'Partially Dispatched']:
            dispatched_lps.append(lp)
        else:
            pending_lps.append(lp)

    return pending_lps + dispatched_lps


# ─────────────────────────────────────────────────────────────────────────────
# API 2 – Core allocation engine & Quantity Satisfaction Index
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_load_plan_dispatch_data(load_plan_name):
    """
    Evaluates virtual availability and constructs the exact Sales Order 
    Physical Quantity Satisfaction Index via the 0-Lag RAM map.
    """
    lp = frappe.get_doc("Nexus Load Plan", load_plan_name)
    if not lp.sales_orders:
        return {"items": [], "current_state": {}, "competing_plans": {}}

    so_names = [row.sales_order for row in lp.sales_orders]
    dispatched_so_set = _get_submitted_dnote_so_set(so_names)

    so_items = frappe.db.sql("""
        SELECT
            soi.name as item_row_name,
            soi.parent as sales_order,
            soi.item_code,
            soi.item_name,
            soi.stock_qty as required_qty,
            so.status as so_status
        FROM `tabSales Order Item` soi
        INNER JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE soi.parent IN %s
    """, (tuple(so_names),), as_dict=True)

    if not so_items:
        return {"items": [], "current_state": {}, "competing_plans": {}}

    # --- SO Satisfaction Index RAM Map (Ordered vs Truth Delivered) ---
    so_ordered_raw = frappe.db.sql("""
        SELECT parent as sales_order, SUM(stock_qty) as ordered_qty
        FROM `tabSales Order Item`
        WHERE parent IN %s
        GROUP BY parent
    """, (tuple(so_names),), as_dict=True)

    so_delivered_raw = frappe.db.sql("""
        SELECT dni.against_sales_order as sales_order, SUM(dni.stock_qty) as delivered_qty
        FROM `tabDelivery Note Item` dni
        INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE dni.against_sales_order IN %s AND dn.docstatus = 1
        GROUP BY dni.against_sales_order
    """, (tuple(so_names),), as_dict=True)
    
    so_quantities = {r.sales_order: {"so_ordered_qty": flt(r.ordered_qty), "so_delivered_qty": 0.0, "so_qty_perc": 0.0} for r in so_ordered_raw}
    for r in so_delivered_raw:
        if r.sales_order in so_quantities:
            so_quantities[r.sales_order]["so_delivered_qty"] = flt(r.delivered_qty)
            
    for so, data in so_quantities.items():
        ord_qty = data["so_ordered_qty"]
        del_qty = data["so_delivered_qty"]
        data["so_qty_perc"] = (del_qty / ord_qty * 100) if ord_qty > 0 else 0.0

    item_codes = tuple(set(item['item_code'] for item in so_items))

    bins = frappe.db.sql("""
        SELECT item_code, SUM(actual_qty) as actual_qty
        FROM `tabBin`
        WHERE item_code IN %s
        GROUP BY item_code
    """, (item_codes,), as_dict=True)
    bin_map = {b['item_code']: b['actual_qty'] for b in bins}

    # Extract dynamic physical completion of competing plans to verify active holds
    other_lps_raw = frappe.db.sql("""
        SELECT 
            lp.name, lp.custom_dispatch_state,
            SUM(soi.stock_qty) as total_ordered
        FROM `tabNexus Load Plan` lp
        LEFT JOIN `tabNexus Load Plan Sales Order` lp_so ON lp_so.parent = lp.name
        LEFT JOIN `tabSales Order Item` soi ON soi.parent = lp_so.sales_order
        WHERE lp.name != %s
          AND lp.docstatus < 2
          AND lp.custom_dispatch_state IS NOT NULL
          AND lp.custom_dispatch_state != ''
        GROUP BY lp.name
    """, (load_plan_name,), as_dict=True)

    other_lps_del_raw = frappe.db.sql("""
        SELECT 
            lp_so.parent as load_plan,
            SUM(dni.stock_qty) as total_delivered
        FROM `tabNexus Load Plan Sales Order` lp_so
        INNER JOIN `tabDelivery Note Item` dni ON dni.against_sales_order = lp_so.sales_order
        INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE lp_so.parent != %s AND dn.docstatus = 1
        GROUP BY lp_so.parent
    """, (load_plan_name,), as_dict=True)
    
    other_del_map = {r.load_plan: flt(r.total_delivered) for r in other_lps_del_raw}

    other_lps = []
    for olp in other_lps_raw:
        tot_ord = flt(olp.total_ordered)
        tot_del = other_del_map.get(olp.name, 0.0)
        pct = (tot_del / tot_ord * 100) if tot_ord > 0 else 0
        if pct < 99.99: # Only extract reservations from non-dispatched trucks
            other_lps.append(olp)

    other_reserved_map = {}
    competing_plans    = {}

    for olp in other_lps:
        try:
            state_dict = json.loads(olp.custom_dispatch_state)
        except (json.JSONDecodeError, TypeError):
            continue

        olp_so_rows = frappe.db.sql("""
            SELECT sales_order FROM `tabNexus Load Plan Sales Order`
            WHERE parent = %s
        """, (olp.name,), as_dict=True)
        olp_so_names       = [r.sales_order for r in olp_so_rows]
        olp_dispatched_sos = _get_submitted_dnote_so_set(olp_so_names)

        for row_name, data in state_dict.items():
            if data.get("state") not in ("Reserved", "Confirmed"):
                continue
            row_so = data.get("sales_order")
            if row_so and row_so in olp_dispatched_sos:
                continue

            ic  = data.get("item_code")
            qty = data.get("qty", 0)
            other_reserved_map[ic] = other_reserved_map.get(ic, 0) + qty

            if ic not in competing_plans:
                competing_plans[ic] = []
            competing_plans[ic].append({
                "plan":  olp.name,
                "qty":   qty,
                "state": data.get("state")
            })

    current_state = {}
    if lp.custom_dispatch_state:
        try:
            current_state = json.loads(lp.custom_dispatch_state)
        except (json.JSONDecodeError, TypeError):
            current_state = {}

    for item in so_items:
        ic            = item['item_code']
        so_id         = item['sales_order']
        actual_bin    = bin_map.get(ic, 0)
        other_reserved = other_reserved_map.get(ic, 0)
        required_qty  = item['required_qty']
        so_dispatched = so_id in dispatched_so_set
        
        # Inject Quantity Satisfaction Index Data
        qty_data = so_quantities.get(so_id, {"so_ordered_qty": 0.0, "so_delivered_qty": 0.0, "so_qty_perc": 0.0})
        item['so_ordered_qty']   = qty_data['so_ordered_qty']
        item['so_delivered_qty'] = qty_data['so_delivered_qty']
        item['so_qty_perc']      = qty_data['so_qty_perc']

        if so_dispatched:
            item['actual_bin']       = actual_bin
            item['other_reserved']   = other_reserved
            item['virtual_available'] = actual_bin - other_reserved - required_qty
            item['less_amount']      = 0
            item['competing']        = []
            item['delivery_status']  = 'Dispatched'
            item['status']           = 'Dispatched'
            item['so_dispatched']    = True
        else:
            virtual_available = actual_bin - other_reserved - required_qty
            item['actual_bin']        = actual_bin
            item['other_reserved']    = other_reserved
            item['virtual_available'] = virtual_available
            item['less_amount']       = virtual_available if virtual_available < 0 else 0
            item['competing']         = competing_plans.get(ic, [])
            item['delivery_status']   = _get_delivery_status(item.get('so_status', ''), qty_data['so_qty_perc'])
            item['so_dispatched']     = False
            
            row_state      = current_state.get(item['item_row_name'], {})
            item['status'] = row_state.get('state', 'Pending')

    return {
        "items":          so_items,
        "current_state":  current_state,
        "competing_plans": competing_plans
    }


# ─────────────────────────────────────────────────────────────────────────────
# API 3 – Save item states
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def save_item_states(load_plan_name, item_states):
    states = json.loads(item_states)
    active_states = {k: v for k, v in states.items() if v.get("state") != "Dispatched"}

    lp = frappe.get_doc("Nexus Load Plan", load_plan_name)
    lp.db_set("custom_dispatch_state", json.dumps(active_states))

    total_items = len(active_states)
    confirmed   = sum(1 for v in active_states.values() if v.get("state") == "Confirmed")
    reserved    = sum(1 for v in active_states.values() if v.get("state") == "Reserved")

    if total_items == 0:
        new_res_status = "Soft"
    elif confirmed == total_items:
        new_res_status = "Reserved"
    elif confirmed > 0 or reserved > 0:
        new_res_status = "Partially Reserved"
    else:
        new_res_status = "Soft"

    lp.db_set("reservation_status", new_res_status)

    return {
        "status":        "success",
        "master_status": new_res_status,
        "confirmed":     confirmed,
        "reserved":      reserved,
        "total":         total_items
    }


# ─────────────────────────────────────────────────────────────────────────────
# API 4 – All planned items without D-Note
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_all_planned_items_without_dnote():
    """
    All items from active Load Plans excluding Fully Dispatched trucks based on RAM mapped physical qty.
    """
    load_plans = frappe.db.sql("""
        SELECT 
            lp.name, SUM(soi.stock_qty) as total_ordered
        FROM `tabNexus Load Plan` lp
        LEFT JOIN `tabNexus Load Plan Sales Order` lp_so ON lp_so.parent = lp.name
        LEFT JOIN `tabSales Order Item` soi ON soi.parent = lp_so.sales_order
        WHERE lp.docstatus < 2
        GROUP BY lp.name
    """, as_dict=True)

    delivered_map_raw = frappe.db.sql("""
        SELECT lp_so.parent as load_plan, SUM(dni.stock_qty) as total_delivered
        FROM `tabNexus Load Plan Sales Order` lp_so
        INNER JOIN `tabDelivery Note Item` dni ON dni.against_sales_order = lp_so.sales_order
        INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE dn.docstatus = 1
        GROUP BY lp_so.parent
    """, as_dict=True)
    del_map = {r.load_plan: flt(r.total_delivered) for r in delivered_map_raw}

    lp_names = []
    for lp in load_plans:
        tot_ord = flt(lp.total_ordered)
        tot_del = del_map.get(lp.name, 0.0)
        pct = (tot_del / tot_ord * 100) if tot_ord > 0 else 0
        if pct < 99.99:
            lp_names.append(lp.name)

    if not lp_names:
        return {"items": []}

    so_rows = frappe.db.sql("""
        SELECT DISTINCT sales_order, parent as load_plan
        FROM `tabNexus Load Plan Sales Order`
        WHERE parent IN %s
    """, (tuple(lp_names),), as_dict=True)

    if not so_rows:
        return {"items": []}

    so_to_lp  = {r['sales_order']: r['load_plan'] for r in so_rows}
    so_names  = list(so_to_lp.keys())

    dispatched_sos = _get_submitted_dnote_so_set(so_names)
    pending_sos    = [so for so in so_names if so not in dispatched_sos]

    if not pending_sos:
        return {"items": []}

    items = frappe.db.sql("""
        SELECT
            soi.name as item_row_name,
            soi.parent as sales_order,
            soi.item_code,
            soi.item_name,
            soi.stock_qty as required_qty,
            so.status as so_status,
            so.transaction_date
        FROM `tabSales Order Item` soi
        INNER JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE soi.parent IN %s
          AND so.status IN ('To Deliver and Bill', 'To Deliver', 'To Bill')
        ORDER BY soi.item_code
    """, (tuple(pending_sos),), as_dict=True)

    if items:
        item_codes = tuple(set(i['item_code'] for i in items))
        bins = frappe.db.sql("""
            SELECT item_code, SUM(actual_qty) as actual_qty
            FROM `tabBin`
            WHERE item_code IN %s
            GROUP BY item_code
        """, (item_codes,), as_dict=True)
        bin_map = {b['item_code']: b['actual_qty'] for b in bins}

        for item in items:
            item['load_plan']  = so_to_lp.get(item['sales_order'], '')
            item['actual_bin'] = bin_map.get(item['item_code'], 0)
            item['balance']    = item['actual_bin'] - item['required_qty']

    return {"items": items}


# ─────────────────────────────────────────────────────────────────────────────
# API 5 – Unplanned confirmed orders
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_unplanned_confirmed_orders():
    """
    Items from confirmed Sales Orders NOT linked to any active Load Plan 
    and have no submitted Delivery Note.
    """
    planned_sos = frappe.db.sql("""
        SELECT DISTINCT sales_order
        FROM `tabNexus Load Plan Sales Order`
        WHERE parent IN (
            SELECT name FROM `tabNexus Load Plan` WHERE docstatus < 2
        )
    """, as_dict=True)
    planned_so_names = {r['sales_order'] for r in planned_sos}

    all_confirmed = frappe.db.sql("""
        SELECT name, status, transaction_date
        FROM `tabSales Order`
        WHERE status IN ('To Deliver and Bill', 'To Deliver', 'To Bill')
          AND docstatus = 1
    """, as_dict=True)

    unplanned_so_names = [
        so['name'] for so in all_confirmed
        if so['name'] not in planned_so_names
    ]

    if not unplanned_so_names:
        return {"items": []}

    dispatched_sos = _get_submitted_dnote_so_set(unplanned_so_names)
    pending_sos    = [so for so in unplanned_so_names if so not in dispatched_sos]

    if not pending_sos:
        return {"items": []}

    items = frappe.db.sql("""
        SELECT
            soi.name as item_row_name,
            soi.parent as sales_order,
            soi.item_code,
            soi.item_name,
            soi.stock_qty as required_qty,
            so.status as so_status,
            so.transaction_date
        FROM `tabSales Order Item` soi
        INNER JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE soi.parent IN %s
        ORDER BY so.transaction_date ASC, soi.item_code ASC
    """, (tuple(pending_sos),), as_dict=True)

    if items:
        item_codes = tuple(set(i['item_code'] for i in items))
        bins = frappe.db.sql("""
            SELECT item_code, SUM(actual_qty) as actual_qty
            FROM `tabBin`
            WHERE item_code IN %s
            GROUP BY item_code
        """, (item_codes,), as_dict=True)
        bin_map = {b['item_code']: b['actual_qty'] for b in bins}

        for item in items:
            item['actual_bin'] = bin_map.get(item['item_code'], 0)
            item['balance']    = item['actual_bin'] - item['required_qty']

    return {"items": items}