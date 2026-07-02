# -*- coding: utf-8 -*-
import frappe

class FastBOMExploder:

    def __init__(self):
        self.load_system_data()

    def load_system_data(self):
        prices = frappe.db.sql("""
            SELECT item_code, price_list_rate
            FROM `tabItem Price`
            WHERE price_list = 'Current Market Price' AND buying = 1
        """, as_dict=True)
        self.price_map = {p.item_code: frappe.utils.flt(p.price_list_rate) for p in prices}

        vals = frappe.db.sql("SELECT name, valuation_rate, item_name, item_group FROM `tabItem`", as_dict=True)
        self.item_val_map = {v.name: frappe.utils.flt(v.valuation_rate) for v in vals}
        self.item_meta_map = {v.name: {"item_name": v.item_name, "item_group": v.item_group} for v in vals}

        boms = frappe.db.sql("""
            SELECT name, item, quantity
            FROM `tabBOM`
            WHERE is_active = 1 AND is_default = 1 AND docstatus = 1
        """, as_dict=True)
        self.bom_map = {
            b.item: {"name": b.name, "qty": frappe.utils.flt(b.quantity) or 1.0}
            for b in boms
        }

        self.bom_items_map = {}
        if boms:
            bom_names = tuple(b.name for b in boms)
            bom_items = frappe.db.sql(f"""
    SELECT parent, item_code, stock_qty, item_name, uom, stock_uom
    FROM `tabBOM Item`
    WHERE parent IN {bom_names}
""", as_dict=True)
            for bi in bom_items:
                self.bom_items_map.setdefault(bi.parent, []).append(bi)

    def explode(self, item_code, depth=0, visited=None):
        if visited is None:
            visited = set()

        if item_code in visited:
            return 0.0, []

        bom_info = self.bom_map.get(item_code)

        # Base Case: Raw Material / Leaf Node
        if not bom_info:
            cost = self.price_map.get(item_code)
            if not cost:
                cost = self.item_val_map.get(item_code, 0.0)
            return frappe.utils.flt(cost), []

        # Recursive Case: Sub-Assembly or FG
        visited.add(item_code)
        bom_name = bom_info["name"]
        bom_yield = bom_info["qty"]
        children = self.bom_items_map.get(bom_name, [])

        total_bom_cost = 0.0
        components = []

        for child in children:
            child_item = child.item_code
            child_qty  = frappe.utils.flt(child.stock_qty)
            child_name = child.item_name
            child_uom  = child.get("uom") or child.get("stock_uom") or ""

            child_unit_cost, child_tree = self.explode(
                child_item, depth + 1, visited.copy()
            )
            extended_cost   = child_qty * child_unit_cost
            total_bom_cost += extended_cost

            is_sub = bool(self.bom_map.get(child_item))

            if is_sub:
                leaf_source = "Sub-Assembly"
            else:
                c_cost = self.price_map.get(child_item)
                if c_cost:
                    leaf_source = "Price List"
                else:
                    c_cost = self.item_val_map.get(child_item, 0.0)
                    leaf_source = "System Valuation" if c_cost else "Zero Cost"

            components.append({
                "item_code":      child_item,
                "item_name":      child_name,
                "uom":            child_uom,
                "qty":            child_qty,
                "unit_cost":      child_unit_cost,
                "extended_cost":  extended_cost,
                "source":         leaf_source,
                "is_subassembly": is_sub,
                "children":       child_tree,
                "depth":          depth + 1
            })

        visited.discard(item_code)
        return frappe.utils.flt(total_bom_cost / bom_yield), components

@frappe.whitelist()
def get_fg_cards():
    """Fetches all FG items and pre-calculates top-level COGS using the fast engine."""
    fg_bounds = frappe.db.get_value("Item Group", "Finished Goods", ["lft", "rgt"], as_dict=True)
    if not fg_bounds:
        return []

    fg_items = frappe.db.sql("""
        SELECT i.name as item_code, i.item_name
        FROM `tabItem` i
        JOIN `tabItem Group` ig ON i.item_group = ig.name
        WHERE i.disabled = 0
          AND ig.lft >= %s AND ig.rgt <= %s
    """, (fg_bounds.lft, fg_bounds.rgt), as_dict=True)

    engine = FastBOMExploder()
    results = []
    
    for it in fg_items:
        if it.item_code in engine.bom_map:
            total_cogs, _ = engine.explode(it.item_code)
            results.append({
                "item_code": it.item_code,
                "item_name": it.item_name,
                "total_cogs": total_cogs
            })
            
    # Sort alphabetically by Item Name
    return sorted(results, key=lambda k: k['item_name'])

@frappe.whitelist()
def get_bom_explosion(item_code):
    """Triggered on click to return the fully traced formulation tree."""
    engine = FastBOMExploder()
    total_cogs, tree = engine.explode(item_code)
    bom_info = engine.bom_map.get(item_code, {})
    return {
        "item_code": item_code,
        "bom_no": bom_info.get("name", ""),
        "total_cogs": total_cogs,
        "tree": tree
    }