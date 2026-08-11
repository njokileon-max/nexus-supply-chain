# apps/nexus_supply_chain/nexus_supply_chain/routing/margin_analysis.py

import frappe
from frappe import _
from frappe.utils import flt

from nexus_supply_chain.routing.optimizer_api import calculate_trip_fuel_cost

VAT_RATE = 0.16


def _normalize_sales_orders(sales_orders):

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

    seen = set()
    unique_names = []
    for name in names:
        if name not in seen:
            seen.add(name)
            unique_names.append(name)
    return unique_names


def _explode_items_and_compute_amount_cogs(sales_order_names):

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

    so_names = _normalize_sales_orders(sales_orders)

    if not so_names:
        frappe.throw(_("No Sales Orders provided for margin analysis."))

    total_amount, total_cogs, zero_cost_items = _explode_items_and_compute_amount_cogs(so_names)

    revenue_excl_vat = total_amount / (1 + VAT_RATE) if total_amount else 0.0
    vat_amount = total_amount - revenue_excl_vat

    gross_profit = revenue_excl_vat - total_cogs
    gross_margin_percentage = (gross_profit / revenue_excl_vat * 100) if revenue_excl_vat > 0 else 0.0

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

            fuel_error_message = str(e)
            frappe.log_error(
                f"Margin analysis fuel estimate failed: {e}",
                "Nexus Margin Analysis — Fuel Estimate"
            )

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