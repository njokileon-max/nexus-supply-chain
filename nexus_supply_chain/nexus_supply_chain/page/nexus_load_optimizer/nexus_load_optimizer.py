# apps/nexus_supply_chain/nexus_supply_chain/page/nexus_load_optimizer/nexus_load_optimizer.py

import frappe
import requests
import json
from frappe.utils import flt, getdate, add_to_date, now_datetime
from datetime import datetime

from nexus_supply_chain.utils.cost_utils import compute_total_theoretical_cost_for_orders

API_URL = "https://crystal-api.crystalapps.dev"
TARGET_WAREHOUSE = "Finished Goods - CAL"

@frappe.whitelist()
def run_optimizer(filters=None):
    """
    Acts as a Data Aggregator:
    1. Fetches unplanned Sales Orders and raw Stock levels.
    2. Retrieves Regional Optimization Radii from the database.
    3. Offloads all clustering, utilization math, and wait-time logic to the External API.
    """
    if isinstance(filters, str):
        filters = frappe.parse_json(filters) or {}
    elif filters is None:
        filters = {}

    company_name = filters.get("company")
    vehicle_type = filters.get("vehicle_type")
    
    if not company_name or not vehicle_type:
        frappe.throw("Company and Vehicle Type are mandatory to run optimization.")

    factory_lat = flt(frappe.db.get_value("Company", company_name, "custom_latitude"))
    factory_lng = flt(frappe.db.get_value("Company", company_name, "custom_longitude"))

    if not factory_lat or not factory_lng:
        frappe.throw(
            msg=f"Missing GPS Coordinates for Company '{company_name}'. Please set 'custom_latitude' and 'custom_longitude' in the Company settings.",
            title="Routing Configuration Error"
        )

    regions = frappe.get_all("Delivery Region", fields=["name", "custom_optimization_radius"])
    radius_map = {r.name: flt(r.custom_optimization_radius) for r in regions}

    conditions = [
        ["docstatus", "=", 1],
        ["company", "=", company_name]
    ]

    planned_sos = frappe.db.sql("""
        SELECT so.sales_order 
        FROM `tabNexus Load Plan Sales Order` so
        INNER JOIN `tabNexus Load Plan` lp ON so.parent = lp.name
        WHERE lp.docstatus < 2
    """, as_dict=True)
    
    if planned_sos:
        planned_so_names = [d.sales_order for d in planned_sos]
        conditions.append(["name", "not in", planned_so_names])

    if filters.get("customer"): conditions.append(["customer", "=", filters.get("customer")])
    if filters.get("sales_order_status"): conditions.append(["status", "=", filters.get("sales_order_status")])
    if filters.get("territory"): conditions.append(["territory", "=", filters.get("territory")])
    if filters.get("delivery_region"): conditions.append(["custom_delivery_region", "=", filters.get("delivery_region")])
    
    if filters.get("from_date"):
        try: conditions.append(["transaction_date", ">=", getdate(filters["from_date"])])
        except: pass
    if filters.get("to_date"):
        try: conditions.append(["transaction_date", "<", add_to_date(getdate(filters["to_date"]), days=1)])
        except: pass

    sos = frappe.get_all(
        "Sales Order", 
        filters=conditions, 
        fields=["name", "customer", "customer_name", "status", "grand_total", "creation", "payment_terms_template", "custom_delivery_region"]
    )

    if not sos:
        return {"groups": [], "message": "No matching un-planned Sales Orders found."}

    customer_ids = list(set([so.customer for so in sos]))
    customers_data = frappe.get_all(
        "Customer", 
        filters={"name": ("in", customer_ids)}, 
        fields=["name", "custom_latitude", "custom_longitude"]
    )
    customer_coords_map = {
        c.name: {
            "lat": flt(c.custom_latitude), 
            "lng": flt(c.custom_longitude)
        } for c in customers_data
    }

    payload_orders = []
    for so in sos:
        region = so.custom_delivery_region
        if not region:
            frappe.throw(f"Sales Order <b>{so.name}</b> has no Delivery Region assigned.")
        
        radius = radius_map.get(region)
        if not radius or radius <= 0:
            frappe.throw(f"The region <b>{region}</b> (assigned to {so.name}) has no Optimization Radius set.")

        so_doc = frappe.get_doc("Sales Order", so.name)
        items = []
        
        for item in so_doc.items:
            weight = flt(frappe.db.get_value("Item", item.item_code, "weight_per_unit") or 0)
            price_list_rate = flt(frappe.db.get_value("Item Price", {"item_code": item.item_code, "price_list": "Standard Selling"}, "price_list_rate") or item.rate)
            stock = flt(frappe.db.get_value("Bin", {"item_code": item.item_code, "warehouse": TARGET_WAREHOUSE}, "actual_qty") or 0)

            items.append({
                "item_code": item.item_code,
                "qty": flt(item.qty),
                "rate": flt(item.rate),
                "weight_per_unit": weight,
                "default_pl_rate": price_list_rate,
                "net_available": stock
            })

        coords = customer_coords_map.get(so.customer, {"lat": 0.0, "lng": 0.0})

        payload_orders.append({
            "sales_order": so.name,
            "creation": so.creation.isoformat(),
            "customer": so.customer,
            "customer_name": so.customer_name or "",
            "payment_terms": so.payment_terms_template or "Standard / Cash", 
            "latitude": coords["lat"],
            "longitude": coords["lng"],
            "delivery_region": region,
            "optimization_radius": radius,
            "items": items,
            "amount": flt(so.grand_total or 0),
            "so_status": so.status
        })

    vt_doc = frappe.get_doc("Vehicle Type", vehicle_type)
    max_tonnage = flt(vt_doc.max_tonnage) or 1000.0

    payload = {
        "sales_orders": payload_orders,
        "vehicle_max_tonnage": max_tonnage,
        "is_on_collection": filters.get("transport_mode") == "On-Collection"
    }

    try:
        resp = requests.post(f"{API_URL}/optimize", json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        return {
            "groups": data.get("groups", []),
            "factory_lat": factory_lat,
            "factory_lng": factory_lng,
            "debug": data.get("debug", {})
        }

    except Exception as e:
        frappe.log_error(str(e), "Nexus Load Optimizer External API Error")
        return {"error": f"The Optimization Engine is currently unreachable: {str(e)}", "groups": []}


@frappe.whitelist()
def create_load_plan(group, action, route_geojson=None, vehicle_type=None, transport_mode=None, company=None):
    """
    Creates a physical Nexus Load Plan document from optimized group data.
    Server-side authoritative route generation ensures 100% accuracy regardless of UI state.
    """
    if isinstance(group, str):
        group = frappe.parse_json(group)

    plan = frappe.new_doc("Nexus Load Plan")
    plan.company = company or frappe.defaults.get_user_default("Company")
    
    if not plan.company:
        frappe.throw("A valid Company is required to create a Load Plan.")

    plan.vehicle_type = vehicle_type or group.get("vehicle_type")
    plan.transport_mode = transport_mode
    plan.total_tonnage = flt(group.get("total_tonnage", 0))
    plan.total_amount = flt(group.get("total_amount", 0))
    plan.utilization = flt(group.get("utilization", 0))
    plan.max_capacity = flt(group.get("max_capacity", 0))
    plan.reservation_status = "Soft" 

    factory_lat = flt(frappe.db.get_value("Company", plan.company, "custom_latitude"))
    factory_lng = flt(frappe.db.get_value("Company", plan.company, "custom_longitude"))

    if not factory_lat or not factory_lng:
        frappe.throw(f"Missing GPS Coordinates for Company '{plan.company}'. Please configure 'custom_latitude' and 'custom_longitude' in the Company record.")

    FACTORY_COORDS = [factory_lng, factory_lat]
    route_coords = [FACTORY_COORDS]

    for so in group.get("sales_orders", []):
        lat = flt(so.get("latitude", 0.0))
        lng = flt(so.get("longitude", 0.0))

        plan.append("sales_orders", {
            "sales_order": so.get("sales_order"),
            "customer": so.get("customer"),
            "customer_name": so.get("customer_name"),
            "wait_time": so.get("wait_time"),
            "revenue_state": so.get("revenue_state"),
            "readiness": so.get("readiness"),
            "payment_terms": so.get("payment_terms"), 
            "total_weight": flt(so.get("total_weight", 0)),
            "amount": flt(so.get("amount", 0)),
            "latitude": lat,
            "longitude": lng
        })
        
        if lat and lng:
            route_coords.append([lng, lat])

    route_coords.append(FACTORY_COORDS)

    approximate_total_distance_km = 0.0
    approximate_fuel_consumption_ltrs = 0.0
    approximate_fuel_cost = 0.0

    if len(route_coords) >= 3:
        try:
            resp = requests.post(
                f"{API_URL}/calculate-route", 
                json={"coordinates": route_coords}, 
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                if "features" in data:
                    plan.route_geojson = json.dumps(data)
                    
                    try:
                        distance_m = flt(data["features"][0]["properties"]["summary"]["distance"])
                        approximate_total_distance_km = distance_m / 1000.0
                    except (KeyError, IndexError):
                        pass
        except Exception as e:
            frappe.log_error(str(e), "Load Plan Route Generation Failed")

    if plan.vehicle_type:
        try:
            vt = frappe.get_doc("Vehicle Type", plan.vehicle_type)
            km_per_ltr = flt(vt.consumption_km_per_ltr)
            litre_cost = flt(vt.litre_cost)
            
            if km_per_ltr > 0:
                approximate_fuel_consumption_ltrs = approximate_total_distance_km / km_per_ltr
                approximate_fuel_cost = approximate_fuel_consumption_ltrs * litre_cost
        except Exception as e:
            frappe.log_error(str(e), "Load Plan Fuel Economic Calculation Failed")

    plan.approximate_total_distance_km = approximate_total_distance_km
    plan.approximate_fuel_consumption_ltrs = approximate_fuel_consumption_ltrs
    plan.approximate_fuel_cost = approximate_fuel_cost

    sales_orders_data = group.get("sales_orders", [])
    total_order_value = flt(group.get("total_amount", 0))
    if not total_order_value and sales_orders_data:
        total_order_value = sum(flt(so.get("amount", 0)) for so in sales_orders_data)

    total_theoretical_cost = compute_total_theoretical_cost_for_orders(sales_orders_data)

    absorbed_overhead = 0.0
    try:
        active_period = frappe.get_all(
            "Nexus Cost Allocation Period",
            filters={"is_active": 1, "docstatus": 1},
            fields=["total_global_overheads", "total_invoiced_sales"],
            limit=1
        )
        if active_period:
            period = active_period[0]
            total_oh = flt(period.total_global_overheads)
            total_sales = flt(period.total_invoiced_sales)
            
            if total_sales > 0:
                overhead_ratio = total_oh / total_sales
                absorbed_overhead = total_order_value * overhead_ratio
    except Exception:
        frappe.log_error("Failed to calculate absorbed overhead. Check Cost Allocation Period.", "Load Plan Margin")

    gross_profit = total_order_value - total_theoretical_cost
    gross_margin_pct = (gross_profit / total_order_value * 100) if total_order_value > 0 else 0.0
    
    net_profit = gross_profit - absorbed_overhead - approximate_fuel_cost
    net_margin_pct = (net_profit / total_order_value * 100) if total_order_value > 0 else 0.0
    
    profitability_status = "Profitable" if net_profit >= 0 else "Loss"

    plan.total_theoretical_cost = total_theoretical_cost
    plan.daily_overhead_allocated = absorbed_overhead 
    plan.profit_loss = net_profit
    plan.margin_percentage = gross_margin_pct
    plan.net_margin = net_margin_pct
    plan.profitability_status = profitability_status
    # ------------------------------------------------------------------

    plan.insert(ignore_permissions=True)

    if action == "soft":
        frappe.db.commit()
        return {"status": "success", "message": f"Load Plan {plan.name} created successfully with optimized routing."}

    res_doc = frappe.new_doc("Nexus Inventory Reservation")
    res_doc.nexus_load_plan = plan.name
    
    expiry_hours = flt(frappe.db.get_single_value("Reservation Settings", "reservation_expiry_hours")) or 24
    res_doc.expiry_date = add_to_date(now_datetime(), hours=expiry_hours)

    for so in group.get("sales_orders", []):
        so_name = so.get("sales_order")
        for item in so.get("items", []):
            res_doc.append("items", {
                "item_code": item.get("item_code"),
                "sales_order": so_name,
                "required_qty": flt(item.get("qty", 0)),
                "reservation_date": now_datetime()
            })

    res_doc.insert(ignore_permissions=True)
    frappe.db.commit()
    
    return {
        "status": "success", 
        "reservation_id": res_doc.name, 
        "message": f"Plan {plan.name} and Draft Reservation {res_doc.name} created."
    }


@frappe.whitelist()
def reanalyze_load_plan(load_plan_name):
    """
    Refreshes the stock readiness of an existing Load Plan.
    """
    plan = frappe.get_doc("Nexus Load Plan", load_plan_name)
    
    existing_res = frappe.db.get_value("Nexus Inventory Reservation", 
        {"nexus_load_plan": plan.name, "docstatus": 1}, "name")
    
    if existing_res:
        return {"status": "exists", "message": "A submitted reservation already exists for this plan."}

    frappe.db.sql("DELETE FROM `tabNexus Inventory Reservation` WHERE nexus_load_plan = %s AND docstatus = 0", (load_plan_name,))

    res_doc = frappe.new_doc("Nexus Inventory Reservation")
    res_doc.nexus_load_plan = plan.name
    
    for so in plan.sales_orders:
        so_doc = frappe.get_doc("Sales Order", so.sales_order)
        for item in so_doc.items:
            res_doc.append("items", {
                "item_code": item.item_code, 
                "sales_order": so.sales_order, 
                "required_qty": item.qty, 
                "reservation_date": now_datetime()
            })
    
    expiry_hours = flt(frappe.db.get_single_value("Reservation Settings", "reservation_expiry_hours")) or 24
    res_doc.expiry_date = add_to_date(now_datetime(), hours=expiry_hours)
    res_doc.insert(ignore_permissions=True)
    
    from nexus_supply_chain.reservation_hooks import sync_load_plan_status
    sync_load_plan_status(plan.name)
    
    frappe.db.commit()
    return {"status": "success", "message": "Reanalysis complete. Fresh stock check applied."}

@frappe.whitelist()
def get_group_margin_data(group, route_geojson=None, vehicle_type=None):
    """
    Receives a load group and dynamic UI parameters to return:
    - total_order_value
    - total_theoretical_cost
    - gross_profit & gross_margin_percentage
    - overhead_ratio & absorbed_overhead
    - approximate fuel metrics
    - net_profit & net_margin_percentage
    """
    if isinstance(group, str):
        group = frappe.parse_json(group)

    sales_orders = group.get("sales_orders", [])
    if not sales_orders:
        return {"error": "No sales orders in this group."}

    total_order_value = flt(group.get("total_amount", 0))
    if not total_order_value:
        total_order_value = sum(flt(so.get("amount", 0)) for so in sales_orders)

    total_theoretical_cost = compute_total_theoretical_cost_for_orders(sales_orders)

    gross_profit = total_order_value - total_theoretical_cost
    gross_margin_pct = (gross_profit / total_order_value * 100) if total_order_value > 0 else 0.0

    overhead_ratio = 0.0
    absorbed_overhead = 0.0
    
    try:
        active_period = frappe.get_all(
            "Nexus Cost Allocation Period",
            filters={"is_active": 1, "docstatus": 1},
            fields=["total_global_overheads", "total_invoiced_sales"],
            limit=1
        )
        if active_period:
            period = active_period[0]
            total_oh = flt(period.total_global_overheads)
            total_sales = flt(period.total_invoiced_sales)
            
            if total_sales > 0:
                overhead_ratio = (total_oh / total_sales) * 100
                absorbed_overhead = total_order_value * (total_oh / total_sales)
    except Exception:
        frappe.log_error("Failed to fetch active Cost Allocation Period.", "Margin Analysis UI")

    approximate_total_distance_km = 0.0
    if route_geojson:
        try:
            if isinstance(route_geojson, str):
                route_data = json.loads(route_geojson)
            else:
                route_data = route_geojson
                
            distance_m = flt(route_data.get("features", [{}])[0].get("properties", {}).get("summary", {}).get("distance", 0))
            approximate_total_distance_km = distance_m / 1000.0
        except Exception:
            pass

    approximate_fuel_consumption_ltrs = 0.0
    approximate_fuel_cost = 0.0

    if vehicle_type:
        try:
            vt = frappe.get_doc("Vehicle Type", vehicle_type)
            km_per_ltr = flt(vt.consumption_km_per_ltr)
            litre_cost = flt(vt.litre_cost)
            
            if km_per_ltr > 0:
                approximate_fuel_consumption_ltrs = approximate_total_distance_km / km_per_ltr
                approximate_fuel_cost = approximate_fuel_consumption_ltrs * litre_cost
        except Exception:
            pass

    net_profit = gross_profit - absorbed_overhead - approximate_fuel_cost
    net_margin_pct = (net_profit / total_order_value * 100) if total_order_value > 0 else 0.0

    return {
        "total_order_value": total_order_value,
        "total_theoretical_cost": total_theoretical_cost,
        "gross_profit": round(gross_profit, 2),
        "gross_margin_percentage": round(gross_margin_pct, 2),
        "overhead_ratio_percentage": round(overhead_ratio, 2),
        "absorbed_overhead": round(absorbed_overhead, 2),
        "approximate_total_distance_km": round(approximate_total_distance_km, 2),
        "approximate_fuel_consumption_ltrs": round(approximate_fuel_consumption_ltrs, 2),
        "approximate_fuel_cost": round(approximate_fuel_cost, 2),
        "net_profit": round(net_profit, 2),
        "net_margin_percentage": round(net_margin_pct, 2),
        "currency": frappe.defaults.get_user_default("currency") or "KES"
    }
