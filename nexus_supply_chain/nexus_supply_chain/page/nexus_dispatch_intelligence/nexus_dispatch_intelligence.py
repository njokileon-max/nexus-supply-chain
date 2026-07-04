# apps/nexus_supply_chain/nexus_supply_chain/page/nexus_dispatch_intelligence/nexus_dispatch_intelligence.py

import frappe
from frappe.utils import today, get_first_day, flt, add_days

from nexus_supply_chain.utils.cost_utils import (
    MarketCostEngine,
    get_current_month_overhead_metrics,
)

@frappe.whitelist()
def get_dashboard_metrics(filter_date=None, start_date=None, end_date=None):
    """
    Computes high-level revenue and margin metrics for the top header cards using
    True Market COGS and exact financial line-item values. 
    However, Load Plan classification (Active vs Dispatched counts) is tied strictly 
    to the Physical Quantity RAM Map to ensure cross-departmental sync.
    """
    current_date = today()
    
    target_start = start_date if filter_date == 'custom' else (current_date if filter_date == 'today' else None)
    target_end = end_date if filter_date == 'custom' else current_date
    
    if filter_date == 'yesterday':
        target_start = target_end = add_days(current_date, -1)
    elif filter_date == 'all':
        target_start = '2000-01-01'

    lp_data = frappe.db.sql("""
        SELECT 
            lp.name as lp_name,
            so.name as so_name,
            soi.item_code,
            soi.qty as ordered_qty,
            soi.stock_qty,
            soi.delivered_qty,
            soi.amount as line_amount,
            soi.billed_amt
        FROM `tabNexus Load Plan` lp
        LEFT JOIN `tabNexus Load Plan Sales Order` lp_so ON lp_so.parent = lp.name
        LEFT JOIN `tabSales Order` so ON so.name = lp_so.sales_order
        LEFT JOIN `tabSales Order Item` soi ON soi.parent = so.name
        WHERE lp.docstatus < 2
          AND DATE(lp.creation) BETWEEN %s AND %s
    """, (target_start, target_end), as_dict=True)

    valid_lps = list(set([r.lp_name for r in lp_data if r.lp_name]))
    del_map = {}
    if valid_lps:
        delivered_map_raw = frappe.db.sql("""
            SELECT 
                lp_so.parent as load_plan,
                SUM(dni.stock_qty) as total_delivered
            FROM `tabNexus Load Plan Sales Order` lp_so
            INNER JOIN `tabDelivery Note Item` dni ON dni.against_sales_order = lp_so.sales_order
            INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
            WHERE dn.docstatus = 1 AND lp_so.parent IN %s
            GROUP BY lp_so.parent
        """, (tuple(valid_lps),), as_dict=True)
        del_map = {r.load_plan: flt(r.total_delivered) for r in delivered_map_raw}

    engine = MarketCostEngine()
    
    active_plans_set = set()
    fully_dispatched_set = set()
    partially_dispatched_set = set()
    
    lp_summary = {} 
    
    for row in lp_data:
        lp_name = row.lp_name
        active_plans_set.add(lp_name)
        
        if lp_name not in lp_summary:
            lp_summary[lp_name] = {
                "ordered_qty_physical": 0.0,
                "ordered_value_ex_vat": 0.0,
                "delivered_value_ex_vat": 0.0,
                "invoiced_value_ex_vat": 0.0,
                "delivered_market_cogs": 0.0
            }
            
        ordered_qty = flt(row.ordered_qty)
        if row.item_code and ordered_qty > 0:
            del_qty = flt(row.delivered_qty)
            
            lp_summary[lp_name]["ordered_qty_physical"] += flt(row.stock_qty)
            
            line_amount_ex_vat = flt(row.line_amount) / 1.16
            unit_revenue_ex_vat = line_amount_ex_vat / ordered_qty
            
            lp_summary[lp_name]["ordered_value_ex_vat"] += line_amount_ex_vat
            lp_summary[lp_name]["delivered_value_ex_vat"] += unit_revenue_ex_vat * del_qty
            lp_summary[lp_name]["invoiced_value_ex_vat"] += flt(row.billed_amt) / 1.16
            
            conv_factor = flt(row.stock_qty) / ordered_qty
            base_del_qty = del_qty * conv_factor
            unit_cost = engine.get_cost(row.item_code)
            lp_summary[lp_name]["delivered_market_cogs"] += unit_cost * base_del_qty

    total_delivered_value_ex_vat = 0.0
    total_billed_value_ex_vat = 0.0
    delivered_market_cogs = 0.0
    
    for lp_name, data in lp_summary.items():
        total_delivered_value_ex_vat += data["delivered_value_ex_vat"]
        total_billed_value_ex_vat += data["invoiced_value_ex_vat"]
        delivered_market_cogs += data["delivered_market_cogs"]
        
        tot_ordered_qty = data["ordered_qty_physical"]
        tot_delivered_qty = del_map.get(lp_name, 0.0)
        
        if tot_ordered_qty > 0:
            pct_del = (tot_delivered_qty / tot_ordered_qty) * 100
            if pct_del >= 99.99:
                fully_dispatched_set.add(lp_name)
                active_plans_set.discard(lp_name)
            elif pct_del > 0:
                partially_dispatched_set.add(lp_name)
                active_plans_set.discard(lp_name)
        
    total_outstanding = total_delivered_value_ex_vat - total_billed_value_ex_vat 
    true_gross_margin = total_delivered_value_ex_vat - delivered_market_cogs
    true_gross_margin_perc = (true_gross_margin / total_delivered_value_ex_vat * 100) if total_delivered_value_ex_vat > 0 else 0.0

    monthly_metrics = get_current_month_overhead_metrics()
    total_monthly_overheads = flt(monthly_metrics.get("total_global_overheads", 0.0))
    total_monthly_invoiced_sales = flt(monthly_metrics.get("total_invoiced_sales", 0.0))

    global_prorated_overhead = 0.0
    if total_monthly_invoiced_sales > 0:
        global_revenue_ratio = total_delivered_value_ex_vat / total_monthly_invoiced_sales
        global_prorated_overhead = total_monthly_overheads * global_revenue_ratio

    true_net_profit = true_gross_margin - global_prorated_overhead

    return {
        "total_outstanding_value": total_outstanding,
        "filtered_delivered_total": total_delivered_value_ex_vat,
        "delivered_market_cogs": delivered_market_cogs,
        "true_gross_margin": true_gross_margin,
        "true_gross_margin_perc": true_gross_margin_perc,
        
        "global_prorated_overhead": global_prorated_overhead,
        "true_net_profit": true_net_profit,
        
        "fully_dispatched": len(fully_dispatched_set),
        "partially_dispatched": len(partially_dispatched_set),
        "active_plans": len(active_plans_set)
    }

@frappe.whitelist()
def get_intelligence_plans():

    load_plans = frappe.db.sql("""
        SELECT
            lp.name, lp.docstatus, lp.vehicle_type, lp.transport_mode,
            lp.creation,
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

    for lp in load_plans:
        tot_ordered = flt(lp.total_ordered)
        tot_delivered = del_map.get(lp.name, 0.0)

        if lp.so_count == 0 or tot_ordered == 0:
            lp['dispatch_status'] = 'Pending'
            lp['delivered_percentage'] = 0
        else:
            pct_delivered = (tot_delivered / tot_ordered) * 100
            lp['delivered_percentage'] = pct_delivered
            
            if pct_delivered >= 99.99:
                lp['dispatch_status'] = 'Fully Dispatched'
            elif pct_delivered > 0:
                lp['dispatch_status'] = 'Partially Dispatched'
            else:
                lp['dispatch_status'] = 'Pending'

    return load_plans


@frappe.whitelist()
def get_load_plan_audit(load_plan_name):
    """
    Calculates exact Delivered Value, Invoiced Value, and Delivered Market COGS 
    line-by-line. Uses Tax-Exclusive revenue universally for true profit metrics.
    """
    lp = frappe.get_doc("Nexus Load Plan", load_plan_name)
    if not lp.sales_orders:
        return {"sales_orders": [], "summary": {}}

    so_names = [row.sales_order for row in lp.sales_orders]

    sos = frappe.db.sql("""
        SELECT 
            name as sales_order, customer_name, custom_delivery_region as region, 
            payment_terms_template as payment_terms, grand_total as revenue
        FROM `tabSales Order`
        WHERE name IN %s
    """, (tuple(so_names),), as_dict=True)

    items = frappe.db.sql("""
        SELECT 
            parent as sales_order, item_code, 
            qty as ordered_qty, stock_qty,
            delivered_qty, amount as line_amount, billed_amt
        FROM `tabSales Order Item`
        WHERE parent IN %s
    """, (tuple(so_names),), as_dict=True)

    engine = MarketCostEngine()
    
    so_execution = {
        so: {
            "delivered_value_ex_vat": 0.0, 
            "invoiced_value_ex_vat": 0.0, 
            "delivered_cogs": 0.0, 
            "ordered_cogs": 0.0
        } for so in so_names
    }
    
    for it in items:
        ordered_qty = flt(it['ordered_qty'])
        if ordered_qty > 0:
            so = it['sales_order']
            del_qty = flt(it['delivered_qty'])
            
            line_amount_ex_vat = flt(it['line_amount']) / 1.16
            unit_revenue_ex_vat = line_amount_ex_vat / ordered_qty
            
            so_execution[so]["delivered_value_ex_vat"] += unit_revenue_ex_vat * del_qty
            so_execution[so]["invoiced_value_ex_vat"] += flt(it['billed_amt']) / 1.16
            
            conv_factor = flt(it['stock_qty']) / ordered_qty
            base_del_qty = del_qty * conv_factor
            
            unit_cost = engine.get_cost(it['item_code'])
            so_execution[so]["delivered_cogs"] += unit_cost * base_del_qty
            so_execution[so]["ordered_cogs"] += unit_cost * flt(it['stock_qty'])

    total_revenue_ex_vat = 0.0
    total_market_cogs = 0.0
    total_delivered_value_ex_vat = 0.0
    total_invoiced_value_ex_vat = 0.0
    total_delivered_market_cogs = 0.0

    final_sos = []
    for so in sos:
        so_name = so['sales_order']
        rev_ex_vat = flt(so['revenue']) / 1.16
        
        del_val_ex_vat = so_execution[so_name]["delivered_value_ex_vat"]
        inv_val_ex_vat = so_execution[so_name]["invoiced_value_ex_vat"]
        del_cogs = so_execution[so_name]["delivered_cogs"]
        ordered_cogs = so_execution[so_name]["ordered_cogs"]
        
        del_margin = del_val_ex_vat - del_cogs
        del_margin_perc = (del_margin / del_val_ex_vat * 100) if del_val_ex_vat > 0 else 0.0

        total_revenue_ex_vat += rev_ex_vat
        total_market_cogs += ordered_cogs
        total_delivered_value_ex_vat += del_val_ex_vat
        total_invoiced_value_ex_vat += inv_val_ex_vat
        total_delivered_market_cogs += del_cogs

        so['revenue'] = rev_ex_vat 
        so['market_cogs'] = ordered_cogs
        so['delivered_cogs'] = del_cogs
        so['delivered_margin'] = del_margin
        so['delivered_margin_perc'] = del_margin_perc
        so['delivered_value'] = del_val_ex_vat
        so['invoiced_value'] = inv_val_ex_vat

        final_sos.append(so)

    total_delivered_gross_margin = total_delivered_value_ex_vat - total_delivered_market_cogs
    delivered_gross_margin_perc = (total_delivered_gross_margin / total_delivered_value_ex_vat * 100) if total_delivered_value_ex_vat > 0 else 0.0

    monthly_metrics = get_current_month_overhead_metrics()
    total_monthly_overheads = flt(monthly_metrics.get("total_global_overheads", 0.0))
    total_monthly_invoiced_sales = flt(monthly_metrics.get("total_invoiced_sales", 0.0))

    lp_prorated_overhead = 0.0
    if total_monthly_invoiced_sales > 0:
        lp_revenue_ratio = total_delivered_value_ex_vat / total_monthly_invoiced_sales
        lp_prorated_overhead = total_monthly_overheads * lp_revenue_ratio
        
    lp_net_profit = total_delivered_gross_margin - lp_prorated_overhead

    summary = {
        "planned_margin_perc": flt(lp.margin_percentage), 
        "market_margin_perc": delivered_gross_margin_perc,          
        "delivered_gross_margin_perc": delivered_gross_margin_perc,
        "delivered_gross_margin": total_delivered_gross_margin,
        "delivered_market_cogs": total_delivered_market_cogs,
        
        "lp_prorated_overhead": lp_prorated_overhead,
        "lp_net_profit": lp_net_profit,
        
        "total_revenue": total_revenue_ex_vat,
        "total_market_cogs": total_market_cogs,
        "total_delivered_value": total_delivered_value_ex_vat,
        "total_invoiced_value": total_invoiced_value_ex_vat
    }

    return {"sales_orders": final_sos, "summary": summary}

def _get_submitted_dnote_so_set(so_names):
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