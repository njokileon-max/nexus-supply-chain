import frappe

def get_true_recursive_unit_cost(item_code, depth=0, visited=None):
    """
    Recursively explodes items down to leaf raw materials.
    Normalizes costs against BOM yields automatically without double-multiplying pack weights.
    """
    if visited is None:
        visited = set()
        
    # Safeguard against circular BOM dependencies
    if item_code in visited:
        return 0.0
        
    # Check if this item has an active, submitted default BOM
    bom_no = frappe.db.get_value("BOM", {"item": item_code, "is_default": 1, "docstatus": 1}, "name")
    
    # LEAF NODE CASE: No sub-BOM exists (Raw Material or Packaging)
    if not bom_no:
        # Pull live stock ledger valuation
        val_rate = frappe.db.get_value("Bin", {"item_code": item_code}, "valuation_rate")
        if not val_rate or float(val_rate) == 0.0:
            val_rate = frappe.db.get_value("Item", item_code, "valuation_rate") or 0.0
        return float(val_rate)
        
    # SUB-ASSEMBLY / PARENT CASE: Recurse into child items
    visited.add(item_code)
    bom_doc = frappe.get_doc("BOM", bom_no)
    bom_yield_qty = float(bom_doc.quantity or 1.0)
    
    total_accumulated_bom_cost = 0.0
    
    # Fetch all items in this BOM level in a single query
    bom_items = frappe.db.sql("""
        SELECT item_code, stock_qty 
        FROM `tabBOM Item` 
        WHERE parent = %s
    """, (bom_no,), as_dict=True)
    
    for item in bom_items:
        child_item = item.item_code
        child_qty = float(item.stock_qty)
        
        # Recursive step: Go deeper until hitting raw material leaf nodes
        child_unit_cost = get_true_recursive_unit_cost(child_item, depth + 1, visited.copy())
        
        extended_cost = child_qty * child_unit_cost
        total_accumulated_bom_cost += extended_cost
        
    # Normalize back to 1 unit of this parent item (Total Cost / Yield Quantity)
    return total_accumulated_bom_cost / bom_yield_qty


@frappe.whitelist()
def get_portfolio_data():
    """
    TEST MODE: Hardcoded to test specific items to check for computation timeouts.
    LOGIC FIX: Explicitly grabbing the valuation_rate strictly from the Item Master.
    """
    items_to_test = ('BIPHGSTAN02', 'FGWSTW0250JL', 'FGHGLBU053DL', 'FGSVEW0050DL')
    
    items = frappe.db.sql("""
        SELECT name as item_code, item_name, item_group, valuation_rate as static_item_val
        FROM `tabItem`
        WHERE disabled = 0
        AND name IN %s
    """, (items_to_test,), as_dict=True)

    portfolio_data = []

    for it in items:
        bom_no = frappe.db.get_value("BOM", {"item": it.item_code, "is_default": 1, "docstatus": 1}, "name")
        if not bom_no:
            continue  # Skip if no default BOM exists

        # 1. Calculate True Theoretical Cost via Recursion
        theoretical_cost = get_true_recursive_unit_cost(it.item_code)

        # 2. Force the System Value to equal the Item Master explicitly
        system_val = float(it.static_item_val or 0.0)

        # 3. Calculate Operational Variance
        variance = theoretical_cost - system_val

        portfolio_data.append({
            "item_code": it.item_code,
            "item_name": it.item_name,
            "item_group": it.item_group,
            "bom_no": bom_no,
            "theoretical_cost": theoretical_cost,
            "system_val": system_val,
            "variance": variance
        })

    return portfolio_data