# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nexus Supply Chain
# For license information, please see license.txt

import frappe
from frappe.utils import flt


class FastBOMExploder:
    """
    In-memory caching engine to perform rapid BOM explosions.
    Used by:
      - Delivery Note before_submit hook (snapshot COGS at time of delivery)
      - Delivery Margin Report (historical patch / backfill)
    Source Tracking (Price List vs System Valuation vs Zero Cost).
    """

    def __init__(self):
        self.load_system_data()

    def load_system_data(self):
        prices = frappe.db.sql("""
            SELECT item_code, price_list_rate
            FROM `tabItem Price`
            WHERE price_list = 'Current Market Price' AND buying = 1
            ORDER BY creation ASC
        """, as_dict=True)
        self.price_map = {p.item_code: flt(p.price_list_rate) for p in prices}

        vals = frappe.db.sql("SELECT name, valuation_rate, item_name, item_group FROM `tabItem`", as_dict=True)
        self.item_val_map = {v.name: flt(v.valuation_rate) for v in vals}

        # Only the default, active, submitted BOM per item — matches Item master's linked default BOM
        boms = frappe.db.sql("""
            SELECT name, item, quantity
            FROM `tabBOM`
            WHERE is_active = 1 AND is_default = 1 AND docstatus = 1
        """, as_dict=True)
        self.bom_map = {
            b.item: {"name": b.name, "qty": flt(b.quantity) or 1.0}
            for b in boms
        }

        self.bom_items_map = {}
        if boms:
            bom_names = tuple(b.name for b in boms)
            bom_items = frappe.db.sql("""
                SELECT parent, item_code, stock_qty
                FROM `tabBOM Item`
                WHERE parent IN %(bom_names)s
            """, {"bom_names": bom_names}, as_dict=True)
            for bi in bom_items:
                self.bom_items_map.setdefault(bi.parent, []).append(bi)

    def explode(self, item_code, depth=0, visited=None):
        """
        Recursively explodes an item's default BOM into a per-unit market cost.
        Returns: (unit_cost: float, sources: set[str])
        """
        if visited is None:
            visited = set()

        if item_code in visited:
            # Circular BOM reference guard — do not loop back into an ancestor
            return 0.0, set()

        bom_info = self.bom_map.get(item_code)

        if not bom_info:
            # Leaf / raw material — no BOM, so price comes from market data
            cost = self.price_map.get(item_code)
            if cost:
                return flt(cost), {"Price List"}

            cost = self.item_val_map.get(item_code, 0.0)
            if cost:
                return flt(cost), {"System Valuation"}

            return 0.0, {"Zero Cost"}

        # Sub-assembly — recurse into children
        visited.add(item_code)
        bom_name = bom_info["name"]
        bom_yield = bom_info["qty"]
        children = self.bom_items_map.get(bom_name, [])

        total_bom_cost = 0.0
        sources = set()

        for child in children:
            child_item = child.item_code
            child_qty = flt(child.stock_qty)

            child_unit_cost, child_sources = self.explode(child_item, depth + 1, visited.copy())
            total_bom_cost += (child_qty * child_unit_cost)
            sources.update(child_sources)

        visited.discard(item_code)
        return flt(total_bom_cost / bom_yield), sources