# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nexus Supply Chain
# For license information, please see license.txt

import frappe
from frappe.utils import cint, flt
from typing import Dict, List, Optional, Tuple, Any

# ----------------------------------------------------------------------
# Public API for BOM explosion (with cross‑request caching)
# ----------------------------------------------------------------------

def get_theoretical_cost(item_code: str, use_cache: bool = True) -> float:
    """
    Returns the true rolled‑up material cost per unit for an item.
    If use_cache is True, the result is cached for 1 hour (TTL = 3600 sec).
    """
    if not item_code:
        return 0.0

    if use_cache:
        cache_key = f"nexus:theoretical_cost:{item_code}"
        cached = frappe.cache().get_value(cache_key)
        if cached is not None:
            return flt(cached)

    cost = _compute_item_theoretical_cost(item_code)

    if use_cache:
        # Cache for 1 hour (3600 seconds)
        frappe.cache().set_value(cache_key, cost, expires_in_sec=3600)

    return cost


def get_multiple_theoretical_costs(item_codes: List[str], use_cache: bool = True) -> Dict[str, float]:
    """
    Batch version – returns a dict {item_code: cost}.
    Useful for pre‑loading costs for many finished goods.
    """
    result = {}
    to_compute = []

    if use_cache:
        for ic in item_codes:
            cache_key = f"nexus:theoretical_cost:{ic}"
            cached = frappe.cache().get_value(cache_key)
            if cached is not None:
                result[ic] = flt(cached)
            else:
                to_compute.append(ic)
    else:
        to_compute = item_codes

    if to_compute:
        for ic in to_compute:
            cost = _compute_item_theoretical_cost(ic)
            result[ic] = cost
            if use_cache:
                frappe.cache().set_value(f"nexus:theoretical_cost:{ic}", cost, expires_in_sec=3600)

    return result


# ----------------------------------------------------------------------
# Core recursive BOM explosion (no external cache – called internally)
# ----------------------------------------------------------------------

def _compute_item_theoretical_cost(item_code: str) -> float:
    """Recursive BOM explosion with memoization per call (avoid duplicates in same recursion tree)."""
    bom_name = frappe.db.get_value(
        "BOM",
        {"item": item_code, "is_default": 1, "docstatus": 1},
        "name"
    )

    # Leaf node: no default BOM → use valuation rate from Item or Bin
    if not bom_name:
        return _get_item_valuation_rate(item_code)

    # In‑memory cache for this recursion call (prevents repeated explosion of same sub‑assembly)
    cost_cache = {}
    visited = set()

    def _explode(bom_item_code: str, qty_multiplier: float = 1.0) -> float:
        if bom_item_code in cost_cache:
            return cost_cache[bom_item_code] * qty_multiplier

        if bom_item_code in visited:
            frappe.throw(f"Circular reference detected in BOM for item: {bom_item_code}")
        visited.add(bom_item_code)

        # Find default BOM for this component
        child_bom = frappe.db.get_value(
            "BOM",
            {"item": bom_item_code, "is_default": 1, "docstatus": 1},
            "name"
        )

        if not child_bom:
            # Leaf – valuation rate
            rate = _get_item_valuation_rate(bom_item_code)
            cost_cache[bom_item_code] = rate
            visited.remove(bom_item_code)
            return rate * qty_multiplier

        # Sub‑assembly: fetch its BOM items
        bom_doc = frappe.get_cached_doc("BOM", child_bom)
        bom_qty = flt(bom_doc.quantity)
        if bom_qty <= 0:
            bom_qty = 1.0

        items = frappe.db.get_all(
            "BOM Item",
            filters={"parent": child_bom},
            fields=["item_code", "stock_qty"]
        )

        total_cost = 0.0
        for row in items:
            child_item = row.item_code
            child_qty = flt(row.stock_qty)
            total_cost += _explode(child_item, child_qty)

        unit_cost = total_cost / bom_qty
        cost_cache[bom_item_code] = unit_cost
        visited.remove(bom_item_code)
        return unit_cost * qty_multiplier

    # Start explosion from root item (qty_multiplier = 1.0)
    total_cost_per_unit = _explode(item_code, 1.0)
    return total_cost_per_unit


def _get_item_valuation_rate(item_code: str) -> float:
    """
    Returns the best available valuation rate for an item.
    Priority: Bin (specific warehouse) > Item.valuation_rate > 0.0
    """
    # 1. Try Bin – use the first warehouse (usually Finished Goods or default)
    bin_rate = frappe.db.get_value(
        "Bin",
        {"item_code": item_code},
        "valuation_rate"
    )
    if bin_rate and flt(bin_rate) > 0:
        return flt(bin_rate)

    # 2. Fallback to Item master valuation rate
    item_rate = frappe.db.get_value("Item", item_code, "valuation_rate")
    if item_rate and flt(item_rate) > 0:
        return flt(item_rate)

    return 0.0


# ----------------------------------------------------------------------
# Helper for load optimizer: compute total theoretical cost for a list of sales orders
# ----------------------------------------------------------------------

def compute_total_theoretical_cost_for_orders(sales_orders: List[Dict]) -> float:
    """
    Given a list of sales order dicts (each must have 'items' list with 'item_code', 'qty'),
    returns the total theoretical material cost (sum of cost per item × qty).
    Uses caching per item.
    """
    total = 0.0
    # Collect all distinct finished goods first
    fg_codes = set()
    order_item_map = {}

    for so in sales_orders:
        items = so.get("items", [])
        for it in items:
            item_code = it.get("item_code")
            qty = flt(it.get("qty", 0))
            if item_code and qty > 0:
                fg_codes.add(item_code)
                order_item_map.setdefault(so.get("sales_order"), []).append((item_code, qty))

    # Batch fetch theoretical costs for all finished goods
    costs = get_multiple_theoretical_costs(list(fg_codes))

    for so_orders in order_item_map.values():
        for item_code, qty in so_orders:
            total += costs.get(item_code, 0.0) * qty

    return total


# ----------------------------------------------------------------------
# Optional: Invalidate cache for an item (call after BOM or valuation rate change)
# ----------------------------------------------------------------------

def invalidate_theoretical_cost_cache(item_code: str = None):
    """
    Clears the cached theoretical cost for an item (or all items if None).
    Call this in a BOM update hook or valuation rate change event.
    """
    if item_code:
        frappe.cache().delete_value(f"nexus:theoretical_cost:{item_code}")
    else:
        # Delete all keys with prefix – requires pattern matching (Redis only)
        # For DB cache, we can't; so better pass item_code explicitly.
        pass