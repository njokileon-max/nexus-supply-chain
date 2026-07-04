# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nexus Supply Chain
# For license information, please see license.txt

import frappe
from frappe.utils import flt

class FastBOMExploder:
    """
    In-memory caching engine to perform rapid BOM explosions for UI display.
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

        if visited is None:
            visited = set()

        if item_code in visited:
            return 0.0, set()

        bom_info = self.bom_map.get(item_code)

        if not bom_info:
            cost = self.price_map.get(item_code)
            if cost:
                return flt(cost), {"Price List"}

            cost = self.item_val_map.get(item_code, 0.0)
            if cost:
                return flt(cost), {"System Valuation"}

            return 0.0, {"Zero Cost"}

        visited.add(item_code)
        bom_name = bom_info["name"]
        bom_yield = bom_info["qty"]
        children = self.bom_items_map.get(bom_name, [])

        total_bom_cost = 0.0
        sources = set()

        for child in children:
            child_item = child.item_code
            child_qty  = flt(child.stock_qty)

            child_unit_cost, child_sources = self.explode(child_item, depth + 1, visited.copy())
            total_bom_cost += (child_qty * child_unit_cost)
            sources.update(child_sources)

        visited.discard(item_code)
        return flt(total_bom_cost / bom_yield), sources


@frappe.whitelist()
def get_delivery_margin_data(from_date, to_date):

    if not from_date or not to_date:
        return []

    data = frappe.db.sql("""
        SELECT
            dn.name as delivery_note,
            dn.posting_date,
            dn.customer,
            dni.item_code,
            dni.item_name,
            dni.qty,
            dni.rate,
            dni.amount as total_amount,
            dn.is_return,
            so.custom_delivery_region
        FROM `tabDelivery Note Item` dni
        JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        LEFT JOIN `tabSales Order` so ON so.name = dni.against_sales_order
        WHERE dn.docstatus = 1
          AND dn.status = 'Completed'
          AND dn.posting_date >= %(from_date)s
          AND dn.posting_date <= %(to_date)s
        ORDER BY dn.posting_date DESC, dn.name DESC
    """, {
        "from_date": from_date,
        "to_date": to_date
    }, as_dict=True)

    engine = FastBOMExploder()
    rows = []

    cogs_cache = {}

    for row in data:
        item_code = row.get("item_code")

        if item_code not in cogs_cache:
            unit_cogs, sources_set = engine.explode(item_code)
            source_string = ", ".join(sorted(list(sources_set))) if sources_set else "Unknown"
            cogs_cache[item_code] = (unit_cogs, source_string)
        else:
            unit_cogs, source_string = cogs_cache[item_code]

        qty = flt(row.get("qty", 0))
        rate = flt(row.get("rate", 0))
        total_amount = flt(row.get("total_amount", 0))  # from DB (dni.amount) to avoid rounding drift

        total_cogs = unit_cogs * qty
        gross_margin_inc_vat = total_amount - total_cogs

        revenue_ex_vat = total_amount / 1.16
        gp_ex_vat = revenue_ex_vat - total_cogs

        gp_percent = (gp_ex_vat / revenue_ex_vat * 100) if revenue_ex_vat != 0 else 0.0

        rows.append({
            "delivery_note": row.get("delivery_note"),
            "posting_date": row.get("posting_date"),
            "customer": row.get("customer"),
            "region": row.get("custom_delivery_region") or "Unassigned",
            "item_code": item_code,
            "item_name": row.get("item_name"),
            "is_return": bool(row.get("is_return")),
            "qty": qty,
            "rate": rate,
            "total_amount": total_amount,
            "unit_cogs": unit_cogs,
            "cogs_source": source_string,
            "total_cogs": total_cogs,
            "gross_margin": gross_margin_inc_vat,
            "gp_ex_vat": gp_ex_vat,
            "gp_percent": gp_percent
        })

    return rows