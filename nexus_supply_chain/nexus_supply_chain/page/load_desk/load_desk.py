# apps/nexus_supply_chain/nexus_supply_chain/page/load_desk/load_desk.py

import frappe
import json
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
    Evaluates delivery status purely on physical quantity execution.
    """
    if so_status in ('Closed', 'Completed'):
        return 'Closed'
    if qty_per_delivered >= 99.99: # 100% (Accounting for tiny float rounding)
        return 'Fully Delivered'
    if qty_per_delivered > 0:
        return 'Partially Delivered'
    if so_status == 'On Hold':
        return 'On Hold'
    return 'Pending'

# ─────────────────────────────────────────────────────────────────────────────
# API 1 – Sidebar Load Plans (Lightning Fast Physical Quantity RAM Map)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_load_desk_plans():
    """
    Fetches Load Plans calculating dispatch statuses exactly on Physical 
    Box Quantities (Ordered vs Delivered) using 0-Lag RAM mapping.
    """
    # 1. Map total ordered physical quantities per Load Plan
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

    # 2. Map absolute truth of delivered physical quantities direct from submitted D-Notes
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
# API 2 – Sales Order Exact Quantity Aggregation Engine
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_load_plan_sales_orders(load_plan_name):
    """
    Computes precise line-item physical execution (Ordered Qty, Delivered Qty, 
    and True Outstanding Shortfall) per Sales Order via 0-Lag RAM mapping.
    """
    lp = frappe.get_doc("Nexus Load Plan", load_plan_name)
    if not lp.sales_orders:
        return {"sales_orders": []}

    so_names = [row.sales_order for row in lp.sales_orders]
    dispatched_so_set = _get_submitted_dnote_so_set(so_names)

    # Fetch header details (custom_delivery_region synced with Intelligence Dashboard)
    sos = frappe.db.sql("""
        SELECT 
            name as sales_order, customer_name, custom_delivery_region as region, 
            payment_terms_template as payment_terms, status as so_status
        FROM `tabSales Order`
        WHERE name IN %s
    """, (tuple(so_names),), as_dict=True)

    # --- RAM Map for Exact Physical SO Quantities ---
    so_ordered_raw = frappe.db.sql("""
        SELECT parent as sales_order, SUM(stock_qty) as ordered_qty, COUNT(name) as total_items
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

    # Build RAM Map
    so_quantities = {r.sales_order: {"ordered_qty": flt(r.ordered_qty), "total_items": r.total_items, "delivered_qty": 0.0} for r in so_ordered_raw}
    for r in so_delivered_raw:
        if r.sales_order in so_quantities:
            so_quantities[r.sales_order]["delivered_qty"] = flt(r.delivered_qty)

    # Process Reservation Statuses from Custom State JSON
    state_dict = json.loads(lp.custom_dispatch_state) if lp.custom_dispatch_state else {}
    so_stats = {so: {"confirmed": 0, "reserved": 0} for so in so_names}
    
    for row_name, data in state_dict.items():
        so = data.get("sales_order")
        if so in so_stats:
            if data.get("state") == "Confirmed":
                so_stats[so]["confirmed"] += 1
            elif data.get("state") == "Reserved":
                so_stats[so]["reserved"] += 1

    final_sos = []
    for so in sos:
        so_id = so['sales_order']
        stats = so_stats.get(so_id, {"confirmed": 0, "reserved": 0})
        qty_data = so_quantities.get(so_id, {"ordered_qty": 0.0, "delivered_qty": 0.0, "total_items": 0})
        
        # Exact Physical Mathematics
        ord_qty = qty_data["ordered_qty"]
        del_qty = qty_data["delivered_qty"]
        qty_perc = (del_qty / ord_qty * 100) if ord_qty > 0 else 0.0
        outstanding_qty = ord_qty - del_qty

        # Map to UI JSON payload
        so['so_ordered_qty'] = ord_qty
        so['so_delivered_qty'] = del_qty
        so['so_qty_perc'] = qty_perc
        so['outstanding_qty'] = outstanding_qty
        
        # Dispatch & Reservation Tracking
        so['so_dispatched'] = so_id in dispatched_so_set
        so['delivery_status'] = 'Dispatched' if so['so_dispatched'] else _get_delivery_status(so['so_status'], qty_perc)
        
        total_items = qty_data["total_items"]
        if so['so_dispatched']:
            so['reservation_status'] = 'Consumed'
        elif total_items > 0 and stats['confirmed'] == total_items:
            so['reservation_status'] = 'Reserved'
        elif stats['confirmed'] > 0 or stats['reserved'] > 0:
            so['reservation_status'] = 'Partially Reserved'
        else:
            so['reservation_status'] = 'Soft'
            
        final_sos.append(so)

    return {"sales_orders": final_sos}


# ─────────────────────────────────────────────────────────────────────────────
# API 3 – Delivery Note Mapper (Draft & Redirect)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def create_dn_from_so(sales_order, load_plan_name):
    from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
    dn = make_delivery_note(sales_order)
    
    lp = frappe.get_doc("Nexus Load Plan", load_plan_name)
    state_dict = json.loads(lp.custom_dispatch_state) if lp.custom_dispatch_state else {}
    
    confirmed_qty_map = {}
    for row_name, data in state_dict.items():
        if data.get("sales_order") == sales_order and data.get("state") == "Confirmed":
            confirmed_qty_map[row_name] = data.get("qty", 0)
            
    valid_items = []
    for item in dn.get("items"):
        if item.so_detail in confirmed_qty_map:
            item.qty = confirmed_qty_map[item.so_detail]
            valid_items.append(item)
            
    if not valid_items:
        frappe.throw("No items have been Confirmed by the dispatch floor yet. Cannot create Delivery Note.")
        
    dn.set("items", valid_items)
    
    if hasattr(dn, 'custom_nexus_load_plan'):
        dn.custom_nexus_load_plan = load_plan_name
        
    dn.set_posting_time = 1
    dn.insert(ignore_permissions=True)
    
    return {"name": dn.name}


# ─────────────────────────────────────────────────────────────────────────────
# API 4 – Unplanned Confirmed Orders Fetcher (Layer 1: SOs)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_unplanned_confirmed_orders():
    """Fetches high-level SO details for confirmed SOs not attached to any active Load Plan."""
    planned_sos = frappe.db.sql("""
        SELECT DISTINCT sales_order
        FROM `tabNexus Load Plan Sales Order`
        WHERE parent IN (
            SELECT name FROM `tabNexus Load Plan` WHERE docstatus < 2
        )
    """, as_dict=True)
    planned_so_names = {r['sales_order'] for r in planned_sos}

    all_confirmed = frappe.db.sql("""
        SELECT name
        FROM `tabSales Order`
        WHERE status IN ('To Deliver and Bill', 'To Deliver', 'To Bill')
          AND docstatus = 1
    """, as_dict=True)

    unplanned_so_names = [so['name'] for so in all_confirmed if so['name'] not in planned_so_names]

    if not unplanned_so_names:
        return {"sales_orders": [], "count": 0}

    dispatched_sos = _get_submitted_dnote_so_set(unplanned_so_names)
    pending_sos = [so for so in unplanned_so_names if so not in dispatched_sos]

    if not pending_sos:
        return {"sales_orders": [], "count": 0}

    sos = frappe.db.sql("""
        SELECT 
            name as sales_order, customer_name, custom_delivery_region as region,
            payment_terms_template as payment_terms, grand_total, transaction_date
        FROM `tabSales Order`
        WHERE name IN %s
        ORDER BY transaction_date ASC
    """, (tuple(pending_sos),), as_dict=True)

    return {"sales_orders": sos, "count": len(pending_sos)}


# ─────────────────────────────────────────────────────────────────────────────
# API 5 – Explode Unplanned Items (Layer 2: Items)
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_exploded_unplanned_items():
    """Explodes the pending SOs into item-level details mapping available bin stock."""
    planned_sos = frappe.db.sql("""
        SELECT DISTINCT sales_order
        FROM `tabNexus Load Plan Sales Order`
        WHERE parent IN (
            SELECT name FROM `tabNexus Load Plan` WHERE docstatus < 2
        )
    """, as_dict=True)
    planned_so_names = {r['sales_order'] for r in planned_sos}

    all_confirmed = frappe.db.sql("""
        SELECT name FROM `tabSales Order`
        WHERE status IN ('To Deliver and Bill', 'To Deliver', 'To Bill') AND docstatus = 1
    """, as_dict=True)

    unplanned_so_names = [so['name'] for so in all_confirmed if so['name'] not in planned_so_names]
    if not unplanned_so_names: return {"items": []}

    dispatched_sos = _get_submitted_dnote_so_set(unplanned_so_names)
    pending_sos = [so for so in unplanned_so_names if so not in dispatched_sos]
    if not pending_sos: return {"items": []}

    items = frappe.db.sql("""
        SELECT
            soi.name as item_row_name,
            soi.parent as sales_order,
            soi.item_code,
            soi.item_name,
            soi.stock_qty as required_qty,
            so.customer_name,
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
            item['balance'] = item['actual_bin'] - item['required_qty']

    return {"items": items}


# ─────────────────────────────────────────────────────────────────────────────
# API 6 – Print Engine Data Fetcher
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_print_data(load_plans, print_type):
    lps = json.loads(load_plans)
    if not lps:
        return {"items": [], "customers": []}

    so_rows = frappe.db.sql("""
        SELECT parent as load_plan, sales_order 
        FROM `tabNexus Load Plan Sales Order` 
        WHERE parent IN %s
    """, (tuple(lps),), as_dict=True)
    
    if not so_rows:
        return {"items": [], "customers": []}
        
    so_names = list(set([r['sales_order'] for r in so_rows]))
    
    items = frappe.db.sql("""
        SELECT 
            soi.item_code, soi.item_name, soi.stock_qty as qty,
            so.name as sales_order, so.customer_name
        FROM `tabSales Order Item` soi
        INNER JOIN `tabSales Order` so ON so.name = soi.parent
        WHERE soi.parent IN %s
    """, (tuple(so_names),), as_dict=True)
    
    company = frappe.defaults.get_user_default("Company") or "Nexus Logistics"
    
    if print_type == 'loading_sheet':
        agg = {}
        for it in items:
            ic = it['item_code']
            if ic not in agg:
                agg[ic] = {"item_code": ic, "description": it['item_name'], "qty": 0}
            agg[ic]['qty'] += it['qty']
            
        final_items = sorted(list(agg.values()), key=lambda x: x['item_code'])
        return {"company": company, "items": final_items, "orders_count": len(so_names), "total_items": len(final_items)}
        
    elif print_type == 'packing_list':
        agg = {}
        for it in items:
            cust = it['customer_name'] or it['sales_order']
            if cust not in agg:
                agg[cust] = {"customer": cust, "items": []}
            agg[cust]['items'].append({
                "item_code": it['item_code'], 
                "description": it['item_name'], 
                "qty": it['qty']
            })
            
        final_customers = sorted(list(agg.values()), key=lambda x: x['customer'])
        return {"company": company, "customers": final_customers, "orders_count": len(so_names)}