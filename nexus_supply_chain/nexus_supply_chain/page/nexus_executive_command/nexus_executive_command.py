# apps/nexus_supply_chain/nexus_supply_chain/page/nexus_executive_command/nexus_executive_command.py

import frappe
from frappe.utils import flt, getdate, nowdate
from datetime import timedelta

# --- WEBSOCKET TRIGGER ---
def publish_realtime_production(doc, method):
    """Broadcasts a signal to the frontend whenever production happens or is cancelled."""
    if doc.purpose == "Manufacture":
        frappe.publish_realtime('nexus_production_sync', message={'status': 'updated'})

def get_working_days(start_date, end_date):
    """Calculates working days between two dates, strictly excluding Sundays."""
    start = getdate(start_date)
    end = getdate(end_date)
    days = 0
    curr = start
    while curr <= end:
        if curr.weekday() != 6:  # 6 represents Sunday
            days += 1
        curr += timedelta(days=1)
    return days if days > 0 else 1

@frappe.whitelist()
def get_executive_dashboard_data(start_date=None, end_date=None, custom_working_days=None):
    today_str = nowdate()
    if not end_date:
        end_date = today_str
    if not start_date:
        start_date = getdate(end_date).replace(day=1).strftime('%Y-%m-%d')
        
    start_dt = getdate(start_date)
    end_dt = getdate(end_date)
    
    if custom_working_days:
        days_elapsed = flt(custom_working_days)
    else:
        days_elapsed = get_working_days(start_dt, end_dt)
        
    if days_elapsed <= 0: 
        days_elapsed = 1

    # =========================================================================
    # 1. THE BURN ENGINE (Strict Explicit Tagging - Matches Daily Overhead)
    # =========================================================================
    has_custom_pool = frappe.db.has_column("Account", "nexus_cost_pool")
    target_field = "nexus_cost_pool" if has_custom_pool else "custom_nexus_cost_pool"

    pool_data = frappe.db.sql(f"""
        SELECT acc.{target_field} as pool_type, SUM(gl.debit) - SUM(gl.credit) AS net_expense
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc ON gl.account = acc.name
        WHERE gl.posting_date BETWEEN %(start)s AND %(end)s
            AND gl.is_cancelled = 0
            AND acc.root_type = 'Expense'
            AND acc.{target_field} IN ('Labour', 'Energy', 'Admin')
        GROUP BY acc.{target_field}
    """, {"start": start_dt, "end": end_dt}, as_dict=True)

    labour_pool = 0.0
    energy_pool = 0.0
    admin_pool = 0.0

    for row in pool_data:
        val = flt(row.net_expense)
        if row.pool_type == 'Labour':
            labour_pool += val
        elif row.pool_type == 'Energy':
            energy_pool += val
        elif row.pool_type == 'Admin':
            admin_pool += val

    total_overhead = labour_pool + energy_pool + admin_pool

    # =========================================================================
    # 2. THE MARKET ENGINE (Sales Revenue)
    # =========================================================================
    sales_data = frappe.db.sql("""
        SELECT SUM(base_net_total) as net_revenue
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND is_return = 0
        AND posting_date BETWEEN %(start)s AND %(end)s
    """, {"start": start_dt, "end": end_dt}, as_dict=True)
    total_sales_revenue = flt(sales_data[0].net_revenue) if sales_data and sales_data[0].net_revenue else 0.0

    # =========================================================================
    # 3. HISTORICAL GL COGS (The Absolute Financial Truth)
    # =========================================================================
    cogs_accounts = (
        '611050 - Cost of Sales - CAL',
        '611055 - Cost Of Sales (All) - CAL',
        '611060 - Cost of Sales- Paints - CAL',
        '611065 - Cost Of Sales -Paints Automotive - CAL',
        '611070 - Cost of Sales - Fillers - CAL',
        '611080 - Cost of Sales - Adhensives - CAL',
        '611090 - Cost of Sales - Solvents - CAL',
        '611095 - Cost of Sales - Putty - CAL',
        '611097 - Cost of Sales - Preservatives - CAL',
        '611100 - Cost of Sales - Others - CAL',
        '611115 - Cost Of Sales - Projects - CAL'
    )
    
    cogs_data = frappe.db.sql("""
        SELECT SUM(gl.debit) - SUM(gl.credit) AS cogs
        FROM `tabGL Entry` gl
        WHERE gl.posting_date BETWEEN %(start)s AND %(end)s
            AND gl.is_cancelled = 0
            AND gl.account IN %(accounts)s
    """, {"start": start_dt, "end": end_dt, "accounts": cogs_accounts}, as_dict=True)
    total_cogs = flt(cogs_data[0].cogs) if cogs_data and cogs_data[0].cogs else 0.0

    # =========================================================================
    # 4. RAW MATERIAL CASH PIPELINE
    # =========================================================================
    rm_purchased_data = frappe.db.sql("""
        SELECT SUM(pri.base_amount) as total
        FROM `tabPurchase Receipt Item` pri
        INNER JOIN `tabPurchase Receipt` pr ON pri.parent = pr.name
        INNER JOIN `tabItem` i ON pri.item_code = i.name
        WHERE pr.docstatus = 1 AND pr.is_return = 0
        AND i.item_group = 'Raw Material'
        AND pr.posting_date BETWEEN %(start)s AND %(end)s
    """, {"start": start_dt, "end": end_dt}, as_dict=True)
    rm_purchased = flt(rm_purchased_data[0].total) if rm_purchased_data and rm_purchased_data[0].total else 0.0

    rm_consumed_data = frappe.db.sql("""
        SELECT SUM(sed.amount) as total
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se ON sed.parent = se.name
        INNER JOIN `tabItem` i ON sed.item_code = i.name
        WHERE se.docstatus = 1 AND se.purpose = 'Manufacture'
        AND i.item_group = 'Raw Material'
        AND se.posting_date BETWEEN %(start)s AND %(end)s
    """, {"start": start_dt, "end": end_dt}, as_dict=True)
    rm_consumed = flt(rm_consumed_data[0].total) if rm_consumed_data and rm_consumed_data[0].total else 0.0

    # =========================================================================
    # 5. PRODUCTION & INVENTORY PULLS (Strictly FG Only, Actual Booked Value)
    # =========================================================================
    manufactured_items = frappe.db.sql("""
        SELECT 
            i.item_group, sed.item_code, SUM(sed.qty) as qty,
            SUM(sed.amount) as total_val,
            MAX(CASE WHEN i.weight_per_unit > 0 THEN i.weight_per_unit ELSE 1.0 END) as weight_per_unit
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed ON se.name = sed.parent
        INNER JOIN `tabItem` i ON sed.item_code = i.name
        WHERE se.purpose = 'Manufacture' AND se.docstatus = 1 AND sed.is_finished_item = 1
        AND i.item_group NOT IN ('Work In Progress', 'Raw Material')
        AND se.posting_date BETWEEN %(start)s AND %(end)s
        GROUP BY i.item_group, sed.item_code
    """, {"start": start_dt, "end": end_dt}, as_dict=True)

    recent_stock_entries = frappe.db.sql("""
        SELECT 
            se.name, se.posting_date, se.posting_time,
            sed.item_code, i.item_name,
            SUM(sed.qty * CASE WHEN i.weight_per_unit > 0 THEN i.weight_per_unit ELSE 1.0 END) as yield_kg,
            SUM(sed.amount) as total_value_kes
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed ON se.name = sed.parent
        INNER JOIN `tabItem` i ON sed.item_code = i.name
        WHERE se.purpose = 'Manufacture' AND se.docstatus = 1 AND sed.is_finished_item = 1
        AND i.item_group NOT IN ('Work In Progress', 'Raw Material')
        AND se.posting_date BETWEEN %(start)s AND %(end)s
        GROUP BY se.name, se.posting_date, se.posting_time, sed.item_code, i.item_name
        ORDER BY se.posting_date DESC, se.posting_time DESC
    """, {"start": start_dt, "end": end_dt}, as_dict=True)

    fg_bins = frappe.db.sql("""
        SELECT SUM(b.stock_value) as total_value
        FROM `tabBin` b
        INNER JOIN `tabItem` i ON b.item_code = i.name
        WHERE b.actual_qty > 0 AND i.default_bom IS NOT NULL AND i.default_bom != ''
    """, as_dict=True)
    total_fg_store_value = flt(fg_bins[0].total_value) if fg_bins and fg_bins[0].total_value else 0.0

    # =========================================================================
    # 6. EXECUTING METRICS
    # =========================================================================
    net_fg_yield_kg = 0.0
    net_value_manufactured = 0.0
    item_group_distribution = {}

    for item in manufactured_items:
        qty = flt(item.qty)
        weight = flt(item.weight_per_unit) if flt(item.weight_per_unit) > 0 else 1.0
        kg_yield = qty * weight
        val_yield = flt(item.total_val)
        
        net_fg_yield_kg += kg_yield
        net_value_manufactured += val_yield

        grp = item.item_group or "Unknown"
        if grp not in item_group_distribution:
            item_group_distribution[grp] = 0.0
        item_group_distribution[grp] += kg_yield

    # =========================================================================
    # 7. CALCULATING FINANCIAL RATIOS & CONTEXT
    # =========================================================================
    sell_through_pct = (total_cogs / net_value_manufactured * 100) if net_value_manufactured > 0 else 0.0
    gross_margin_pct = ((total_sales_revenue - total_cogs) / total_sales_revenue * 100) if total_sales_revenue > 0 else 0.0
    rm_consumption_ratio = (rm_consumed / rm_purchased * 100) if rm_purchased > 0 else 0.0

    # 🚨 COSTING MATRIX ENGINE IN RAM
    month_str = start_dt.strftime('%B')
    year_str = str(start_dt.year)
    
    active_matrix_name = frappe.db.get_value("Nexus Cost Allocation Period", 
        {"month": month_str, "year": year_str, "is_active": 1, "docstatus": 1}, 
        "name"
    )
    
    matrix_map = {}
    if active_matrix_name:
        rows = frappe.get_all("Nexus Costing Matrix Row", 
            filters={"parent": active_matrix_name}, 
            fields=["item_group", "labour_allocation", "energy_allocation"]
        )
        for r in rows:
            matrix_map[r.item_group] = {
                "labour_pct": flt(r.labour_allocation) / 100.0,
                "energy_pct": flt(r.energy_allocation) / 100.0
            }

    group_array = []
    for grp, kg in item_group_distribution.items():
        pct = (kg / net_fg_yield_kg * 100) if net_fg_yield_kg > 0 else 0.0
        
        rate_per_kg = 0.0
        if active_matrix_name and grp in matrix_map:
            labour_allocated = labour_pool * matrix_map[grp]["labour_pct"]
            energy_allocated = energy_pool * matrix_map[grp]["energy_pct"]
            admin_allocated = admin_pool * (kg / net_fg_yield_kg) if net_fg_yield_kg > 0 else 0.0
            
            allocated_overhead = labour_allocated + energy_allocated + admin_allocated
            rate_per_kg = allocated_overhead / kg if kg > 0 else 0.0

        group_array.append({
            "group": grp, 
            "kg": kg, 
            "pct": pct,
            "rate_per_kg": rate_per_kg
        })
        
    group_array.sort(key=lambda x: x['kg'], reverse=True)

    return {
        "period": f"{start_dt.strftime('%d-%b-%Y')} to {end_dt.strftime('%d-%b-%Y')}",
        "working_days_computed": days_elapsed,
        "net_fg_yield_kg": net_fg_yield_kg,
        "total_sales_revenue": total_sales_revenue,
        "total_overhead": total_overhead,
        "total_cogs": total_cogs,
        "net_value_manufactured": net_value_manufactured,
        "total_fg_store_value": total_fg_store_value,
        "rm_purchased": rm_purchased,
        "rm_consumed": rm_consumed,
        "rm_consumption_ratio": rm_consumption_ratio,
        "sell_through_pct": sell_through_pct,
        "gross_margin_pct": gross_margin_pct,
        "recent_stock_entries": recent_stock_entries,
        "item_group_distribution": group_array
    }