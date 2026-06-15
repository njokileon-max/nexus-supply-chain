import frappe
import json
import hashlib
from frappe.utils import flt

@frappe.whitelist()
def get_planning_data(**kwargs):
    filter_hash = hashlib.md5(json.dumps(kwargs, sort_keys=True).encode()).hexdigest()
    cache_key = f"planner_cache_{filter_hash}"
    all_keys_registry = "nexus_planner_all_keys"
    
    cached_res = frappe.cache().get_value(cache_key)
    if cached_res:
        return cached_res

    conditions = ["docstatus = 1", "status NOT IN ('Completed', 'Closed', 'Cancelled')"]
    values = {}
    
    mapping = {
        "company": "company", 
        "customer": "customer", 
        "territory": "territory",
        "sales_order_status": "status", 
        "delivery_region": "custom_delivery_region",
        "tonnage_plan": "custom_tonnage_plan"
    }
    
    for key, db_col in mapping.items():
        if kwargs.get(key):
            conditions.append(f"`{db_col}` = %({key})s")
            values[key] = kwargs.get(key)
            
    if kwargs.get("from_date"):
        conditions.append("transaction_date >= %(from_date)s")
        values["from_date"] = kwargs.get("from_date")
    if kwargs.get("to_date"):
        conditions.append("transaction_date <= %(to_date)s")
        values["to_date"] = kwargs.get("to_date")
    if kwargs.get("from_delivery_date"):
        conditions.append("delivery_date >= %(from_delivery_date)s")
        values["from_delivery_date"] = kwargs.get("from_delivery_date")
    if kwargs.get("to_delivery_date"):
        conditions.append("delivery_date <= %(to_delivery_date)s")
        values["to_delivery_date"] = kwargs.get("to_delivery_date")

    where_clause = " AND ".join(conditions)
    
    sos = frappe.db.sql(f"""
        SELECT name as sales_order, transaction_date as sales_order_date, 
        customer, customer_name, grand_total, territory, 
        custom_delivery_region as delivery_region, status 
        FROM `tabSales Order` WHERE {where_clause}
    """, values, as_dict=1)

    if not sos:
        return {"sos": [], "fgs": [], "subs": [], "rms": [], "stock": {}, "so_status": {}, "so_shortages": {}}

    so_names = [d.sales_order for d in sos]
    
    item_filter = ""
    if kwargs.get("item_code"):
        item_filter = " AND item_code = %(item_code)s"
        values["item_code"] = kwargs.get("item_code")

    so_items = frappe.db.sql(f"SELECT parent, item_code, qty FROM `tabSales Order Item` WHERE parent IN %(so_names)s {item_filter}", 
                             {"so_names": so_names, "item_code": kwargs.get("item_code")}, as_dict=1)
    
    default_boms = {d.item: d.name for d in frappe.db.sql("SELECT item, name FROM `tabBOM` WHERE is_active = 1 AND is_default = 1", as_dict=1)}
    
    bom_children = {}
    for d in frappe.db.sql("SELECT parent, item_code, qty_consumed_per_unit FROM `tabBOM Item`", as_dict=1):
        bom_children.setdefault(d.parent, []).append(d)

    item_master = {d.name: {"name": d.item_name, "weight": d.weight_per_unit} 
                   for d in frappe.get_all("Item", fields=["name", "item_name", "weight_per_unit"])}

    stock_map = {d.item_code: {"actual": flt(d.actual)} for d in frappe.db.sql("""
        SELECT item_code, SUM(actual_qty) as actual FROM `tabBin` GROUP BY item_code
    """, as_dict=1)}

    wos = frappe.db.sql("""SELECT name, status, qty, produced_qty, production_item as item 
                           FROM `tabWork Order` WHERE status IN ('Not Started', 'In Progress')""", as_dict=1)
    nsq_map, ipq_map = {}, {}
    for w in wos:
        if w.status == "Not Started":
            nsq_map[w.item] = nsq_map.get(w.item, 0) + w.qty
        else:
            ipq_map[w.item] = ipq_map.get(w.item, 0) + (w.qty - w.produced_qty)

    fgs, subs, rms = [], [], []
    so_shortages = {}
    so_total_items_count = {}

    def explode(item_code, qty, p_so, p_fg, level):
        if level > 10: return
        bom = default_boms.get(item_code)
        info = item_master.get(item_code, {"name": item_code, "weight": 0})
        stock = stock_map.get(item_code, {"actual": 0})["actual"]
        
        entry = {
            "item_code": item_code, "item_name": info["name"], "qty": qty, "req_qty": qty, 
            "weight_per_unit": flt(info["weight"]), "parent": p_so, "sales_order": p_so,
            "parent_fg": p_fg, "level": level, "default_bom": bom
        }

        if level == 0:
            fgs.append(entry)
            so_total_items_count[p_so] = so_total_items_count.get(p_so, 0) + 1
            if stock < qty: 
                so_shortages.setdefault(p_so, []).append({"code": item_code, "name": info["name"]})
        elif bom: 
            subs.append(entry)
        else: 
            rms.append(entry)

        if bom in bom_children:
            for c in bom_children[bom]:
                explode(c.item_code, qty * c.qty_consumed_per_unit, p_so, p_fg or item_code, level + 1)

    for si in so_items:
        explode(si.item_code, si.qty, si.parent, si.item_code, 0)

    so_status = {}
    for s in so_names:
        shortages = so_shortages.get(s, [])
        total_items = so_total_items_count.get(s, 0)
        
        if not shortages:
            so_status[s] = "green"
        elif len(shortages) < total_items:
            so_status[s] = "orange"
        else:
            so_status[s] = "red"

    result = {
        "sos": sos, "fgs": fgs, "subs": subs, "rms": rms, "stock": stock_map, 
        "so_status": so_status, "so_shortages": so_shortages, 
        "not_started_qty": nsq_map, "in_process_qty": ipq_map
    }

    existing_keys = frappe.cache().get_value(all_keys_registry) or []
    if cache_key not in existing_keys:
        existing_keys.append(cache_key)
        frappe.cache().set_value(all_keys_registry, existing_keys)

    frappe.cache().set_value(cache_key, result, expires_in_sec=300)
    return result

def enqueue_clear_planner_cache(doc=None, method=None):
    """
    Called by hooks.py. Offloads the heavy cache clearing to a background worker.
    """
    frappe.enqueue(
        'nexus_supply_chain.nexus_supply_chain.page.nexus_inventory_sync.nexus_inventory_sync.clear_planner_cache',
        queue='default',
        timeout=300,
        is_async=True,
        at_front=True
    )

def clear_planner_cache():
    """
    The actual execution logic that clears out all registered planner cache keys.
    """
    all_keys_registry = "nexus_planner_all_keys"
    keys_to_clear = frappe.cache().get_value(all_keys_registry)
    
    if keys_to_clear:
        for key in keys_to_clear:
            frappe.cache().delete_value(key)
        
        frappe.cache().delete_value(all_keys_registry)
