# apps/nexus_supply_chain/nexus_supply_chain/routing/margin_analysis.py

import frappe
from frappe import _
from frappe.utils import flt

from nexus_supply_chain.routing.optimizer_api import calculate_trip_fuel_cost

# Standard Kenyan VAT rate. Matches the rate already hardcoded in
# nexus_load_optimizer.py (SALES_TAX_RATE = 0.16) — kept as a local constant
# here rather than importing it, since margin_analysis.py is meant to be a
# standalone, reusable engine (Truck Assignment today, potentially other
# callers later) and shouldn't carry a dependency on the Load Optimizer file.
VAT_RATE = 0.16


def _normalize_sales_orders(sales_orders):
    """
    Accepts either a JSON-encoded string (as arrives from frappe.call) or an
    already-parsed list, and returns a clean list of Sales Order names.
    Also tolerates a list of dicts (e.g. [{"sales_order": "SO-0001"}, ...])
    in case a caller passes richer objects instead of bare names.
    """
    if isinstance(sales_orders, str):
        sales_orders = frappe.parse_json(sales_orders) or []

    if not sales_orders:
        return []

    names = []
    for so in sales_orders:
        if isinstance(so, dict):
            name = so.get("sales_order") or so.get("name")
        else:
            name = so
        if name:
            names.append(name)

    # De-duplicate while preserving order (a truck's order list should never
    # contain the same SO twice, but the awaiting-orders + "include current
    # load" merge on the frontend could theoretically double it up).
    seen = set()
    unique_names = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)
    return unique_names


def _explode_items_and_compute_amount_cogs(sales_order_names):
    """
    Bulk-fetches every Sales Order Item row for the given SOs in ONE query,
    resolves each distinct item_code's Default BOM in ONE query, resolves
    each distinct BOM's (total_cost, quantity) in ONE query — so this stays
    O(3 queries) regardless of how many orders or line items are involved,
    not O(n) per item like a naive per-row frappe.db.get_value loop would be.

    Returns (total_amount, total_cogs, zero_cost_items) where zero_cost_items
    is a list of item_codes that had no Default BOM (or a BOM with no cost/
    quantity data), so their COGS contribution was 0 — surfaced to the panel
    as an accuracy warning, same pattern as the Load Optimizer's margin engine.
    """
    if not sales_order_names:
        return 0.0, 0.0, []

    placeholders = ", ".join(["%s"] * len(sales_order_names))

    item_rows = frappe.db.sql(f"""
        SELECT item_code, qty, rate
        FROM `tabSales Order Item`
        WHERE parent IN ({placeholders})
    """, tuple(sales_order_names), as_dict=True)

    if not item_rows:
        return 0.0, 0.0, []

    distinct_item_codes = list({r.item_code for r in item_rows if r.item_code})

    default_bom_map = {}
    if distinct_item_codes:
        bom_placeholders = ", ".join(["%s"] * len(distinct_item_codes))
        item_bom_rows = frappe.db.sql(f"""
            SELECT name AS item_code, default_bom
            FROM `tabItem`
            WHERE name IN ({bom_placeholders})
        """, tuple(distinct_item_codes), as_dict=True)
        default_bom_map = {r.item_code: r.default_bom for r in item_bom_rows if r.default_bom}

    distinct_boms = list(set(default_bom_map.values()))
    bom_unit_cost_map = {}
    if distinct_boms:
        bom_placeholders = ", ".join(["%s"] * len(distinct_boms))
        bom_rows = frappe.db.sql(f"""
            SELECT name AS bom_name, total_cost, quantity
            FROM `tabBOM`
            WHERE name IN ({bom_placeholders})
        """, tuple(distinct_boms), as_dict=True)
        for r in bom_rows:
            qty = flt(r.quantity)
            if qty > 0:
                bom_unit_cost_map[r.bom_name] = flt(r.total_cost) / qty
            else:
                bom_unit_cost_map[r.bom_name] = 0.0

    total_amount = 0.0
    total_cogs = 0.0
    zero_cost_items = set()

    for row in item_rows:
        qty = flt(row.qty)
        rate = flt(row.rate)
        total_amount += qty * rate

        item_code = row.item_code
        bom_name = default_bom_map.get(item_code)
        unit_cost = bom_unit_cost_map.get(bom_name, 0.0) if bom_name else 0.0

        if not bom_name or unit_cost <= 0:
            zero_cost_items.add(item_code)

        total_cogs += qty * unit_cost

    return round(total_amount, 2), round(total_cogs, 2), sorted(zero_cost_items)


@frappe.whitelist()
def get_margin_analysis(sales_orders, distance_km=None, vehicle_type=None, truck_number=None):
    """
    Preview-only margin calculator for the Truck Assignment page's "Analyse
    Margins" action. Writes nothing to the database — this is purely for the
    person's own review before deciding to dispatch. The authoritative,
    persisted version of this same computation runs again server-side inside
    dispatch_truck() (crystal_custom), which calls this function directly via
    a same-process cross-app import rather than duplicating this logic.

    Args:
        sales_orders: JSON string or list of Sales Order names (or dicts
            containing a "sales_order"/"name" key) — the orders currently
            assigned to the truck, or the awaiting-orders selection.
        distance_km: float, the ORS-routed roundtrip distance already
            computed client-side (Truck Assignment) or server-side
            (dispatch_truck). None/omitted if no valid-coordinate stops
            exist yet — fuel and the narrowed figure are simply skipped,
            never estimated from a fabricated distance.
        vehicle_type: Vehicle Type name, if already known (e.g. truck's
            capacity_kg field). Takes priority over truck_number.
        truck_number: fallback resolution path, same as
            calculate_trip_fuel_cost's own truck_number resolution.

    Returns a dict — see the return statement for the full shape.
    """
    so_names = _normalize_sales_orders(sales_orders)

    if not so_names:
        frappe.throw(_("No Sales Orders provided for margin analysis."))

    total_amount, total_cogs, zero_cost_items = _explode_items_and_compute_amount_cogs(so_names)

    revenue_excl_vat = total_amount / (1 + VAT_RATE) if total_amount else 0.0
    vat_amount = total_amount - revenue_excl_vat

    gross_profit = revenue_excl_vat - total_cogs
    gross_margin_percentage = (gross_profit / revenue_excl_vat * 100) if revenue_excl_vat > 0 else 0.0

    # ── Fuel estimate — only attempted if a real distance was actually
    # computed (i.e. at least one order had valid coordinates). A missing
    # distance is treated as "unavailable," never silently coerced to 0,
    # since 0 km would understate fuel cost and overstate the margin.
    fuel_cost = 0.0
    fuel_litres = 0.0
    distance_available = distance_km is not None
    fuel_available = False
    fuel_error_message = None

    if distance_available:
        try:
            distance_km = flt(distance_km)
        except (TypeError, ValueError):
            distance_available = False
            distance_km = None

    if distance_available and (vehicle_type or truck_number):
        try:
            fuel_result = calculate_trip_fuel_cost(
                distance_km=distance_km,
                vehicle_type=vehicle_type,
                truck_number=truck_number
            )
            if fuel_result and fuel_result.get("status") == "success":
                fuel_cost = flt(fuel_result.get("estimated_fuel_cost"))
                fuel_litres = flt(fuel_result.get("total_litres_required"))
                fuel_available = True
        except Exception as e:
            # calculate_trip_fuel_cost uses frappe.throw for hard config
            # errors (e.g. consumption_km_per_ltr not set) — caught here so
            # a misconfigured Vehicle Type degrades the panel gracefully
            # ("fuel unavailable") instead of crashing the whole analysis.
            fuel_error_message = str(e)
            frappe.log_error(
                f"Margin analysis fuel estimate failed: {e}",
                "Nexus Margin Analysis — Fuel Estimate"
            )

    # ── Narrowed Gross Margin — gross profit further reduced by fuel cost
    # only when fuel was actually resolved. If fuel isn't available (no
    # vehicle assigned yet, or distance unavailable), the narrowed figure
    # simply equals the gross figure and the caller is expected to show
    # "assign a truck to estimate fuel" rather than a falsely-narrowed number.
    if fuel_available:
        narrowed_gross_profit = gross_profit - fuel_cost
    else:
        narrowed_gross_profit = gross_profit

    narrowed_gross_margin_percentage = (
        (narrowed_gross_profit / revenue_excl_vat * 100) if revenue_excl_vat > 0 else 0.0
    )

    return {
        "status": "success",
        "order_count": len(so_names),
        "sales_orders": so_names,

        "total_order_value": total_amount,
        "vat_rate_percentage": VAT_RATE * 100,
        "vat_amount": round(vat_amount, 2),
        "revenue_excl_vat": round(revenue_excl_vat, 2),

        "total_theoretical_cost": total_cogs,
        "gross_profit": round(gross_profit, 2),
        "gross_margin_percentage": round(gross_margin_percentage, 2),

        "distance_available": distance_available,
        "distance_km": round(distance_km, 2) if distance_available else None,

        "fuel_available": fuel_available,
        "fuel_error_message": fuel_error_message,
        "fuel_litres_required": round(fuel_litres, 2) if fuel_available else None,
        "approximate_fuel_cost": round(fuel_cost, 2) if fuel_available else None,

        "narrowed_gross_profit": round(narrowed_gross_profit, 2),
        "narrowed_gross_margin_percentage": round(narrowed_gross_margin_percentage, 2),

        "zero_cost_items": zero_cost_items,

        "currency": frappe.defaults.get_global_default("default_currency") or "KES"
    }


@frappe.whitelist()
def get_item_wise_margin_breakdown(sales_orders):
    """
    Line-item margin breakdown for the "Item-Wise Analysis" popup on the
    Truck Assignment page's Margin Analysis panel. Reuses the same
    sales-order-name normalization as get_margin_analysis but returns a
    per-line-item table instead of an aggregate summary.

    For each Sales Order Item row:
        - rate: as entered on the Sales Order (incl. VAT)
        - rate_excl_vat: rate / (1 + VAT_RATE)
        - default_bom: the FG item's Default BOM name, or None if unset
        - total_cogs: the Default BOM's raw total_cost (0 if no Default BOM,
          flagged via default_bom being None so the frontend can render it in red)
        - gross_margin: rate_excl_vat - total_cogs
        - gross_margin_percentage: gross_margin / rate_excl_vat * 100
    """
    so_names = _normalize_sales_orders(sales_orders)
    if not so_names:
        frappe.throw(_("No Sales Orders provided for item-wise margin analysis."))

    placeholders = ", ".join(["%s"] * len(so_names))
    item_rows = frappe.db.sql(f"""
        SELECT parent AS sales_order, item_code, item_name, qty, rate
        FROM `tabSales Order Item`
        WHERE parent IN ({placeholders})
        ORDER BY parent, idx
    """, tuple(so_names), as_dict=True)

    currency = frappe.defaults.get_global_default("default_currency") or "KES"

    if not item_rows:
        return {"status": "success", "rows": [], "currency": currency}

    distinct_item_codes = list({r.item_code for r in item_rows if r.item_code})
    default_bom_map = {}
    if distinct_item_codes:
        bom_placeholders = ", ".join(["%s"] * len(distinct_item_codes))
        item_bom_rows = frappe.db.sql(f"""
            SELECT name AS item_code, default_bom
            FROM `tabItem`
            WHERE name IN ({bom_placeholders})
        """, tuple(distinct_item_codes), as_dict=True)
        default_bom_map = {r.item_code: r.default_bom for r in item_bom_rows if r.default_bom}

    distinct_boms = list(set(default_bom_map.values()))
    bom_cost_map = {}
    if distinct_boms:
        bom_placeholders = ", ".join(["%s"] * len(distinct_boms))
        bom_rows = frappe.db.sql(f"""
            SELECT name AS bom_name, total_cost
            FROM `tabBOM`
            WHERE name IN ({bom_placeholders})
        """, tuple(distinct_boms), as_dict=True)
        bom_cost_map = {r.bom_name: flt(r.total_cost) for r in bom_rows}

    rows = []
    for r in item_rows:
        rate = flt(r.rate)
        rate_excl_vat = rate / (1 + VAT_RATE) if rate else 0.0

        bom_name = default_bom_map.get(r.item_code)
        has_bom = bool(bom_name) and bom_name in bom_cost_map
        total_cogs = bom_cost_map.get(bom_name, 0.0) if has_bom else 0.0

        gross_margin = rate_excl_vat - total_cogs
        gross_margin_percentage = (gross_margin / rate_excl_vat * 100) if rate_excl_vat > 0 else 0.0

        rows.append({
            "sales_order": r.sales_order,
            "item_code": r.item_code,
            "item_name": r.item_name,
            "rate": round(rate, 2),
            "rate_excl_vat": round(rate_excl_vat, 2),
            "qty": flt(r.qty),
            "default_bom": bom_name if has_bom else None,
            "total_cogs": round(total_cogs, 2),
            "gross_margin": round(gross_margin, 2),
            "gross_margin_percentage": round(gross_margin_percentage, 2),
        })

    return {
        "status": "success",
        "rows": rows,
        "vat_rate_percentage": VAT_RATE * 100,
        "currency": currency,
    }

