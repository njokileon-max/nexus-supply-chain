# apps/nexus_supply_chain/nexus_supply_chain/page/nexus_daily_overhead/nexus_daily_overhead.py

import frappe
import calendar
from frappe.utils import flt, date_diff, getdate
from datetime import datetime, timedelta

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
def get_daily_overhead_data(start_date, end_date, custom_working_days=None):
    start_dt = getdate(start_date)
    
    # 1. Determine Working Days for Daily Burn Math
    if custom_working_days:
        days_elapsed = flt(custom_working_days)
    else:
        days_elapsed = get_working_days(start_date, end_date)
        
    if days_elapsed <= 0: 
        days_elapsed = 1
    
    # Dynamic Monthly Projection Math
    last_day = calendar.monthrange(start_dt.year, start_dt.month)[1]
    month_start_date = start_dt.replace(day=1)
    month_end_date = start_dt.replace(day=last_day)
    working_days_in_month = flt(get_working_days(month_start_date, month_end_date))

    # =========================================================================
    # QUERY A: General Ledger Expense Aggregation 
    # (Strict Explicit Tagging - Matches Cost Allocation Page)
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
    """, {"start": start_date, "end": end_date}, as_dict=True)

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

    mtd_total_overhead = labour_pool + energy_pool + admin_pool

    # Fetch individual accounts for the detailed ledger table below
    gl_data = frappe.db.sql(f"""
        SELECT 
            gl.account AS account_id,
            acc.account_number,
            acc.account_name,
            SUM(gl.debit) - SUM(gl.credit) AS net_expense
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc ON gl.account = acc.name
        WHERE gl.posting_date BETWEEN %(start)s AND %(end)s
            AND gl.is_cancelled = 0
            AND acc.root_type = 'Expense'
            AND acc.{target_field} IN ('Labour', 'Energy', 'Admin')
        GROUP BY gl.account, acc.account_number, acc.account_name
        HAVING net_expense > 0
        ORDER BY net_expense DESC
    """, {"start": start_date, "end": end_date}, as_dict=True)

    active_accounts = []
    for row in gl_data:
        pct = (flt(row.net_expense) / mtd_total_overhead * 100) if mtd_total_overhead > 0 else 0.0
        active_accounts.append({
            "account_id": row.account_id,
            "account_number": row.account_number,
            "account_name": row.account_name,
            "net_expense": flt(row.net_expense),
            "pct_of_total": pct
        })

    # =========================================================================
    # QUERY B: Production Yield & Actual Value (Strictly FG Only)
    # =========================================================================
    manufactured_items = frappe.db.sql("""
        SELECT 
            sed.item_code, 
            SUM(sed.qty) as total_qty_produced,
            SUM(sed.amount) as actual_value,
            MAX(CASE WHEN i.weight_per_unit > 0 THEN i.weight_per_unit ELSE 1.0 END) as weight_per_unit
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed ON se.name = sed.parent
        INNER JOIN `tabItem` i ON sed.item_code = i.name
        WHERE se.purpose = 'Manufacture' 
            AND se.docstatus = 1 
            AND sed.is_finished_item = 1
            AND i.item_group NOT IN ('Work In Progress', 'Raw Material')
            AND se.posting_date BETWEEN %(start)s AND %(end)s
        GROUP BY sed.item_code
    """, {"start": start_date, "end": end_date}, as_dict=True)

    mtd_yield_kg = 0.0
    mtd_actual_material_value = 0.0
    
    for item in manufactured_items:
        qty = flt(item.total_qty_produced)
        weight = flt(item.weight_per_unit)
        mtd_yield_kg += (qty * weight)
        mtd_actual_material_value += flt(item.actual_value)

    # =========================================================================
    # QUERY C: Market Engine (Sales & GL COGS) 
    # **Splitting Gross (Cash Flow) and Net (Profit)**
    # =========================================================================
    sales_invoices = frappe.db.sql("""
        SELECT 
            SUM(base_grand_total) as total_gross,
            SUM(base_net_total) as total_net
        FROM `tabSales Invoice`
        WHERE docstatus = 1 AND is_return = 0
        AND posting_date BETWEEN %(start)s AND %(end)s
    """, {"start": start_date, "end": end_date}, as_dict=True)
    
    total_invoiced_gross = flt(sales_invoices[0].total_gross) if sales_invoices else 0.0
    total_invoiced_net = flt(sales_invoices[0].total_net) if sales_invoices else 0.0
    vat_liability = total_invoiced_gross - total_invoiced_net

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
    """, {"start": start_date, "end": end_date, "accounts": cogs_accounts}, as_dict=True)
    total_cogs = flt(cogs_data[0].cogs) if cogs_data and cogs_data[0].cogs else 0.0

    # =========================================================================
    # QUERY D: RAW MATERIAL CASH PIPELINE
    # **Fetching both Net (for Ratio) and Gross (for Cash Flow)**
    # =========================================================================
    rm_purchased_data = frappe.db.sql("""
        SELECT 
            SUM(pri.base_amount) as total_net,
            SUM(pri.base_amount * CASE WHEN pr.base_net_total > 0 THEN (pr.base_grand_total / pr.base_net_total) ELSE 1.0 END) as total_gross
        FROM `tabPurchase Receipt Item` pri
        INNER JOIN `tabPurchase Receipt` pr ON pri.parent = pr.name
        INNER JOIN `tabItem` i ON pri.item_code = i.name
        WHERE pr.docstatus = 1 AND pr.is_return = 0
        AND i.item_group = 'Raw Material'
        AND pr.posting_date BETWEEN %(start)s AND %(end)s
    """, {"start": start_date, "end": end_date}, as_dict=True)
    
    rm_purchased_net = flt(rm_purchased_data[0].total_net) if rm_purchased_data else 0.0
    rm_purchased_gross = flt(rm_purchased_data[0].total_gross) if rm_purchased_data else 0.0

    rm_consumed_data = frappe.db.sql("""
        SELECT SUM(sed.amount) as total
        FROM `tabStock Entry Detail` sed
        INNER JOIN `tabStock Entry` se ON sed.parent = se.name
        INNER JOIN `tabItem` i ON sed.item_code = i.name
        WHERE se.docstatus = 1 AND se.purpose = 'Manufacture'
        AND i.item_group = 'Raw Material'
        AND se.posting_date BETWEEN %(start)s AND %(end)s
    """, {"start": start_date, "end": end_date}, as_dict=True)
    rm_consumed = flt(rm_consumed_data[0].total) if rm_consumed_data and rm_consumed_data[0].total else 0.0

    # =========================================================================
    # CORE FINANCIAL MATH
    # =========================================================================
    total_value_manufactured = mtd_actual_material_value + mtd_total_overhead
    daily_burn_rate = mtd_total_overhead / days_elapsed
    prorated_projection = daily_burn_rate * working_days_in_month

    if total_value_manufactured > 0:
        overhead_pct = (mtd_total_overhead / total_value_manufactured) * 100
        material_pct = (mtd_actual_material_value / total_value_manufactured) * 100
    else:
        overhead_pct = 0.0
        material_pct = 0.0

    # 1. The Profit Engine (Strictly NET Values)
    total_margin = total_invoiced_net - total_cogs
    daily_margin = total_margin / days_elapsed
    daily_profit_loss = daily_margin - daily_burn_rate
    margin_pct = (total_margin / total_invoiced_net * 100) if total_invoiced_net > 0 else 0.0

    # 2. The Cash Flow Engine (Strictly GROSS Values)
    daily_invoiced_gross = total_invoiced_gross / days_elapsed
    daily_rm_purchased_gross = rm_purchased_gross / days_elapsed
    net_daily_cash_position = daily_invoiced_gross - daily_rm_purchased_gross - daily_burn_rate

    # 3. Operations & Inventory (Strictly NET Values for Apples-to-Apples)
    rm_consumption_ratio = (rm_consumed / rm_purchased_net * 100) if rm_purchased_net > 0 else 0.0

    return {
        "mtd_total_overhead": mtd_total_overhead,
        "mtd_yield_kg": mtd_yield_kg,
        "mtd_actual_material_value": mtd_actual_material_value,
        "total_value_manufactured": total_value_manufactured,
        "daily_burn_rate": daily_burn_rate,
        "overhead_pct": overhead_pct,
        "material_pct": material_pct,
        "prorated_projection": prorated_projection,
        "total_invoiced_gross": total_invoiced_gross,
        "total_invoiced_net": total_invoiced_net,
        "vat_liability": vat_liability,
        "total_cogs": total_cogs,
        "total_margin": total_margin,
        "daily_margin": daily_margin,
        "daily_profit_loss": daily_profit_loss,
        "margin_pct": margin_pct,
        "active_accounts": active_accounts,
        "working_days_computed": days_elapsed,
        "rm_purchased_net": rm_purchased_net,
        "rm_purchased_gross": rm_purchased_gross,
        "rm_consumed": rm_consumed,
        "rm_consumption_ratio": rm_consumption_ratio,
        "daily_invoiced_gross": daily_invoiced_gross,
        "daily_rm_purchased_gross": daily_rm_purchased_gross,
        "net_daily_cash_position": net_daily_cash_position
    }