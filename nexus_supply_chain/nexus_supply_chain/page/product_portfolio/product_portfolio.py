# gnleon29@gmail.com

import frappe
from frappe.utils import flt

def publish_catalog_update(doc, method):
    frappe.publish_realtime('nexus_catalog_sync', message={'status': 'updated'})

@frappe.whitelist()
def get_active_price_lists():
    return frappe.get_all("Price List", filters={"selling": 1, "enabled": 1}, pluck="name")

@frappe.whitelist()
def get_existing_price_map(price_list):
    rows = frappe.db.sql("""
        SELECT name, item_code
        FROM `tabItem Price`
        WHERE price_list = %(price_list)s
          AND selling = 1
    """, {"price_list": price_list}, as_dict=True)
    
    return {r.item_code: r.name for r in rows}

@frappe.whitelist()
def get_existing_costing_map():
    rows = frappe.db.sql("""
        SELECT name, item_code
        FROM `tabNexus Item Costing`
    """, as_dict=True)
    
    return {r.item_code: r.name for r in rows}

@frappe.whitelist()
def generate_costing_data_import_handoff(csv_content, strategy):
    try:
        file_name = f"Costing_Update_{frappe.utils.nowdate()}.csv"
        
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": file_name,
            "content": csv_content,
            "is_private": 1
        })
        file_doc.save(ignore_permissions=True)
        
        import_type = "Insert New Records" if strategy == "insert" else "Update Existing Records"
        
        data_import = frappe.get_doc({
            "doctype": "Data Import",
            "reference_doctype": "Nexus Item Costing",
            "import_type": import_type,
            "import_file": file_doc.file_url,
            "submit_after_import": 0
        })
        data_import.insert(ignore_permissions=True)
        
        frappe.db.commit()
        return data_import.name
        
    except Exception as e:
        frappe.log_error(message=frappe.get_traceback(), title="Costing Data Import Handoff Error")
        frappe.throw(f"Failed to generate Costing Data Import template: {str(e)}")

@frappe.whitelist()
def generate_data_import_handoff(csv_content, strategy, price_list):
    try:
        file_name = f"Pricing_Update_{price_list.replace(' ', '_')}_{frappe.utils.nowdate()}.csv"
        
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": file_name,
            "content": csv_content,
            "is_private": 1
        })
        file_doc.save(ignore_permissions=True)
        
        import_type = "Insert New Records" if strategy == "insert" else "Update Existing Records"
        
        data_import = frappe.get_doc({
            "doctype": "Data Import",
            "reference_doctype": "Item Price",
            "import_type": import_type,
            "import_file": file_doc.file_url,
            "submit_after_import": 0  
        })
        data_import.insert(ignore_permissions=True)
        
        frappe.db.commit()
        return data_import.name
        
    except Exception as e:
        frappe.log_error(message=frappe.get_traceback(), title="Data Import Handoff Error")
        frappe.throw(f"Failed to generate Data Import template: {str(e)}")


@frappe.whitelist()
def get_portfolio_matrix():
    active_matrix = frappe.db.get_value("Nexus Cost Allocation Period", 
        {"is_active": 1, "docstatus": 1}, 
        "name"
    )
    
    rate_map = {}
    if active_matrix:
        rows = frappe.get_all("Nexus Costing Matrix Row", 
            filters={"parent": active_matrix}, 
            fields=["item_group", "rate_per_kg"]
        )
        for r in rows:
            rate_map[r.item_group] = flt(r.rate_per_kg)

    items = frappe.db.sql("""
        SELECT 
            name as item_code, 
            item_name, 
            item_group, 
            default_bom, 
            valuation_rate,
            custom_crystal_base,
            custom_nexus_uom_pool,
            CASE WHEN weight_per_unit > 0 THEN weight_per_unit ELSE 1.0 END as weight
        FROM `tabItem`
        WHERE is_stock_item = 1 AND disabled = 0
    """, as_dict=True)

    bin_vals = frappe.db.sql("""
        SELECT item_code, MAX(valuation_rate) as val_rate
        FROM `tabBin`
        WHERE valuation_rate > 0
        GROUP BY item_code
    """, as_dict=True)
    bin_val_map = {b.item_code: flt(b.val_rate) for b in bin_vals}

    item_vals = frappe.db.sql("SELECT name, valuation_rate FROM `tabItem`", as_dict=True)
    item_val_map = {i.name: flt(i.valuation_rate) for i in item_vals}

    boms = frappe.db.sql("""
        SELECT name, item, quantity
        FROM `tabBOM`
        WHERE docstatus = 1 AND is_active = 1 AND is_default = 1
    """, as_dict=True)
    
    bom_map = {b.name: b for b in boms}
    item_to_bom_map = {b.item: b.name for b in boms if b.item}

    bom_items = frappe.db.sql("""
        SELECT parent, item_code, stock_qty
        FROM `tabBOM Item`
        WHERE parent IN (
            SELECT name FROM `tabBOM` WHERE docstatus = 1 AND is_active = 1 AND is_default = 1
        )
    """, as_dict=True)

    bom_structure = {}
    for bi in bom_items:
        bom_structure.setdefault(bi.parent, []).append(bi)

    cost_cache = {}
    visited_nodes = set()

    def calculate_in_memory_cost(item_code):
        if item_code in cost_cache:
            return cost_cache[item_code]

        if item_code in visited_nodes:
            return 0.0 
            
        visited_nodes.add(item_code)
        bom_name = item_to_bom_map.get(item_code)

        if not bom_name:
            val_rate = bin_val_map.get(item_code)
            if not val_rate or val_rate == 0.0:
                val_rate = item_val_map.get(item_code, 0.0)
            
            cost_cache[item_code] = val_rate
            visited_nodes.remove(item_code)
            return val_rate

        bom_data = bom_map.get(bom_name)
        bom_yield_qty = flt(bom_data.quantity) or 1.0
        children = bom_structure.get(bom_name, [])

        total_accumulated_bom_cost = 0.0
        for child in children:
            child_unit_cost = calculate_in_memory_cost(child.item_code)
            extended_cost = child_unit_cost * flt(child.stock_qty)
            total_accumulated_bom_cost += extended_cost

        unit_cost = total_accumulated_bom_cost / bom_yield_qty
        
        cost_cache[item_code] = unit_cost
        visited_nodes.remove(item_code)
        return unit_cost

    portfolio_results = []
    
    for item in items:
        if item.default_bom: 
            theo_cost = calculate_in_memory_cost(item.item_code)
            sys_val = flt(item.valuation_rate)
            variance = theo_cost - sys_val
            
            overhead_rate = rate_map.get(item.item_group, 0.0)
            overhead_cost = overhead_rate * flt(item.weight)
            true_landed_cost = theo_cost + overhead_cost

            portfolio_results.append({
                "item_code": item.item_code,
                "item_name": item.item_name,
                "item_group": item.item_group,
                "custom_crystal_base": item.custom_crystal_base,
                "custom_nexus_uom_pool": item.custom_nexus_uom_pool,
                "default_bom": item.default_bom,
                "weight": flt(item.weight),
                "system_valuation_rate": sys_val,
                "theoretical_cost": theo_cost,
                "variance": variance,
                "overhead_rate": overhead_rate,
                "overhead_cost": overhead_cost,
                "true_landed_cost": true_landed_cost,
                "system_suggested_price": 0.0,
                "regional_price": 0.0
            })

    return {
        "rates": rate_map,
        "portfolio": portfolio_results
    }
