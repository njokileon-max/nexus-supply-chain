# apps/nexus_supply_chain/nexus_supply_chain/page/ai_scenario_analyzer/ai_scenario_analyzer.py

import frappe
from frappe.utils import flt

@frappe.whitelist()
def get_filters_data():
    """Fetches available Price Lists for the dropdown."""
    price_lists = frappe.get_all("Price List", filters={"selling": 1, "enabled": 1}, pluck="name")
    return {"price_lists": price_lists}

@frappe.whitelist()
def get_scenario_data(start_date, end_date, price_list):
    """
    Executes high-speed, isolated queries to build the exact financial truth.
    Now optimized to fetch True Landed Costs directly from the Nexus Item Costing DocType.
    """
    
    # =========================================================================
    # QUERY 1: GL OVERHEADS (The Sunk Cash Burn - For Audit)
    # =========================================================================
    has_custom_pool = frappe.db.has_column("Account", "nexus_cost_pool")
    target_field = "nexus_cost_pool" if has_custom_pool else "custom_nexus_cost_pool"

    overhead_entries = frappe.db.sql(f"""
        SELECT acc.{target_field} AS pool_type, SUM(gl.debit) - SUM(gl.credit) AS total
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc ON gl.account = acc.name
        WHERE gl.posting_date BETWEEN %s AND %s
            AND gl.is_cancelled = 0
            AND acc.root_type = 'Expense'
            AND acc.{target_field} IN ('Labour', 'Energy', 'Admin')
        GROUP BY acc.{target_field}
    """, (start_date, end_date), as_dict=True)
    
    total_overheads = sum([flt(p.total) for p in overhead_entries if p.pool_type])

    # =========================================================================
    # QUERY 2: GL SALES TRUTH (Income Accounts - For Audit)
    # =========================================================================
    gl_sales_entries = frappe.db.sql("""
        SELECT SUM(gl.credit) - SUM(gl.debit) AS total
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc ON gl.account = acc.name
        WHERE gl.posting_date BETWEEN %s AND %s
            AND gl.is_cancelled = 0
            AND acc.root_type = 'Income'
    """, (start_date, end_date), as_dict=True)
    
    total_gl_sales = flt(gl_sales_entries[0].total) if gl_sales_entries else 0.0

    # =========================================================================
    # QUERY 3: ACTUAL SALES VOLUME (Grouped by Item & EXACT Rate)
    # =========================================================================
    sales_items = frappe.db.sql("""
        SELECT 
            sii.item_code, 
            sii.item_name, 
            sii.rate as exact_billed_rate, 
            SUM(sii.qty) as total_qty, 
            SUM(sii.amount) as total_billed_gross
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON sii.parent = si.name
        WHERE si.docstatus = 1 AND si.is_return = 0
        AND si.posting_date BETWEEN %s AND %s
        GROUP BY sii.item_code, sii.item_name, sii.rate
    """, (start_date, end_date), as_dict=True)

    if not sales_items:
        return {
            "sales_data": [],
            "total_overheads": total_overheads,
            "total_gl_sales": total_gl_sales
        }

    # =========================================================================
    # QUERY 4: METADATA & COSTING MAPPING (0-Lag Dictionary Lookups)
    # =========================================================================
    sold_item_codes = list(set([row.item_code for row in sales_items]))
    
    # 4a. Fetch Target Prices
    prices = frappe.get_all("Item Price", 
        filters={"item_code": ("in", sold_item_codes), "price_list": price_list},
        fields=["item_code", "price_list_rate"]
    )
    price_map = {p.item_code: flt(p.price_list_rate) for p in prices}

    # 4b. Fetch True Landed Costs directly from new DocType
    costings = frappe.get_all("Nexus Item Costing",
        filters={"item_code": ("in", sold_item_codes)},
        fields=["item_code", "true_landed_cost"]
    )
    costing_map = {c.item_code: flt(c.true_landed_cost) for c in costings}

    # 4c. Fetch Metadata for UI Filtering
    items_meta = frappe.get_all("Item",
        filters={"name": ("in", sold_item_codes)},
        fields=["name", "item_group", "custom_crystal_base"]
    )
    meta_map = {i.name: {"group": i.item_group, "base": i.custom_crystal_base} for i in items_meta}

    # =========================================================================
    # ASSEMBLE FINAL DATASET
    # =========================================================================
    for row in sales_items:
        row.target_price = price_map.get(row.item_code, 0.0)
        row.unit_landed_cost = costing_map.get(row.item_code, 0.0)
        
        meta = meta_map.get(row.item_code, {})
        row.item_group = meta.get("group", "")
        row.custom_crystal_base = meta.get("base", "")

    return {
        "sales_data": sales_items,
        "total_overheads": total_overheads,
        "total_gl_sales": total_gl_sales
    }