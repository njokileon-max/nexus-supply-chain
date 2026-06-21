# apps/nexus_supply_chain/nexus_supply_chain/doctype/nexus_cost_allocation_period/nexus_cost_allocation_period.py

import frappe
from frappe.model.document import Document
from frappe.utils import flt, get_last_day

class NexusCostAllocationPeriod(Document):
    pass

@frappe.whitelist()
def get_monthly_ledger_and_yields(month, year):
    """
    Fetches the 0-lag Financial Cost Pools, Pure FG Yields, and Total Invoiced Sales for the given month.
    Ensures WIP and Raw Materials are excluded to prevent Double-Counting.
    """
    month_map = {
        "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
        "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12
    }
    
    m_num = month_map.get(month)
    if not m_num:
        frappe.throw("Invalid Month selected.")
        
    try:
        y_num = int(year)
    except ValueError:
        frappe.throw("Invalid Year format.")
    
    # Calculate exact date range for the selected month
    start_date = f"{y_num}-{m_num:02d}-01"
    end_date = get_last_day(start_date)
    
    # =========================================================================
    # 1. FETCH COST POOLS (Targeting the 'nexus_cost_pool' field)
    # Check if custom field exists to prevent errors during installation
    # =========================================================================
    has_custom_pool = frappe.db.has_column("Account", "nexus_cost_pool")
    target_field = "nexus_cost_pool" if has_custom_pool else "custom_nexus_cost_pool"

    pools = frappe.db.sql(f"""
        SELECT acc.{target_field} AS pool_type, SUM(gl.debit) - SUM(gl.credit) AS total
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc ON gl.account = acc.name
        WHERE gl.posting_date BETWEEN %s AND %s
            AND gl.is_cancelled = 0
            AND acc.root_type = 'Expense'
            AND acc.{target_field} IN ('Labour', 'Energy', 'Admin')
        GROUP BY acc.{target_field}
    """, (start_date, end_date), as_dict=True)
    
    pool_dict = {"Labour": 0.0, "Energy": 0.0, "Admin": 0.0}
    for p in pools:
        if p.pool_type:
            pool_dict[p.pool_type] = flt(p.total)
            
    # Calculate the Total Global Overheads
    total_global_overheads = pool_dict["Labour"] + pool_dict["Energy"] + pool_dict["Admin"]
            
    # =========================================================================
    # 2. FETCH PURE FG YIELDS (Strictly Excludes WIP and Raw Materials)
    # =========================================================================
    yields = frappe.db.sql("""
        SELECT 
            i.item_group,
            SUM(sed.qty * CASE WHEN i.weight_per_unit > 0 THEN i.weight_per_unit ELSE 1.0 END) as yield_kg
        FROM `tabStock Entry` se
        INNER JOIN `tabStock Entry Detail` sed ON se.name = sed.parent
        INNER JOIN `tabItem` i ON sed.item_code = i.name
        WHERE se.purpose = 'Manufacture' AND se.docstatus = 1 AND sed.is_finished_item = 1
        AND i.item_group NOT IN ('Work In Progress', 'Raw Material')
        AND se.posting_date BETWEEN %s AND %s
        GROUP BY i.item_group
    """, (start_date, end_date), as_dict=True)

    # =========================================================================
    # 3. FETCH TOTAL INVOICED SALES (Excluding Returns and Cancelled)
    # =========================================================================
    sales_data = frappe.db.sql("""
        SELECT SUM(base_grand_total) as total_sales
        FROM `tabSales Invoice`
        WHERE posting_date BETWEEN %s AND %s
        AND docstatus = 1
        AND is_return = 0
    """, (start_date, end_date), as_dict=True)

    total_invoiced_sales = flt(sales_data[0].total_sales) if sales_data and sales_data[0].total_sales else 0.0

    return {
        "pools": pool_dict,
        "total_global_overheads": total_global_overheads,
        "yields": yields,
        "total_invoiced_sales": total_invoiced_sales
    }