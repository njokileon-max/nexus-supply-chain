# -*- coding: utf-8 -*-
# Copyright (c) 2026, Nexus Supply Chain
# For license information, please see license.txt

import frappe
from frappe.utils import flt
from nexus_supply_chain.nexus_supply_chain.utils.bom_exploder import FastBOMExploder


def set_cogs_snapshot(doc, method):
    """
    before_submit hook on Delivery Note.
    Snapshots theoretical market COGS onto each Delivery Note Item child row
    using the item's default BOM explosion, at the market prices/valuations
    in effect right now (i.e. at time of delivery submission).

    This locks the historical margin figure so later price/BOM changes
    do not retroactively alter already-submitted deliveries.
    """
    if not doc.items:
        return

    engine = FastBOMExploder()
    cogs_cache = {}

    for item_row in doc.items:
        item_code = item_row.item_code
        qty = flt(item_row.qty)

        if item_code not in cogs_cache:
            unit_cost, sources = engine.explode(item_code)
            cogs_cache[item_code] = unit_cost
        else:
            unit_cost = cogs_cache[item_code]

        item_row.custom_unit_market_cogs = unit_cost
        item_row.custom_total_market_cogs = flt(unit_cost * qty)