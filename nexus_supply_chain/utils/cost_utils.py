# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nexus Supply Chain
# For license information, please see license.txt

import frappe
from frappe.utils import flt, now_datetime, get_first_day, get_last_day
from typing import Dict, List


# ----------------------------------------------------------------------
# In-Memory Market Cost Engine (aligned with TrueMarketCostEngine in
# nexus_dispatch_intelligence.py — Current Market price as primary
# source, Item.valuation_rate as fallback, zero-lag RAM mapping)
# ----------------------------------------------------------------------

class MarketCostEngine:
    """
    Matches the logic of TrueMarketCostEngine in the Dispatch Intelligence page.
    Loads all required data in bulk at construction time (zero per-item DB hits
    during BOM explosion), then resolves costs recursively with memoization.

    Leaf-node price priority:
        1. Current Market Price list (tabItem Price, buying = 1)
        2. Item.valuation_rate
        3. 0.0
    """

    def __init__(self):
        # 🚨 FIX 1: Add ORDER BY creation ASC so that dictionary iterations
        # overwrite older duplicates, leaving the latest entry active.
        prices = frappe.db.sql("""
            SELECT item_code, price_list_rate
            FROM `tabItem Price`
            WHERE price_list = 'Current Market Price'
              AND buying = 1
            ORDER BY creation ASC
        """, as_dict=True)
        self.price_map: Dict[str, float] = {
            p.item_code: flt(p.price_list_rate) for p in prices
        }

        # 2. Item-level valuation rates (fallback)
        item_vals = frappe.db.sql("""
            SELECT name, valuation_rate FROM `tabItem`
        """, as_dict=True)
        self.item_val_map: Dict[str, float] = {
            i.name: flt(i.valuation_rate) for i in item_vals
        }

        # 3. Default active BOMs (item → {name, qty})
        boms = frappe.db.sql("""
            SELECT name, item, quantity
            FROM `tabBOM`
            WHERE is_active = 1
              AND is_default = 1
              AND docstatus = 1
        """, as_dict=True)
        self.bom_map: Dict[str, dict] = {
            b.item: {"name": b.name, "qty": flt(b.quantity) or 1.0}
            for b in boms
        }

        # 4. BOM items (bulk fetch — avoids N+1 queries during explosion)
        bom_names = tuple(b.name for b in boms)
        self.bom_items_map: Dict[str, list] = {}
        if bom_names:
            bom_items = frappe.db.sql("""
                SELECT parent, item_code, stock_qty
                FROM `tabBOM Item`
                WHERE parent IN %s
            """, (bom_names,), as_dict=True)
            for bi in bom_items:
                self.bom_items_map.setdefault(bi.parent, []).append(bi)

        # Per-instance memoization and circular-reference guard
        self.cost_cache: Dict[str, float] = {}
        self.visited: set = set()
        
        # 🚨 FIX 2: Dynamic list container tracking items lacking valuation setups
        self.zero_cost_items: List[str] = []

    def get_cost(self, item_code: str) -> float:
        """
        Returns the true market cost per stock-UOM unit for item_code.
        Identical logic to TrueMarketCostEngine.get_cost().
        """
        if item_code in self.cost_cache:
            return self.cost_cache[item_code]

        # Circular reference guard — return 0.0 rather than infinite loop
        if item_code in self.visited:
            return 0.0

        self.visited.add(item_code)
        bom_info = self.bom_map.get(item_code)

        if not bom_info:
            # Leaf node: Current Market price → Item valuation fallback
            cost = self.price_map.get(item_code)
            if not cost:
                cost = self.item_val_map.get(item_code, 0.0)
            
            # 🚨 FIX 2: Trap zero valuations and cache them to tracking system
            if flt(cost) <= 0.0:
                if item_code not in self.zero_cost_items:
                    self.zero_cost_items.append(item_code)

            self.cost_cache[item_code] = cost
            self.visited.discard(item_code)
            return cost

        # Sub-assembly: sum children recursively then normalise by BOM yield
        bom_name = bom_info["name"]
        bom_yield = bom_info["qty"]
        children = self.bom_items_map.get(bom_name, [])

        total_bom_cost = 0.0
        for child in children:
            total_bom_cost += self.get_cost(child.item_code) * flt(child.stock_qty)

        unit_cost = total_bom_cost / bom_yield
        
        # Catch sub-assemblies costing nothing
        if flt(unit_cost) <= 0.0 and item_code not in self.zero_cost_items:
            self.zero_cost_items.append(item_code)

        self.cost_cache[item_code] = unit_cost
        self.visited.discard(item_code)
        return unit_cost


# ----------------------------------------------------------------------
# Public API — used by both the Load Optimizer and any other caller
# ----------------------------------------------------------------------

def compute_total_theoretical_cost_for_orders(sales_orders: List[dict]) -> float:
    """
    Given a list of sales-order dicts (each must contain an 'items' list
    with 'item_code' and 'qty'), returns the total theoretical market
    cost (sum of market_cost_per_unit × qty).

    Uses MarketCostEngine (Current Market Price → valuation_rate fallback)
    to match the COGS figures shown in Dispatch Intelligence.
    """
    if not sales_orders:
        return 0.0

    engine = MarketCostEngine()
    total = 0.0

    for so in sales_orders:
        for item in so.get("items", []):
            item_code = item.get("item_code")
            qty = flt(item.get("qty", 0))
            if item_code and qty > 0:
                total += engine.get_cost(item_code) * qty

    return total


def get_theoretical_cost(item_code: str, use_cache: bool = True) -> float:
    """
    Returns the true rolled-up market cost per unit for a single item.
    Optionally caches the result in Redis for 1 hour.
    """
    if not item_code:
        return 0.0

    if use_cache:
        cache_key = f"nexus:market_cost:{item_code}"
        cached = frappe.cache().get_value(cache_key)
        if cached is not None:
            return flt(cached)

    cost = MarketCostEngine().get_cost(item_code)

    if use_cache:
        frappe.cache().set_value(cache_key, cost, expires_in_sec=3600)

    return cost


def get_multiple_theoretical_costs(
    item_codes: List[str], use_cache: bool = True
) -> Dict[str, float]:
    """
    Batch version — returns {item_code: market_cost}.
    Shares a single MarketCostEngine instance for efficiency.
    """
    result: Dict[str, float] = {}
    to_compute: List[str] = []

    if use_cache:
        for ic in item_codes:
            cache_key = f"nexus:market_cost:{ic}"
            cached = frappe.cache().get_value(cache_key)
            if cached is not None:
                result[ic] = flt(cached)
            else:
                to_compute.append(ic)
    else:
        to_compute = list(item_codes)

    if to_compute:
        engine = MarketCostEngine()
        for ic in to_compute:
            cost = engine.get_cost(ic)
            result[ic] = cost
            if use_cache:
                frappe.cache().set_value(
                    f"nexus:market_cost:{ic}", cost, expires_in_sec=3600
                )

    return result


def invalidate_theoretical_cost_cache(item_code: str = None):
    """
    Clears cached market cost for an item (or all, if item_code is None).
    Hook this into BOM save/update and Item Price change events.
    """
    if item_code:
        frappe.cache().delete_value(f"nexus:market_cost:{item_code}")


# ----------------------------------------------------------------------
# Overhead Allocation Engine (0-Lag Caching for Real-Time Net Profit)
# ----------------------------------------------------------------------

def get_current_month_overhead_metrics(force_refresh: bool = False) -> Dict[str, float]:
    """
    Dynamically computes the Total Global Overheads (Labour, Energy, Admin) 
    and Total Invoiced Sales for the CURRENT month to establish the company's 
    baseline overhead ratio.
    
    Results are cached in Redis for 1 hour to prevent DB locking when analyzing 
    dozens of Load Plans interactively.
    """
    today = now_datetime()
    start_date = get_first_day(today).strftime("%Y-%m-%d")
    end_date = get_last_day(today).strftime("%Y-%m-%d")
    
    cache_key = f"nexus:overhead_metrics:{start_date}"

    if not force_refresh:
        cached_data = frappe.cache().get_value(cache_key)
        if cached_data:
            return cached_data

    # 1. Fetch the 3 Nexus Cost Pools for the current month
    has_custom_pool = frappe.db.has_column("Account", "nexus_cost_pool")
    target_field = "nexus_cost_pool" if has_custom_pool else "custom_nexus_cost_pool"

    pools = frappe.db.sql(f"""
        SELECT SUM(gl.debit) - SUM(gl.credit) AS total
        FROM `tabGL Entry` gl
        INNER JOIN `tabAccount` acc ON gl.account = acc.name
        WHERE gl.posting_date BETWEEN %s AND %s
            AND gl.is_cancelled = 0
            AND acc.root_type = 'Expense'
            AND acc.{target_field} IN ('Labour', 'Energy', 'Admin')
    """, (start_date, end_date), as_dict=True)

    total_global_overheads = flt(pools[0].total) if pools and pools[0].total else 0.0

    # 2. Fetch Total Invoiced Sales for the current month (Strictly docstatus 1, non-return)
    sales_data = frappe.db.sql("""
        SELECT SUM(base_grand_total) as total_sales
        FROM `tabSales Invoice`
        WHERE posting_date BETWEEN %s AND %s
            AND docstatus = 1
            AND is_return = 0
    """, (start_date, end_date), as_dict=True)

    total_invoiced_sales = flt(sales_data[0].total_sales) if sales_data and sales_data[0].total_sales else 0.0
    
    # 3. Compile and Cache
    result = {
        "total_global_overheads": total_global_overheads,
        "total_invoiced_sales": total_invoiced_sales
    }

    frappe.cache().set_value(cache_key, result, expires_in_sec=3600)
    
    return result